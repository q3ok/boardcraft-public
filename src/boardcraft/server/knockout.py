"""Knockout (successive halving) tournaments built from ordinary line-up tournaments.

Every round gives each remaining candidate one line-up tournament: the candidate and the same sparring partners,
seats rotating, on seeds shared by all candidates of that round. When the round is complete the candidates are ordered
by their results so far (all rounds added up) and the better part goes on. The rounds and their tournaments are kept
in a small JSON file per knockout; the tournaments themselves are ordinary and durable.
"""

import hashlib
import hmac
import json
import math
import random
import secrets
import threading
import time
import uuid
from pathlib import Path
from typing import Literal

from pydantic import Field, StrictInt

from .schemas import ConfigInput, Input, SeatInput
from .views import AccessDenied, Conflict

MAX_GAMES = 100_000


class Unknown(ValueError):
    pass


class KnockoutInput(Input):
    name: str = Field(min_length=1, max_length=128, description="Explicit tournament name.")
    config: ConfigInput = Field(description="Exact public game, variant and player count (2 or more).")
    candidates: list[SeatInput] = Field(min_length=2, max_length=64, description="Bot or model choices compared with each other.")
    sparring: list[SeatInput] = Field(min_length=1, description="Partners filling the other seats of every table (players - 1).")
    games: StrictInt = Field(ge=1, le=MAX_GAMES, description="Total game budget over all rounds.")
    keep: float = Field(gt=0, lt=1, allow_inf_nan=False, description="Share of the candidates going on after each round.")
    min_candidates: StrictInt = Field(default=1, ge=1, description="The tournament ends when this many candidates are left.")
    metric: Literal["wins", "place"] = Field(default="wins", description="Order: win rate (ties: average place) or average place (ties: wins).")
    seed: StrictInt | None = Field(default=None, ge=0, le=2**53 - 1, description="Fixed seed for repeatable games; omitted = random.")
    max_inflight: StrictInt = Field(default=4, ge=1, le=64, description="Simultaneous games of each round tournament.")
    start: bool = Field(default=False, description="Start right away in the same request (the server owns starting).")


class KnockoutAction(Input):
    revision: StrictInt = Field(ge=0)


def halving_plan(n, games, keep, min_candidates):
    """Rounds: candidates, survivors and the game budget of each round (the same plan as the shared design)."""
    sizes = [n]
    while sizes[-1] > min_candidates:
        cur = sizes[-1]
        sizes.append(min(cur - 1, max(min_candidates, math.ceil(cur * keep - 1e-9))))
    rounds = len(sizes) - 1
    base, extra = divmod(games, rounds)
    return [
        {"round": i + 1, "candidates": c, "survivors": sizes[i + 1], "games": base + (1 if i >= rounds - extra else 0)}
        for i, c in enumerate(sizes[:-1])
    ]


def standings(rows, metric):
    """Candidates ordered best first: wins (ties by average place) or average place (ties by wins)."""
    def key(r):
        games = max(1, r["games"])
        win, place = r["wins"] / games, r["place"] / games
        return (-win, place) if metric == "wins" else (place, -win)
    return sorted(rows, key=key)


class Knockouts:
    def __init__(self, service, directory):
        self.service = service
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.drivers = {}
        self.closed = False

    # ---------- storage ----------
    def _path(self, id):
        if not (isinstance(id, str) and len(id) == 32 and all(c in "0123456789abcdef" for c in id)):
            raise Unknown("Unknown knockout tournament")
        return self.dir / f"{id}.json"

    def _load(self, id):
        path = self._path(id)
        if not path.exists():
            raise Unknown("Unknown knockout tournament")
        state = json.loads(path.read_text(encoding="utf-8"))
        if state.get("status") == "deleted":
            raise Unknown("Unknown knockout tournament")
        return state

    def _save(self, state):
        path = self._path(state["knockout_id"])
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
        temp.replace(path)

    def _authorize(self, state, credential, revision):
        # Open management (a private server): no creator credential is needed; the revision still guards changes.
        if not getattr(self.service, "open_management", False) and (not credential or not hmac.compare_digest(hashlib.sha256(credential.encode()).hexdigest(), state["credential_sha"])):
            raise AccessDenied("Only the creator can change this tournament")
        if revision != state["revision"]:
            raise Conflict("Tournament revision changed")

    # ---------- public operations ----------
    def create(self, body):
        data = KnockoutInput.model_validate(body).model_dump()
        start = data.pop("start")
        players = data["config"]["players"]
        if players < 2 or len(data["sparring"]) != players - 1:
            raise ValueError("A knockout table seats one candidate and players - 1 sparring partners")
        n = len(data["candidates"])
        if data["min_candidates"] >= n:
            raise ValueError(f"With {n} candidates the tournament can end at {n - 1} at most")
        plan = halving_plan(n, data["games"], data["keep"], data["min_candidates"])
        if plan[0]["games"] // plan[0]["candidates"] < players:
            raise ValueError("Too few games: every candidate needs at least one full rotation in the first round")
        module = self.service.module(data["config"]["game_id"])
        info = module.info()
        # Pin every choice once now: an unavailable bot or model fails here, not in a later round.
        descriptors = {item.choice_id: item for item in self.service._bot_descriptors(module.game_id)}
        for choice in data["candidates"] + data["sparring"]:
            self.service._pin_choice(module, choice, descriptors)
        id, credential = uuid.uuid4().hex, secrets.token_urlsafe(32)
        names = {d.choice_id: d.display_name for d in self.service._bot_descriptors(module.game_id)}
        label = lambda c: (c if isinstance(c, str) else c["choice_id"])  # noqa: E731
        state = {
            "knockout_id": id, "credential_sha": hashlib.sha256(credential.encode()).hexdigest(), "revision": 0,
            # Saved already running when it starts right away: the driver (and the startup recovery) take it from here.
            "status": "running" if start else "pending", "error": None, "created": time.time(),
            "spec": {**data, "seed": data["seed"] if data["seed"] is not None else secrets.randbits(32),
                     "random_seed": data["seed"] is None, "rules_version": info.rules_version, "engine_version": info.engine_version},
            "labels": {"candidates": [names.get(label(c), "Combined player" if label(c).startswith("hybrid:") else label(c)) for c in data["candidates"]],
                       "sparring": [names.get(label(c), "Combined player" if label(c).startswith("hybrid:") else label(c)) for c in data["sparring"]]},
            "plan": plan, "rounds": [], "winner": None, "top": [],
            "secrets": {},
        }
        with self.lock:
            self._save(state)
        if start:
            self._ensure_driver(id)
        return {"knockout_id": id, "creator_credential": credential, "revision": 0, "status": state["status"]}

    def start(self, id, credential, revision):
        with self.lock:
            state = self._load(id)
            self._authorize(state, credential, revision)
            if state["status"] not in ("pending", "running"):
                raise Conflict("The tournament cannot start in this state")
            state["status"] = "running"
            state["revision"] += 1
            self._save(state)
            self._ensure_driver(id)
            return self.status(id)

    def stop(self, id, credential, revision):
        with self.lock:
            state = self._load(id)
            self._authorize(state, credential, revision)
            if state["status"] in ("completed", "stopped"):
                return self.status(id)
            state["status"] = "stopped"
            state["revision"] += 1
            self._save(state)
        for child, secret in self._current_children(state):
            try:
                current = self.service.tournaments().status(child)
                if current["status"] in ("running", "pending"):
                    self.service.stop_tournament(child, secret, current["revision"])
            except Exception:
                pass
        return self.status(id)

    def delete(self, id, credential, revision):
        with self.lock:
            state = self._load(id)
            self._authorize(state, credential, revision)
            if state["status"] == "running":
                raise Conflict("Stop the tournament first")
            state["status"] = "deleted"
            state["revision"] += 1
            self._save(state)
        return {"knockout_id": id, "status": "deleted"}

    def list(self):
        """Every knockout, newest first (by its creation time)."""
        out = []
        for path in sorted(self.dir.glob("*.json")):
            try:
                out.append(self.status(path.stem))
            except ValueError:
                continue
        out.sort(key=lambda k: k.get("created") or 0, reverse=True)
        return out

    def status(self, id):
        with self.lock:
            state = self._load(id)
        if state["status"] == "running":
            self._ensure_driver(id)
        totals = self._totals(state)
        rounds = []
        for r in state["rounds"]:
            done = total = 0
            for child in r["tournaments"].values():
                try:
                    t = self.service.tournaments().brief(child)
                except Exception:
                    continue
                total += t["spec"]["match_count"]
                done += t["done"]
            table = [
                {"candidate": c, "label": state["labels"]["candidates"][c], **self._rates(totals.get(c))}
                for c in r["candidates"]
            ]
            rounds.append({
                "round": r["round"], "candidates": len(r["candidates"]), "games": done, "planned_games": total,
                "finished": r["finished"], "survivors": r["survivors"], "tournaments": r["tournaments"],
                "table": [dict(row, rank=i + 1) for i, row in enumerate(standings(table, state["spec"]["metric"]))],
            })
        games_done = sum(r["games"] for r in rounds)
        public = {k: v for k, v in state.items() if k not in ("credential_sha", "secrets")}
        return {**public, "rounds": rounds, "games_done": games_done, "games_total": self._scheduled(state),
                "management": "open" if getattr(self.service, "open_management", False) else "credential",
                "can_manage": bool(getattr(self.service, "open_management", False))}

    @staticmethod
    def _scheduled(state):
        """Games the plan schedules: every candidate of a round gets whole rotations (at least one)."""
        n = state["spec"]["config"]["players"]
        return sum(p["candidates"] * math.ceil(max(n, p["games"] // p["candidates"]) / n) * n for p in state["plan"])

    def close(self):
        self.closed = True
        for thread in list(self.drivers.values()):
            thread.join(timeout=5)

    # ---------- the rounds ----------
    @staticmethod
    def _rates(row):
        row = row or {"games": 0, "wins": 0, "place": 0}
        g = max(1, row["games"])
        return {"games": row["games"], "wins": row["wins"], "win_rate": row["wins"] / g if row["games"] else 0.0,
                "avg_place": row["place"] / g if row["games"] else 0.0, "place": row["place"]}

    def _totals(self, state):
        """Results of every candidate over all its round tournaments so far (the candidate is entrant 0), from the
        stored tournament summaries."""
        totals = {}
        for r in state["rounds"]:
            for c, child in r["tournaments"].items():
                try:
                    t = self.service.tournaments().brief(child)
                except Exception:
                    continue
                row = totals.setdefault(int(c), {"games": 0, "wins": 0, "place": 0})
                for key in ("games", "wins", "place"):
                    row[key] += t["results"][0][key]
        return totals

    def _current_children(self, state):
        if not state["rounds"]:
            return []
        r = state["rounds"][-1]
        return [(child, state["secrets"][child]) for child in r["tournaments"].values() if child in state["secrets"]]

    def _ensure_driver(self, id):
        with self.lock:
            thread = self.drivers.get(id)
            if self.closed or (thread and thread.is_alive()):
                return
            thread = threading.Thread(target=self._drive, args=(id,), name=f"knockout-{id[:8]}", daemon=True)
            self.drivers[id] = thread
            thread.start()

    def _resume(self, id):
        """After a server restart the open round's unfinished tournaments are stopped or owned by nobody: restart them."""
        with self.lock:
            state = self._load(id)
        controller = self.service.tournaments()
        for child, secret in self._current_children(state):
            current = controller.status(child)
            if current["status"] in ("running", "draining", "stopped") and child not in controller.owners:
                self.service.restart_tournament(child, secret, current["spec_sha"], current["revision"])

    def _drive(self, id):
        try:
            self._resume(id)
        except Exception:  # noqa: BLE001 -- the round check below reports a tournament that cannot go on
            pass
        while not self.closed:
            try:
                with self.lock:
                    state = self._load(id)
                    if state["status"] != "running":
                        return
                    if not self._step(state):
                        self._save(state)
                        if state["status"] != "running":
                            return
            except Unknown:
                return
            except Exception as error:  # a failed round stops the knockout with a readable reason
                with self.lock:
                    try:
                        state = self._load(id)
                        state.update(status="failed", error=str(error) or type(error).__name__)
                        state["revision"] += 1
                        self._save(state)
                    except Unknown:
                        pass
                return
            time.sleep(0.2)

    def _step(self, state):
        """One look at the knockout. Returns True when nothing changed."""
        if not state["rounds"]:
            self._open_round(state, list(range(len(state["spec"]["candidates"]))))
            return False
        current = state["rounds"][-1]
        statuses = [self.service.tournaments().brief(child)["status"] for child in current["tournaments"].values()]
        if any(s in ("failed", "stopped", "cleanup_unconfirmed") for s in statuses):
            raise RuntimeError(f"A round {current['round']} tournament ended as {', '.join(sorted(set(statuses)))}")
        if not all(s == "completed" for s in statuses):
            return True
        totals = self._totals(state)
        plan = state["plan"][current["round"] - 1]
        rows = [{"candidate": c, **self._rates(totals.get(c))} for c in current["candidates"]]
        ordered = [r["candidate"] for r in standings(rows, state["spec"]["metric"])]
        current["finished"] = True
        current["survivors"] = ordered[: plan["survivors"]]
        if current["round"] == len(state["plan"]):
            state["status"] = "completed"
            state["top"] = current["survivors"]
            state["winner"] = ordered[0]
            state["revision"] += 1
        else:
            self._open_round(state, current["survivors"])
        return False

    def _open_round(self, state, candidates):
        spec = state["spec"]
        number = len(state["rounds"]) + 1
        plan = state["plan"][number - 1]
        n = spec["config"]["players"]
        per = max(n, plan["games"] // len(candidates))
        groups = max(1, math.ceil(per / n))
        rng = random.Random(f"{spec['seed']}:{number}")
        seeds = rng.sample(range(2**31), groups)
        children = {}
        for c in candidates:
            seats = [spec["candidates"][c], *spec["sparring"]]
            body = {
                "name": f"Knockout: {spec['name']} · round {number} · {state['labels']['candidates'][c]}"[:128],
                "config": spec["config"], "rules_version": spec["rules_version"], "engine_version": spec["engine_version"],
                "seats": seats, "seeds": seeds, "sampler_seeds": [[g * n + k for k in range(n)] for g in range(groups)],
                "rotations": list(range(n)), "match_count": groups * n, "schedule_cap": groups * n,
                "max_inflight": spec["max_inflight"], "max_record_bytes": 50_000_000, "max_decisions": 200_000,
            }
            made = self.service.create_tournament(body)
            state["secrets"][made["tournament_id"]] = made["creator_credential"]
            self.service.start_tournament(made["tournament_id"], made["creator_credential"], made["spec_sha"], made["revision"])
            children[str(c)] = made["tournament_id"]
        state["rounds"].append({"round": number, "candidates": candidates, "tournaments": children, "finished": False, "survivors": []})
        state["revision"] += 1


def register_knockout_routes(app, service, credential):
    from fastapi import Header

    @app.get("/api/knockouts")
    def listing():
        return service.knockouts().list()

    @app.post("/api/knockouts")
    def create(body: KnockoutInput):
        return service.knockouts().create(body.model_dump())

    @app.get("/api/knockouts/{id}")
    def status(id: str):
        return service.knockouts().status(id)

    @app.post("/api/knockouts/{id}/start")
    def start(id: str, body: KnockoutAction, authorization: str | None = Header(default=None)):
        return service.knockouts().start(id, credential(authorization), body.revision)

    @app.post("/api/knockouts/{id}/stop")
    def stop(id: str, body: KnockoutAction, authorization: str | None = Header(default=None)):
        return service.knockouts().stop(id, credential(authorization), body.revision)

    @app.post("/api/knockouts/{id}/delete")
    def delete(id: str, body: KnockoutAction, authorization: str | None = Header(default=None)):
        return service.knockouts().delete(id, credential(authorization), body.revision)
