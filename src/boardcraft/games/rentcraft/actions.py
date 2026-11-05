"""Typed decisions, commands and events for a Rentcraft position."""

from dataclasses import dataclass, field
from enum import Enum


class ActionKind(Enum):
    ROLL = "roll"
    BUY = "buy"
    DECLINE = "decline"
    BID = "bid"
    PASS_BID = "pass_bid"
    BUILD = "build"
    SELL_BUILDING = "sell_building"
    MORTGAGE = "mortgage"
    UNMORTGAGE = "unmortgage"
    PAY_JAIL_FINE = "pay_jail_fine"
    USE_JAIL_CARD = "use_jail_card"
    END_TURN = "end_turn"
    RESIGN = "resign"
    PROPOSE_TRADE = "propose_trade"
    ACCEPT_TRADE = "accept_trade"
    REJECT_TRADE = "reject_trade"
    COUNTER_TRADE = "counter_trade"
    PASS = "pass"
    DECLARE_BANKRUPTCY = "declare_bankruptcy"
    REQUEST_TRADE = "request_trade"


@dataclass(frozen=True)
class Action:
    kind: ActionKind
    space: int | None = None
    amount: int | None = None
    trade: object | None = None


class Phase(Enum):
    PRE_ROLL = "pre_roll"
    BUY_DECISION = "buy_decision"
    AUCTION = "auction"
    RAISE_FUNDS = "raise_funds"
    TRADE_RESPONSE = "trade_response"
    POST_ROLL = "post_roll"
    GAME_OVER = "game_over"
    TRADE_WINDOW = "trade_window"
    MORTGAGE_TAKEOVER = "mortgage_takeover"


@dataclass(frozen=True)
class Decision:
    player: int
    phase: Phase
    actions: tuple[Action, ...]


@dataclass(frozen=True)
class Event:
    kind: str
    player: int | None
    data: dict = field(default_factory=dict)


class IllegalActionError(Exception):

    def __init__(self, message: str = "", code: str | None = None, **fields):
        super().__init__(message)
        self.code = code or getattr(message, "code", None)
        self.fields = {**getattr(message, "fields", {}), **fields}
