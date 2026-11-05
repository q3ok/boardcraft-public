"""Versioned JSON representation of a local position, including future chance."""
import random
from dataclasses import fields, is_dataclass
from enum import Enum
from functools import lru_cache
from types import UnionType
from typing import get_args, get_origin, get_type_hints

from boardcraft.contracts import _copy_json
from . import state as state_types
from .actions import Phase
from .board import BUYABLE, GROUPS, TOTAL_HOTELS, TOTAL_HOUSES
from .cards import DECKS, JAIL_FREE_ID
from .dice import FixedDice, RandomDice
from .memory import Memory
from .state import GameState
from .trade import TradeState


def plain(value):
    if is_dataclass(value):
        return {f.name: plain(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, set):
        return [plain(v) for v in sorted(value)]
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return value


@lru_cache(maxsize=None)
def _field_types(cls):
    if cls is GameState:
        return get_type_hints(cls, {**vars(state_types), "Memory": Memory, "TradeState": TradeState})
    return get_type_hints(cls)


def decode(kind, value):
    """Decode only the declared type. No class or import path comes from the document."""
    origin, args = get_origin(kind), get_args(kind)
    if origin is UnionType:
        if value is None and type(None) in args:
            return None
        return decode(next(t for t in args if t is not type(None)), value)
    if is_dataclass(kind):
        shape = _field_types(kind)
        # Legacy positions and hypothetical rule worlds carry no proof of a full start.
        # Keep this exception local; every supplied history field is still mandatory.
        if kind is Memory and type(value) is dict and value.keys() == shape.keys() - {"model_history"}:
            value = {**value, "model_history": None}
        if type(value) is not dict or shape.keys() != value.keys():
            raise ValueError(f"Invalid {kind.__name__} fields")
        return kind(**{key: decode(t, value[key]) for key, t in shape.items()})
    if origin in (list, tuple, set):
        if type(value) is not list:
            raise ValueError("Expected a JSON array")
        if origin is tuple and args[-1] is not Ellipsis:
            if len(value) != len(args):
                raise ValueError("Incorrect tuple length")
            return tuple(decode(t, x) for t, x in zip(args, value))
        items = [decode(args[0], x) for x in value]
        if origin is set and len(set(items)) != len(items):
            raise ValueError("Repeated item in a set")
        return origin(items)
    if origin is dict:
        if type(value) is not dict or any(type(k) is not str for k in value):
            raise ValueError("Expected a JSON object")
        if args[0] is int and any(not k.isascii() or not k.isdigit() or str(int(k)) != k for k in value):
            raise ValueError("Expected a numeric object key")
        return {args[0](k): decode(args[1], v) for k, v in value.items()}
    if isinstance(kind, type) and issubclass(kind, Enum):
        return kind(value)
    if kind is object:
        return _copy_json(value)
    if type(value) is not kind:
        raise ValueError(f"Expected {kind.__name__}")
    return value


def validate_position(s):
    n = s.config.num_players
    seats = range(n)
    if (not 2 <= n <= 4 or len(s.players) != n or s.current not in seats or s.first_player not in seats
            or min(s.turn, s.round) < 1 or s.memory is None):
        raise ValueError("Invalid seat or turn state")
    for i, player in enumerate(s.players):
        if (player.id != i or player.cash < 0 or player.position not in range(40)
                or player.jail_turns not in range(4) or any(k not in DECKS for k in player.jail_cards)):
            raise ValueError("Invalid player state")
    fallen = [p.bankrupt_order for p in s.players if p.bankrupt]
    if (any(order is None for order in fallen) or sorted(fallen) != list(range(1, s.bankrupt_count + 1))
            or any(not p.bankrupt and p.bankrupt_order is not None for p in s.players)
            or any(p.in_jail and p.position != 10 for p in s.players)):
        raise ValueError("Invalid bankruptcy or jail state")
    if (not s.start_rolls or any(seat not in seats or attempt < 1 or any(d not in range(1, 7) for d in dice)
                                 for seat, dice, attempt in s.start_rolls)
            or s.last_roll is not None and any(d not in range(1, 7) for d in s.last_roll)):
        raise ValueError("Invalid recorded dice")
    if (any(seat not in seats or sp not in BUYABLE for seat, sp in s.pending_takeovers)
            or any(seat not in seats or sp not in BUYABLE or amt < 1 for seat, sp, amt in s.takeover_owed)
            or any(seat not in seats for seat in s.trade_requests)
            or any(seat not in seats or not 0 <= count <= s.config.max_trades_per_turn
                   for seat, count in s.requests_this_turn.items())
            or any(sp not in BUYABLE for sp in s.pending_auctions)
            or s.pending_buy is not None and s.pending_buy not in BUYABLE):
        raise ValueError("Invalid queued seat or title")
    payments = s.pending_payments + (s.window.payments if s.window else [])
    for payer, recipient, amount, info in payments:
        if (payer not in seats or recipient is not None and recipient not in seats or amount < 1
                or set(info) - {"space", "reason"} or info.get("reason") not in ("rent", "tax", "card", "jail_fine")
                or "space" in info and (type(info["space"]) is not int or info["space"] not in range(40))):
            raise ValueError("Invalid queued payment")
    if len(s.owner) != 40 or len(s.buildings) != 40 or len(s.mortgaged) != 40:
        raise ValueError("Invalid board dimensions")
    for i, (owner, level, mortgaged) in enumerate(zip(s.owner, s.buildings, s.mortgaged)):
        if (owner is not None and (owner not in seats or i not in BUYABLE or s.players[owner].bankrupt)
                or level not in range(6) or owner is None and (mortgaged or level)
                or level and (mortgaged or not any(i in group for group in GROUPS.values()))):
            raise ValueError("Invalid title deed")
    if (s.bank_houses < 0 or s.bank_hotels < 0 or s.bank_hotels + s.buildings.count(5) != TOTAL_HOTELS
            or s.bank_houses + sum(v for v in s.buildings if v != 5) != TOTAL_HOUSES):
        raise ValueError("Invalid building inventory")
    if set(DECKS) != set(s.decks) or set(DECKS) != set(s.memory.known_bottom):
        raise ValueError("Invalid deck names")
    for name, cards in DECKS.items():
        held = [JAIL_FREE_ID[name] for p in s.players for deck in p.jail_cards if deck == name]
        order = s.decks[name]
        known = s.memory.known_bottom[name]
        if (sorted(order + held) != list(range(len(cards)))
                or known and order[-len(known):] != known):
            raise ValueError("Invalid card inventory or revealed card order")
    if s.auction is not None:
        a = s.auction
        if (a.space not in BUYABLE or not a.bidders or a.turn_idx not in range(len(a.bidders))
                or any(i not in seats for i in a.bidders) or any(i not in seats or v < 0 for i, v in a.trades.items()) or len(set(a.bidders)) != len(a.bidders)
                or a.high_bid not in range(10001) or a.high_bidder is not None and a.high_bidder not in a.bidders):
            raise ValueError("Invalid auction")
    if any(d.debtor not in seats or d.creditor is not None and d.creditor not in seats or d.amount <= 0 for d in s.debts):
        raise ValueError("Invalid outstanding debt")
    if s.trade is not None:
        t = s.trade
        if (not t.history or t.history[-1] != t.offer or t.exchanges != len(t.history)
                or any(o.proposer not in seats or o.target not in seats or o.target == o.proposer
                       or min(o.give_cash, o.get_cash, o.give_cards, o.get_cards) < 0
                       or any(i not in BUYABLE for i in o.get_spaces + o.give_spaces) for o in t.history)):
            raise ValueError("Invalid negotiation")
    if s.window and (any(i not in seats for i in (*s.window.queue, *s.window.requested)) or s.window.then not in
                     ("end_turn", "continue", "payments", "restore") or s.window.kind not in state_types.WINDOW_KINDS):
        raise ValueError("Invalid trade window")
    _validate_memory(s.memory, n)
    _validate_model_history(s)
    _validate_decision_contexts(s)


def _validate_memory(m, n):
    vectors = (m.proposals, m.accepted, m.rejected, m.auctions_won, m.auction_paid, m.auction_list, m.declined)
    matrices = (m.paid, m.offers, m.rejects, m.streak, m.last_cash, m.last_turn, m.accepted_pair, m.countered,
                m.pair_offers)
    if (any(len(v) != n or any(value < 0 for value in v) for v in vectors)
            or any(len(matrix) != n or any(len(row) != n for row in matrix) for matrix in matrices)
            or any(seat not in range(n) for seat in m.auction_bids)
            or any(space not in BUYABLE for space in m.changed)):
        raise ValueError("Invalid play history dimensions")
    for entries in (m.seen_cards, m.card_order, m.cards_drawn, m.known_bottom):
        if entries.keys() != DECKS.keys():
            raise ValueError("Invalid play history decks")
    for name in DECKS:
        if (m.cards_drawn[name] < 0 or not m.seen_cards[name] <= set(range(len(DECKS[name])))
                or not set(m.card_order[name]) <= m.seen_cards[name]):
            raise ValueError("Invalid public card history")


def _validate_model_history(s):
    h = s.memory.model_history
    if h is None:
        return
    n = len(s.players)
    seats = range(n)
    if (h.schema != 1 or h.last_current not in range(-1, n)
            or h.complete_from_start and h.last_current != s.current
            or any(seat not in seats for seat in h.snaps)):
        raise ValueError("Invalid model history header")
    for snap in h.snaps.values():
        vectors = (snap.cash, snap.worth, snap.owned, snap.buildings, snap.mortgaged)
        if (any(len(v) != n or any(x < 0 for x in v) for v in vectors)
                or any(x > 28 for x in snap.owned + snap.mortgaged)
                or any(x > 140 for x in snap.buildings)
                or any(m > o for m, o in zip(snap.mortgaged, snap.owned))
                or len(snap.paid) != n or any(len(row) != n or any(x < 0 for x in row) for row in snap.paid)):
            raise ValueError("Invalid model turn snapshot")
    kinds = {"buy", "decline", "build", "sell_building", "mortgage", "unmortgage", "bid", "auction_won",
             "jail_fine", "use_jail_card", "trade_offer", "trade_counter", "accept_trade", "reject_trade",
             "pass_window", "interest"}
    offers = {"trade_offer", "trade_counter"}
    responses = {"accept_trade", "reject_trade"}
    property_kinds = {"buy", "decline", "build", "mortgage", "unmortgage", "bid", "auction_won", "interest"}
    empty_kinds = {"use_jail_card", "pass_window"}
    counts = [0] * n
    turns = {a.turn for a in h.own_acts}
    if len(turns) > 1:
        raise ValueError("Mixed model action turns")
    for a in h.own_acts:
        if (a.kind not in kinds or a.who not in seats or not 1 <= a.turn <= s.turn
                or a.phase not in {p.value for p in Phase if p is not Phase.GAME_OVER}
                or a.window not in ("", *state_types.WINDOW_KINDS)
                or a.trigger is not None and (a.trigger not in seats or not a.window)
                or a.to is not None and (a.to not in seats or a.to == a.who)
                or a.space is not None and a.space not in BUYABLE
                or a.kind in property_kinds and a.space is None
                or a.kind in empty_kinds | {"jail_fine"} and a.space is not None
                or a.kind in empty_kinds | {"decline"} and a.amount != 0
                or any(sp not in BUYABLE for sp in a.give + a.get)
                or len(set(a.give + a.get)) != len(a.give + a.get)
                or any(c not in range(3) for c in a.cards)
                or a.kind in offers and a.outcome not in ("pending", "accepted", "rejected", "countered")
                or a.kind not in offers and a.outcome != ""
                or a.kind in offers | responses and (a.to is None or a.space is not None)
                or a.kind not in offers | responses and (a.to is not None or a.give or a.get or a.cards != (0, 0) or a.amount < 0)):
            raise ValueError("Invalid accepted model action")
        counts[a.who] += 1
        if counts[a.who] > 64:
            raise ValueError("Too many accepted model actions")


def _validate_decision_contexts(s):
    resources = {
        Phase.AUCTION: s.auction is not None,
        Phase.RAISE_FUNDS: bool(s.debts),
        Phase.TRADE_RESPONSE: s.trade is not None,
        Phase.TRADE_WINDOW: bool(s.window and s.window.queue),
        Phase.BUY_DECISION: s.pending_buy in BUYABLE,
        Phase.MORTGAGE_TAKEOVER: bool(s.pending_takeovers and s.takeover_return),
    }

    def require(phase):
        if phase is Phase.GAME_OVER or not resources.get(phase, True):
            raise ValueError("Missing decision context")

    def require_continuation(phase):
        # The turn continuation closes an empty window and skips a bankrupt player's purchase.
        # Neither case exposes that phase as a decision to a player.
        if phase is Phase.TRADE_WINDOW and s.window is not None:
            return
        if phase is Phase.BUY_DECISION and s.players[s.current].bankrupt:
            return
        require(phase)

    if s.phase is not Phase.GAME_OVER:
        require(s.phase)
    require_continuation(s.resume_phase)
    parents = {}
    if s.trade:
        t = s.trade
        parents[Phase.TRADE_RESPONSE] = (t.resume_phase, t.outer_resume)
        require(t.resume_phase)
        require_continuation(t.outer_resume)
    if s.takeover_return:
        parent, outer = s.takeover_return
        parents[Phase.MORTGAGE_TAKEOVER] = (parent, outer)
        require(parent)
        require_continuation(outer)
    if s.window:
        w = s.window
        parents[Phase.TRADE_WINDOW] = (w.phase, w.resume_phase)
        if w.phase in (Phase.TRADE_RESPONSE, Phase.TRADE_WINDOW, Phase.MORTGAGE_TAKEOVER, Phase.GAME_OVER):
            raise ValueError("Invalid trade window parent")
        # A completed auction can open an acquisition window. A live request window can also outlast
        # its auction or debt. The corresponding continuation advances the queues when they are empty.
        advances_queue = (w.phase is Phase.AUCTION and w.then in ("continue", "restore")
                          or w.phase is Phase.RAISE_FUNDS and w.then == "restore")
        if not advances_queue:
            if w.then == "restore":
                require_continuation(w.phase)
            else:
                require(w.phase)
        require_continuation(w.resume_phase)

    def check_chain(phase, visited):
        if phase in visited:
            raise ValueError("Cyclic decision return context")
        for parent in parents.get(phase, ()):
            check_chain(parent, visited | {phase})

    for phase in parents:
        check_chain(phase, set())


def chance_data(game):
    dice = game.dice
    if isinstance(dice, FixedDice):
        fixed = {"rolls": plain(dice.rolls), "position": dice.pos}
    elif isinstance(dice, RandomDice) and dice.rng is game.rng:
        fixed = None
    else:
        raise ValueError("Unsupported dice source for a snapshot")
    return {"rng": plain(game.rng.getstate()), "fixed": fixed}


def restore_chance(data):
    if type(data) is not dict or data.keys() != {"rng", "fixed"}:
        raise ValueError("Invalid chance state")
    rng = random.Random(0)
    version, numbers, spare = data["rng"]
    if type(version) is not int or type(numbers) is not list or spare is not None:
        raise ValueError("Invalid random generator state")
    rng.setstate((version, tuple(numbers), spare))
    if data["fixed"] is None:
        return rng, RandomDice(rng)
    fixed = data["fixed"]
    rolls = decode(list[tuple[int, int]], fixed["rolls"])
    if (any(d not in range(1, 7) for roll in rolls for d in roll) or type(fixed["position"]) is not int
            or fixed["position"] not in range(len(rolls) + 1)):
        raise ValueError("Invalid scripted dice")
    dice = FixedDice(rolls)
    dice.pos = fixed["position"]
    return rng, dice

