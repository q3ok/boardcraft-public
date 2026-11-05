"""Immutable runtime views of the canonical base-sheet data."""
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
import json

Grid = tuple[tuple[int | None, ...], ...]
Rewards = tuple[tuple[int, str], ...]


@dataclass(frozen=True)
class YellowSpec:
    rows: Grid
    premarked: frozenset[tuple[int, int]]
    column_points: tuple[int, ...]
    row_bonuses: tuple[str, ...]
    main_diagonal_bonus: str


@dataclass(frozen=True)
class BlueSpec:
    rows: Grid
    values: frozenset[int]
    points_by_count: tuple[int, ...]
    row_bonuses: tuple[str, ...]
    column_bonuses: tuple[str, ...]


@dataclass(frozen=True)
class GreenSpec:
    thresholds: tuple[int, ...]
    points_by_count: tuple[int, ...]
    bonuses_by_position: Rewards


@dataclass(frozen=True)
class OrangeSpec:
    multipliers: tuple[int, ...]
    bonuses_by_position: Rewards


@dataclass(frozen=True)
class PurpleSpec:
    length: int
    bonuses_by_position: Rewards


@dataclass(frozen=True)
class SheetSpec:
    round_counts: tuple[int, ...]
    round_rewards: tuple[str | None, ...]
    resource_slots: int
    yellow: YellowSpec
    blue: BlueSpec
    green: GreenSpec
    orange: OrangeSpec
    purple: PurpleSpec


def sheet_data() -> dict:
    """Return a fresh JSON-compatible copy of the printed sheet."""
    return json.loads(files('boardcraft').joinpath('games/dice_circuit/data/base-sheet.json').read_text(encoding='utf-8'))


def _rewards(area: dict) -> Rewards:
    return tuple((int(position), reward) for position, reward in area['bonusesByPosition1Based'].items())


@lru_cache(maxsize=1)
def load_sheet() -> SheetSpec:
    data = sheet_data()
    rounds = data['rounds']
    availability = [set(range(1, 5)) if not item.startswith('players-') else
                    set(range(int(item.split('-')[1]), int(item.split('-')[2]) + 1))
                    for item in rounds]
    yellow, blue, green, orange, purple = (data[name] for name in ('yellow', 'blue', 'green', 'orange', 'purple'))
    return SheetSpec(
        tuple(sum(player in seats for seats in availability) for player in range(1, 5)),
        tuple(None if item.startswith('players-') else item for item in rounds),
        data['resourceSlots'],
        YellowSpec(tuple(map(tuple, yellow['rows'])), frozenset(map(tuple, yellow['premarked'])),
                   tuple(yellow['columnPoints']), tuple(yellow['rowBonuses']), yellow['mainDiagonalBonus']),
        BlueSpec(tuple(map(tuple, blue['rows'])), frozenset(value for row in blue['rows'] for value in row if value is not None),
                 tuple(blue['pointsByCount']), tuple(blue['rowBonuses']), tuple(blue['columnBonuses'])),
        GreenSpec(tuple(green['thresholds']), tuple(green['pointsByCount']), _rewards(green)),
        OrangeSpec(tuple(orange['multipliers']), _rewards(orange)),
        PurpleSpec(purple['length'], _rewards(purple)),
    )
