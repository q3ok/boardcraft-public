"""Batch comparison evidence, with no games or model execution."""

import math

import pytest
from boardcraft.play.ranking import RatingFitSpec, RatingKey, fit_ratings

KEY = RatingKey("fixture", "1", "plain", 2)


def settings(**changes):
    return RatingFitSpec(
        **dict(
            gauge="component_sum_zero",
            elo_origin=0.0,
            ridge_lambda=0.0,
            prior_pseudocount=0.0,
            solver="newton_backtracking",
            max_iterations=80,
            max_backtracks=30,
            gradient_inf_tolerance=1e-10,
            armijo_coefficient=0.01,
            backtrack_factor=0.5,
            **changes,
        )
    )


def result(a="A", b="B", placements=(1, 2)):
    return {
        "key": KEY.data(),
        "engine_version": "1",
        "identities": [a, b],
        "placements": list(placements),
        "scores": [3, 1],
    }


def test_three_to_one_exact_elo_and_ties():
    out = fit_ratings(
        [result()] * 3 + [result(placements=(2, 1))], KEY, fit_spec=settings()
    )
    assert out["components"][0]["status"] == "converged"
    rows = {r["identity"]: r for r in out["ratings"]}
    assert rows["A"]["elo"] - rows["B"]["elo"] == pytest.approx(
        400 * math.log10(3), abs=1e-6
    )
    tie = fit_ratings([result(placements=(1, 1))], KEY, fit_spec=settings())
    assert all(r["elo"] == 0 for r in tie["ratings"])


def test_disconnected_separation_duplicates_and_solo():
    out = fit_ratings([result(), result("C", "D")], KEY, fit_spec=settings())
    assert out["cross_component_order"] == "unidentified"
    assert len(out["components"]) == 2
    assert all(c["status"] == "unbounded" for c in out["components"])
    assert all(r["elo"] is None for r in out["ratings"])
    same = fit_ratings([result("A", "A")], KEY, fit_spec=settings())
    assert same["observed_pairs"] == 0
    solo = RatingKey("fixture", "1", "plain", 1)
    out = fit_ratings(
        [
            {
                "key": solo.data(),
                "engine_version": "1",
                "identities": ["A"],
                "placements": [1],
                "scores": [7],
            }
        ],
        solo,
        fit_spec=None,
    )
    assert out["points"]["A"] == [7] and out["ratings"] == []


def test_explicit_fit_and_strict_stratum():
    with pytest.raises(ValueError):
        fit_ratings([result()], KEY, fit_spec=None)
    with pytest.raises(ValueError):
        fit_ratings(
            [result()], RatingKey("fixture", "1", "other", 2), fit_spec=settings()
        )
    out = fit_ratings(
        [result()],
        KEY,
        fit_spec=RatingFitSpec(**{**settings().data(), "prior_pseudocount": 1.0}),
    )
    assert out["components"][0]["prior_driven"]
    assert out["observed_pairs"] == 1


def test_engine_semantics_cannot_be_silently_mixed():
    with pytest.raises(ValueError):
        fit_ratings(
            [result(), {**result(), "engine_version": "other"}],
            KEY,
            fit_spec=settings(),
        )


def test_multiplayer_half_ties_duplicate_self_pairs_and_iteration_failure():
    key = RatingKey("fixture", "1", "plain", 3)
    row = {
        "key": key.data(),
        "engine_version": "1",
        "identities": ["A", "B", "C"],
        "placements": [1, 1, 3],
        "scores": [5, 5, 1],
    }
    chosen = RatingFitSpec(**{**settings().data(), "prior_pseudocount": 1.0})
    out = fit_ratings([row], key, fit_spec=chosen)
    by_id = {r["identity"]: r for r in out["ratings"]}
    assert (
        out["observed_games"] == 1 and out["observed_pairs"] == 3 and out["ties"] == 1
    )
    assert (
        by_id["A"]["elo"] == pytest.approx(by_id["B"]["elo"])
        and by_id["C"]["elo"] < by_id["A"]["elo"]
    )
    duplicate = fit_ratings(
        [{**row, "identities": ["A", "A", "B"]}], key, fit_spec=chosen
    )
    assert duplicate["observed_pairs"] == 2 and len(duplicate["ratings"]) == 2
    failed = fit_ratings(
        [result()] * 3 + [result(placements=(2, 1))],
        KEY,
        fit_spec=RatingFitSpec(**{**settings().data(), "max_iterations": 1}),
    )
    assert failed["components"][0]["status"] == "iteration_limit" and all(
        r["elo"] is None for r in failed["ratings"]
    )
