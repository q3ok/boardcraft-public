"""Decision-local standard-v2 drafts and a seat-owned memory-note overlay."""
from collections.abc import Callable
from copy import copy
from dataclasses import dataclass, field, replace

import numpy as np

from ..memory import ModelAct, ModelHistory, ModelSnapshot, act_context, sent_this_turn, offer_key
from ..board import BOARD, GROUPS
from ..actions import Action, ActionKind, Phase
from ..engine import Game
from ..board import BUYABLE
from ..liquidity import bank_funds
from ..trade import TRADE_PHASES, TradeOffer, trade_proposer, trades_used, validate_offer
from .actions import StandardActionSpaceV1

BUYABLE_LIST = sorted(BUYABLE)
BUYABLE_POS = {s: k for k, s in enumerate(BUYABLE_LIST)}
DRAFT_CASH = (25, 100, 500)
MAX_EDIT_STEPS = 16
MAX_DRAFT_OPENS = 3

DRAFT_ORIGINS = ("candidate", "generated_counter", "empty", "received_offer")

STEP_KINDS = ("give_property", "get_property", "give_cash", "get_cash", "clear_cash", "give_card", "get_card")

@dataclass(frozen=True)
class PrivateAct(ModelAct):
    """One seat's accepted action with bot-only notes, or an abandoned draft."""

    origin: str = ""
    steps: int = 0


def _act_key(act):
    # Outcomes change in place in engine history; they do not identify a new action.
    return (act.kind, act.who, act.turn, act.to, act.space, act.give, act.get,
            act.amount, act.cards, act.phase, act.window, act.trigger)


class NoteOverlay:
    """Merge engine evidence and private notes in source insertion order, bounded at 64.

    Engine rows are reconciled by their ordered overlap, including rows already
    displaced by private notes. Updating an outcome never loses origin or steps.
    A bot owns one instance per seat; a DecisionGame never owns persistent drafts.
    """

    def __init__(self, seat):
        self.seat = seat
        self.turn = -1
        self.accepted = []
        self.accepted_ids = []
        self.sequence = 0
        self.records = []
        self.acts = []
        self.pending = None
        self.final_kind = None
        self.revision = None
        self.accepted_tail = None

    def _publish(self):
        del self.records[:-64]
        self.acts[:] = [act for _, act in self.records]

    def sync(self, turn, accepted, revision=None):
        tail = accepted[-1] if accepted else None
        changed = revision != self.revision if revision is not None else tail is not self.accepted_tail
        kinds = {"decline": "decline_buy", "jail_fine": "pay_jail_fine", "trade_offer": "offer",
                 "trade_counter": "counter_offer", "interest": "pay_interest"}
        accepted = [replace(a, kind=kinds.get(a.kind, a.kind)) for a in accepted]
        if turn != self.turn:
            self.turn = turn
            self.accepted = []
            self.accepted_ids = []
            self.records = []
            self.pending = None
            self.sequence = 0
            self.final_kind = None
        old_keys = [_act_key(a) for a in self.accepted]
        keys = [_act_key(a) for a in accepted]
        overlap = min(len(keys), len(old_keys))
        while overlap and old_keys[-overlap:] != keys[:overlap]:
            overlap -= 1
        # At the engine's 64-row boundary, identical values cannot distinguish a
        # new accepted command. A returned command plus fresh engine evidence can.
        if (overlap == len(keys) == 64 and changed and self.final_kind == accepted[-1].kind):
            overlap = 63
        ids = self.accepted_ids[-overlap:] if overlap else []
        updates = dict(zip(ids, accepted[:overlap]))
        self.records = [(i, replace(a, outcome=updates[i].outcome) if i in updates else a)
                        for i, a in self.records]
        for act in accepted[overlap:]:
            self.sequence += 1
            ids.append(self.sequence)
            private = PrivateAct(**vars(act))
            if act.kind in ("offer", "counter_offer") and self.pending is not None:
                sent_turn, key, origin, steps = self.pending
                if act.who == key[0]:
                    same = (sent_turn == turn and act.to == key[1]
                            and tuple(sorted(act.give)) == key[2] and tuple(sorted(act.get)) == key[3]
                            and act.cards == key[6:] and abs(act.amount - (key[4]-key[5])) < 25)
                    if same:
                        private = replace(private, origin=origin, steps=steps)
                    self.pending = None
            self.records.append((self.sequence, private))
        self.accepted, self.accepted_ids = list(accepted), ids
        self.accepted_tail, self.revision = tail, revision
        if changed:
            self.final_kind = None
        self._publish()

    def abandon(self, act):
        if act.who != self.seat or act.turn != self.turn:
            raise ValueError("Private notes must belong to this seat and turn")
        self.records.append((None, act))
        self._publish()

    def sent(self, turn, proposer, offer, origin, steps):
        if proposer != self.seat:
            raise ValueError("Draft notes must belong to this seat")
        self.pending = (turn, offer_key(offer), origin, steps)

    def final(self, action, phase):
        kind = action.kind.value
        self.final_kind = {"decline": "decline_buy", "pay_jail_fine": "pay_jail_fine",
                           "propose_trade": "offer", "counter_trade": "counter_offer",
                           "pass": "pass_window" if phase is Phase.TRADE_WINDOW else "pay_interest"}.get(kind, kind)


class DecisionGame:
    """Read-only rules facade and fresh draft workspace for exactly one decision."""

    def __init__(self, state, decision, overlay=None, *, revision=None):
        history = state.memory.model_history
        if history is None or not history.complete_from_start:
            raise ValueError("Model play requires complete history from the start")
        seat = decision.player if decision is not None else state.current
        self.overlay = overlay if overlay is not None else NoteOverlay(seat)
        if self.overlay.seat != seat:
            raise ValueError("Model notes belong to a different seat")
        self.overlay.sync(state.turn, [a for a in history.own_acts if a.who == seat and a.turn == state.turn], revision)
        self.state = copy(state)
        self.state.memory = copy(state.memory)
        self.state.memory.model_acts = self.overlay.acts
        from ..persistence import plain
        self.state.memory.model_snaps = {seat: plain(history.snaps[seat])} if seat in history.snaps else {}
        self._decision = decision
        self._game = Game.__new__(Game)
        self._game.state = self.state
        self.trade_draft = None
        self.trade_draft_opens = None

    @classmethod
    def from_observation(cls, observation, overlay=None):
        from boardcraft.contracts import GameConfig
        from ..module import game_settings
        from ..observation import VISIBLE, require_model_context
        from ..persistence import decode, plain
        from ..state import GameState

        context = require_model_context(observation)
        data = observation.data
        config = GameConfig("rentcraft", data["variant"], len(data["players"]), data["settings"])
        rules = game_settings(config, 0)
        public = {key: data[key] for key in VISIBLE[GameState]}
        state = decode(GameState, {**public, "config": plain(rules), "decks": {}})
        state.memory.model_history = ModelHistory(
            complete_from_start=True,
            snaps={observation.viewer: decode(ModelSnapshot, context["snapshot"])} if context["snapshot"] else {},
            own_acts=decode(list[ModelAct], context["own_acts"]))
        game = Game.__new__(Game)
        game.state = state
        decision = game.decision()
        if decision is None or decision.player != observation.viewer:
            raise ValueError("A model needs its own current decision")
        return cls(state, decision, overlay, revision=observation.revision)

    def decision(self):
        return self._decision

    def legal_actions(self):
        return self._decision.actions if self._decision is not None else ()

    def can_propose(self):
        return self._game.can_propose()

    def can_counter(self):
        return self._game.can_counter()

    def validate_trade(self, offer):
        return self._game.validate_trade(offer)


@dataclass(frozen=True)
class DraftStep:

    kind: str
    space: int | None
    sign: int
    amount: int
    net: int

@dataclass
class TradeDraft:

    proposer: int
    target: int
    counter: bool
    give: set = field(default_factory=set)
    get: set = field(default_factory=set)
    give_cash: int = 0
    get_cash: int = 0
    give_cards: int = 0
    get_cards: int = 0
    steps: int = 0

    origin: str = field(default="", compare=False)
    start: TradeOffer | None = field(default=None, compare=False)

    history: list = field(default_factory=list, compare=False)

    turn: int = field(default=-1, compare=False)
    context: tuple | None = field(default=None, compare=False)

    def abandoned(self) -> PrivateAct:

        o = self.offer()
        return PrivateAct("abandoned_draft", self.proposer, self.turn, self.target, None, o.give_spaces, o.get_spaces,
                       self.net_cash(), (o.give_cards, o.get_cards), origin=self.origin, steps=self.steps)

    @classmethod
    def from_offer(cls, offer: TradeOffer, counter: bool = False) -> "TradeDraft":
        return cls(offer.proposer, offer.target, counter, set(offer.give_spaces), set(offer.get_spaces),
                   offer.give_cash, offer.get_cash, offer.give_cards, offer.get_cards)

    @classmethod
    def reply_to(cls, offer: TradeOffer) -> "TradeDraft":

        return cls(offer.target, offer.proposer, True, set(offer.get_spaces), set(offer.give_spaces),
                   offer.get_cash, offer.give_cash, offer.get_cards, offer.give_cards)

    def net_cash(self) -> int:

        return self.give_cash - self.get_cash

    def offer(self) -> TradeOffer:
        net = self.net_cash()
        return TradeOffer(self.proposer, self.target, tuple(sorted(self.give)), tuple(sorted(self.get)),
                          max(net, 0), max(-net, 0), self.give_cards, self.get_cards)

    def modified(self) -> bool:

        return self.start is None or self.offer() != self.start

    def own_work(self) -> bool:

        return self.origin == "empty" or self.modified()

def get_draft(game) -> TradeDraft | None:
    return getattr(game, "trade_draft", None)

def _note_abandoned7(game, draft):
    turn = draft.turn if draft.turn >= 0 else game.state.turn
    if turn == game.state.turn:
        phase, window, trigger = draft.context or act_context(game.state)
        game.overlay.abandon(replace(draft.abandoned(), turn=turn, phase=phase, window=window, trigger=trigger))


def set_draft(game, draft):
    game.trade_draft = draft


def _tradeable(state, space: int, who: int) -> bool:

    if state.owner[space] != who:
        return False
    group = BOARD[space].group
    return not (group and any(state.buildings[j] for j in GROUPS[group]))

class TradeBuilderActionSpaceV2(StandardActionSpaceV1):
    name = "standard-v2"

    def __init__(self):
        super().__init__()
        o = self.counter_index + 1
        self.empty0 = o
        self.counter_received = o + 3
        self.toggle0 = o + 4
        self.cash0 = self.toggle0 + len(BUYABLE_LIST)
        self.clear_cash = self.cash0 + 2 * len(DRAFT_CASH)
        self.card_give = self.clear_cash + 1
        self.card_get = self.card_give + 1
        self.send = self.card_get + 1
        self.cancel = self.send + 1
        self.n = self.cancel + 1

    def mask(self, game: Game) -> np.ndarray:
        draft = get_draft(game)
        d = game.decision()
        if draft is not None:
            return self._draft_mask(game, draft)
        m = super().mask(game)
        if d is None:
            return m
        if not self._opens_left(game):
            m[self.propose0 : self.counter_index + 1] = False
            return m
        n = len(game.state.players)
        if d.phase in TRADE_PHASES and game.can_propose():
            for r in range(1, min(n, 4)):
                if not game.state.players[(d.player + r) % n].bankrupt:
                    m[self.empty0 + r - 1] = True
        elif d.phase is Phase.TRADE_RESPONSE and game.can_counter():
            m[self.counter_received] = True
        return m

    def _draft_mask(self, game: Game, draft: TradeDraft) -> np.ndarray:
        m = np.zeros(self.n, dtype=bool)
        m[self.cancel] = True
        s = game.state
        offer = draft.offer()

        m[self.send] = validate_offer(s, offer, raisable=True) is None and not sent_this_turn(s, offer)
        if draft.steps >= MAX_EDIT_STEPS:

            m[self.send] = False
            return m
        for k, sp in enumerate(BUYABLE_LIST):
            if sp in draft.give or sp in draft.get or _tradeable(s, sp, draft.proposer) or _tradeable(s, sp, draft.target):
                m[self.toggle0 + k] = True
        net = draft.net_cash()
        target_most = bank_funds(s, draft.target).max_cash
        for k, step in enumerate(DRAFT_CASH):
            m[self.cash0 + k] = net + step <= s.players[draft.proposer].cash
            m[self.cash0 + len(DRAFT_CASH) + k] = step - net <= target_most
        m[self.clear_cash] = bool(draft.give_cash or draft.get_cash)
        m[self.card_give] = bool(draft.give_cards or s.players[draft.proposer].jail_cards)
        m[self.card_get] = bool(draft.get_cards or s.players[draft.target].jail_cards)
        return m

    @staticmethod
    def _open_key(game) -> tuple:
        s = game.state

        bid = s.auction.high_bid if s.phase is Phase.AUCTION and s.auction is not None else 0
        debt = len(s.debts) if s.phase is Phase.RAISE_FUNDS else 0
        window = s.window.serial if s.window is not None else 0
        return (s.turn, s.phase, trade_proposer(s), trades_used(s), bid, debt, s.trade.exchanges if s.trade else 0, window)

    def _opens_left(self, game) -> bool:
        opens = getattr(game, "trade_draft_opens", None)
        return opens is None or opens[0] != self._open_key(game) or opens[1] < MAX_DRAFT_OPENS

    def _count_open(self, game) -> None:
        key, opens = self._open_key(game), getattr(game, "trade_draft_opens", None)
        game.trade_draft_opens = (key, opens[1] + 1 if opens and opens[0] == key else 1)

    def is_trade_start(self, i: int) -> bool:
        return self.propose0 <= i <= self.counter_received

    def to_action(self, game: Game, i: int):
        action = self._to_action(game, i)
        if action is not None:
            game.overlay.final(action, game.state.phase)
        return action

    def _to_action(self, game: Game, i: int):
        draft = get_draft(game)
        if draft is None:
            if i < self.propose0:
                return self.fixed_action(game, i)
            player = game.decision().player
            if i < self.counter_index:
                draft = TradeDraft.from_offer(self.proposal_candidates(game, player)[i - self.propose0])
                draft.origin = "candidate"
            elif i == self.counter_index:
                draft = TradeDraft.from_offer(self.counter_candidate(game, player), counter=True)
                draft.origin = "generated_counter"
            elif i < self.counter_received:
                draft = TradeDraft(player, (player + i - self.empty0 + 1) % len(game.state.players), counter=False)
                draft.origin = "empty"
            elif i == self.counter_received:
                draft = TradeDraft.reply_to(game.state.trade.offer)
                draft.origin = "received_offer"
            else:
                raise ValueError(f"Action {i} requires an open offer draft.")
            draft.start = draft.offer()
            draft.turn, draft.context = game.state.turn, act_context(game.state)
            self._count_open(game)
            set_draft(game, draft)
            return None
        if i == self.send:
            set_draft(game, None)
            offer = draft.offer()
            game.overlay.sent(game.state.turn, draft.proposer, offer, draft.origin, draft.steps)
            return Action(ActionKind.COUNTER_TRADE if draft.counter else ActionKind.PROPOSE_TRADE, trade=offer)
        if i == self.cancel:
            set_draft(game, None)
            _note_abandoned7(game, draft)
            return None
        draft.steps += 1
        step = None
        if self.toggle0 <= i < self.cash0:
            sp = BUYABLE_LIST[i - self.toggle0]
            if sp in draft.give or sp in draft.get:
                step = ("give_property" if sp in draft.give else "get_property", sp, -1, 0)
                draft.give.discard(sp)
                draft.get.discard(sp)
            else:
                mine = game.state.owner[sp] == draft.proposer
                step = ("give_property" if mine else "get_property", sp, 1, 0)
                (draft.give if mine else draft.get).add(sp)
        elif self.cash0 <= i < self.clear_cash:
            k = i - self.cash0
            if k < len(DRAFT_CASH):
                draft.give_cash += DRAFT_CASH[k]
                step = ("give_cash", None, 1, DRAFT_CASH[k])
            else:
                draft.get_cash += DRAFT_CASH[k - len(DRAFT_CASH)]
                step = ("get_cash", None, 1, DRAFT_CASH[k - len(DRAFT_CASH)])
        elif i == self.clear_cash:
            step = ("clear_cash", None, -1, draft.net_cash())
            draft.give_cash = draft.get_cash = 0
        elif i == self.card_give:
            draft.give_cards = 0 if draft.give_cards else 1
            step = ("give_card", None, 1 if draft.give_cards else -1, 0)
        elif i == self.card_get:
            draft.get_cards = 0 if draft.get_cards else 1
            step = ("get_card", None, 1 if draft.get_cards else -1, 0)
        if step is not None:
            draft.history.append(DraftStep(*step, draft.net_cash()))
        return None

def without_trade_starts(space, game, mask: np.ndarray) -> np.ndarray:

    if get_draft(game) is not None:
        return mask
    m = mask.copy()
    m[space.propose0 : getattr(space, "counter_received", space.counter_index) + 1] = False
    return m if m.any() else mask

def choose_index(space, game, pick: Callable[[np.ndarray], int], limit: int = 64):

    chosen = []
    for _ in range(limit):
        i = pick(space.mask(game))
        chosen.append(i)
        action = space.to_action(game, i)
        if action is not None:
            return action, chosen
    set_draft(game, None)
    raise RuntimeError("Model did not finish building an offer.")
