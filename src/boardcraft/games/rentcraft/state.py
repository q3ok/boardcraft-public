"""State of the board and the decisions that can interrupt a turn."""

from __future__ import annotations

from dataclasses import dataclass, field

from .actions import Phase
from typing import TYPE_CHECKING

from .board import START_CASH

if TYPE_CHECKING:
    from .memory import Memory
    from .trade import TradeState

Payment = tuple[int, int | None, int, dict[str, object]]


@dataclass
class GameConfig:
    num_players: int
    seed: int
    max_rounds: int | None = None
    start_cash: int = START_CASH
    max_trades_per_turn: int = 3
    max_trade_exchanges: int = 4
    trade_windows: str = "full"


WINDOW_KINDS = ("payment", "acquisition", "after_trade", "turn_end", "request")


@dataclass
class TradeWindow:

    kind: str
    trigger: int | None
    queue: list[int]
    then: str
    phase: Phase
    resume_phase: Phase | None
    serial: int
    payments: list[Payment] = field(default_factory=list)
    space: int | None = None
    requested: set[int] = field(default_factory=set)
    in_auction: bool = False


@dataclass
class PlayerState:
    id: int
    cash: int
    position: int = 0
    in_jail: bool = False
    jail_turns: int = 0
    jail_cards: list[str] = field(default_factory=list)
    bankrupt: bool = False
    bankrupt_order: int | None = None


@dataclass
class AuctionState:
    space: int
    bidders: list[int]
    turn_idx: int = 0
    high_bid: int = 0
    high_bidder: int | None = None
    trades: dict[int, int] = field(default_factory=dict)


@dataclass
class Debt:
    debtor: int
    creditor: int | None
    amount: int
    trades: int = 0


@dataclass
class GameState:
    config: GameConfig
    players: list[PlayerState]
    owner: list[int | None]
    buildings: list[int]
    mortgaged: list[bool]
    bank_houses: int
    bank_hotels: int
    decks: dict[str, list[int]]
    current: int = 0
    phase: Phase = Phase.PRE_ROLL
    doubles: int = 0
    last_roll: tuple[int, int] | None = None
    roll_count: int = 0
    round: int = 1
    turn: int = 1
    auction: AuctionState | None = None
    debts: list[Debt] = field(default_factory=list)
    pending_auctions: list[int] = field(default_factory=list)
    pending_buy: int | None = None
    pending_move: int | None = None
    extra_roll: bool = False
    bankrupt_count: int = 0
    by_round_limit: bool = False
    resume_phase: Phase | None = None
    trade: TradeState | None = None
    trades_this_turn: int = 0
    window: TradeWindow | None = None
    pending_payments: list[Payment] = field(default_factory=list)
    trade_requests: list[int] = field(default_factory=list)
    windows_opened: int = 0
    requests_this_turn: dict[int, int] = field(default_factory=dict)
    memory: Memory | None = None
    first_player: int = 0
    start_rolls: list[tuple[int, tuple[int, int], int]] = field(default_factory=list)
    pending_takeovers: list[tuple[int, int]] = field(default_factory=list)
    takeover_owed: list[tuple[int, int, int]] = field(default_factory=list)
    takeover_return: tuple[Phase, Phase | None] | None = None
    takeover_trades: int = 0
