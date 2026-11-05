"""Printed-sheet values and complete score/ranking oracles."""
import importlib.util
import pytest


def sheet_api():
    assert importlib.util.find_spec('boardcraft.games.dice_circuit.sheet') is not None, 'Typed sheet loader is missing'
    from boardcraft.games.dice_circuit.sheet import load_sheet
    return load_sheet()


def test_typed_sheet_preserves_all_printed_values():
    spec = sheet_api()
    assert spec.resource_slots == 7
    assert spec.round_counts == (6, 6, 5, 4)
    assert spec.round_rewards == ('reroll', 'extra', 'reroll', 'blackX-or-black6', None, None)
    assert spec.yellow.rows == ((3, 6, 5, None), (2, 1, None, 5), (1, None, 2, 4), (None, 3, 4, 6))
    assert spec.yellow.premarked == frozenset({(0, 3), (1, 2), (2, 1), (3, 0)})
    assert spec.yellow.column_points == (10, 14, 16, 20)
    assert spec.yellow.row_bonuses == ('blueX', 'orange4', 'greenX', 'endgame')
    assert spec.yellow.main_diagonal_bonus == 'extra'
    assert spec.blue.rows == ((None, 2, 3, 4), (5, 6, 7, 8), (9, 10, 11, 12))
    assert spec.blue.values == frozenset(range(2, 13))
    assert spec.blue.points_by_count == (0, 1, 2, 4, 7, 11, 16, 22, 29, 37, 46, 56)
    assert spec.blue.row_bonuses == ('orange5', 'yellowX', 'endgame')
    assert spec.blue.column_bonuses == ('reroll', 'greenX', 'purple6', 'extra')
    assert spec.green.thresholds == (1, 2, 3, 4, 5, 1, 2, 3, 4, 5, 6)
    assert spec.green.points_by_count == (0, 1, 3, 6, 10, 15, 21, 28, 36, 45, 55, 66)
    assert spec.green.bonuses_by_position == ((4, 'extra'), (6, 'blueX'), (7, 'endgame'), (9, 'purple6'), (10, 'reroll'))
    assert spec.orange.multipliers == (1, 1, 1, 2, 1, 1, 2, 1, 2, 1, 3)
    assert spec.orange.bonuses_by_position == ((3, 'reroll'), (5, 'yellowX'), (6, 'extra'), (8, 'endgame'), (10, 'purple6'))
    assert spec.purple.length == 11
    assert spec.purple.bonuses_by_position == ((3, 'reroll'), (4, 'blueX'), (5, 'extra'), (6, 'yellowX'), (7, 'endgame'), (8, 'reroll'), (9, 'greenX'), (10, 'orange6'), (11, 'extra'))


def test_player_sheet_initial_marks_and_independent_mutable_resources():
    sheet_api()
    from boardcraft.games.dice_circuit.state import PlayerSheet
    first, second = PlayerSheet(), PlayerSheet()
    assert first.yellow == {(0, 3), (1, 2), (2, 1), (3, 0)}
    assert (first.blue, first.green, first.orange, first.purple, first.earned_rewards, first.rerolls, first.extras, first.emblems) == (set(), 0, [], [], set(), 0, 0, 0)
    first.yellow.add((0, 0))
    first.earned_rewards.add('yellow:row:0')
    first.rerolls = 9
    assert (0, 0) not in second.yellow
    assert second.earned_rewards == set()
    assert second.rerolls == 0


@pytest.mark.parametrize('count,blue,green', [(0, 0, 0), (1, 1, 1), (2, 2, 3), (3, 4, 6), (4, 7, 10), (5, 11, 15), (6, 16, 21), (7, 22, 28), (8, 29, 36), (9, 37, 45), (10, 46, 55), (11, 56, 66)])
def test_blue_and_green_full_scoring_oracles(count, blue, green):
    sheet_api()
    from boardcraft.games.dice_circuit.state import PlayerSheet
    from boardcraft.games.dice_circuit.scoring import score_sheet
    score = score_sheet(PlayerSheet(blue=set(range(2, count + 2)), green=count))
    assert (score.blue, score.green) == (blue, green)


@pytest.mark.parametrize('column,points', [(0, 10), (1, 14), (2, 16), (3, 20)])
def test_yellow_scores_only_complete_columns(column, points):
    sheet_api()
    from boardcraft.games.dice_circuit.state import PlayerSheet
    from boardcraft.games.dice_circuit.scoring import score_sheet
    sheet = PlayerSheet()
    sheet.yellow.update((row, column) for row in range(4))
    assert score_sheet(sheet).yellow == points
    sheet.yellow.remove((0 if column != 3 else 1, column))
    assert score_sheet(sheet).yellow == 0


def test_full_yellow_orange_multiplier_and_purple_sum():
    sheet_api()
    from boardcraft.games.dice_circuit.state import PlayerSheet
    from boardcraft.games.dice_circuit.scoring import score_sheet, orange_recorded_value
    assert [orange_recorded_value(4, i) for i in range(11)] == [4, 4, 4, 8, 4, 4, 8, 4, 8, 4, 12]
    score = score_sheet(PlayerSheet(yellow={(r, c) for r in range(4) for c in range(4)}, orange=[4, 4, 4, 8], purple=[2, 5, 6, 3]))
    assert (score.yellow, score.orange, score.purple) == (60, 20, 16)
    assert score.total == 96


def test_emblems_score_minimum_area_and_zero_area():
    sheet_api()
    from boardcraft.games.dice_circuit.scoring import AreaScore
    score = AreaScore(10, 20, 30, 40, 50, 2)
    assert score.areas == (10, 20, 30, 40, 50)
    assert (score.emblem_score, score.total) == (20, 170)
    assert AreaScore(0, 20, 30, 40, 50, 2).emblem_score == 0


def test_ranking_uses_total_then_highest_area_and_competition_ties():
    sheet_api()
    from boardcraft.games.dice_circuit.scoring import AreaScore, rank_scores
    scores = (AreaScore(20, 20, 20, 20, 20), AreaScore(40, 15, 15, 15, 15), AreaScore(30, 30, 0, 0, 40), AreaScore(50, 0, 0, 0, 0))
    result = rank_scores(scores)
    assert result.scores == (100, 100, 100, 50)
    assert result.area_scores == (
        {'yellow': 20, 'blue': 20, 'green': 20, 'orange': 20, 'purple': 20},
        {'yellow': 40, 'blue': 15, 'green': 15, 'orange': 15, 'purple': 15},
        {'yellow': 30, 'blue': 30, 'green': 0, 'orange': 0, 'purple': 40},
        {'yellow': 50, 'blue': 0, 'green': 0, 'orange': 0, 'purple': 0})
    assert result.emblem_scores == (0, 0, 0, 0)
    assert result.placements == (3, 1, 1, 4)
    assert result.reason == 'completed'
    assert rank_scores((scores[3],)).placements == (1,)
    with pytest.raises(ValueError):
        rank_scores(())
