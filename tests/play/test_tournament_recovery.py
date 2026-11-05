"""Recovery/ownership regressions using scripted work and the real durable store."""

import hashlib
from dataclasses import replace

import pytest
from boardcraft.play.records import write_record
from boardcraft.play.tournaments import MatchSpec
from test_tournaments import ScriptModule, controller, scripted_record, spec


def prepared(tmp_path, monkeypatch):
    c, store, executor = controller(tmp_path)
    c.module_for = lambda id: ScriptModule()
    chosen = replace(spec(), rotations=(0,), match_count=1)
    made = c.create(chosen)
    id = made["tournament_id"]
    c.start(id, made["creator_credential"], made["spec_sha"], 0)
    c.advance(id)
    match = MatchSpec.from_data(store.matches(id)[0]["spec"])
    executor.tasks[-1].record = scripted_record(chosen, match)
    executor.tasks[-1].ready = True
    original = store.verify_file

    def interrupted(*args):
        original(*args)
        raise OSError("Scripted crash after publication")

    monkeypatch.setattr(store, "verify_file", interrupted)
    c.advance(id)
    monkeypatch.setattr(store, "verify_file", original)
    with store.transaction() as db:
        store.load(db, id)
        row = dict(db.execute("SELECT * FROM tournament_records").fetchone())
    return c, store, executor, made, chosen, match, row


@pytest.mark.parametrize(
    "kind", ["valid", "missing_unpublished", "corrupt_bytes", "different_match"]
)
def test_restart_verifies_prepared_immutable_dependency_before_dispatch(
    tmp_path, monkeypatch, kind
):
    c, store, executor, made, chosen, match, obj = prepared(tmp_path, monkeypatch)
    id = made["tournament_id"]
    path = store.path(obj["relpath"])
    if kind == "missing_unpublished":
        path.unlink()
    elif kind == "corrupt_bytes":
        path.write_bytes(b"corrupt prepared object")
    elif kind == "different_match":
        foreign = tmp_path / "other.json.gz"
        write_record(scripted_record(chosen, replace(match, seed=99)), foreign)
        sha = hashlib.sha256(foreign.read_bytes()).hexdigest()
        target = store.path(sha + ".json.gz")
        foreign.replace(target)
        with store.transaction(True) as db:
            db.execute(
                "INSERT INTO tournament_records VALUES(?,?,?,?)",
                (sha, target.stat().st_size, target.name, "publishing"),
            )
            db.execute("UPDATE tournament_record_refs SET sha256=?", (sha,))
            db.execute("UPDATE tournament_attempts SET record_sha=?", (sha,))
    if kind in ("corrupt_bytes", "different_match"):
        with pytest.raises(ValueError):
            c.restart(
                id,
                made["creator_credential"],
                made["spec_sha"],
                c.status(id)["revision"],
            )
        assert (
            not c.owners
            and len(executor.tasks) == 1
            and store.matches(id)[0]["result"] is None
        )
    else:
        c.restart(
            id, made["creator_credential"], made["spec_sha"], c.status(id)["revision"]
        )
        c.advance(id)
        assert len(executor.tasks) == 2
        executor.tasks[-1].ready = True
        executor.tasks[-1].error = ValueError("End scripted work")
        c.request_stop(id, made["creator_credential"], c.status(id)["revision"])
        c.advance(id)


def active(tmp_path):
    c, store, executor = controller(tmp_path)
    c.module_for = lambda id: ScriptModule()
    chosen = replace(spec(), rotations=(0,), match_count=1)
    made = c.create(chosen)
    id = made["tournament_id"]
    c.start(id, made["creator_credential"], made["spec_sha"], 0)
    c.advance(id)
    match = MatchSpec.from_data(store.matches(id)[0]["spec"])
    with store.transaction() as db:
        store.load(db, id)
        attempt = dict(db.execute("SELECT * FROM tournament_attempts").fetchone())
    return c, store, executor, made, chosen, match, attempt


@pytest.mark.parametrize("role", ["stage", "temporary", "witness"])
@pytest.mark.parametrize("operation", ["delete", "restart", "shutdown"])
def test_foreign_reserved_file_survives_failure_and_entire_lifecycle(
    tmp_path, role, operation
):
    c, store, executor, made, chosen, match, attempt = active(tmp_path)
    id = made["tournament_id"]
    rel = {
        "stage": attempt["stage_relpath"],
        "temporary": attempt["temporary_relpath"],
        "witness": id + "-" + attempt["token"] + ".witness",
    }[role]
    path = store.path(rel)
    foreign = b"Foreign reserved-path bytes"
    path.write_bytes(foreign)
    executor.tasks[-1].record = scripted_record(chosen, match)
    executor.tasks[-1].ready = True
    c.advance(id)
    assert path.read_bytes() == foreign
    assert store.matches(id)[0]["result"] is None
    if operation == "delete":
        result = c.delete(id, made["creator_credential"], c.status(id)["revision"])
        assert (
            result["status"] == "cleanup_unconfirmed" and result["cleanup_unconfirmed"]
        )
    elif operation == "restart":
        with pytest.raises(ValueError):
            c.restart(
                id,
                made["creator_credential"],
                made["spec_sha"],
                c.status(id)["revision"],
            )
        assert len(executor.tasks) == 1 and not c.owners
    else:
        c.close()
    assert path.read_bytes() == foreign


def interrupted_temporary(tmp_path, monkeypatch, *, claimed):
    import os

    from boardcraft.play import tournaments
    from boardcraft.play.records import _file_identity

    c, store, executor, made, chosen, match, attempt = active(tmp_path)

    def interrupted(record, path, *, temporary_path=None, on_created=None):
        descriptor = os.open(
            temporary_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
        )
        with os.fdopen(descriptor, "wb") as stream:
            if claimed:
                on_created(
                    "temporary",
                    temporary_path,
                    _file_identity(os.fstat(stream.fileno())),
                )
            stream.write(b"Scripted interrupted gzip bytes")
        raise OSError("Scripted process-loss boundary without writer finally")

    monkeypatch.setattr(tournaments, "write_record", interrupted)
    executor.tasks[-1].record = scripted_record(chosen, match)
    executor.tasks[-1].ready = True
    c.advance(made["tournament_id"])
    return c, store, executor, made, chosen, match, attempt


@pytest.mark.parametrize("operation", ["delete", "restart"])
def test_claimed_unfinished_temp_is_cleaned_with_retained_witness(
    tmp_path, monkeypatch, operation
):
    c, store, executor, made, _, _, attempt = interrupted_temporary(
        tmp_path, monkeypatch, claimed=True
    )
    id = made["tournament_id"]
    temporary = store.path(attempt["temporary_relpath"])
    witness = store.path(id + "-" + attempt["token"] + ".witness")
    assert (
        temporary.exists()
        and witness.exists()
        and temporary.stat().st_ino == witness.stat().st_ino
    )
    assert not c.status(id)["cleanup_unconfirmed"]
    if operation == "delete":
        assert (
            c.delete(id, made["creator_credential"], c.status(id)["revision"])["status"]
            == "deleted"
        )
    else:
        c.restart(
            id, made["creator_credential"], made["spec_sha"], c.status(id)["revision"]
        )
        c.advance(id)
        assert len(executor.tasks) == 2
        executor.tasks[-1].ready = True
        executor.tasks[-1].error = ValueError("End scripted work")
        c.close()
    assert not temporary.exists() and not witness.exists()


@pytest.mark.parametrize(
    "damage",
    [
        "unclaimed_gap",
        "missing_witness",
        "foreign_witness",
        "foreign_temporary",
        "corrupt_claim",
    ],
)
def test_missing_or_tampered_proof_preserves_ambiguous_files_and_reports_unconfirmed(
    tmp_path, monkeypatch, damage
):
    c, store, _, made, _, _, attempt = interrupted_temporary(
        tmp_path, monkeypatch, claimed=damage != "unclaimed_gap"
    )
    id = made["tournament_id"]
    temporary = store.path(attempt["temporary_relpath"])
    witness = store.path(id + "-" + attempt["token"] + ".witness")
    expected = temporary.read_bytes()
    if damage in ("missing_witness", "foreign_witness"):
        witness.unlink()
    if damage == "foreign_witness":
        witness.write_bytes(b"Foreign witness")
    if damage == "foreign_temporary":
        temporary.unlink()
        temporary.write_bytes(b"Foreign replacement")
        expected = b"Foreign replacement"
    if damage == "corrupt_claim":
        with store.transaction(True) as db:
            db.execute(
                "UPDATE tournament_file_claims SET identity_sha=? WHERE attempt_token=? AND role='witness'",
                ("0" * 64, attempt["token"]),
            )
    result = c.delete(id, made["creator_credential"], c.status(id)["revision"])
    assert result["status"] == "cleanup_unconfirmed" and result["cleanup_unconfirmed"]
    assert temporary.read_bytes() == expected
    if damage == "foreign_witness":
        assert witness.read_bytes() == b"Foreign witness"


def test_default_writer_remains_canonical_without_ownership_hooks(tmp_path):
    from boardcraft.play.records import read_record, validate_record

    chosen = spec()
    match = __import__("boardcraft.play.tournaments", fromlist=["schedule"]).schedule(
        chosen
    )[0]
    record = scripted_record(chosen, match)
    path = tmp_path / "default.json.gz"
    write_record(record, path)
    assert read_record(path) == validate_record(record)


@pytest.mark.parametrize("boundary", ["before_claim", "after_claim"])
def test_stage_publication_claim_boundary_is_honest_and_preserves_ambiguous_link(
    tmp_path, monkeypatch, boundary
):
    c, store, executor, made, chosen, match, attempt = active(tmp_path)
    id = made["tournament_id"]
    original = store.claim_file

    def interrupted(*args):
        if args[4] == "stage" and boundary == "before_claim":
            raise OSError("Scripted stage create/claim crash gap")
        original(*args)
        if args[4] == "stage":
            raise OSError("Scripted crash after durable stage claim")

    monkeypatch.setattr(store, "claim_file", interrupted)
    executor.tasks[-1].record = scripted_record(chosen, match)
    executor.tasks[-1].ready = True
    c.advance(id)
    stage = store.path(attempt["stage_relpath"])
    assert stage.exists() and store.matches(id)[0]["result"] is None
    result = c.delete(id, made["creator_credential"], c.status(id)["revision"])
    assert result["status"] == (
        "cleanup_unconfirmed" if boundary == "before_claim" else "deleted"
    )
    assert stage.exists() == (boundary == "before_claim")


@pytest.mark.parametrize("boundary", ["before_write", "after_write"])
def test_real_canonical_writer_failure_after_witness_claim_has_owned_cleanup(
    tmp_path, monkeypatch, boundary
):
    import gzip

    c, store, executor, made, chosen, match, attempt = active(tmp_path)
    id = made["tournament_id"]
    original = gzip.GzipFile.write

    def interrupted(self, data):
        if boundary == "after_write":
            original(self, data)
        raise OSError("Scripted gzip failure")

    monkeypatch.setattr(gzip.GzipFile, "write", interrupted)
    executor.tasks[-1].record = scripted_record(chosen, match)
    executor.tasks[-1].ready = True
    c.advance(id)
    witness = store.path(id + "-" + attempt["token"] + ".witness")
    assert witness.exists() and store.matches(id)[0]["result"] is None
    assert (
        c.delete(id, made["creator_credential"], c.status(id)["revision"])["status"]
        == "deleted"
    )
    assert (
        not witness.exists() and not store.path(attempt["temporary_relpath"]).exists()
    )


def test_expansion_limit_does_not_poison_restart(tmp_path):
    c, store, executor = controller(tmp_path)
    c.module_for = lambda _: ScriptModule()
    chosen = replace(spec(), rotations=(0,), match_count=1, max_record_bytes=850)
    made = c.create(chosen)
    tid = made['tournament_id']
    c.start(tid, made['creator_credential'], made['spec_sha'], 0)
    c.advance(tid)
    match = MatchSpec.from_data(store.matches(tid)[0]['spec'])
    accepted = scripted_record(chosen, match)
    verbose = replace(accepted.decisions[0], explanation='x' * 4000)
    executor.tasks[-1].record = replace(accepted, decisions=(verbose,))
    executor.tasks[-1].ready = True
    c.advance(tid)
    assert store.matches(tid)[0]['status'] == 'failed'
    with store.transaction() as db:
        assert db.execute('SELECT count(*) FROM tournament_record_refs').fetchone()[0] == 0
    c.restart(tid, made['creator_credential'], made['spec_sha'], c.status(tid)['revision'])
    c.request_stop(tid, made['creator_credential'], c.status(tid)['revision'])
    c.advance(tid)


def _draining_with_dead_run(tmp_path, complete_first=False):
    c, store, executor = controller(tmp_path)
    c.module_for = lambda id: ScriptModule()
    chosen = spec()
    made = c.create(chosen)
    id = made["tournament_id"]
    cred = made["creator_credential"]
    started = c.start(id, cred, made["spec_sha"], 0)
    c.advance(id)
    if complete_first:
        match = MatchSpec.from_data(store.matches(id)[0]["spec"])
        executor.tasks[0].record = scripted_record(chosen, match)
        executor.tasks[0].ready = True
        c.advance(id)
    c.request_stop(id, cred, c.status(id)["revision"])
    assert c.status(id)["status"] == "draining"
    # The server process dies: its ownership lock goes with it, the database keeps "draining".
    c.owners.pop(id).close()
    c.handles.pop(id)
    c.executors.pop(id)
    del started
    return store, made, chosen


def test_draining_tournament_of_dead_run_settles_to_stopped_and_deletes(tmp_path):
    store, made, _chosen = _draining_with_dead_run(tmp_path)
    id = made["tournament_id"]
    from boardcraft.play.tournaments import TournamentController

    restarted = TournamentController(store, runner=lambda m: None, driver=None)
    restarted.module_for = lambda id: ScriptModule()
    assert restarted.recover_orphans() == [id]
    state = restarted.status(id)
    assert state["status"] == "stopped"
    with store.transaction() as db:
        rows = db.execute("SELECT status FROM tournament_attempts WHERE tournament_id=?", (id,)).fetchall()
    assert rows and all(r[0] == "interrupted" for r in rows)
    assert restarted.recover_orphans() == []
    restarted.delete(id, made["creator_credential"], state["revision"])
    assert restarted.status(id)["status"] == "deleted"


def test_completed_results_survive_settling_until_delete(tmp_path):
    store, made, _chosen = _draining_with_dead_run(tmp_path, complete_first=True)
    id = made["tournament_id"]
    from boardcraft.play.tournaments import TournamentController

    restarted = TournamentController(store, runner=lambda m: None, driver=None)
    restarted.module_for = lambda id: ScriptModule()
    restarted.recover_orphans()
    assert restarted.status(id)["status"] == "stopped"
    statuses = sorted(r["status"] for r in store.matches(id))
    assert statuses == ["completed", "pending"]


def test_delete_settles_orphan_draining_tournament_itself(tmp_path):
    store, made, _chosen = _draining_with_dead_run(tmp_path)
    id = made["tournament_id"]
    from boardcraft.play.tournaments import TournamentController

    restarted = TournamentController(store, runner=lambda m: None, driver=None)
    restarted.module_for = lambda id: ScriptModule()
    restarted.delete(id, made["creator_credential"], restarted.status(id)["revision"])
    assert restarted.status(id)["status"] == "deleted"


def test_live_owner_keeps_draining_untouched(tmp_path):
    c, store, executor = controller(tmp_path)
    chosen = spec()
    made = c.create(chosen)
    id = made["tournament_id"]
    c.start(id, made["creator_credential"], made["spec_sha"], 0)
    c.advance(id)
    c.request_stop(id, made["creator_credential"], c.status(id)["revision"])
    assert c.recover_orphans() == []
    assert c.status(id)["status"] == "draining"
