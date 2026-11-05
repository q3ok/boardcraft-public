"""Global rankings: every completed tournament of a game, aggregated per exact stratum (game, variant, rules,
settings, players, engine). The server computes and stores them when a tournament completes or is deleted and again
at startup; a read only returns the stored result with the players' current display names."""

import json
import math
import sqlite3
import statistics
import time

from .storage import digest, encode

MIN_RANKED_GAMES = 30
REFERENCE_BOT = "greedy"

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS global_rankings(stratum_sha TEXT PRIMARY KEY,game_id TEXT NOT NULL,players INTEGER NOT NULL,"
    "input_sha TEXT NOT NULL,payload TEXT NOT NULL,checksum TEXT NOT NULL)"
)


def _tables(db):
    return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _kind(participant):
    if participant.get("kind") == "model":
        return "model"
    if participant.get("builtin_kind") == "hybrid":
        return "hybrid"
    return "bot"


def _fallback_name(participant):
    builtin = participant.get("builtin_kind")
    if builtin == "hybrid":
        return "Combined player"
    if builtin:
        return builtin.replace("_", " ").capitalize()
    try:
        arch = json.loads(participant.get("architecture_json") or "{}")
    except ValueError:
        arch = {}
    family = arch.get("family")
    if family == "transformer":
        shape = f"transformer {arch.get('layers', '?')}x{arch.get('dim', '?')}"
    elif family == "mlp":
        shape = "mlp " + "x".join(str(x) for x in arch.get("hidden") or []) if arch.get("hidden") else "mlp"
    else:
        shape = ""
    return f"Removed model ({shape})" if shape else "Removed model"


def _collect(service):
    """Completed results of the completed tournaments with each match's chance seed, grouped by stratum."""
    from .library import _tstore, removed_matches

    ts = _tstore(service.store)
    groups = {}
    tournaments = []
    with service.store.transaction() as db:
        db.row_factory = sqlite3.Row
        if "tournaments" not in _tables(db):
            return groups, tournaments
        ids = [r[0] for r in db.execute("SELECT tournament_id FROM tournaments WHERE status='completed' ORDER BY tournament_id")]
        # Games deleted from the replay library leave the rankings.
        removed = removed_matches(db)
        for id in ids:
            try:
                results = ts.rating_results(db, id)
                seeds = {m: json.loads(raw)["seed"] for m, raw in db.execute(
                    "SELECT match_id,spec_json FROM tournament_matches WHERE tournament_id=? AND status='completed'", (id,))}
            except Exception:  # noqa: BLE001 -- an unreadable tournament is left out, the others still count
                continue
            tournaments.append(id)
            for result in results:
                if (id, result["match_id"]) in removed:
                    continue
                stratum = encode([result["key"], result["engine_version"]])
                groups.setdefault(stratum, []).append({**result, "seed": seeds.get(result["match_id"]),
                                                       "tournament_id": id})
    return groups, tournaments


def _ci(values):
    if len(values) < 2:
        return 0.0
    return 1.96 * statistics.stdev(values) / math.sqrt(len(values))


def _compute(rows, key, engine):
    from boardcraft.play.ranking import RatingFitSpec, RatingKey, fit_ratings
    from .tournaments import recommended_fit

    players = key["players"]
    stats = {}
    for row in rows:
        for seat, identity in enumerate(row["identities"]):
            s = stats.setdefault(identity, {"participant": row["participants"][seat], "scores": [], "places": [], "seeds": {}})
            score = row["scores"][seat]
            s["scores"].append(score)
            s["places"].append(row["placements"][seat])
            if row.get("seed") is not None:
                s["seeds"].setdefault(row["seed"], []).append(score)
    out_rows = []
    reference = None
    if players == 1:
        greedy = [i for i, s in stats.items() if s["participant"].get("builtin_kind") == REFERENCE_BOT]
        if greedy:
            reference = max(greedy, key=lambda i: len(stats[i]["scores"]))
    fit = None
    elo = {}
    if players > 1:
        fit = fit_ratings([{k: r[k] for k in ("key", "engine_version", "identities", "placements", "scores")} for r in rows],
                          RatingKey(**key), fit_spec=RatingFitSpec(**recommended_fit()))
        elo = {r["identity"]: r for r in fit["ratings"]}
    for identity, s in stats.items():
        scores = s["scores"]
        n = len(scores)
        wins = sum(1 for p in s["places"] if p == 1)
        row = {
            "identity": identity,
            "kind": _kind(s["participant"]),
            "fallback_name": _fallback_name(s["participant"]),
            "games": n,
            "mean_score": sum(scores) / n,
            "ci": _ci(scores),
            "median": statistics.median(scores),
            "best_score": max(scores),
            "worst_score": min(scores),
            "wins": wins,
            "win_rate": wins / n,
        }
        if players > 1:
            rating = elo.get(identity) or {}
            row["elo"] = rating.get("elo")
            row["component"] = rating.get("component")
            row["avg_place"] = sum(s["places"]) / n
        elif reference is not None and identity != reference:
            ref = stats[reference]["seeds"]
            diffs = [sum(v) / len(v) - sum(ref[seed]) / len(ref[seed]) for seed, v in s["seeds"].items() if seed in ref]
            row["paired"] = ({"vs": reference, "delta": sum(diffs) / len(diffs), "ci": _ci(diffs), "pairs": len(diffs)}
                             if len(diffs) >= 2 else None)
        out_rows.append(row)
    if players == 1:
        out_rows.sort(key=lambda r: (-r["mean_score"], -r["games"], r["identity"]))
    else:
        out_rows.sort(key=lambda r: (r["elo"] is None, -(r["elo"] or 0), -r["games"], r["identity"]))
    for r in out_rows:
        r["ranked"] = r["games"] >= MIN_RANKED_GAMES
    return {
        "key": key,
        "engine_version": engine,
        "players": players,
        "games": len(rows),
        "tournaments": sorted({r["tournament_id"] for r in rows}),
        "reference": reference,
        "rows": out_rows,
        "fit": None if fit is None else {"fit_spec": fit["fit_spec"], "components": fit["components"],
                                         "observed_pairs": fit["observed_pairs"]},
        "computed_at": time.time(),
    }


def refresh(service):
    """Stores the rankings of every stratum whose completed results changed and drops the strata left without any.
    Returns the number of recomputed strata."""
    groups, _ = _collect(service)
    with service.store.transaction() as db:
        if "tournaments" not in _tables(db) and "global_rankings" not in _tables(db):
            return 0
    inputs = {}
    for stratum, rows in groups.items():
        inputs[digest(stratum)] = (stratum, rows, digest(encode(sorted(digest(encode(
            {k: r[k] for k in ("tournament_id", "match_id", "record_sha256", "scores", "placements", "identities", "seed")}))
            for r in rows))))
    with service.store.transaction(True) as db:
        db.execute(_SCHEMA)
        stored = dict(db.execute("SELECT stratum_sha,input_sha FROM global_rankings"))
        for sha in set(stored) - set(inputs):
            db.execute("DELETE FROM global_rankings WHERE stratum_sha=?", (sha,))
    changed = 0
    for sha, (stratum, rows, input_sha) in sorted(inputs.items()):
        if stored.get(sha) == input_sha:
            continue
        key, engine = json.loads(stratum)
        payload = encode(_compute(rows, key, engine))
        with service.store.transaction(True) as db:
            db.execute("INSERT OR REPLACE INTO global_rankings VALUES(?,?,?,?,?,?)",
                       (sha, key["game_id"], key["players"], input_sha, payload, digest(payload)))
        changed += 1
    return changed


def _names(service, game_id, tournaments):
    names = {}
    try:
        names.update(service.tournaments().labels(set(tournaments)))
    except Exception:  # noqa: BLE001
        pass
    try:
        for item in service._bot_descriptors(game_id):
            names[item.reference.identity] = item.display_name
    except Exception:  # noqa: BLE001 -- the saved names are used when the bot list cannot be read
        pass
    return names


def read(service, game_id, players):
    """The stored global ranking of one game and table size: one table per stratum (most games first)."""
    if type(game_id) is not str or type(players) is not int or players < 1:
        raise ValueError("Choose a game and a table size")
    service.module(game_id)
    with service.store.transaction() as db:
        if "global_rankings" not in _tables(db):
            rows = []
        else:
            rows = db.execute("SELECT payload,checksum FROM global_rankings WHERE game_id=? AND players=?",
                              (game_id, players)).fetchall()
    tables = []
    for payload, checksum in rows:
        if digest(payload) != checksum:
            continue
        tables.append(json.loads(payload))
    tables.sort(key=lambda t: (-t["games"], encode(t["key"])))
    names = _names(service, game_id, [id for t in tables for id in t["tournaments"]])
    for t in tables:
        for r in t["rows"]:
            r["name"] = names.get(r["identity"]) or r["fallback_name"]
            if r.get("paired"):
                r["paired"]["vs_name"] = names.get(r["paired"]["vs"]) or "Greedy"
        t["reference_name"] = None if t["reference"] is None else names.get(t["reference"], "Greedy")
    return {"game_id": game_id, "players": players, "min_ranked_games": MIN_RANKED_GAMES,
            "computed_at": max((t["computed_at"] for t in tables), default=None), "tables": tables}
