"""Explicit durable gameplay batches; reads never schedule work."""

import gzip
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path

from boardcraft.contracts import GameConfig, config_data
from boardcraft.inference.catalog import (
    reference_data,
    reference_from_data,
    validate_reference,
)
from boardcraft.registry import get_game
from boardcraft.server.storage import digest, encode
from boardcraft.server.views import AccessDenied, Conflict

from .records import (
    ParticipantRecord,
    _file_identity,
    read_record,
    validate_record,
    write_record,
)
from .replay import replay
from .runner import record_version, run_configured_game, simulation_config
from .ranking import RatingKey


def _integer(v, minimum=0):
    if type(v) is not int or not minimum <= v <= 2**53 - 1:
        raise ValueError("Invalid explicit tournament integer")


@dataclass(frozen=True)
class TournamentSpec:
    name: str
    config: GameConfig
    rules_version: str
    engine_version: str
    references: tuple
    seeds: tuple
    sampler_seeds: tuple
    rotations: tuple
    match_count: int
    schedule_cap: int
    max_inflight: int
    max_record_bytes: int
    max_decisions: int
    # A pool tournament: the references are the pool and each seed group seats the listed entrants (indices into the
    # pool, one per seat). Empty for a fixed line-up, where every group seats all references.
    lineups: tuple = ()

    def __post_init__(self):
        if type(self.name) is not str or not self.name.strip() or len(self.name) > 128:
            raise ValueError("Choose a tournament name")
        for k in ("references", "seeds", "sampler_seeds", "rotations", "lineups"):
            if type(getattr(self, k)) is not tuple:
                raise ValueError("Tournament schedules must be immutable")
        if (
            not isinstance(self.config, GameConfig)
            or not self.rules_version
            or not self.engine_version
        ):
            raise ValueError("Choose exact gameplay compatibility")
        for v in (
            self.match_count,
            self.schedule_cap,
            self.max_inflight,
            self.max_record_bytes,
            self.max_decisions,
        ):
            _integer(v, 1)
        n = self.config.players
        if self.lineups:
            if len(self.references) < max(n, 2) or len(self.lineups) != len(self.seeds):
                raise ValueError("A pool needs one line-up per seed group")
            for lineup in self.lineups:
                if type(lineup) is not tuple or len(lineup) != n or len(set(lineup)) != n:
                    raise ValueError("Every line-up seats distinct pool entrants")
                for i in lineup:
                    _integer(i)
                    if i >= len(self.references):
                        raise ValueError("Line-up outside the pool")
        if (
            (len(self.references) != n and not self.lineups)
            or not self.seeds
            or not self.rotations
            or len(set(self.rotations)) != len(self.rotations)
            or len(self.sampler_seeds) != len(self.seeds)
        ):
            raise ValueError("Incomplete tournament schedule")
        for seed in self.seeds:
            _integer(seed)
        if len(set(self.seeds)) != len(self.seeds):
            raise ValueError("Duplicate seed groups")
        for row in self.sampler_seeds:
            if type(row) is not tuple or len(row) != n:
                raise ValueError("Choose every bot sampler seed")
            for seed in row:
                _integer(seed)
        for r in self.rotations:
            _integer(r)
            if r >= n:
                raise ValueError("Rotation outside the lineup")
        for ref in self.references:
            reference_data(ref)
        if (
            self.match_count != len(self.seeds) * len(self.rotations)
            or self.match_count > self.schedule_cap
            or self.max_inflight > self.schedule_cap
        ):
            raise ValueError(
                "Explicit count differs from bounded materialized schedule"
            )

    def data(self):
        extra = {"lineups": [list(x) for x in self.lineups]} if self.lineups else {}
        return dict(
            **extra,
            name=self.name,
            config=config_data(self.config),
            rules_version=self.rules_version,
            engine_version=self.engine_version,
            references=[reference_data(r) for r in self.references],
            seeds=list(self.seeds),
            sampler_seeds=[list(s) for s in self.sampler_seeds],
            rotations=list(self.rotations),
            **{
                k: getattr(self, k)
                for k in (
                    "match_count",
                    "schedule_cap",
                    "max_inflight",
                    "max_record_bytes",
                    "max_decisions",
                )
            },
        )

    @classmethod
    def from_data(cls, data):
        try:
            d = dict(data)
            d["config"] = GameConfig(**d["config"])
            d["references"] = tuple(reference_from_data(r) for r in d["references"])
            d["sampler_seeds"] = tuple(tuple(s) for s in d["sampler_seeds"])
            d["seeds"] = tuple(d["seeds"])
            d["rotations"] = tuple(d["rotations"])
            d["lineups"] = tuple(tuple(x) for x in d.get("lineups") or ())
            return cls(**d)
        except (TypeError, KeyError) as e:
            raise ValueError("Invalid tournament spec") from e


@dataclass(frozen=True)
class MatchSpec:
    match_id: str
    ordinal: int
    group: int
    rotation: int
    seed: int
    references: tuple
    sampler_seeds: tuple

    def data(self):
        return {
            "match_id": self.match_id,
            "ordinal": self.ordinal,
            "group": self.group,
            "rotation": self.rotation,
            "seed": self.seed,
            "references": [reference_data(r) for r in self.references],
            "sampler_seeds": list(self.sampler_seeds),
        }

    @classmethod
    def from_data(cls, data):
        d = dict(data)
        d["references"] = tuple(reference_from_data(r) for r in d["references"])
        d["sampler_seeds"] = tuple(d["sampler_seeds"])
        return cls(**d)


def schedule(spec, *, module=None):
    spec = TournamentSpec.from_data(spec.data())
    module = get_game(spec.config.game_id) if module is None else module
    info = module.info()
    if (
        (info.game_id, info.rules_version, info.engine_version)
        != (spec.config.game_id, spec.rules_version, spec.engine_version)
        or spec.config.variant not in info.variants
        or not info.min_players <= spec.config.players <= info.max_players
    ):
        raise ValueError("Incompatible tournament game")
    rows = []
    for group, seed in enumerate(spec.seeds):
        seated = (
            tuple(spec.references[i] for i in spec.lineups[group])
            if spec.lineups
            else spec.references
        )
        for rotation in spec.rotations:
            refs = seated[-rotation:] + seated[:-rotation] if rotation else seated
            seeds = spec.sampler_seeds[group]
            seeds = seeds[-rotation:] + seeds[:-rotation] if rotation else seeds
            ordinal = len(rows)
            body = [1, group, rotation, seed, [reference_data(r) for r in refs], seeds]
            rows.append(
                MatchSpec(
                    digest(encode(body)), ordinal, group, rotation, seed, refs, seeds
                )
            )
    return tuple(rows)


def _id(value):
    if type(value) is not str or re.fullmatch("[0-9a-f]{32}", value) is None:
        raise ValueError("Invalid tournament ID")
    return value


class Ownership:
    """OS-owned advisory byte lock, released only by its owner or process exit."""

    def __init__(self, path):
        self.file = open(path, "a+b")  # noqa: SIM115 -- held through owned execution and record drain
        self.file.seek(0, 2)
        if self.file.tell() == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            self.file.close()
            raise Conflict("Tournament has an active owner") from e

    def close(self):
        if not self.file.closed:
            self.file.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
            self.file.close()


class TournamentStore:
    def __init__(self, store):
        self.store = store
        self.root = store.directory / "tournament-records-v1"
        self.path(".", directory=True, create=True)
        with self.transaction(True) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS tournaments(tournament_id TEXT PRIMARY KEY,spec_json TEXT NOT NULL,spec_sha TEXT NOT NULL,creator_hash TEXT NOT NULL,status TEXT NOT NULL,revision INTEGER NOT NULL,run_token TEXT,stop_requested INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS tournament_matches(tournament_id TEXT NOT NULL,match_id TEXT NOT NULL,ordinal INTEGER NOT NULL,spec_json TEXT NOT NULL,spec_sha TEXT NOT NULL,status TEXT NOT NULL,current_attempt_token TEXT,result_json TEXT,result_sha TEXT,PRIMARY KEY(tournament_id,match_id),UNIQUE(tournament_id,ordinal),FOREIGN KEY(tournament_id) REFERENCES tournaments(tournament_id));
                CREATE TABLE IF NOT EXISTS tournament_attempts(token TEXT PRIMARY KEY,tournament_id TEXT NOT NULL,match_id TEXT NOT NULL,run_token TEXT NOT NULL,status TEXT NOT NULL,stage_relpath TEXT NOT NULL,temporary_relpath TEXT NOT NULL,record_sha TEXT,error_json TEXT,UNIQUE(tournament_id,match_id,token),FOREIGN KEY(tournament_id,match_id) REFERENCES tournament_matches(tournament_id,match_id));
                CREATE TABLE IF NOT EXISTS tournament_records(sha256 TEXT PRIMARY KEY,size INTEGER NOT NULL,relpath TEXT UNIQUE NOT NULL,state TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS tournament_record_refs(tournament_id TEXT NOT NULL,match_id TEXT NOT NULL,attempt_token TEXT NOT NULL,sha256 TEXT NOT NULL,role TEXT NOT NULL,PRIMARY KEY(tournament_id,match_id,attempt_token,sha256),FOREIGN KEY(tournament_id,match_id,attempt_token) REFERENCES tournament_attempts(tournament_id,match_id,token),FOREIGN KEY(sha256) REFERENCES tournament_records(sha256));
                CREATE TABLE IF NOT EXISTS tournament_ratings(tournament_id TEXT NOT NULL,key_sha TEXT NOT NULL,fit_spec_sha TEXT NOT NULL,input_results_sha TEXT NOT NULL,payload TEXT NOT NULL,checksum TEXT NOT NULL,PRIMARY KEY(tournament_id,key_sha,fit_spec_sha),FOREIGN KEY(tournament_id) REFERENCES tournaments(tournament_id));
                CREATE TABLE IF NOT EXISTS tournament_autostart(tournament_id TEXT PRIMARY KEY,FOREIGN KEY(tournament_id) REFERENCES tournaments(tournament_id));
                CREATE TABLE IF NOT EXISTS tournament_labels(tournament_id TEXT PRIMARY KEY,labels_json TEXT NOT NULL,FOREIGN KEY(tournament_id) REFERENCES tournaments(tournament_id));
                CREATE TABLE IF NOT EXISTS tournament_file_claims(attempt_token TEXT NOT NULL,role TEXT NOT NULL,relpath TEXT UNIQUE NOT NULL,state TEXT NOT NULL,identity_json TEXT,identity_sha TEXT,cleanup_unconfirmed INTEGER NOT NULL,PRIMARY KEY(attempt_token,role),FOREIGN KEY(attempt_token) REFERENCES tournament_attempts(token));
                CREATE TABLE IF NOT EXISTS tournament_summaries(tournament_id TEXT PRIMARY KEY,done INTEGER NOT NULL,payload TEXT NOT NULL,checksum TEXT NOT NULL,FOREIGN KEY(tournament_id) REFERENCES tournaments(tournament_id));
                CREATE TABLE IF NOT EXISTS tournament_created(tournament_id TEXT PRIMARY KEY,created_at REAL NOT NULL,FOREIGN KEY(tournament_id) REFERENCES tournaments(tournament_id));
            """)

    def transaction(self, write=False):
        return self.store.transaction(write, foreign_keys=True)

    @staticmethod
    def reserve_files(db, id, token, stage, temporary):
        if (
            stage != _id(id) + "-" + _id(token) + ".stage.json.gz"
            or temporary != id + "-" + token + ".atomic.tmp"
        ):
            raise ValueError("Invalid attempt file reservation")
        for role, rel in (
            ("stage", stage),
            ("temporary", temporary),
            ("witness", id + "-" + token + ".witness"),
        ):
            db.execute(
                "INSERT OR IGNORE INTO tournament_file_claims VALUES(?,?,?,?,?,?,?)",
                (token, role, rel, "reserved", None, None, 0),
            )

    def file_claims(self, token):
        # Explicit recovery of older attempts adds reservations, NEVER ownership.
        with self.transaction(True) as db:
            db.row_factory = __import__("sqlite3").Row
            attempt = db.execute(
                "SELECT * FROM tournament_attempts WHERE token=?", (token,)
            ).fetchone()
            if attempt is None:
                raise ValueError("Unknown file-owning attempt")
            self.reserve_files(
                db,
                attempt["tournament_id"],
                token,
                attempt["stage_relpath"],
                attempt["temporary_relpath"],
            )
            claims = {
                row["role"]: dict(row)
                for row in db.execute(
                    "SELECT * FROM tournament_file_claims WHERE attempt_token=?",
                    (token,),
                )
            }
            expected = {
                "stage": attempt["stage_relpath"],
                "temporary": attempt["temporary_relpath"],
                "witness": attempt["tournament_id"] + "-" + token + ".witness",
            }
            if set(claims) != set(expected) or any(
                claims[role]["relpath"] != rel for role, rel in expected.items()
            ):
                raise ValueError("Corrupt attempt file-claim membership")
            return claims

    def claim_file(self, id, match, token, run, role, path, identity):
        claims = self.file_claims(token)
        if (
            role not in ("temporary", "stage")
            or path != self.path(claims[role]["relpath"])
            or _file_identity(path.stat(follow_symlinks=False)) != identity
        ):
            raise ValueError("Created file differs from its reserved identity")
        if role == "stage":
            witness = claims["witness"]
            if (
                witness["state"] != "owned"
                or self.checked(witness["identity_json"], witness["identity_sha"])
                != identity
                or _file_identity(
                    self.path(witness["relpath"]).stat(follow_symlinks=False)
                )
                != identity
            ):
                raise ValueError("Created stage lacks its retained ownership witness")

        def persist(which):
            with self.transaction(True) as db:
                self.current(db, id, match, token, run)
                raw = encode(identity)
                changed = db.execute(
                    "UPDATE tournament_file_claims SET state='owned',identity_json=?,identity_sha=?,cleanup_unconfirmed=0 WHERE attempt_token=? AND role=? AND state='reserved'",
                    (raw, digest(raw), token, which),
                ).rowcount
                if changed != 1:
                    raise Conflict("File ownership claim already changed")

        persist(role)
        if role == "temporary":
            witness = self.path(claims["witness"]["relpath"])
            os.link(path, witness)
            if _file_identity(witness.stat(follow_symlinks=False)) != identity:
                raise ValueError("Ownership witness differs from the created file")
            persist("witness")

    def cleanup_attempt(self, token, *, remove=True):
        """Only witnessed successful claims permit unlink; ambiguous files remain."""
        claims = self.file_claims(token)
        present = {}
        identities = {}
        uncertain = set()
        for role, claim in claims.items():
            try:
                path = self.path(claim["relpath"])
                present[role] = path if path.exists() else None
                if present[role] is not None:
                    identities[role] = _file_identity(path.stat(follow_symlinks=False))
            except (OSError, ValueError):
                present[role] = None
                uncertain.add(role)

        def claimed_identity(claim):
            if claim["state"] != "owned":
                return None
            if (
                type(claim["identity_json"]) is not str
                or type(claim["identity_sha"]) is not str
            ):
                return None
            try:
                identity = self.checked(claim["identity_json"], claim["identity_sha"])
                if (
                    type(identity) is not dict
                    or set(identity) != {"device", "inode"}
                    or any(type(v) is not int for v in identity.values())
                    or identity["device"] < 0
                    or identity["inode"] <= 0
                ):
                    return None
                return identity
            except (TypeError, ValueError):
                return None

        anchor = claims["witness"]
        anchor_identity = None
        if present["witness"] is not None and anchor["state"] == "owned":
            anchor_identity = claimed_identity(anchor)
            if identities.get("witness") != anchor_identity:
                anchor_identity = None
        for role in ("temporary", "stage"):
            if present[role] is None:
                continue
            claim = claims[role]
            expected = claimed_identity(claim)
            if (
                anchor_identity is None
                or expected != anchor_identity
                or identities.get(role) != expected
            ):
                uncertain.add(role)
                continue
            if remove:
                try:
                    path = self.path(claim["relpath"])
                    witness = self.path(anchor["relpath"])
                    if (
                        _file_identity(path.stat(follow_symlinks=False)) != expected
                        or _file_identity(witness.stat(follow_symlinks=False))
                        != expected
                    ):
                        raise ValueError("Owned link identity changed")
                    path.unlink()
                except (OSError, ValueError):
                    uncertain.add(role)
        if present["witness"] is not None:
            if anchor_identity is None:
                uncertain.add("witness")
            elif remove and not uncertain:
                try:
                    path = self.path(anchor["relpath"])
                    if (
                        _file_identity(path.stat(follow_symlinks=False))
                        != anchor_identity
                    ):
                        raise ValueError("Ownership witness changed")
                    path.unlink()
                except (OSError, ValueError):
                    uncertain.add("witness")
        with self.transaction(True) as db:
            for role in claims:
                db.execute(
                    "UPDATE tournament_file_claims SET cleanup_unconfirmed=? WHERE attempt_token=? AND role=?",
                    (int(role in uncertain), token, role),
                )
        return not uncertain

    def cleanup_attempts(self, id):
        with self.transaction() as db:
            tokens = [
                row[0]
                for row in db.execute(
                    "SELECT token FROM tournament_attempts WHERE tournament_id=?", (id,)
                )
            ]
        results = [self.cleanup_attempt(token) for token in tokens]
        return all(results)

    def path(self, relative, *, directory=False, create=False):
        relative = Path(relative)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Unsafe tournament record path")
        if self.store.directory.resolve() != self.store.directory:
            raise ValueError("Runtime root was redirected")
        target = self.root / relative
        for p in (
            self.root,
            *[
                self.root.joinpath(*relative.parts[:i])
                for i in range(1, len(relative.parts) + 1)
            ],
        ):
            if p.is_symlink() or (
                p.exists()
                and getattr(p.stat(follow_symlinks=False), "st_file_attributes", 0)
                & 0x400
            ):
                raise ValueError("Redirected tournament path")
        if (
            not target.resolve().is_relative_to(self.root)
            or self.root.resolve() != self.root
        ):
            raise ValueError("Unsafe tournament record containment")
        if create:
            target.mkdir(parents=True, exist_ok=True)
        if target.exists() and (
            not target.is_dir() if directory else not target.is_file()
        ):
            raise ValueError("Unexpected tournament path type")
        return target

    @staticmethod
    def checked(payload, sha):
        if digest(payload) != sha:
            raise ValueError("Corrupt tournament state")
        return json.loads(payload)

    def load(self, db, id):
        _id(id)
        db.row_factory = __import__("sqlite3").Row
        row = db.execute(
            "SELECT * FROM tournaments WHERE tournament_id=?", (id,)
        ).fetchone()
        if row is None:
            raise ValueError("Unknown tournament")
        row = dict(row)
        row["spec"] = self.checked(row["spec_json"], row["spec_sha"])
        TournamentSpec.from_data(row["spec"])
        return row

    def matches(self, id):
        with self.transaction() as db:
            self.load(db, id)
            rows = []
            for r in db.execute(
                "SELECT * FROM tournament_matches WHERE tournament_id=? ORDER BY ordinal",
                (id,),
            ):
                r = dict(r)
                r["spec"] = self.checked(r["spec_json"], r["spec_sha"])
                r["result"] = (
                    None
                    if r["result_json"] is None
                    else self.checked(r["result_json"], r["result_sha"])
                )
                rows.append(r)
            return rows

    # ---- stored summaries: what the tournament list shows, kept up to date by the server on every completion ----

    @staticmethod
    def brief_spec(spec):
        """The spec fields a tournament list needs (no seeds, schedules or full references)."""
        return {
            "name": spec["name"],
            "config": spec["config"],
            "rules_version": spec["rules_version"],
            "engine_version": spec["engine_version"],
            "match_count": spec["match_count"],
            "max_inflight": spec["max_inflight"],
            "rotations": spec["rotations"],
            "pool": bool(spec.get("lineups")),
            "seed_count": len(spec["seeds"]),
            "first_seed": spec["seeds"][0] if spec["seeds"] else None,
            "references": [
                {k: r.get(k) for k in ("kind", "identity", "builtin_kind") if r.get(k) is not None}
                for r in spec["references"]
            ],
        }

    @classmethod
    def empty_summary(cls, spec):
        """No completed match yet: per entrant (in spec order) games, wins, sum of places and of scores; a solo
        tournament (one player per table) also keeps every score by match ordinal."""
        return {
            "spec": cls.brief_spec(spec),
            "done": 0,
            "results": [
                {"games": 0, "wins": 0, "place": 0, "score": 0}
                for _ in spec["references"]
            ],
            "scores": [None] * spec["match_count"] if spec["config"]["players"] == 1 else None,
        }

    @staticmethod
    def add_result(summary, spec, ordinal, group, rotation, result):
        """Counts one completed match. Seat s holds line-up member (s - rotation) mod players, as scheduled."""
        players = spec["config"]["players"]
        lineup = spec["lineups"][group] if spec.get("lineups") else list(range(len(spec["references"])))
        for seat in range(players):
            row = summary["results"][lineup[(seat - rotation) % players]]
            row["games"] += 1
            row["place"] += result["placements"][seat]
            row["score"] += result["scores"][seat]
            row["wins"] += int(result["placements"][seat] == 1)
        summary["done"] += 1
        if summary["scores"] is not None:
            summary["scores"][ordinal] = result["scores"][0]
        return summary

    def summarize(self, db, id, spec=None):
        """The summary recomputed from the stored completed matches (recovery and rows not stored yet)."""
        spec = self.load(db, id)["spec"] if spec is None else spec
        summary = self.empty_summary(spec)
        for raw, sha, result_json, result_sha in db.execute(
            "SELECT spec_json,spec_sha,result_json,result_sha FROM tournament_matches WHERE tournament_id=? AND status='completed' ORDER BY ordinal",
            (id,),
        ):
            match = self.checked(raw, sha)
            self.add_result(summary, spec, match["ordinal"], match["group"], match["rotation"],
                            self.checked(result_json, result_sha))
        return summary

    @staticmethod
    def store_summary(db, id, summary):
        payload = encode(summary)
        db.execute(
            "INSERT OR REPLACE INTO tournament_summaries VALUES(?,?,?,?)",
            (id, summary["done"], payload, digest(payload)),
        )

    def summary(self, db, id):
        """The stored summary; a tournament the startup recovery has not reached yet is summarized here, unstored."""
        row = db.execute(
            "SELECT payload,checksum FROM tournament_summaries WHERE tournament_id=?", (id,)
        ).fetchone()
        if row is not None and digest(row[0]) == row[1]:
            return json.loads(row[0])
        return self.summarize(db, id)

    def rating_results(self, db, id, *, match_id=None, match_ids=None):
        """Read every completed match, including rows whose publication binding is missing."""
        rows = db.execute("""
            SELECT m.match_id, m.spec_json, m.spec_sha, m.result_json, m.result_sha,
                   a.token, o.sha256, o.size, o.relpath
            FROM tournament_matches m
            LEFT JOIN tournament_attempts a ON a.tournament_id=m.tournament_id AND a.match_id=m.match_id
                AND a.token=m.current_attempt_token AND a.status='completed'
            LEFT JOIN tournament_record_refs f ON f.tournament_id=a.tournament_id AND f.match_id=a.match_id
                AND f.attempt_token=a.token AND f.sha256=a.record_sha AND f.role='completed'
            LEFT JOIN tournament_records o ON o.sha256=f.sha256 AND o.state='ready'
            WHERE m.tournament_id=? AND m.status='completed' AND (? IS NULL OR m.match_id=?)
                AND (? IS NULL OR m.match_id IN (SELECT value FROM json_each(?))) ORDER BY m.ordinal
        """, (id, match_id, match_id, None if match_ids is None else encode(match_ids),
                  None if match_ids is None else encode(match_ids)))
        results = []
        for row in rows:
            result = self.checked(row["result_json"], row["result_sha"])
            if (row["sha256"] is None or row["relpath"] != row["sha256"] + ".json.gz"
                    or (row["match_id"], row["token"], row["sha256"], row["size"]) !=
                       (result["match_id"], result["attempt_token"], result["record_sha256"], result["record_size"])):
                raise ValueError("Missing or inconsistent completed record binding")
            if result["participants"] != self.checked(row["spec_json"], row["spec_sha"])["references"]:
                raise ValueError("Completed result differs from frozen participants")
            results.append(result)
        return results

    def authorize(self, row, credential, revision, sha=None):
        # Open management (a private server): no creator credential is needed; the revision still guards changes.
        if not getattr(self, "open_management", False) and (
            type(credential) is not str
            or not hmac.compare_digest(row["creator_hash"], digest(credential))
        ):
            raise AccessDenied("Invalid tournament creator credential")
        if (
            type(revision) is not int
            or row["revision"] != revision
            or (sha is not None and sha != row["spec_sha"])
        ):
            raise Conflict("Tournament revision or schedule changed")

    @staticmethod
    def update(db, id, *, status, token=None, stop=False):
        db.execute(
            "UPDATE tournaments SET status=?,run_token=?,stop_requested=?,revision=revision+1 WHERE tournament_id=?",
            (status, token, int(stop), id),
        )

    @staticmethod
    def check_expansion(path, limit):
        with gzip.open(path, "rb") as stream:
            if len(stream.read(limit + 1)) > limit:
                raise ValueError("Tournament record expansion limit")

    def verify_file(self, sha, size, rel, spec):
        if (
            type(sha) is not str
            or re.fullmatch("[0-9a-f]{64}", sha) is None
            or rel != sha + ".json.gz"
        ):
            raise ValueError("Invalid tournament record membership")
        path = self.path(rel)
        if (
            not path.is_file()
            or path.stat().st_size != size
            or size > spec.max_record_bytes
            or hashlib.sha256(path.read_bytes()).hexdigest() != sha
        ):
            raise ValueError("Corrupt tournament record object")
        # Bound expansion before canonical decoding.
        self.check_expansion(path, spec.max_record_bytes)
        return read_record(path)

    def validate(self, record, spec, match, module):
        record = validate_record(record)
        if (
            record.version != record_version(module)
            or not record.complete
            or record.config != spec.config
            or (record.rules_version, record.engine_version)
            != (spec.rules_version, spec.engine_version)
            or len(record.steps) > spec.max_decisions
            or tuple(p.bot_ref for p in record.participants) != match.references
        ):
            raise ValueError("Record differs from frozen match")
        # Canonical engine snapshots prove the scheduled chance seed, not a label.
        expected = module.new(spec.config, match.seed)
        if module.restore(record.initial_snapshot).snapshot() != expected.snapshot():
            raise ValueError("Record differs from scheduled seed")
        replay(record, module=module)
        return record

    def verify_prepared(self, id, spec, matches, module):
        """Validate existing immutable dependencies before retiring/retrying work."""
        frozen = {row["match_id"]: row for row in matches}
        with self.transaction() as db:
            self.load(db, id)
            refs = [
                dict(row)
                for row in db.execute(
                    """
                SELECT f.match_id,f.attempt_token,f.sha256,a.record_sha,r.size,r.relpath,r.state
                FROM tournament_record_refs f
                JOIN tournament_attempts a ON a.token=f.attempt_token
                    AND a.tournament_id=f.tournament_id AND a.match_id=f.match_id
                JOIN tournament_records r ON r.sha256=f.sha256
                WHERE f.tournament_id=? AND f.role IN ('prepared','retired')
            """,
                    (id,),
                )
            ]
        for ref in refs:
            row = frozen.get(ref["match_id"])
            if (
                row is None
                or ref["record_sha"] != ref["sha256"]
                or ref["relpath"] != ref["sha256"] + ".json.gz"
                or re.fullmatch("[0-9a-f]{64}", ref["sha256"]) is None
                or ref["state"] not in ("publishing", "ready")
            ):
                raise ValueError("Invalid prepared tournament dependency")
            path = self.path(ref["relpath"])
            if not path.exists() and ref["state"] == "publishing":
                # Reservation may precede the first immutable hardlink.
                continue
            record = self.verify_file(ref["sha256"], ref["size"], ref["relpath"], spec)
            self.validate(record, spec, MatchSpec.from_data(row["spec"]), module)

    def complete(self, id, match, token, run, record, module):
        with self.transaction() as db:
            self.current(db, id, match.match_id, token, run)
            row = self.load(db, id)
            spec = TournamentSpec.from_data(row["spec"])
            frozen = db.execute(
                "SELECT spec_json,spec_sha FROM tournament_matches WHERE tournament_id=? AND match_id=?",
                (id, match.match_id),
            ).fetchone()
            if self.checked(frozen[0], frozen[1]) != match.data():
                raise ValueError("Completion differs from the stored immutable match")
            attempt = db.execute(
                "SELECT * FROM tournament_attempts WHERE token=?", (token,)
            ).fetchone()
            if attempt is None:
                raise Conflict("Unknown tournament attempt")
            attempt = dict(attempt)
            if (
                attempt["tournament_id"],
                attempt["match_id"],
                attempt["run_token"],
                attempt["stage_relpath"],
                attempt["temporary_relpath"],
            ) != (
                id,
                match.match_id,
                run,
                id + "-" + _id(token) + ".stage.json.gz",
                id + "-" + token + ".atomic.tmp",
            ):
                raise ValueError("Invalid attempt membership")
        self.validate(record, spec, match, module)
        stage = self.path(attempt["stage_relpath"])
        temporary = self.path(attempt["temporary_relpath"])
        try:
            write_record(
                record,
                stage,
                temporary_path=temporary,
                on_created=lambda role, path, identity: self.claim_file(
                    id, match.match_id, token, run, role, path, identity
                ),
            )
        except Exception:
            self.cleanup_attempt(token, remove=False)
            raise
        if not self.cleanup_attempt(token, remove=False):
            raise Conflict("Tournament staging cleanup_unconfirmed; files retained")
        size = stage.stat().st_size
        if size > spec.max_record_bytes:
            raise ValueError("Tournament record byte limit")
        self.check_expansion(stage, spec.max_record_bytes)
        sha = hashlib.sha256(stage.read_bytes()).hexdigest()
        rel = sha + ".json.gz"
        with self.transaction(True) as db:
            self.current(db, id, match.match_id, token, run)
            obj = db.execute(
                "SELECT * FROM tournament_records WHERE sha256=?", (sha,)
            ).fetchone()
            if obj is not None and dict(obj)["state"] == "gc_pending":
                raise Conflict("Record object pending deletion")
            db.execute(
                "INSERT OR IGNORE INTO tournament_records VALUES(?,?,?,?)",
                (sha, size, rel, "publishing"),
            )
            db.execute(
                "INSERT OR IGNORE INTO tournament_record_refs VALUES(?,?,?,?,?)",
                (id, match.match_id, token, sha, "prepared"),
            )
            db.execute(
                "UPDATE tournament_attempts SET record_sha=?,status=? WHERE token=?",
                (sha, "prepared", token),
            )
        target = self.path(rel)
        try:
            os.link(stage, target)
        except FileExistsError:
            pass
        self.verify_file(sha, size, rel, spec)
        result = {
            "key": RatingKey.from_config(spec.config, spec.rules_version, module=module).data(),
            "engine_version": spec.engine_version,
            "participants": [reference_data(r) for r in match.references],
            "identities": [r.identity for r in match.references],
            "placements": list(record.final_result.placements),
            "scores": list(record.final_result.scores),
            "record_sha256": sha,
            "record_size": size,
            "match_id": match.match_id,
            "attempt_token": token,
        }
        with self.transaction(True) as db:
            self.current(db, id, match.match_id, token, run)
            db.execute(
                "UPDATE tournament_records SET state=? WHERE sha256=?", ("ready", sha)
            )
            db.execute(
                "UPDATE tournament_record_refs SET role=? WHERE tournament_id=? AND match_id=? AND attempt_token=?",
                ("completed", id, match.match_id, token),
            )
            db.execute(
                "UPDATE tournament_matches SET status=?,result_json=?,result_sha=? WHERE tournament_id=? AND match_id=?",
                (
                    "completed",
                    encode(result),
                    digest(encode(result)),
                    id,
                    match.match_id,
                ),
            )
            db.execute(
                "UPDATE tournament_attempts SET status=? WHERE token=?",
                ("completed", token),
            )
            db.execute("DELETE FROM tournament_ratings WHERE tournament_id=?", (id,))
            # The list summary follows in the same transaction: incrementally, or recomputed when it is missing or
            # does not count exactly the earlier completions.
            stored = db.execute(
                "SELECT done,payload,checksum FROM tournament_summaries WHERE tournament_id=?", (id,)
            ).fetchone()
            counted = db.execute(
                "SELECT COUNT(*) FROM tournament_matches WHERE tournament_id=? AND status='completed'", (id,)
            ).fetchone()[0]
            if stored is not None and digest(stored[1]) == stored[2] and stored[0] == counted - 1:
                summary = self.add_result(json.loads(stored[1]), row["spec"], match.ordinal, match.group, match.rotation, result)
            else:
                summary = self.summarize(db, id, row["spec"])
            self.store_summary(db, id, summary)
        self.cleanup_attempt(token)
        return result

    def current(self, db, id, match, token, run):
        row = self.load(db, id)
        active = db.execute(
            "SELECT current_attempt_token,status FROM tournament_matches WHERE tournament_id=? AND match_id=?",
            (id, match),
        ).fetchone()
        if (
            row["run_token"] != run
            or row["status"] not in ("running", "draining")
            or active is None
            or active[0] != token
            or active[1] == "completed"
        ):
            raise Conflict("Stale tournament attempt")


class FutureExecutor:
    """Constructing this adapter does not create a pool or start games."""

    def __init__(self, workers):
        self.workers = workers
        self.pool = None

    def submit(self, work):
        if self.pool is None:
            self.pool = ThreadPoolExecutor(
                max_workers=self.workers, thread_name_prefix="boardcraft-tournament"
            )
        return self.pool.submit(work)

    def poll(self, h):
        return h.done()

    def result(self, h):
        return h.result()

    def cancel_queued(self, h):
        return h.cancel()

    def drained(self, h):
        return h.done()

    def close(self):
        if self.pool:
            self.pool.shutdown(wait=True, cancel_futures=True)


class ThreadDriver:
    def start(self, work):
        threading.Thread(
            target=work, name="boardcraft-tournament-control", daemon=True
        ).start()


def gameplay_runner(spec, catalog, module=None):
    module = get_game(spec.config.game_id) if module is None else module

    def run(match):
        bots = []
        try:
            for ref, seed in zip(match.references, match.sampler_seeds):
                bots.append(catalog.resolve(ref, seed))
            participants = tuple(
                ParticipantRecord(i, r.kind, r) for i, r in enumerate(match.references)
            )
            return run_configured_game(
                module, spec.config, match.seed, tuple(bots), participants=participants, max_steps=spec.max_decisions
            )
        finally:
            for bot in bots:
                close = getattr(bot, "close", None)
                if close:
                    close()

    return run


class TournamentController:
    def __init__(
        self,
        store,
        *,
        catalog=None,
        runner=None,
        executor=None,
        driver=None,
        module_for=get_game,
        auto_fit=None,
        on_completed=None,
    ):
        # Called (without arguments) after a tournament completed and was fitted: the server refreshes its rankings.
        self.on_completed = on_completed
        # The rating fit the server applies by itself to every completed line-up tournament (None: no automatic fit).
        self.auto_fit_spec = auto_fit
        self.store = store
        self.catalog = catalog
        self.runner = runner
        self.executor = executor
        self.driver = ThreadDriver() if driver is None else driver
        self.module_for = module_for
        self.owners = {}
        self.handles = {}
        self.executors = {}
        self.guard = threading.RLock()

    def create(self, spec, *, autostart=False, labels=None):
        """Saves a pending tournament. ``autostart``: the server starts it itself (right away through the caller, and
        again by the startup recovery if a crash came between saving and starting). ``labels``: display names of the
        pinned entrants by identity, kept for the rankings when a model later disappears."""
        module = self.module_for(spec.config.game_id)
        spec = replace(spec, config=simulation_config(module, spec.config))
        matches = schedule(spec, module=module)
        for ref in spec.references:
            validate_reference(ref, module)
        id = uuid.uuid4().hex
        credential = secrets.token_urlsafe(32)
        body = encode(spec.data())
        sha = digest(body)
        with self.store.transaction(True) as db:
            db.execute(
                "INSERT INTO tournaments VALUES(?,?,?,?,?,?,?,?)",
                (id, body, sha, digest(credential), "pending", 0, None, 0),
            )
            for m in matches:
                raw = encode(m.data())
                db.execute(
                    "INSERT INTO tournament_matches VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        id,
                        m.match_id,
                        m.ordinal,
                        raw,
                        digest(raw),
                        "pending",
                        None,
                        None,
                        None,
                    ),
                )
            self.store.store_summary(db, id, self.store.empty_summary(spec.data()))
            db.execute("INSERT INTO tournament_created VALUES(?,?)", (id, time.time()))
            if autostart:
                db.execute("INSERT INTO tournament_autostart VALUES(?)", (id,))
            if labels:
                db.execute("INSERT INTO tournament_labels VALUES(?,?)", (id, encode(labels)))
        return {
            "tournament_id": id,
            "creator_credential": credential,
            "spec_sha": sha,
            "status": "pending",
            "revision": 0,
        }

    def close(self):
        """Stop reservations, then drain owned execution AND publication."""
        with self.guard:
            for id in list(self.owners):
                with self.store.transaction(True) as db:
                    row = self.store.load(db, id)
                    self.store.update(
                        db, id, status="draining", token=row["run_token"], stop=True
                    )
        while self.owners:
            for id in list(self.owners):
                with self.guard:
                    if id in self.owners:
                        self.advance(id)
            if self.owners:
                time.sleep(0.02)

    def status(self, id):
        with self.store.transaction() as db:
            r = self.store.load(db, id)
            uncertain = bool(
                db.execute(
                    "SELECT 1 FROM tournament_file_claims f JOIN tournament_attempts a ON a.token=f.attempt_token WHERE a.tournament_id=? AND f.cleanup_unconfirmed=1 LIMIT 1",
                    (id,),
                ).fetchone()
            )
        rows = self.store.matches(id)
        return {
            "tournament_id": id,
            "spec_sha": r["spec_sha"],
            "spec": r["spec"],
            "status": r["status"],
            "revision": r["revision"],
            "cleanup_unconfirmed": uncertain or r["status"] == "cleanup_unconfirmed",
            "matches": [
                {
                    "match_id": m["match_id"],
                    "ordinal": m["ordinal"],
                    "status": m["status"],
                    "result": m["result"],
                }
                for m in rows
            ],
        }

    def list(self):
        with self.store.transaction() as db:
            ids = [
                r[0]
                for r in db.execute(
                    "SELECT tournament_id FROM tournaments WHERE status!='deleted' ORDER BY rowid DESC"
                )
            ]
        return [self.status(id) for id in ids]

    def list_brief(self):
        """The tournament list from the stored summaries: status, progress and per-entrant results, no matches.
        Newest first: by the saved creation time, and in saving order for tournaments saved without one."""
        with self.store.transaction() as db:
            rows = db.execute(
                "SELECT t.tournament_id,t.spec_sha,t.status,t.revision,EXISTS(SELECT 1 FROM tournament_file_claims f "
                "JOIN tournament_attempts a ON a.token=f.attempt_token WHERE a.tournament_id=t.tournament_id "
                "AND f.cleanup_unconfirmed=1) FROM tournaments t LEFT JOIN tournament_created c ON c.tournament_id=t.tournament_id "
                "WHERE t.status!='deleted' ORDER BY coalesce(c.created_at,0) DESC,t.rowid DESC"
            ).fetchall()
            return [self._brief(db, *row) for row in rows]

    def brief(self, id):
        """One tournament as the list shows it, with the scores of a solo tournament by match ordinal."""
        with self.store.transaction() as db:
            row = self.store.load(db, id)
            uncertain = db.execute(
                "SELECT 1 FROM tournament_file_claims f JOIN tournament_attempts a ON a.token=f.attempt_token WHERE a.tournament_id=? AND f.cleanup_unconfirmed=1 LIMIT 1",
                (id,),
            ).fetchone()
            return self._brief(db, id, row["spec_sha"], row["status"], row["revision"], uncertain, scores=True)

    def _brief(self, db, id, spec_sha, status, revision, uncertain, *, scores=False):
        summary = self.store.summary(db, id)
        created = db.execute("SELECT created_at FROM tournament_created WHERE tournament_id=?", (id,)).fetchone()
        out = {
            "tournament_id": id,
            "created": None if created is None else created[0],
            "spec_sha": spec_sha,
            "spec": summary["spec"],
            "status": status,
            "revision": revision,
            "cleanup_unconfirmed": bool(uncertain) or status == "cleanup_unconfirmed",
            "done": summary["done"],
            "results": summary["results"],
        }
        if scores:
            out["scores"] = summary["scores"]
        return out

    def match_page(self, id, offset=0, limit=100):
        """Matches in schedule order, one page at a time."""
        if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 500:
            raise ValueError("Choose an offset and a page of 1 to 500 matches")
        with self.store.transaction() as db:
            self.store.load(db, id)
            total = db.execute("SELECT COUNT(*) FROM tournament_matches WHERE tournament_id=?", (id,)).fetchone()[0]
            items = [
                {
                    "match_id": match_id,
                    "ordinal": ordinal,
                    "status": status,
                    "result": None if raw is None else self.store.checked(raw, sha),
                }
                for match_id, ordinal, status, raw, sha in db.execute(
                    "SELECT match_id,ordinal,status,result_json,result_sha FROM tournament_matches WHERE tournament_id=? "
                    "ORDER BY ordinal LIMIT ? OFFSET ?",
                    (id, limit, offset),
                )
            ]
        return {"tournament_id": id, "offset": offset, "limit": limit, "total": total, "items": items}

    def recover_summaries(self):
        """Startup recovery (idempotent): stores the summary of every tournament that has none or whose count differs
        from its completed matches. Returns the recomputed IDs."""
        with self.store.transaction() as db:
            ids = [r[0] for r in db.execute(
                "SELECT t.tournament_id FROM tournaments t LEFT JOIN tournament_summaries s ON s.tournament_id=t.tournament_id "
                "WHERE s.tournament_id IS NULL OR s.done!=(SELECT COUNT(*) FROM tournament_matches m "
                "WHERE m.tournament_id=t.tournament_id AND m.status='completed') ORDER BY t.tournament_id")]
        fixed = []
        for id in ids:
            try:
                with self.store.transaction(True) as db:
                    self.store.store_summary(db, id, self.store.summarize(db, id))
                fixed.append(id)
            except Exception:  # noqa: BLE001 -- a tournament that cannot be summarized is summarized on reads
                continue
        return fixed

    def verified_rating_results(self, row, results):
        """Verify the canonical files for a previously bound completed snapshot."""
        spec = TournamentSpec.from_data(row["spec"])
        key = RatingKey.from_config(spec.config, spec.rules_version, module=self.module_for(spec.config.game_id))
        for result in results:
            if result.get("key") != key.data():
                raise ValueError("Completed result differs from its canonical stratum")
            record = self.store.verify_file(
                result["record_sha256"],
                result["record_size"],
                result["record_sha256"] + ".json.gz",
                spec,
            )
            if (
                not record.complete
                or record.config != spec.config
                or record.rules_version != spec.rules_version
                or record.engine_version != spec.engine_version
                or result["engine_version"] != record.engine_version
                or list(record.final_result.scores) != result["scores"]
                or list(record.final_result.placements) != result["placements"]
                or [reference_data(p.bot_ref) for p in record.participants]
                != result["participants"]
                or [p.bot_ref.identity for p in record.participants] != result["identities"]
            ):
                raise ValueError("Completed result differs from its canonical record")
        return results

    def fit(self, id, credential, revision, fit, *, system=False):
        """Fits ratings of the completed results. The creator may choose any profile; the server's own automatic
        fit (``system``) needs no credential and only ever happens after completion."""
        from .ranking import RatingFitSpec, fit_ratings

        with self.store.transaction() as db:
            row = self.store.load(db, id)
            if system:
                if row["status"] != "completed":
                    raise Conflict("Only a completed tournament is fitted automatically")
            else:
                self.store.authorize(row, credential, revision)
            results = self.store.rating_results(db, id)
        spec = TournamentSpec.from_data(row["spec"])
        key = RatingKey.from_config(spec.config, spec.rules_version, module=self.module_for(spec.config.game_id))
        settings = RatingFitSpec(**fit)
        self.verified_rating_results(row, results)
        input_sha = digest(encode(results))
        payload = encode(fit_ratings(results, key, fit_spec=settings))
        ks = digest(encode(key.data()))
        fs = digest(encode(settings.data()))
        with self.store.transaction(True) as db:
            latest = self.store.load(db, id)
            if not system:
                self.store.authorize(latest, credential, revision)
            if digest(encode(self.store.rating_results(db, id))) != input_sha:
                raise Conflict("Completed result snapshot changed")
            if latest["status"] in ("deleting", "deleted"):
                raise Conflict("Tournament is deleted")
            db.execute(
                "INSERT OR REPLACE INTO tournament_ratings VALUES(?,?,?,?,?,?)",
                (id, ks, fs, input_sha, payload, digest(payload)),
            )
        return json.loads(payload)

    def needs_auto_fit(self, id):
        """A completed line-up tournament (more than one player) without the automatic fit yet. Solo tournaments
        compare scores only and never enter the Elo rankings."""
        if self.auto_fit_spec is None:
            return False
        from .ranking import RatingFitSpec

        fs = digest(encode(RatingFitSpec(**self.auto_fit_spec).data()))
        with self.store.transaction() as db:
            row = self.store.load(db, id)
            if row["status"] != "completed" or row["spec"]["config"]["players"] < 2:
                return False
            return db.execute(
                "SELECT 1 FROM tournament_ratings WHERE tournament_id=? AND fit_spec_sha=? LIMIT 1", (id, fs)
            ).fetchone() is None

    def auto_fit(self, id):
        """Fits a completed line-up tournament once with the recommended profile. Idempotent: an existing fit is kept."""
        if not self.needs_auto_fit(id):
            return False
        self.fit(id, None, None, self.auto_fit_spec, system=True)
        return True

    def recover_fits(self):
        """Startup recovery: fits every completed line-up tournament that has no automatic fit (a crash between
        completion and the fit, or a tournament completed before automatic fits existed). Returns the fitted IDs."""
        if self.auto_fit_spec is None:
            return []
        with self.store.transaction() as db:
            ids = [r[0] for r in db.execute(
                "SELECT tournament_id FROM tournaments WHERE status='completed' ORDER BY tournament_id")]
        fitted = []
        for id in ids:
            try:
                if self.auto_fit(id):
                    fitted.append(id)
            except Exception:  # noqa: BLE001 -- one unreadable tournament must not stop the others
                continue
        return fitted

    def ratings(self, id):
        with self.store.transaction() as db:
            self.store.load(db, id)
            return [
                self.store.checked(r[0], r[1])
                for r in db.execute(
                    "SELECT payload,checksum FROM tournament_ratings WHERE tournament_id=? ORDER BY key_sha,fit_spec_sha",
                    (id,),
                )
            ]

    def match_replay(self, id, match_id, position=0):
        from boardcraft.server.replay import project_record

        row = next(
            (r for r in self.store.matches(id) if r["match_id"] == match_id), None
        )
        if row is None or row["status"] != "completed":
            raise ValueError("Completed tournament match unavailable")
        status = self.status(id)
        spec = TournamentSpec.from_data(status["spec"])
        result = row["result"]
        record = self.store.verify_file(
            result["record_sha256"],
            result["record_size"],
            result["record_sha256"] + ".json.gz",
            spec,
        )
        return project_record(
            record, self.module_for(spec.config.game_id), None, position
        )

    def start(self, id, credential, sha, revision):
        return self._start(id, credential, sha, revision, False)

    def restart(self, id, credential, sha, revision):
        return self._start(id, credential, sha, revision, True)

    def start_saved(self, id):
        """The server's own start of a tournament saved with ``autostart`` that is still pending (no credential)."""
        with self.store.transaction() as db:
            row = self.store.load(db, id)
            flagged = db.execute("SELECT 1 FROM tournament_autostart WHERE tournament_id=?", (id,)).fetchone()
        if flagged is None or row["status"] != "pending":
            return False
        self._start(id, None, row["spec_sha"], row["revision"], False, system=True)
        return True

    def recover_starts(self):
        """Startup recovery: starts every tournament saved to start right away that is still pending. Returns the IDs."""
        with self.store.transaction() as db:
            ids = [r[0] for r in db.execute(
                "SELECT a.tournament_id FROM tournament_autostart a JOIN tournaments t ON t.tournament_id=a.tournament_id "
                "WHERE t.status='pending' ORDER BY a.tournament_id")]
        started = []
        for id in ids:
            try:
                if self.start_saved(id):
                    started.append(id)
            except Exception:  # noqa: BLE001 -- one tournament that cannot start must not stop the others
                continue
        return started

    def settle_orphan(self, id):
        """A draining tournament that no live run owns (its server stopped mid-run) settles to ``stopped``: the
        in-flight attempts of the dead run are marked interrupted (their partial work is never counted), completed
        matches are kept. Returns True when the tournament was settled."""
        with self.guard:
            if id in self.owners:
                return False
            with self.store.transaction() as db:
                row = self.store.load(db, id)
            if row["status"] != "draining":
                return False
            try:
                lock = Ownership(self.store.path(_id(id) + ".lock"))
            except Conflict:
                return False  # another live process still owns the run
            try:
                self.store.cleanup_attempts(id)
                with self.store.transaction(True) as db:
                    row = self.store.load(db, id)
                    if row["status"] != "draining":
                        return False
                    db.execute(
                        "UPDATE tournament_attempts SET status='interrupted' WHERE tournament_id=? AND status NOT IN ('completed','failed','cancelled','interrupted')",
                        (id,),
                    )
                    db.execute(
                        "UPDATE tournament_matches SET status='pending',current_attempt_token=NULL WHERE tournament_id=? AND status NOT IN ('completed','failed')",
                        (id,),
                    )
                    db.execute(
                        "UPDATE tournament_matches SET current_attempt_token=NULL WHERE tournament_id=? AND status='failed'",
                        (id,),
                    )
                    self.store.update(db, id, status="stopped", token=row["run_token"], stop=True)
            finally:
                lock.close()
            return True

    def recover_orphans(self):
        """Startup recovery: settles every draining tournament without a live owner. Returns the IDs."""
        with self.store.transaction() as db:
            ids = [r[0] for r in db.execute("SELECT tournament_id FROM tournaments WHERE status='draining' ORDER BY tournament_id")]
        settled = []
        for id in ids:
            try:
                if self.settle_orphan(id):
                    settled.append(id)
            except Exception:  # noqa: BLE001 -- one tournament that cannot settle must not stop the others
                continue
        return settled

    def recover_details(self, labels_for):
        """Startup recovery (idempotent) for tournaments saved by an earlier server: a creation time (the time of the
        first stored game file, else the time of the tournament saved before it) and the display names of every
        entrant (``labels_for(spec)`` gives names by identity; names saved at creation win). Returns the changed IDs."""
        changed = []
        with self.store.transaction() as db:
            rows = db.execute(
                "SELECT t.tournament_id,t.spec_json,c.created_at,l.labels_json FROM tournaments t "
                "LEFT JOIN tournament_created c ON c.tournament_id=t.tournament_id "
                "LEFT JOIN tournament_labels l ON l.tournament_id=t.tournament_id ORDER BY t.rowid"
            ).fetchall()
            files = {}
            for id, relpath in db.execute(
                "SELECT f.tournament_id,o.relpath FROM tournament_record_refs f JOIN tournament_records o ON o.sha256=f.sha256"
            ):
                files.setdefault(id, []).append(relpath)
        previous = 0.0
        for id, spec_json, created, raw_labels in rows:
            if created is None:
                times = []
                for relpath in files.get(id, []):
                    try:
                        times.append((self.store.root / relpath).stat().st_mtime)
                    except OSError:
                        continue
                created = max(min(times), previous) if times else previous
                new_created = created
            else:
                new_created = None
            previous = max(previous, created)
            try:
                saved = json.loads(raw_labels) if raw_labels else {}
                labels = {**labels_for(json.loads(spec_json)), **saved}
            except Exception:  # noqa: BLE001 -- a tournament whose names cannot be read keeps what it has
                labels = None
            if new_created is None and (labels is None or labels == saved):
                continue
            try:
                with self.store.transaction(True) as db:
                    if new_created is not None:
                        db.execute("INSERT OR IGNORE INTO tournament_created VALUES(?,?)", (id, new_created))
                    if labels is not None and labels != saved:
                        db.execute("INSERT OR REPLACE INTO tournament_labels VALUES(?,?)", (id, encode(labels)))
            except Exception:  # noqa: BLE001 -- one tournament that cannot be written must not stop the others
                continue
            changed.append(id)
        return changed

    def labels(self, ids=None):
        """Entrant display names saved at creation, by identity (later tournaments win)."""
        out = {}
        with self.store.transaction() as db:
            for id, raw in db.execute("SELECT tournament_id,labels_json FROM tournament_labels ORDER BY rowid"):
                if ids is None or id in ids:
                    try:
                        out.update({k: v for k, v in json.loads(raw).items() if type(k) is str and type(v) is str})
                    except (ValueError, AttributeError):
                        continue
        return out

    def _start(self, id, credential, sha, revision, restart, *, system=False):
        if type(sha) is not str or re.fullmatch("[0-9a-f]{64}", sha) is None:
            raise ValueError("Choose the exact saved tournament hash")
        def authorize(row):
            if system:
                if row["spec_sha"] != sha or row["revision"] != revision:
                    raise Conflict("Tournament revision or schedule changed")
            else:
                self.store.authorize(row, credential, revision, sha)
        with self.guard:
            with self.store.transaction() as db:
                row = self.store.load(db, id)
                authorize(row)
            if id in self.owners:
                raise Conflict("Tournament has an active owner")
            lock = Ownership(self.store.path(_id(id) + ".lock"))
            try:
                allowed = (
                    ("stopped", "failed", "running", "draining")
                    if restart
                    else ("pending",)
                )
                if row["status"] not in allowed:
                    raise Conflict("Tournament cannot start in this state")
                spec = TournamentSpec.from_data(row["spec"])
                module = self.module_for(spec.config.game_id)
                expected = schedule(spec, module=module)
                rows = self.store.matches(id)
                if [m.data() for m in expected] != [r["spec"] for r in rows]:
                    raise ValueError("Frozen tournament schedule differs")
                self.store.verify_prepared(id, spec, rows, module)
                for ref in spec.references:
                    validate_reference(ref, module)
                    from boardcraft.inference.catalog import require_greedy_reference
                    require_greedy_reference(ref)
                if self.runner is None:
                    if self.catalog is None:
                        raise ValueError("Pinned tournament catalog unavailable")
                    for i, ref in enumerate(spec.references):
                        seed = spec.sampler_seeds[0][i % spec.config.players]
                        bot = self.catalog.resolve(ref, seed)
                        close = getattr(bot, "close", None)
                        if close:
                            close()
                for r in rows:
                    if r["status"] == "completed":
                        result = r["result"]
                        record = self.store.verify_file(
                            result["record_sha256"],
                            result["record_size"],
                            result["record_sha256"] + ".json.gz",
                            spec,
                        )
                        self.store.validate(
                            record, spec, MatchSpec.from_data(r["spec"]), module
                        )
                if not self.store.cleanup_attempts(id):
                    raise Conflict(
                        "Tournament staging cleanup_unconfirmed; files retained"
                    )
                # Prepared attempts are retired explicitly; complete artifacts remain unrated until retried.
                with self.store.transaction(True) as db:
                    current = self.store.load(db, id)
                    authorize(current)
                    db.execute("DELETE FROM tournament_autostart WHERE tournament_id=?", (id,))
                    db.execute(
                        "UPDATE tournament_attempts SET status='interrupted' WHERE tournament_id=? AND status NOT IN ('completed','failed','cancelled','interrupted')",
                        (id,),
                    )
                    db.execute(
                        "UPDATE tournament_record_refs SET role='retired' WHERE tournament_id=? AND role='prepared'",
                        (id,),
                    )
                    db.execute(
                        "UPDATE tournament_matches SET status='pending',current_attempt_token=NULL WHERE tournament_id=? AND status!='completed'",
                        (id,),
                    )
                    run = uuid.uuid4().hex
                    self.store.update(db, id, status="running", token=run)
                self.owners[id] = lock
                self.handles[id] = {}
                self.executors[id] = self.executor or FutureExecutor(spec.max_inflight)
                self.driver.start(lambda: self._drive(id))
            except BaseException:
                if id in self.owners:
                    self.owners.pop(id)
                    self.handles.pop(id)
                    executor = self.executors.pop(id)
                    if self.executor is None:
                        executor.close()
                    with self.store.transaction(True) as db:
                        self.store.update(db, id, status="failed", stop=True)
                lock.close()
                raise
        return self.status(id)

    def _drive(self, id):
        while True:
            with self.guard:
                if id not in self.owners:
                    return
                try:
                    self.advance(id)
                except Exception:  # noqa: BLE001 -- dispatch boundary must stop reservations and drain
                    if id not in self.owners:
                        return
                    with self.store.transaction(True) as db:
                        r = self.store.load(db, id)
                        self.store.update(
                            db, id, status="draining", token=r["run_token"], stop=True
                        )
            time.sleep(0.02)

    def request_stop(self, id, credential, revision):
        with self.store.transaction(True) as db:
            row = self.store.load(db, id)
            self.store.authorize(row, credential, revision)
            if row["status"] not in ("running", "draining"):
                raise Conflict("Tournament is not running")
            self.store.update(
                db, id, status="draining", token=row["run_token"], stop=True
            )
        return self.status(id)

    def advance(self, id):
        with self.guard:
            if id not in self.owners:
                raise Conflict("This controller does not own the tournament")
            executor = self.executors[id]
            handles = self.handles[id]
            with self.store.transaction() as db:
                row = self.store.load(db, id)
            spec = TournamentSpec.from_data(row["spec"])
            module = self.module_for(spec.config.game_id)
            for token, (match, run, h) in list(handles.items()):
                if row["stop_requested"]:
                    executor.cancel_queued(h)
                if not executor.poll(h) or not executor.drained(h):
                    continue
                try:
                    self.store.complete(
                        id, match, token, run, executor.result(h), module
                    )
                except Exception:  # noqa: BLE001 -- failed work must not publish a completed result
                    with self.store.transaction(True) as db:
                        try:
                            self.store.current(db, id, match.match_id, token, run)
                        except Conflict:
                            pass
                        else:
                            db.execute(
                                "UPDATE tournament_attempts SET status='failed',error_json=? WHERE token=?",
                                (
                                    encode(
                                        {
                                            "message": "Match execution or record verification failed"
                                        }
                                    ),
                                    token,
                                ),
                            )
                            db.execute(
                                "UPDATE tournament_matches SET status='failed' WHERE tournament_id=? AND match_id=?",
                                (id, match.match_id),
                            )
                            r = self.store.load(db, id)
                            self.store.update(
                                db, id, status="draining", token=run, stop=True
                            )
                del handles[token]
            with self.store.transaction() as db:
                row = self.store.load(db, id)
            if not row["stop_requested"]:
                for r in self.store.matches(id):
                    if len(handles) >= spec.max_inflight:
                        break
                    if r["status"] != "pending":
                        continue
                    token = uuid.uuid4().hex
                    match = MatchSpec.from_data(r["spec"])
                    stage = id + "-" + token + ".stage.json.gz"
                    with self.store.transaction(True) as db:
                        current = self.store.load(db, id)
                        if (
                            current["status"] != "running"
                            or current["stop_requested"]
                            or current["run_token"] != row["run_token"]
                        ):
                            break
                        db.execute(
                            "INSERT INTO tournament_attempts VALUES(?,?,?,?,?,?,?,?,?)",
                            (
                                token,
                                id,
                                match.match_id,
                                row["run_token"],
                                "running",
                                stage,
                                id + "-" + token + ".atomic.tmp",
                                None,
                                None,
                            ),
                        )
                        self.store.reserve_files(
                            db, id, token, stage, id + "-" + token + ".atomic.tmp"
                        )
                        db.execute(
                            "UPDATE tournament_matches SET status='running',current_attempt_token=? WHERE tournament_id=? AND match_id=? AND status='pending'",
                            (token, id, match.match_id),
                        )
                    runner = self.runner or gameplay_runner(spec, self.catalog, module)
                    try:
                        h = executor.submit(
                            lambda m=match, run_match=runner: run_match(m)
                        )
                    except Exception:  # noqa: BLE001 -- submission failure must retire its reserved attempt
                        with self.store.transaction(True) as db:
                            db.execute(
                                "UPDATE tournament_attempts SET status='failed' WHERE token=?",
                                (token,),
                            )
                            db.execute(
                                "UPDATE tournament_matches SET status='failed' WHERE tournament_id=? AND match_id=?",
                                (id, match.match_id),
                            )
                            self.store.update(
                                db,
                                id,
                                status="draining",
                                token=row["run_token"],
                                stop=True,
                            )
                        break
                    handles[token] = (match, row["run_token"], h)
            with self.store.transaction() as db:
                row = self.store.load(db, id)
            rows = self.store.matches(id)
            if not handles and (
                row["stop_requested"] or all(r["status"] == "completed" for r in rows)
            ):
                with self.store.transaction(True) as db:
                    self.store.update(
                        db,
                        id,
                        status="completed"
                        if all(r["status"] == "completed" for r in rows)
                        else "stopped",
                        token=row["run_token"],
                        stop=bool(row["stop_requested"]),
                    )
                if self.executor is None:
                    executor.close()
                self.owners.pop(id).close()
                self.handles.pop(id)
                self.executors.pop(id)
                # The rankings are the server's job: a completed line-up tournament is fitted right away
                # (a failure here is repaired by the startup recovery).
                try:
                    self.auto_fit(id)
                except Exception:  # noqa: BLE001
                    pass
                if self.on_completed is not None:
                    try:
                        self.on_completed()
                    except Exception:  # noqa: BLE001 -- the startup recovery refreshes the rankings again
                        pass
            return self.status(id)

    def delete(self, id, credential, revision):
        with self.guard:
            if id in self.owners:
                raise Conflict("Stop and drain tournament before deletion")
            with self.store.transaction() as db:
                row = self.store.load(db, id)
                self.store.authorize(row, credential, revision)
            if self.settle_orphan(id):
                revision = self.status(id)["revision"]
            with self.store.transaction() as db:
                row = self.store.load(db, id)
                self.store.authorize(row, credential, revision)
            lock = Ownership(self.store.path(_id(id) + ".lock"))
            try:
                with self.store.transaction(True) as db:
                    row = self.store.load(db, id)
                    self.store.authorize(row, credential, revision)
                    if row["status"] in ("running", "draining"):
                        raise Conflict(
                            "Restart and drain interrupted tournament before deletion"
                        )
                    self.store.update(db, id, status="deleting", stop=True)
                    db.execute(
                        "DELETE FROM tournament_ratings WHERE tournament_id=?", (id,)
                    )
                    db.execute(
                        "DELETE FROM tournament_record_refs WHERE tournament_id=?",
                        (id,),
                    )
                    db.execute(
                        "UPDATE tournament_records SET state='gc_pending' WHERE sha256 IN (SELECT record_sha FROM tournament_attempts WHERE tournament_id=?) AND NOT EXISTS(SELECT 1 FROM tournament_record_refs r WHERE r.sha256=tournament_records.sha256)",
                        (id,),
                    )
                    objects = [
                        tuple(r)
                        for r in db.execute(
                            "SELECT sha256,relpath FROM tournament_records WHERE state='gc_pending' AND sha256 IN (SELECT record_sha FROM tournament_attempts WHERE tournament_id=?)",
                            (id,),
                        )
                    ]
                confirmed = self.store.cleanup_attempts(id)
                for sha, rel in objects:
                    if (
                        rel != sha + ".json.gz"
                        or re.fullmatch("[0-9a-f]{64}", sha) is None
                    ):
                        raise ValueError("Invalid owned record membership")
                    self.store.path(rel).unlink(missing_ok=True)
                    with self.store.transaction(True) as db:
                        db.execute(
                            "DELETE FROM tournament_records WHERE sha256=? AND state='gc_pending'",
                            (sha,),
                        )
                with self.store.transaction(True) as db:
                    self.store.update(
                        db,
                        id,
                        status="deleted" if confirmed else "cleanup_unconfirmed",
                        stop=True,
                    )
            finally:
                lock.close()
        return self.status(id)


def run_tournament(spec, runner, store):
    """Explicit blocking launch helper; never used by discovery or verification."""
    controller = TournamentController(store, runner=runner)
    created = controller.create(spec)
    id = created["tournament_id"]
    controller.start(id, created["creator_credential"], created["spec_sha"], 0)
    while controller.status(id)["status"] in ("running", "draining"):
        time.sleep(0.02)
    return controller.status(id)
