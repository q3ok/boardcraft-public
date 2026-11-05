"""Negotiate transfers of titles, cash and jail release cards."""

from dataclasses import dataclass, field

from .actions import Event, Phase
from .board import BOARD, GROUPS
from .liquidity import bank_funds
from .state import GameState


@dataclass(frozen=True)
class TradeOffer:

    proposer: int
    target: int
    give_spaces: tuple[int, ...] = ()
    get_spaces: tuple[int, ...] = ()
    give_cash: int = 0
    get_cash: int = 0
    give_cards: int = 0
    get_cards: int = 0

    def is_empty(self) -> bool:
        return not (self.give_spaces or self.get_spaces or self.give_cash or self.get_cash or self.give_cards or self.get_cards)


@dataclass
class TradeState:
    offer: TradeOffer
    resume_phase: Phase
    history: list[TradeOffer] = field(default_factory=list)
    exchanges: int = 1
    outer_resume: Phase | None = None
    in_window: bool = False


class OfferProblem(str):

    code: str
    fields: dict

    def __new__(cls, text: str, code: str = "INVALID_OFFER", **fields):
        obj = super().__new__(cls, text)
        obj.code = code
        obj.fields = fields
        return obj


def _side_problem(state: GameState, who: int, spaces: tuple[int, ...], cash: int, cards: int,
                  recipient: bool = False, raisable: bool = False) -> str | None:
    p = state.players[who]
    if len(set(spaces)) != len(spaces):
        return OfferProblem("A space appears twice.", "DUPLICATE_SPACE")
    for sp in spaces:
        if not 0 <= sp < len(BOARD) or state.owner[sp] != who:
            name = BOARD[sp].name if 0 <= sp < len(BOARD) else str(sp)
            return OfferProblem(f"Seat {who} does not own {name}.", "SPACE_NOT_OWNED", space=sp, player=who)
        group = BOARD[sp].group
        if group and any(state.buildings[j] for j in GROUPS[group]):
            return OfferProblem(f"Sell the buildings in the group containing {BOARD[sp].name} first.",
                                "BUILDINGS_IN_COLOR", space=sp, player=who)
    if cash < 0 or cards < 0:
        return OfferProblem("Cash and card counts must be nonnegative.", "NEGATIVE_AMOUNT")
    if cash > p.cash:
        if not raisable:
            code = "RECIPIENT_CASH_TOO_LOW" if recipient else "CASH_TOO_LOW"
            return OfferProblem(f"Seat {who} has only ${p.cash} available.",
                                code, player=who, requested=cash, available=p.cash)
        most = bank_funds(state, who).max_cash
        if cash > most:
            return OfferProblem(
                f"Seat {who} could raise only ${most} from the bank.",
                "RECIPIENT_CASH_TOO_LOW", player=who, requested=cash, available=p.cash, max_raisable=most)
    if cards > len(p.jail_cards):
        return OfferProblem(f"Seat {who} lacks enough jail release cards.", "NOT_ENOUGH_CARDS",
                            player=who, requested=cards, available=len(p.jail_cards))
    return None


TRADE_PHASES = (Phase.PRE_ROLL, Phase.POST_ROLL, Phase.BUY_DECISION, Phase.RAISE_FUNDS, Phase.AUCTION, Phase.TRADE_WINDOW,
                Phase.MORTGAGE_TAKEOVER)


def trade_proposer(state: GameState) -> int | None:
    if state.trade is not None or state.phase not in TRADE_PHASES:
        return None
    if state.phase is Phase.MORTGAGE_TAKEOVER:
        return state.pending_takeovers[0][0] if state.pending_takeovers else None
    if state.phase is Phase.RAISE_FUNDS:
        return state.debts[0].debtor if state.debts else None
    if state.phase is Phase.AUCTION:
        a = state.auction
        return a.bidders[a.turn_idx] if a is not None and a.bidders else None
    if state.phase is Phase.TRADE_WINDOW:
        w = state.window
        return w.queue[0] if w is not None and w.queue else None
    return state.current


def trades_used(state: GameState) -> int:
    if state.phase is Phase.RAISE_FUNDS and state.debts:
        return state.debts[0].trades
    if state.phase is Phase.MORTGAGE_TAKEOVER:
        return state.takeover_trades
    if state.phase is Phase.AUCTION and state.auction is not None:
        return state.auction.trades.get(trade_proposer(state), 0)
    if state.phase is Phase.TRADE_WINDOW:
        return 0
    return state.trades_this_turn


def trade_limit(state: GameState) -> int:
    return 1 if state.phase is Phase.TRADE_WINDOW else state.config.max_trades_per_turn


def has_tradeables(state: GameState, who: int) -> bool:
    p = state.players[who]
    if p.bankrupt:
        return False
    if p.cash > 0 or p.jail_cards:
        return True
    for sp, owner in enumerate(state.owner):
        if owner == who:
            group = BOARD[sp].group
            if not (group and any(state.buildings[j] for j in GROUPS[group])):
                return True
    return False


def proposal_possible(state: GameState, who: int) -> bool:
    others = [i for i, p in enumerate(state.players) if i != who and not p.bankrupt]
    if state.players[who].bankrupt or not others:
        return False
    return has_tradeables(state, who) or any(has_tradeables(state, i) for i in others)


def can_propose(state: GameState) -> bool:
    who = trade_proposer(state)
    return state.config.max_trade_exchanges > 0 and who is not None and trades_used(state) < trade_limit(state) and proposal_possible(state, who)


def validate_offer(state: GameState, offer: TradeOffer, raisable: bool = False) -> str | None:
    n = len(state.players)
    if not (0 <= offer.proposer < n and 0 <= offer.target < n) or offer.proposer == offer.target:
        return OfferProblem("Choose two distinct active seats.", "INVALID_TARGET")
    if state.players[offer.proposer].bankrupt or state.players[offer.target].bankrupt:
        return OfferProblem("An eliminated player cannot trade.", "PLAYER_ELIMINATED")
    if offer.is_empty():
        return OfferProblem("The offer is empty.", "EMPTY_OFFER")
    return _side_problem(state, offer.proposer, offer.give_spaces, offer.give_cash, offer.give_cards) or _side_problem(
        state, offer.target, offer.get_spaces, offer.get_cash, offer.get_cards, recipient=True, raisable=raisable
    )


def execute_offer(state: GameState, offer: TradeOffer) -> list[Event]:
    a, b = state.players[offer.proposer], state.players[offer.target]
    for sp in offer.give_spaces:
        state.owner[sp] = offer.target
    for sp in offer.get_spaces:
        state.owner[sp] = offer.proposer
    a.cash += offer.get_cash - offer.give_cash
    b.cash += offer.give_cash - offer.get_cash
    for _ in range(offer.give_cards):
        b.jail_cards.append(a.jail_cards.pop(0))
    for _ in range(offer.get_cards):
        a.jail_cards.append(b.jail_cards.pop(0))
    events = [Event("trade", offer.proposer, {"offer": offer})]
    for receiver, spaces in ((offer.target, offer.give_spaces), (offer.proposer, offer.get_spaces)):
        mortgaged = [sp for sp in spaces if state.mortgaged[sp]]
        state.pending_takeovers.extend((receiver, sp) for sp in mortgaged)
    return events
