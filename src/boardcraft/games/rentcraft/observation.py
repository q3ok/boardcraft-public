"""Perspective-safe board state, receipts and simulation from public knowledge."""
import random

from boardcraft.contracts import Event, Observation
from .actions import Phase
from .board import BOARD, BUYABLE, GROUPS
from .cards import DECKS, JAIL_FREE_ID
from .dice import RandomDice
from .memory import Memory, LogEntry, PairOffer
from .persistence import decode, plain
from .state import AuctionState, Debt, GameState, PlayerState, TradeWindow, WINDOW_KINDS
from .trade import TradeOffer, TradeState


# Only these fields are part of the public observation protocol.
VISIBLE = {
    GameState: "players owner buildings mortgaged bank_houses bank_hotels current phase doubles last_roll roll_count "
               "round turn auction debts pending_auctions pending_buy pending_move extra_roll bankrupt_count "
               "by_round_limit resume_phase trade trades_this_turn window pending_payments trade_requests "
               "windows_opened requests_this_turn memory first_player start_rolls pending_takeovers takeover_owed "
               "takeover_return takeover_trades",
    PlayerState: "id cash position in_jail jail_turns jail_cards bankrupt bankrupt_order",
    Debt: "debtor creditor amount trades",
    AuctionState: "space bidders turn_idx high_bid high_bidder trades",
    TradeState: "offer resume_phase history exchanges outer_resume in_window",
    TradeOffer: "proposer target give_spaces get_spaces give_cash get_cash give_cards get_cards",
    TradeWindow: "kind trigger queue then phase resume_phase serial payments space requested in_auction",
    Memory: "proposals accepted rejected auctions_won auction_paid auction_list paid recent offers rejects streak "
            "last_cash last_turn changed changed_turn seen_cards sent_offers sent_turn log accepted_pair countered "
            "declined card_order cards_drawn pair_offers auction_bids known_bottom",
    LogEntry: "kind who to space card give get truncated bids amount round turn",
    PairOffer: "give get cash cards outcome round counter ctx",
}
VISIBLE = {cls: tuple(names.split()) for cls, names in VISIBLE.items()}


def visible(value):
    if type(value) in VISIBLE:
        return {k: visible(getattr(value, k)) for k in VISIBLE[type(value)]}
    if isinstance(value, (list, tuple, set)):
        items = sorted(value) if isinstance(value, set) else value
        return [visible(v) for v in items]
    if isinstance(value, dict):
        return {str(k): visible(v) for k, v in value.items()}
    if isinstance(value, Phase):
        return value.value
    if value is None or type(value) in (str, bool, int):
        return value
    raise ValueError("Unexpected public observation field")


EVENT_FIELDS = {
    "start_roll": "dice tiebreak", "start_player": "total", "roll": "dice for_card?",
    "move": "to", "pass_go": "amount", "jail": "", "jail_release": "",
    "card": "deck card text", "buy": "space price", "decline": "space",
    "bid": "space bid", "pass_bid": "space", "auction_won": "space price",
    "auction_unsold": "space", "pay": "to amount reason? space?", "debt": "to amount reason? space?",
    "build": "space level cost", "sell_building": "space? level? group? levels? refund",
    "mortgage": "space amount", "unmortgage": "space amount takeover?",
    "trade_offer": "offer", "trade_counter": "offer", "trade": "offer", "trade_reject": "offer",
    "trade_request": "", "trade_window": "window space amount queue", "trade_window_end": "window",
    "resign": "", "bankrupt": "to spaces creditors?", "game_over": "",
}


def event_fields(kind, data):
    fields = [key.rstrip("?") for key in EVENT_FIELDS[kind].split()]
    payload = {k: visible(data[k]) for k in fields if k in data and k != "creditors"}
    if "creditors" in fields and "creditors" in data:
        payload["creditors"] = [{"seat": k, "amount": v} for k, v in data["creditors"].items()]
    return payload


def receipt(events, cursor):
    return tuple(Event(cursor + i, e.kind, {"player": e.player, **event_fields(e.kind, e.data)})
                 for i, e in enumerate(events, 1))


def observe(engine, viewer):
    engine.validate_viewer(viewer)
    data = visible(engine.game.state)
    data.update(settings=engine.config.settings, variant=engine.config.variant, event_cursor=engine.cursor,
                active_player=engine.game.state.current)
    d = engine.decision()
    data["acting_seat"] = d.player if d else None
    data["commands"] = list(engine.command_specs(viewer))
    h = engine.game.state.memory.model_history
    data["model_context"] = {
        "schema": 1, "complete_from_start": h is not None and h.complete_from_start,
        "turn": engine.game.state.turn,
        "snapshot": plain(h.snaps.get(viewer)) if h is not None and viewer is not None else None,
        "own_acts": [plain(a) for a in h.own_acts if a.who == viewer and a.turn == engine.game.state.turn] if h else [],
    }
    if (outcome := engine.result()) is not None:
        data["result"] = plain(outcome)
    return Observation("rentcraft", "1", "1", viewer, engine.revision,
                       d if d and d.player == viewer else None, data)


def require_model_context(observation):
    """Refuse model play when the engine cannot certify history from the start."""
    if observation.viewer is None:
        raise ValueError("Model context requires a player seat")
    context = observation.data.get("model_context")
    if (not isinstance(context, dict) or context.get("schema") != 1
            or context.get("complete_from_start") is not True):
        raise ValueError("Model play requires complete history from the start")
    return context


def sample_decks(players, known_bottom, rng):
    """Sample unknown cards, retaining public returns at the bottom in their known order."""
    decks = {}
    for name, cards in DECKS.items():
        held = {JAIL_FREE_ID[name]} if any(name in p.jail_cards for p in players) else set()
        known = known_bottom[name]
        unknown = [c.id for c in cards if c.id not in held and c.id not in known]
        rng.shuffle(unknown)
        decks[name] = unknown + list(known)
    return decks


def hypothetical_game(observation: Observation, seed: int):
    """Build a detached rules position using a player's own random source."""
    from .engine import Game
    from .module import game_settings
    from boardcraft.contracts import GameConfig

    if observation.game_id != "rentcraft" or observation.observation_version != "1":
        raise ValueError("Unknown Rentcraft observation")
    data = observation.data
    config = GameConfig("rentcraft", data["variant"], len(data["players"]), data["settings"])
    rules = game_settings(config, seed)
    public = {k: data[k] for k in VISIBLE[GameState]}
    players = decode(list[PlayerState], public["players"])
    rng = random.Random(seed)
    decks = sample_decks(players, public["memory"]["known_bottom"], rng)
    state = decode(GameState, {**public, "config": plain(rules), "decks": decks})
    return Game(state, rng, RandomDice(rng))



def validate_saved_payload(kind, payload, seats):
    """Validate public content before treating a restored receipt as engine output."""
    spec = EVENT_FIELDS[kind].split()
    required = {"player", *(key for key in spec if not key.endswith("?"))}
    allowed = {"player", *(key.rstrip("?") for key in spec)}
    if type(payload) is not dict or not required <= payload.keys() <= allowed:
        raise ValueError("Invalid saved event fields")

    def number(value, choices=None):
        return type(value) is int and value >= 0 and (choices is None or value in choices)

    def seat(value):
        return number(value, range(seats))

    def indices(value, choices):
        return (type(value) is list and len(value) <= len(choices)
                and all(number(item, choices) for item in value) and len(set(value)) == len(value))

    p = payload
    nullable_actor = kind in ("auction_unsold", "trade_window", "trade_window_end")
    if not (seat(p["player"]) or nullable_actor and p["player"] is None):
        raise ValueError("Invalid event actor")
    for key in ("amount", "cost", "refund", "price", "bid"):
        if key in p and not number(p[key]):
            raise ValueError("Invalid event amount")
    if "to" in p:
        valid = number(p["to"], range(len(BOARD))) if kind == "move" else p["to"] is None or seat(p["to"])
        if not valid:
            raise ValueError("Invalid event destination")
    if "space" in p and not (kind == "trade_window" and p["space"] is None
                             or number(p["space"], range(len(BOARD)))):
        raise ValueError("Invalid event space")
    if "dice" in p and (type(p["dice"]) is not list or len(p["dice"]) != 2
                        or any(not number(d, range(1, 7)) for d in p["dice"])):
        raise ValueError("Invalid dice in event")
    if "tiebreak" in p and (not number(p["tiebreak"]) or p["tiebreak"] == 0):
        raise ValueError("Invalid opening roll")
    if "total" in p and not number(p["total"], range(2, 13)):
        raise ValueError("Invalid opening total")
    if any(key in p and type(p[key]) is not bool for key in ("for_card", "takeover")):
        raise ValueError("Invalid event flag")
    if "reason" in p and p["reason"] not in ("auction", "bankruptcy", "card", "interest", "jail_fine", "rent", "tax"):
        raise ValueError("Unknown payment reason")
    if kind == "card" and (p["deck"] not in DECKS or not number(p["card"], range(len(DECKS[p["deck"]])))
                           or p["text"] != DECKS[p["deck"]][p["card"]].text):
        raise ValueError("Invalid revealed card")
    if "window" in p and p["window"] not in WINDOW_KINDS or "queue" in p and not indices(p["queue"], range(seats)):
        raise ValueError("Invalid event window")
    if "level" in p and not number(p["level"], range(6)):
        raise ValueError("Invalid building level")
    if kind == "sell_building":
        details = p.keys() - {"player", "refund"}
        if details not in ({"space", "level"}, {"group", "levels"}):
            raise ValueError("Invalid building sale")
        if "group" in p and (p["group"] not in GROUPS or type(p["levels"]) is not list
                             or len(p["levels"]) != len(GROUPS[p["group"]])
                             or any(not number(v, range(5)) for v in p["levels"])):
            raise ValueError("Invalid sale of a building group")
    if "spaces" in p and not indices(p["spaces"], BUYABLE):
        raise ValueError("Invalid property transfer")
    if "creditors" in p:
        creditors = p["creditors"]
        if (type(creditors) is not list or not 1 <= len(creditors) <= seats + 1
                or any(type(row) is not dict or row.keys() != {"seat", "amount"} or not number(row["amount"])
                       or row["seat"] is not None and not seat(row["seat"]) for row in creditors)
                or len({row["seat"] for row in creditors}) != len(creditors)):
            raise ValueError("Invalid creditor shares")
    if "offer" in p:
        offer = decode(TradeOffer, p["offer"])
        if (not seat(offer.proposer) or not seat(offer.target) or offer.proposer == offer.target
                or not indices(p["offer"]["give_spaces"] + p["offer"]["get_spaces"], BUYABLE)
                or min(offer.give_cash, offer.get_cash, offer.give_cards, offer.get_cards) < 0
                or max(offer.give_cards, offer.get_cards) > len(DECKS) or offer.is_empty()):
            raise ValueError("Invalid published offer")
