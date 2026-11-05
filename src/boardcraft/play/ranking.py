"""Batch Bradley–Terry evidence within exact gameplay strata."""

import math
import re
from dataclasses import asdict, dataclass

import numpy as np

from boardcraft.registry import get_game
from boardcraft.server.storage import digest, encode
from boardcraft.settings import normalize_settings


@dataclass(frozen=True)
class RatingKey:
    game_id: str
    rules_version: str
    variant: str
    players: int
    settings_digest: str = ""

    def __post_init__(self):
        if (
            any(
                type(v) is not str or not v
                for v in (self.game_id, self.rules_version, self.variant)
            )
            or type(self.players) is not int
            or type(self.settings_digest) is not str
            or (self.settings_digest and not re.fullmatch("[0-9a-f]{64}", self.settings_digest))
            or self.players < 1
        ):
            raise ValueError("Invalid rating stratum")

    @classmethod
    def from_config(cls, config, rules_version, *, module=None):
        overrides = config.settings
        if overrides:
            module = get_game(config.game_id) if module is None else module
            overrides = normalize_settings(module.settings_schema(), overrides)
        settings_digest = digest(encode(overrides)) if overrides else ""
        return cls(config.game_id, rules_version, config.variant, config.players, settings_digest)

    def data(self):
        data = asdict(self)
        if not self.settings_digest:
            del data["settings_digest"]
        return data


@dataclass(frozen=True)
class RatingFitSpec:
    gauge: str
    elo_origin: float
    ridge_lambda: float
    prior_pseudocount: float
    solver: str
    max_iterations: int
    max_backtracks: int
    gradient_inf_tolerance: float
    armijo_coefficient: float
    backtrack_factor: float

    def __post_init__(self):
        if self.gauge != "component_sum_zero" or self.solver != "newton_backtracking":
            raise ValueError("Unsupported explicit rating gauge or solver")
        for k in (
            "elo_origin",
            "ridge_lambda",
            "prior_pseudocount",
            "gradient_inf_tolerance",
            "armijo_coefficient",
            "backtrack_factor",
        ):
            v = getattr(self, k)
            if type(v) not in (int, float) or not math.isfinite(v):
                raise ValueError("Rating controls must be finite")
        if (
            min(self.ridge_lambda, self.prior_pseudocount) < 0
            or self.gradient_inf_tolerance <= 0
            or not 0 < self.armijo_coefficient < 1
            or not 0 < self.backtrack_factor < 1
        ):
            raise ValueError("Invalid rating controls")
        for v in (self.max_iterations, self.max_backtracks):
            if type(v) is not int or v < 1:
                raise ValueError("Choose positive solver limits")

    def data(self):
        return asdict(self)


def _reachable(start, nodes, edges):
    seen = {start}
    stack = [start]
    while stack:
        a = stack.pop()
        for b in nodes:
            if (a, b) in edges and b not in seen:
                seen.add(b)
                stack.append(b)
    return seen


def _component_fit(nodes, wins, spec):
    n = len(nodes)
    idx = {x: i for i, x in enumerate(nodes)}
    # Include edges whose first direction had zero wins.
    pairs = {tuple(sorted((a, b))) for a, b in wins if a in idx and b in idx}
    edges = [
        (idx[a], idx[b], wins.get((a, b), 0.0), wins.get((b, a), 0.0))
        for a, b in sorted(pairs)
    ]
    directed = {(a, b) for (a, b), w in wins.items() if w > 0 and a in idx and b in idx}
    separated = n > 1 and (
        len(_reachable(nodes[0], nodes, directed)) < n
        or len(_reachable(nodes[0], nodes, {(b, a) for a, b in directed})) < n
    )
    driven = spec.ridge_lambda > 0 or spec.prior_pseudocount > 0
    if not edges:
        return None, {
            "status": "unobserved",
            "iterations": 0,
            "prior_driven": driven,
            "separated": False,
        }
    if separated and not driven:
        return None, {
            "status": "unbounded",
            "iterations": 0,
            "prior_driven": False,
            "separated": True,
        }
    # Sum-zero coordinates: beta=B theta. No arbitrary pinned competitor.
    basis = np.vstack((np.eye(n - 1), -np.ones((1, n - 1))))

    def objective(beta, derivatives=False):
        loss = spec.ridge_lambda * float(beta @ beta) / 2
        g = spec.ridge_lambda * beta.copy()
        h = spec.ridge_lambda * np.eye(n)
        for i, j, wi, wj in edges:
            wi += spec.prior_pseudocount
            wj += spec.prior_pseudocount
            d = beta[i] - beta[j]
            total = wi + wj
            loss += wi * float(np.logaddexp(0.0, -d)) + wj * float(np.logaddexp(0.0, d))
            p = float(np.exp(-np.logaddexp(0.0, -d)))
            residual = total * p - wi
            curvature = total * p * (1 - p)
            g[i] += residual
            g[j] -= residual
            h[i, i] += curvature
            h[j, j] += curvature
            h[i, j] -= curvature
            h[j, i] -= curvature
        return (loss, basis.T @ g, basis.T @ h @ basis) if derivatives else loss

    theta = np.zeros(n - 1)
    status = "iteration_limit"
    iteration = 0
    norm = None
    for iteration in range(spec.max_iterations + 1):
        loss, g, h = objective(basis @ theta, True)
        norm = float(np.max(np.abs(g)))
        if (
            not math.isfinite(loss)
            or not np.all(np.isfinite(g))
            or not np.all(np.isfinite(h))
        ):
            status = "nonfinite"
            break
        if norm <= spec.gradient_inf_tolerance:
            status = "converged"
            break
        if iteration == spec.max_iterations:
            break
        try:
            step = np.linalg.solve(h, -g)
        except np.linalg.LinAlgError:
            status = "singular"
            break
        descent = float(g @ step)
        if not np.all(np.isfinite(step)) or descent >= 0:
            status = "no_descent"
            break
        scale = 1.0
        for _ in range(spec.max_backtracks):
            trial = theta + scale * step
            value = objective(basis @ trial)
            if (
                math.isfinite(value)
                and value <= loss + spec.armijo_coefficient * scale * descent
            ):
                theta = trial
                break
            scale *= spec.backtrack_factor
        else:
            status = "line_search_failed"
            break
    diagnostic = {
        "status": status,
        "iterations": iteration,
        "gradient_inf": norm,
        "prior_driven": driven,
        "separated": separated,
    }
    return (basis @ theta if status == "converged" else None), diagnostic


def fit_ratings(results, key, *, fit_spec):
    if not isinstance(key, RatingKey):
        raise ValueError("Choose an exact rating stratum")  # noqa: TRY004 -- transport validation uses ValueError
    if key.players > 1 and not isinstance(fit_spec, RatingFitSpec):
        raise ValueError("Choose explicit rating fit controls")
    identities = set()
    wins = {}
    games = 0
    pairs = 0
    ties = 0
    points = {}
    engine = None
    for row in results:
        if row["key"] != key.data():
            raise ValueError("Incompatible rating strata")
        if (
            type(row.get("engine_version")) is not str
            or not row["engine_version"]
            or (engine is not None and engine != row["engine_version"])
        ):
            raise ValueError("Incompatible rating engine semantics")
        engine = row["engine_version"]
        ids = row["identities"]
        places = row["placements"]
        scores = row["scores"]
        if (
            any(len(c) != key.players for c in (ids, places, scores))
            or any(type(x) is not str or not x for x in ids)
            or any(type(x) is not int or x < 1 for x in places)
            or any(type(x) not in (int, float) or not math.isfinite(x) for x in scores)
        ):
            raise ValueError("Invalid completed rating result")
        games += 1
        identities.update(ids)
        for identity, score in zip(ids, scores):
            points.setdefault(identity, []).append(score)
        for i in range(key.players):
            for j in range(i + 1, key.players):
                a, b = ids[i], ids[j]
                if a == b:
                    continue
                score = 0.5 if places[i] == places[j] else float(places[i] < places[j])
                pairs += 1
                ties += score == 0.5
                wins[a, b] = wins.get((a, b), 0.0) + score
                wins[b, a] = wins.get((b, a), 0.0) + 1 - score
    out = {
        "key": key.data(),
        "engine_version": engine,
        "observed_games": games,
        "observed_pairs": pairs,
        "ties": ties,
        "points": points,
        "ratings": [],
        "components": [],
        "cross_component_order": "unidentified",
        "fit_spec": None if fit_spec is None else fit_spec.data(),
    }
    if key.players == 1:
        return out
    remaining = set(identities)
    while remaining:
        first = min(remaining)
        nodes = sorted(_reachable(first, remaining, set(wins)))
        remaining.difference_update(nodes)
        cid = len(out["components"])
        beta, diagnostic = _component_fit(nodes, wins, fit_spec)
        out["components"].append(dict(component=cid, identities=nodes, **diagnostic))
        for i, identity in enumerate(nodes):
            out["ratings"].append(
                {
                    "identity": identity,
                    "component": cid,
                    "elo": None
                    if beta is None
                    else fit_spec.elo_origin + 400 / math.log(10) * float(beta[i]),
                }
            )
    return out
