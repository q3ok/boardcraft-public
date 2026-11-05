"""Calculate scheduled rent from ownership, buildings and movement cards."""

from .board import BOARD, GROUPS, RAILROAD_RENTS, RAILROADS, UTILITIES, UTILITY_MULTIPLIERS, Kind
from .state import GameState


def owns_full_group(state: GameState, player: int, group: str) -> bool:
    return all(state.owner[i] == player for i in GROUPS[group])


def rent_for(state: GameState, space: int, dice_total: int, *, card_multiplier: str | None = None) -> int:
    owner = state.owner[space]
    if owner is None or state.mortgaged[space]:
        return 0
    s = BOARD[space]
    if s.kind is Kind.PROPERTY:
        level = state.buildings[space]
        if level > 0:
            return s.rents[level]
        base = s.rents[0]
        return base * 2 if owns_full_group(state, owner, s.group) else base
    if s.kind is Kind.RAILROAD:
        count = sum(1 for i in RAILROADS if state.owner[i] == owner)
        rent = RAILROAD_RENTS[count - 1]
        return rent * 2 if card_multiplier == "railroad_double" else rent
    if s.kind is Kind.UTILITY:
        if card_multiplier == "utility_ten":
            return 10 * dice_total
        count = sum(1 for i in UTILITIES if state.owner[i] == owner)
        return UTILITY_MULTIPLIERS[count - 1] * dice_total
    return 0
