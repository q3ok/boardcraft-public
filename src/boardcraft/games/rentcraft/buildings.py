"""Even construction, group liquidation, mortgages and the bank building supply."""

import math

from .actions import Action, ActionKind, Event
from .board import BOARD, GROUPS, HOTEL, MORTGAGE_INTEREST, Kind
from .rent import owns_full_group
from .state import GameState


def _levels(state: GameState, group: str) -> list[int]:
    return [state.buildings[i] for i in GROUPS[group]]


_BUILD = tuple(Action(ActionKind.BUILD, space=i) for i in range(len(BOARD)))
_SELL = tuple(Action(ActionKind.SELL_BUILDING, space=i) for i in range(len(BOARD)))
_MORTGAGE = tuple(Action(ActionKind.MORTGAGE, space=i) for i in range(len(BOARD)))
_UNMORTGAGE = tuple(Action(ActionKind.UNMORTGAGE, space=i) for i in range(len(BOARD)))


def unmortgage_cost(space: int) -> int:
    value = BOARD[space].mortgage_value
    return value + math.ceil(value * MORTGAGE_INTEREST)


def owned_spaces(state: GameState, player: int) -> list[int]:
    return [i for i, o in enumerate(state.owner) if o == player]


def build_actions(state: GameState, player: int, owned: list[int] | None = None) -> list[Action]:
    cash = state.players[player].cash
    acts = []
    for i in (owned if owned is not None else owned_spaces(state, player)):
        s = BOARD[i]
        if s.kind is not Kind.PROPERTY or not owns_full_group(state, player, s.group):
            continue
        group = GROUPS[s.group]
        if any(state.mortgaged[j] for j in group):
            continue
        level = state.buildings[i]
        if level >= HOTEL or level != min(_levels(state, s.group)) or cash < s.house_cost:
            continue
        if level == 4 and state.bank_hotels < 1:
            continue
        if level < 4 and state.bank_houses < 1:
            continue
        acts.append(_BUILD[i])
    return acts


def sell_actions(state: GameState, player: int, owned: list[int] | None = None) -> list[Action]:
    acts = []
    for i in (owned if owned is not None else owned_spaces(state, player)):
        s = BOARD[i]
        level = state.buildings[i]
        if level > 0 and level == max(_levels(state, s.group)):
            acts.append(_SELL[i])
    return acts


def mortgage_actions(state: GameState, player: int, owned: list[int] | None = None) -> list[Action]:
    acts = []
    built: dict[str, bool] = {}
    for i in (owned if owned is not None else owned_spaces(state, player)):
        if state.mortgaged[i]:
            continue
        group = BOARD[i].group
        if group is not None:
            if group not in built:
                built[group] = any(state.buildings[j] for j in GROUPS[group])
            if built[group]:
                continue
        acts.append(_MORTGAGE[i])
    return acts


def unmortgage_actions(state: GameState, player: int, owned: list[int] | None = None) -> list[Action]:
    cash = state.players[player].cash
    return [
        _UNMORTGAGE[i]
        for i in (owned if owned is not None else owned_spaces(state, player))
        if state.mortgaged[i] and cash >= unmortgage_cost(i)
    ]


def _breakdown(state: GameState, group: str) -> tuple[list[int], list[int], int, int]:
    # Spend all available houses on the even distribution, in board order for ties.
    spaces = GROUPS[group]
    old = [state.buildings[j] for j in spaces]
    houses_on_group = sum(lv for lv in old if lv < HOTEL)
    pool = min(state.bank_houses + houses_on_group, 4 * len(spaces))
    base, extra = divmod(pool, len(spaces))
    new = [base + (1 if k < extra else 0) for k in range(len(spaces))]
    return old, new, houses_on_group, pool


def sell_refund(state: GameState, space: int) -> int:
    s = BOARD[space]
    if state.buildings[space] == HOTEL and state.bank_houses < 4:
        old, new, _, _ = _breakdown(state, s.group)
        return sum(o - n for o, n in zip(old, new)) * (s.house_cost // 2)
    return s.house_cost // 2


def _break_down_group(state: GameState, pid: int, group: str) -> list[Event]:
    spaces = GROUPS[group]
    old, new, houses_on_group, pool = _breakdown(state, group)
    state.bank_hotels += sum(1 for lv in old if lv == HOTEL)
    state.bank_houses -= pool - houses_on_group
    house_cost = BOARD[spaces[0]].house_cost
    refund = sum(o - n for o, n in zip(old, new)) * (house_cost // 2)
    for j, lv in zip(spaces, new):
        state.buildings[j] = lv
    state.players[pid].cash += refund
    return [Event("sell_building", pid, {"group": group, "levels": new, "refund": refund})]


def apply_property_action(state: GameState, action: Action) -> list[Event]:
    i = action.space
    pid = state.owner[i]
    p = state.players[pid]
    s = BOARD[i]
    kind = action.kind
    if kind is ActionKind.BUILD:
        p.cash -= s.house_cost
        if state.buildings[i] == 4:
            state.bank_hotels -= 1
            state.bank_houses += 4
        else:
            state.bank_houses -= 1
        state.buildings[i] += 1
        return [Event("build", pid, {"space": i, "level": state.buildings[i], "cost": s.house_cost})]
    if kind is ActionKind.SELL_BUILDING:
        level = state.buildings[i]
        if level == HOTEL and state.bank_houses < 4:
            return _break_down_group(state, pid, s.group)
        if level == HOTEL:
            state.bank_hotels += 1
            state.bank_houses -= 4
            new_level = 4
        else:
            state.bank_houses += 1
            new_level = level - 1
        refund = (level - new_level) * (s.house_cost // 2)
        state.buildings[i] = new_level
        p.cash += refund
        return [Event("sell_building", pid, {"space": i, "level": new_level, "refund": refund})]
    if kind is ActionKind.MORTGAGE:
        state.mortgaged[i] = True
        p.cash += s.mortgage_value
        return [Event("mortgage", pid, {"space": i, "amount": s.mortgage_value})]
    if kind is ActionKind.UNMORTGAGE:
        cost = unmortgage_cost(i)
        state.mortgaged[i] = False
        p.cash -= cost
        return [Event("unmortgage", pid, {"space": i, "amount": cost})]
    raise ValueError(f"Unsupported property action {kind.value}")
