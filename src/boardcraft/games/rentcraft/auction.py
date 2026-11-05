"""Turn-ordered auctions with fixed raises and settlement through the debt queue."""

from .actions import Action, ActionKind, Event
from .board import AUCTION_MAX_BID, BID_STEPS
from .debt import charge
from .state import AuctionState, GameState

PASS_BID = Action(ActionKind.PASS_BID)


def start_auction(state: GameState, space: int, first_bidder: int) -> None:
    n = len(state.players)
    order = [(first_bidder + k) % n for k in range(n)]
    bidders = [i for i in order if not state.players[i].bankrupt]
    state.auction = AuctionState(space=space, bidders=bidders)


def current_bidder(state: GameState) -> int:
    a = state.auction
    return a.bidders[a.turn_idx]


def bid_limit(state: GameState, player: int) -> int:
    return AUCTION_MAX_BID


def auction_actions(state: GameState) -> tuple[Action, ...]:
    a = state.auction
    limit = bid_limit(state, current_bidder(state))
    bids = [Action(ActionKind.BID, amount=step) for step in BID_STEPS if a.high_bid + step <= limit]
    return (*bids, PASS_BID)


def apply_auction(state: GameState, action: Action) -> list[Event]:
    a = state.auction
    pid = current_bidder(state)
    events: list[Event] = []
    if action.kind is ActionKind.BID:
        a.high_bid += action.amount
        a.high_bidder = pid
        a.turn_idx = (a.turn_idx + 1) % len(a.bidders)
        events.append(Event("bid", pid, {"space": a.space, "bid": a.high_bid}))
    else:
        a.bidders.pop(a.turn_idx)
        if a.bidders:
            a.turn_idx %= len(a.bidders)
        events.append(Event("pass_bid", pid, {"space": a.space}))
    _check_end(state, events)
    return events


def remove_bidder(state: GameState, player: int) -> list[Event]:
    a = state.auction
    events: list[Event] = []
    if player not in a.bidders:
        return events
    idx = a.bidders.index(player)
    a.bidders.pop(idx)
    if idx < a.turn_idx:
        a.turn_idx -= 1
    if a.bidders:
        a.turn_idx %= len(a.bidders)
    if a.high_bidder == player:
        a.high_bidder = None
    _check_end(state, events)
    return events


def _check_end(state: GameState, events: list[Event]) -> None:
    a = state.auction
    if not a.bidders:
        state.auction = None
        events.append(Event("auction_unsold", None, {"space": a.space}))
    elif len(a.bidders) == 1 and a.high_bidder == a.bidders[0]:
        winner = a.high_bidder
        state.owner[a.space] = winner
        state.auction = None
        events.append(Event("auction_won", winner, {"space": a.space, "price": a.high_bid}))
        for e in charge(state, winner, None, a.high_bid):
            events.append(Event(e.kind, e.player, {**e.data, "reason": "auction", "space": a.space}))
