"""Bank liquidation proceeds and the order for raising cash on resignation."""

from dataclasses import dataclass

from .actions import Action
from .board import BOARD, GROUPS
from .buildings import mortgage_actions, sell_actions, sell_refund
from .state import GameState


@dataclass(frozen=True)
class BankFunds:

    cash: int
    mortgage_now: int
    building_sales: int
    mortgage_after_sales: int

    @property
    def max_cash(self) -> int:
        return self.cash + self.mortgage_now + self.building_sales + self.mortgage_after_sales


def bank_funds(state: GameState, pid: int) -> BankFunds:
    now = sales = after = 0
    for i, owner in enumerate(state.owner):
        if owner != pid:
            continue
        sp = BOARD[i]
        sales += state.buildings[i] * (sp.house_cost // 2)
        if state.mortgaged[i]:
            continue
        built = sp.group is not None and any(state.buildings[j] for j in GROUPS[sp.group])
        if built:
            after += sp.mortgage_value
        else:
            now += sp.mortgage_value
    return BankFunds(state.players[pid].cash, now, sales, after)


def _full_group(state: GameState, pid: int, space: int) -> bool:
    g = BOARD[space].group
    return g is not None and all(state.owner[j] == pid for j in GROUPS[g])


def liquidation_step(state: GameState, pid: int) -> Action | None:
    morts = mortgage_actions(state, pid)
    if morts:
        return min(morts, key=lambda a: (_full_group(state, pid, a.space), BOARD[a.space].mortgage_value, a.space))
    sells = sell_actions(state, pid)
    if sells:
        return max(sells, key=lambda a: (sell_refund(state, a.space), -a.space))
    return None
