"""Pure area scoring and competition ranking."""
from dataclasses import dataclass

from .sheet import load_sheet
from .state import PlayerSheet

COLOURS = ('yellow', 'blue', 'green', 'orange', 'purple')


@dataclass(frozen=True)
class DiceCircuitResult:
    scores: tuple[int, ...]
    area_scores: tuple[dict[str, int], ...]
    emblem_scores: tuple[int, ...]
    placements: tuple[int, ...]
    reason: str


@dataclass(frozen=True)
class AreaScore:
    yellow: int
    blue: int
    green: int
    orange: int
    purple: int
    emblems: int = 0

    @property
    def areas(self) -> tuple[int, ...]:
        return self.yellow, self.blue, self.green, self.orange, self.purple

    @property
    def emblem_score(self) -> int:
        return min(self.areas) * self.emblems

    @property
    def total(self) -> int:
        return sum(self.areas) + self.emblem_score


def area_scores_view(score: AreaScore) -> dict[str, int]:
    return dict(zip(COLOURS, score.areas))


def score_view(score: AreaScore) -> dict:
    return {'areas': area_scores_view(score), 'emblem_points': score.emblem_score, 'total': score.total}


def result_view(result: DiceCircuitResult) -> dict:
    return {'scores': list(result.scores), 'area_scores': [dict(row) for row in result.area_scores],
            'emblem_scores': list(result.emblem_scores), 'placements': list(result.placements), 'reason': result.reason}


def orange_recorded_value(value: int, position: int) -> int:
    """Apply the multiplier at a zero-based orange field."""
    return value * load_sheet().orange.multipliers[position]


def score_sheet(sheet: PlayerSheet) -> AreaScore:
    spec = load_sheet()
    yellow = sum(points for column, points in enumerate(spec.yellow.column_points)
                 if all((row, column) in sheet.yellow for row in range(len(spec.yellow.rows))))
    return AreaScore(yellow, spec.blue.points_by_count[len(sheet.blue)],
                     spec.green.points_by_count[sheet.green], sum(sheet.orange), sum(sheet.purple), sheet.emblems)


def rank_scores(scores: tuple[AreaScore, ...]) -> DiceCircuitResult:
    if not scores:
        raise ValueError('At least one score is required')
    keys = tuple((score.total, max(score.areas)) for score in scores)
    return DiceCircuitResult(tuple(score.total for score in scores), tuple(area_scores_view(score) for score in scores),
                      tuple(score.emblem_score for score in scores),
                      tuple(1 + sum(other > key for other in keys) for key in keys), 'completed')
