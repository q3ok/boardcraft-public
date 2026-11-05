"""Durable scheduling/control with scripted records, never actual matches."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from boardcraft.contracts import (
    Action,
    Decision,
    GameConfig,
    GameInfo,
)
from boardcraft.inference.catalog import builtin_reference
from boardcraft.play.records import GameRecord, ParticipantRecord, RecordedDecision
from boardcraft.play.tournaments import (
    TournamentController,
    TournamentSpec,
    TournamentStore,
    schedule,
)
from boardcraft.registry import get_game
from boardcraft.server.storage import Store
from boardcraft.games.dice_circuit.scoring import DiceCircuitResult


def spec(players=2):
    module = get_game("dice_circuit")
    refs = tuple(builtin_reference(module, "random", "1") for _ in range(players))
    return TournamentSpec(
        "Fixture",
        GameConfig("dice_circuit", "base", players),
        module.rules_version,
        module.engine_version,
        refs,
        (11,),
        ((21, 22)[:players],),
        tuple(range(players)),
        players,
        20,
        1,
        1000000,
        1000,
    )


def test_generic_five_and_solo_schedule():
    s = spec()
    s = replace(
        s,
        config=GameConfig("dice_circuit", "base", 5),
        references=s.references[:1] * 5,
        sampler_seeds=((1, 2, 3, 4, 5),),
        rotations=tuple(range(5)),
        match_count=5,
    )
    module = SimpleNamespace(
        info=lambda: GameInfo(
            "dice_circuit",
            s.rules_version,
            s.engine_version,
            "Fixture",
            ("base",),
            1,
            5,
            ("random",),
        )
    )
    matches = schedule(s, module=module)
    assert len(matches) == 5 and [m.rotation for m in matches] == list(range(5))
    assert [m.sampler_seeds for m in matches] == [
        (1, 2, 3, 4, 5),
        (5, 1, 2, 3, 4),
        (4, 5, 1, 2, 3),
        (3, 4, 5, 1, 2),
        (2, 3, 4, 5, 1),
    ]
    assert len(schedule(spec(1))) == 1


def test_default_settings_preserve_legacy_tournament_spec_sha(tmp_path):
    from boardcraft.server.storage import digest, encode
    original = spec()
    data = original.data()
    assert data['config'] == {'game_id': 'dice_circuit', 'variant': 'base', 'players': 2}
    legacy_sha = '060799ccd05f22496cf92a2f13f641bfa451cc28b9808de68cf41efdf5e3ac54'
    assert digest(encode(data)) == legacy_sha
    assert TournamentSpec.from_data(data).data() == data
    control, _store, _executor = controller(tmp_path)
    assert control.create(original)['spec_sha'] == legacy_sha


class ManualDriver:
    def __init__(self):
        self.callbacks = []

    def start(self, work):
        self.callbacks.append(work)


class FakeExecutor:
    def __init__(self):
        self.tasks = []

    def submit(self, work):
        h = SimpleNamespace(
            work=work,
            ready=False,
            record=None,
            error=None,
            cancelled=False,
            started=True,
        )
        self.tasks.append(h)
        return h

    def poll(self, h):
        return h.ready

    def result(self, h):
        if h.error:
            raise h.error
        return h.record

    def cancel_queued(self, h):
        return False

    def drained(self, h):
        return h.ready


def controller(tmp_path):
    executor = FakeExecutor()
    store = TournamentStore(Store(tmp_path))
    control = TournamentController(
        store, runner=lambda m: None, executor=executor, driver=ManualDriver()
    )
    return control, store, executor


def test_create_get_are_inert_stop_waits_and_restart_uses_original_schedule(tmp_path):
    c, store, e = controller(tmp_path)
    created = c.create(spec())
    id = created["tournament_id"]
    cred = created["creator_credential"]
    assert c.status(id)["status"] == "pending" and e.tasks == []
    start = c.start(id, cred, created["spec_sha"], 0)
    c.advance(id)
    assert len(e.tasks) == 1
    stopped = c.request_stop(id, cred, start["revision"])
    assert stopped["status"] == "draining"
    c.advance(id)
    assert c.status(id)["status"] == "draining"
    e.tasks[0].ready = True
    e.tasks[0].error = ValueError("scripted failure")
    c.advance(id)
    assert c.status(id)["status"] == "stopped"
    old = store.matches(id)
    state = c.status(id)
    c.restart(id, cred, created["spec_sha"], state["revision"])
    c.advance(id)
    assert len(e.tasks) == 2 and store.matches(id)[0]["spec"] == old[0]["spec"]
    c.request_stop(id, cred, c.status(id)["revision"])
    e.tasks[-1].ready = True
    e.tasks[-1].error = ValueError("end")
    c.advance(id)
    c.delete(id, cred, c.status(id)["revision"])
    assert c.status(id)["status"] == "deleted"


def test_creator_and_second_owner_conflict_and_legacy_fk(tmp_path):
    c, store, _e = controller(tmp_path)
    made = c.create(spec())
    with pytest.raises(ValueError):
        c.start(made["tournament_id"], "wrong", made["spec_sha"], 0)
    c.start(made["tournament_id"], made["creator_credential"], made["spec_sha"], 0)
    other = TournamentController(
        store, runner=lambda m: None, executor=FakeExecutor(), driver=ManualDriver()
    )
    with pytest.raises(ValueError):
        other.start(
            made["tournament_id"], made["creator_credential"], made["spec_sha"], 1
        )
    with store.store.transaction() as db:
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 0
    with store.store.transaction(foreign_keys=True) as db:
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    c.request_stop(
        made["tournament_id"],
        made["creator_credential"],
        c.status(made["tournament_id"])["revision"],
    )
    c.advance(made["tournament_id"])


class ScriptEngine:
    def __init__(self, config, seed):
        self.config = config
        self.seed = seed
        self.done = False

    def snapshot(self):
        return {"config": self.config.__dict__, "seed": self.seed}

    def initial_events(self):
        return ()

    def project_events(self, receipt, viewer):
        return ()

    def observe(self, seat):
        return SimpleNamespace(decision=Decision("fixture", 0, "fixture"))

    def apply(self, seat, action, id):
        self.done = True
        return ()

    def is_over(self):
        return self.done

    def result(self):
        return DiceCircuitResult((3, 1), ({'yellow': 3, 'blue': 0, 'green': 0, 'orange': 0, 'purple': 0},
                                        {'yellow': 1, 'blue': 0, 'green': 0, 'orange': 0, 'purple': 0}),
                                 (0, 0), (1, 2), "scripted")


class ScriptModule:
    game_id = "dice_circuit"
    rules_version = "1"
    engine_version = "1"

    def info(self):
        return get_game(self.game_id).info()

    def encoder(self):
        return get_game(self.game_id).encoder()

    def new(self, config, seed):
        return ScriptEngine(config, seed)

    def restore(self, snapshot):
        return ScriptEngine(GameConfig(**snapshot["config"]), snapshot["seed"])


def scripted_record(s, match):
    engine = ScriptEngine(s.config, match.seed)
    return GameRecord(
        "2",
        s.config,
        s.rules_version,
        s.engine_version,
        engine.snapshot(),
        (),
        (
            RecordedDecision(
                Decision("fixture", 0, "fixture"),
                Action("fixture", "fixture", {}),
                (),
                "Scripted action",
            ),
        ),
        True,
        engine.result(),
        tuple(ParticipantRecord(i, r.kind, r) for i, r in enumerate(match.references)),
    )


def test_complete_record_once_shared_deletion_and_stale_token(tmp_path):
    from boardcraft.play.tournaments import MatchSpec

    c, store, e = controller(tmp_path)
    c.module_for = lambda id: ScriptModule()
    s = replace(spec(), rotations=(0,), match_count=1)
    a = c.create(s)
    b = c.create(s)
    for made in (a, b):
        id = made["tournament_id"]
        c.start(id, made["creator_credential"], made["spec_sha"], 0)
        c.advance(id)
        row = store.matches(id)[0]
        match = MatchSpec.from_data(row["spec"])
        record = scripted_record(s, match)
        e.tasks[-1].record = record
        e.tasks[-1].ready = True
        with store.transaction() as db:
            run = store.load(db, id)["run_token"]
        c.advance(id)
        assert c.status(id)["status"] == "completed"
        with pytest.raises(ValueError):
            store.complete(
                id, match, row["current_attempt_token"], run, record, ScriptModule()
            )
    ra = store.matches(a["tournament_id"])[0]["result"]
    rb = store.matches(b["tournament_id"])[0]["result"]
    assert ra["record_sha256"] == rb["record_sha256"]
    object_path = store.path(ra["record_sha256"] + ".json.gz")
    assert object_path.exists()
    c.delete(
        a["tournament_id"],
        a["creator_credential"],
        c.status(a["tournament_id"])["revision"],
    )
    assert (
        object_path.exists() and c.status(b["tournament_id"])["status"] == "completed"
    )
    c.delete(
        b["tournament_id"],
        b["creator_credential"],
        c.status(b["tournament_id"])["revision"],
    )
    assert not object_path.exists()


def test_delete_does_not_collect_another_tombstones_objects(tmp_path):
    c, store, _e = controller(tmp_path)
    made = c.create(spec())
    foreign = "f" * 64
    path = store.path(foreign + ".json.gz")
    path.write_bytes(b"foreign")
    with store.transaction(True) as db:
        db.execute(
            "INSERT INTO tournament_records VALUES(?,?,?,?)",
            (foreign, 7, path.name, "gc_pending"),
        )
    c.delete(made["tournament_id"], made["creator_credential"], 0)
    assert path.exists()


def test_paths_and_bad_record_do_not_complete(tmp_path):
    c, store, e = controller(tmp_path)
    with pytest.raises(ValueError):
        store.path("../foreign")
    made = c.create(spec())
    id = made["tournament_id"]
    c.start(id, made["creator_credential"], made["spec_sha"], 0)
    c.advance(id)
    from boardcraft.play.tournaments import MatchSpec

    match = MatchSpec.from_data(store.matches(id)[0]["spec"])
    record = scripted_record(spec(), match)
    e.tasks[-1].record = replace(record, complete=False, final_result=None)
    e.tasks[-1].ready = True
    c.advance(id)
    assert (
        store.matches(id)[0]["result"] is None and c.status(id)["status"] == "stopped"
    )


def test_canonical_atomic_temporary_path_has_durable_owned_membership(
    tmp_path, monkeypatch
):
    from boardcraft.play import tournaments

    c, store, e = controller(tmp_path)
    c.module_for = lambda id: ScriptModule()
    s = replace(spec(), rotations=(0,), match_count=1)
    made = c.create(s)
    id = made["tournament_id"]
    c.start(id, made["creator_credential"], made["spec_sha"], 0)
    c.advance(id)
    row = store.matches(id)[0]
    match = tournaments.MatchSpec.from_data(row["spec"])
    seen = []

    def interrupted(record, path, *, temporary_path=None, on_created=None):
        import os

        from boardcraft.play.records import _file_identity

        assert temporary_path is not None
        with store.transaction() as db:
            attempt = db.execute(
                "SELECT temporary_relpath FROM tournament_attempts WHERE token=?",
                (row["current_attempt_token"],),
            ).fetchone()
            assert store.path(attempt[0]) == temporary_path
        descriptor = os.open(
            temporary_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
        )
        with os.fdopen(descriptor, "wb") as stream:
            on_created(
                "temporary", temporary_path, _file_identity(os.fstat(stream.fileno()))
            )
            stream.write(b"interrupted")
        seen.append(temporary_path)
        raise OSError("scripted interruption")

    monkeypatch.setattr(tournaments, "write_record", interrupted)
    e.tasks[-1].record = scripted_record(s, match)
    e.tasks[-1].ready = True
    c.advance(id)
    assert seen and seen[0].exists() and store.matches(id)[0]["result"] is None
    c.delete(id, made["creator_credential"], c.status(id)["revision"])
    assert not seen[0].exists()


def test_canonical_writer_does_not_remove_a_preexisting_temporary_file(tmp_path):
    from boardcraft.play.records import write_record

    s = spec()
    match = schedule(s)[0]
    temp = tmp_path / "owned.tmp"
    temp.write_bytes(b"previous owner")
    with pytest.raises(FileExistsError):
        write_record(
            scripted_record(s, match), tmp_path / "record.gz", temporary_path=temp
        )
    assert temp.read_bytes() == b"previous owner"


def test_start_dependency_failure_and_driver_failure_are_recoverable(tmp_path):
    c, _store, _e = controller(tmp_path)

    class MissingCatalog:
        def resolve(self, ref, seed):
            raise ValueError("Pinned dependency unavailable")

    c.runner = None
    c.catalog = MissingCatalog()
    made = c.create(spec())
    with pytest.raises(ValueError):
        c.start(made["tournament_id"], made["creator_credential"], made["spec_sha"], 0)
    assert c.status(made["tournament_id"])["status"] == "pending" and not c.owners
    c.runner = lambda m: None

    class BrokenDriver:
        def start(self, work):
            raise OSError("No owned driver thread")

    c.driver = BrokenDriver()
    with pytest.raises(OSError):
        c.start(made["tournament_id"], made["creator_credential"], made["spec_sha"], 0)
    assert not c.owners and c.status(made["tournament_id"])["status"] == "failed"


def test_shutdown_drains_complete_inflight_record_without_dispatching_more(tmp_path):
    c, store, e = controller(tmp_path)
    c.module_for = lambda id: ScriptModule()
    made = c.create(spec())
    id = made["tournament_id"]
    c.start(id, made["creator_credential"], made["spec_sha"], 0)
    c.advance(id)
    from boardcraft.play.tournaments import MatchSpec

    match = MatchSpec.from_data(store.matches(id)[0]["spec"])
    e.tasks[-1].record = scripted_record(spec(), match)
    e.tasks[-1].ready = True
    c.close()
    assert not c.owners and len(e.tasks) == 1
    assert (
        c.status(id)["status"] == "stopped"
        and store.matches(id)[0]["status"] == "completed"
    )


def test_prepared_publish_failure_stays_unrated_and_restart_retires_before_retry(
    tmp_path, monkeypatch
):
    from boardcraft.play.tournaments import MatchSpec

    c, store, e = controller(tmp_path)
    c.module_for = lambda id: ScriptModule()
    s = replace(spec(), rotations=(0,), match_count=1)
    made = c.create(s)
    id = made["tournament_id"]
    c.start(id, made["creator_credential"], made["spec_sha"], 0)
    c.advance(id)
    old = store.matches(id)[0]
    match = MatchSpec.from_data(old["spec"])
    record = scripted_record(s, match)
    e.tasks[-1].record = record
    e.tasks[-1].ready = True
    original = store.verify_file

    def interrupted(*args):
        original(*args)
        raise OSError("Published but not committed")

    monkeypatch.setattr(store, "verify_file", interrupted)
    c.advance(id)
    assert store.matches(id)[0]["result"] is None and c.ratings(id) == []
    monkeypatch.setattr(store, "verify_file", original)
    c.restart(
        id, made["creator_credential"], made["spec_sha"], c.status(id)["revision"]
    )
    c.advance(id)
    with store.transaction() as db:
        assert (
            db.execute(
                "SELECT role FROM tournament_record_refs WHERE attempt_token=?",
                (old["current_attempt_token"],),
            ).fetchone()[0]
            == "retired"
        )
        run = store.load(db, id)["run_token"]
    with pytest.raises(ValueError):
        store.complete(
            id, match, old["current_attempt_token"], run, record, ScriptModule()
        )
    e.tasks[-1].record = record
    e.tasks[-1].ready = True
    c.advance(id)
    assert (
        c.status(id)["status"] == "completed"
        and len([r for r in store.matches(id) if r["result"]]) == 1
    )


def test_completion_requires_the_stored_match_and_explicit_start_hash(tmp_path):
    from boardcraft.play.tournaments import MatchSpec

    c, store, e = controller(tmp_path)
    c.module_for = lambda id: ScriptModule()
    made = c.create(spec())
    id = made["tournament_id"]
    with pytest.raises(ValueError):
        c.start(id, made["creator_credential"], None, 0)
    c.start(id, made["creator_credential"], made["spec_sha"], 0)
    c.advance(id)
    row = store.matches(id)[0]
    match = replace(MatchSpec.from_data(row["spec"]), seed=99)
    with store.transaction() as db:
        run = store.load(db, id)["run_token"]
    with pytest.raises(ValueError):
        store.complete(
            id,
            match,
            row["current_attempt_token"],
            run,
            scripted_record(spec(), match),
            ScriptModule(),
        )
    e.tasks[-1].ready = True
    e.tasks[-1].error = ValueError("scripted stop")
    c.request_stop(id, made["creator_credential"], c.status(id)["revision"])
    c.advance(id)


def test_corrupted_completed_object_cannot_enter_an_explicit_rating_fit(tmp_path):
    from boardcraft.play.tournaments import MatchSpec

    c, store, e = controller(tmp_path)
    c.module_for = lambda id: ScriptModule()
    s = replace(spec(), rotations=(0,), match_count=1)
    made = c.create(s)
    id = made["tournament_id"]
    c.start(id, made["creator_credential"], made["spec_sha"], 0)
    c.advance(id)
    match = MatchSpec.from_data(store.matches(id)[0]["spec"])
    e.tasks[-1].record = scripted_record(s, match)
    e.tasks[-1].ready = True
    c.advance(id)
    result = store.matches(id)[0]["result"]
    store.path(result["record_sha256"] + ".json.gz").write_bytes(b"broken")
    fit = {
        "gauge": "component_sum_zero",
        "elo_origin": 0.0,
        "ridge_lambda": 0.0,
        "prior_pseudocount": 0.0,
        "solver": "newton_backtracking",
        "max_iterations": 80,
        "max_backtracks": 30,
        "gradient_inf_tolerance": 1e-10,
        "armijo_coefficient": 0.01,
        "backtrack_factor": 0.5,
    }
    with pytest.raises(ValueError):
        c.fit(id, made["creator_credential"], c.status(id)["revision"], fit)
    assert c.ratings(id) == []


def test_driver_cannot_resurrect_draining_after_an_owner_has_finished(
    tmp_path, monkeypatch
):
    c, store, _ = controller(tmp_path)
    made = c.create(spec())
    id = made["tournament_id"]
    c.start(id, made["creator_credential"], made["spec_sha"], 0)

    def finished(_):
        c.owners.pop(id).close()
        c.handles.pop(id)
        c.executors.pop(id)
        with store.transaction(True) as db:
            store.update(db, id, status="stopped", stop=True)
        raise ValueError("Scripted completion followed by late control callback")

    monkeypatch.setattr(c, "advance", finished)
    c._drive(id)
    assert c.status(id)["status"] == "stopped"


@pytest.mark.parametrize("players", [3, 4])
def test_full_rotation_for_each_multiplayer_size(players):
    original = spec()
    seeds = tuple(range(1, players + 1))
    selected = replace(
        original,
        config=GameConfig("dice_circuit", "base", players),
        references=original.references[:1] * players,
        sampler_seeds=(seeds,),
        rotations=tuple(range(players)),
        match_count=players,
    )
    rows = schedule(selected)
    assert len(rows) == players
    assert all(sorted(row.sampler_seeds) == list(seeds) for row in rows)
    for seat in range(players):
        assert sorted(row.sampler_seeds[seat] for row in rows) == list(seeds)


def test_rating_strata_and_options(tmp_path):
    from boardcraft.inference.catalog import default_catalog
    from boardcraft.play.ranking import RatingKey, fit_ratings, RatingFitSpec
    from boardcraft.play.tournaments import gameplay_runner
    from boardcraft.server.storage import digest, encode

    module = get_game('rentcraft')
    catalog = default_catalog()
    references = tuple(catalog.pin_builtin('rentcraft', 'calculator', {'min_reserve': r}) for r in (350, 420))
    assert references[0].identity != references[1].identity
    config = GameConfig('rentcraft', 'base', 2, {'max_rounds': 1})
    chosen = replace(spec(), config=config, references=references)
    control, store, executor = controller(tmp_path)
    made = control.create(chosen)
    tid = made['tournament_id']
    control.start(tid, made['creator_credential'], made['spec_sha'], 0)
    run = gameplay_runner(chosen, catalog, module)
    for match in schedule(chosen):
        control.advance(tid)
        record = run(match)
        executor.tasks[-1].record = record
        executor.tasks[-1].ready = True
        control.advance(tid)
        row = store.matches(tid)[match.ordinal]
        assert row['status'] == 'completed'
        result = row['result']
        assert result['key']['settings_digest'] == digest(encode(config.settings))
        assert result['identities'] == [ref.identity for ref in match.references]
        assert result['placements'] == list(record.final_result.placements)
        assert match.seed == 11
        assert control.match_replay(tid, match.match_id)['observation']['viewer'] is None
    fit = RatingFitSpec('component_sum_zero', 0., .1, 0., 'newton_backtracking', 80, 30, 1e-10, .01, .5)
    control.fit(tid, made['creator_credential'], control.status(tid)['revision'], fit.data())
    rows = [row['result'] for row in store.matches(tid)]
    for other in (GameConfig('rentcraft', 'base', 2, {'max_rounds': 2}),
                  GameConfig('rentcraft', 'base', 3, {'max_rounds': 1}), GameConfig('dice_circuit', 'base', 2)):
        with pytest.raises(ValueError, match='strata'):
            fit_ratings(rows, RatingKey.from_config(other, '1'), fit_spec=fit)
    control.close()


def test_bankruptcy_and_equal_net_worth_follow_placements_into_elo(tmp_path):
    from boardcraft.inference.catalog import default_catalog
    from boardcraft.play.ranking import RatingFitSpec
    from boardcraft.play.tournaments import gameplay_runner

    module = get_game('rentcraft')
    catalog = default_catalog()
    refs = tuple(catalog.pin('rentcraft', kind) for kind in ('cautious', 'calculator', 'random'))
    config = GameConfig('rentcraft', 'base', 3, {'max_rounds': 2, 'start_cash': 0, 'trade_windows': 'off'})
    chosen = replace(spec(), config=config, references=refs, seeds=(2,), sampler_seeds=((0, 1, 2),),
                     rotations=(0,), match_count=1)
    control, store, executor = controller(tmp_path)
    made = control.create(chosen)
    tid = made['tournament_id']
    control.start(tid, made['creator_credential'], made['spec_sha'], 0)
    control.advance(tid)
    match, = schedule(chosen)
    record = gameplay_runner(chosen, catalog, module)(match)
    assert record.final_result.scores == (0, 0, 0)
    assert record.final_result.placements == (1, 2, 3)
    assert [event.payload['player'] for op in record.operations for event in op.events if event.kind == 'bankrupt'] == [2]
    executor.tasks[-1].record = record
    executor.tasks[-1].ready = True
    control.advance(tid)
    stored, = store.matches(tid)
    assert stored['status'] == 'completed' and stored['result']['placements'] == [1, 2, 3]
    fit = RatingFitSpec('component_sum_zero', 0., .1, 0., 'newton_backtracking', 80, 30, 1e-10, .01, .5)
    result = control.fit(tid, made['creator_credential'], control.status(tid)['revision'], fit.data())
    assert result['ties'] == 0
    elo = {row['identity']: row['elo'] for row in result['ratings']}
    assert elo[refs[0].identity] > elo[refs[1].identity] > elo[refs[2].identity]
    control.close()


def test_rating_rejects_reassigned_seat_identities(tmp_path):
    from boardcraft.play.ranking import RatingFitSpec
    from boardcraft.play.tournaments import MatchSpec
    from boardcraft.server.storage import digest, encode
    control, store, executor = controller(tmp_path)
    control.module_for = lambda _: ScriptModule()
    chosen = replace(spec(), rotations=(0,), match_count=1)
    made = control.create(chosen)
    tid = made['tournament_id']
    control.start(tid, made['creator_credential'], made['spec_sha'], 0)
    control.advance(tid)
    match = MatchSpec.from_data(store.matches(tid)[0]['spec'])
    executor.tasks[-1].record = scripted_record(chosen, match)
    executor.tasks[-1].ready = True
    control.advance(tid)
    row, = store.matches(tid)
    result = row['result']
    result['identities'][1] = 'unrelated-player'
    payload = encode(result)
    with store.transaction(True) as db:
        db.execute('UPDATE tournament_matches SET result_json=?, result_sha=?', (payload, digest(payload)))
    fit = RatingFitSpec('component_sum_zero', 0., .1, 0., 'newton_backtracking', 80, 30, 1e-10, .01, .5)
    with pytest.raises(ValueError, match='canonical record'):
        control.fit(tid, made['creator_credential'], control.status(tid)['revision'], fit.data())


def test_legacy_dice_tie_survives_record_publication_and_fit(tmp_path, monkeypatch):
    from boardcraft.games.dice_circuit.scoring import AreaScore, rank_scores
    from boardcraft.play.ranking import RatingFitSpec
    from boardcraft.play.tournaments import MatchSpec
    from boardcraft.play.replay import replay

    # A synthetic one-step history with an actual Dice Circuit scoring tie.
    tied = rank_scores((AreaScore(40, 15, 15, 15, 15), AreaScore(30, 30, 0, 0, 40)))
    monkeypatch.setattr(ScriptEngine, 'result', lambda self: tied if self.done else None)
    control, store, executor = controller(tmp_path)
    control.module_for = lambda _: ScriptModule()
    module = get_game('dice_circuit')
    chosen = replace(spec(), rotations=(0,), match_count=1, references=tuple(
        builtin_reference(module, kind, '1') for kind in ('random', 'greedy')))
    made = control.create(chosen)
    tid = made['tournament_id']
    control.start(tid, made['creator_credential'], made['spec_sha'], 0)
    control.advance(tid)
    match = MatchSpec.from_data(store.matches(tid)[0]['spec'])
    record = replace(scripted_record(chosen, match), final_result=tied)
    executor.tasks[-1].record = record
    executor.tasks[-1].ready = True
    control.advance(tid)
    result = store.matches(tid)[0]['result']
    sha = result['record_sha256']
    verified = store.verify_file(sha, result['record_size'], sha + '.json.gz', chosen)
    assert verified.version == '2' and replay(verified, module=ScriptModule()).result() == tied
    fit = RatingFitSpec('component_sum_zero', 0., 0., 0., 'newton_backtracking', 80, 30, 1e-10, .01, .5)
    rating = control.fit(tid, made['creator_credential'], control.status(tid)['revision'], fit.data())
    assert rating['ties'] == rating['observed_pairs'] == 1
    assert [row['elo'] for row in rating['ratings']] == [0., 0.]


def test_strategist_tournament_uses_pinned_catalog_and_completes():
    from boardcraft.inference.catalog import default_catalog
    from boardcraft.play.tournaments import gameplay_runner
    catalog = default_catalog()
    module = get_game('dice_circuit')
    chosen = replace(spec(), references=tuple(catalog.pin('dice_circuit', kind) for kind in ('strategist', 'greedy')),
                     rotations=(0,), seeds=(10000,), sampler_seeds=((3, 4),), match_count=1)
    run = gameplay_runner(chosen, catalog, module)
    match, = schedule(chosen)
    record = run(match)
    assert record.final_result is not None
    assert len(record.final_result.scores) == 2
    assert record.participants[0].bot_ref == chosen.references[0]


@pytest.mark.parametrize('game', ['dice_circuit', 'rentcraft'])
def test_rating_requires_the_completed_object_of_each_seed(tmp_path, monkeypatch, game):
    from boardcraft.inference.catalog import default_catalog
    from boardcraft.play import ranking
    from boardcraft.play.tournaments import gameplay_runner
    from boardcraft.server.storage import digest, encode

    module = get_game(game)
    catalog = default_catalog()
    settings = {} if game == 'dice_circuit' else {'max_rounds': 1}
    refs = tuple(catalog.pin(game, kind) for kind in ('random', 'greedy' if game == 'dice_circuit' else 'cautious'))
    chosen = replace(spec(), config=GameConfig(game, 'base', 2, settings), references=refs, rotations=(0,),
                     seeds=(11, 12), sampler_seeds=((4, 5), (4, 5)), match_count=2)
    control, store, executor = controller(tmp_path)
    made = control.create(chosen)
    tid, credential = made['tournament_id'], made['creator_credential']
    control.start(tid, credential, made['spec_sha'], 0)
    run = gameplay_runner(chosen, catalog, module)
    for match in schedule(chosen):
        control.advance(tid)
        executor.tasks[-1].record = run(match)
        executor.tasks[-1].ready = True
        control.advance(tid)
    a, b = [row['result'] for row in store.matches(tid)]
    assert a['participants'] == b['participants'] and a['record_sha256'] != b['record_sha256']
    fit = ranking.RatingFitSpec('component_sum_zero', 0., .1, 0., 'newton_backtracking', 80, 30, 1e-10, .01, .5)
    revision = control.status(tid)['revision']
    assert control.fit(tid, credential, revision, fit.data())['observed_games'] == 2

    def save_a(result):
        raw = encode(result)
        with store.transaction(True) as db:
            db.execute('UPDATE tournament_matches SET result_json=?,result_sha=? WHERE tournament_id=? AND match_id=?',
                       (raw, digest(raw), tid, a['match_id']))

    save_a({**a, **{key: b[key] for key in ('record_sha256', 'record_size', 'scores', 'placements')}})
    with pytest.raises(ValueError, match='completed record binding'):
        control.fit(tid, credential, revision, fit.data())

    save_a(a)
    fit_ratings = ranking.fit_ratings
    def detach_after_fit(*args, **kwargs):
        computed = fit_ratings(*args, **kwargs)
        with store.transaction(True) as db:
            db.execute("UPDATE tournament_record_refs SET role='retired' WHERE tournament_id=? AND match_id=?",
                       (tid, a['match_id']))
        return computed
    monkeypatch.setattr(ranking, 'fit_ratings', detach_after_fit)
    with pytest.raises(ValueError, match='completed record binding'):
        control.fit(tid, credential, revision, fit.data())
    control.close()
