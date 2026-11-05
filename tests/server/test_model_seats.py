"""Models use the same durable seats, worker and transport as builtin bots."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import threading
import pytest
from boardcraft.contracts import GameConfig
from boardcraft.server.service import PlayService
from tests.inference.test_catalog import artifact, catalog_api


def setup(tmp_path):
    api = catalog_api()
    paths = artifact(tmp_path / "artifact")
    catalog = api.RuntimeBotCatalog({"fixture": api.NumpySource(*paths)})
    return (
        api,
        catalog,
        PlayService(tmp_path / "data", seed_factory=lambda: 0, bot_catalog=catalog),
    )


def test_strategist_discovery_mcp_lobby_and_worker(tmp_path):
    from boardcraft.mcp.server import create_server
    from mcp.client import Client
    service = PlayService(tmp_path / 'strategist', seed_factory=lambda: 19)
    expected = next(d for d in service.list_bots('dice_circuit') if d['choice_id'] == 'strategist')
    assert expected['display_name'] == 'Strategist' and expected['description']

    async def discover():
        async with Client(create_server(service)) as client:
            result = await client.call_tool('list_bots', {'game_id': 'dice_circuit'})
            assert not result.is_error
            assert expected in json.loads(result.content[0].text)
    asyncio.run(discover())
    made = service.create_game(GameConfig('dice_circuit', 'base', 1), ['strategist'])
    assert made['seats'][0]['bot_ref'] == expected['reference']
    service.start_game(made['table_id'], made['creator_credential'])
    work, = service.bot_work(made['table_id'])
    choice = service.choose_bot(work)
    assert choice.action in work.actions
    assert service.apply_bot(work, choice)


def test_model_discovery_service_http_mcp_and_standard_legal_move(tmp_path):
    _, catalog, service = setup(tmp_path)
    from fastapi.testclient import TestClient
    from boardcraft.server.api import create_app
    from boardcraft.mcp.server import create_server
    from mcp.client import Client

    expected = service.list_bots("dice_circuit")
    assert expected[-1]["display_name"] == "Synthetic policy"
    with TestClient(create_app(service, run_bots=False)) as http:
        assert http.get("/api/games/dice_circuit/bots").json() == expected
        made = http.post(
            "/api/tables",
            json={
                "config": {"game_id": "dice_circuit", "variant": "base", "players": 1},
                "seats": ["fixture"],
            },
        ).json()
        http.post(
            f"/api/tables/{made['table_id']}/start",
            headers={"Authorization": "Bearer " + made["creator_credential"]},
        )

    async def run():
        async with Client(create_server(service)) as client:
            result = await client.call_tool("list_bots", {"game_id": "dice_circuit"})
            assert not result.is_error
            assert json.loads(result.content[0].text) == expected

    asyncio.run(run())
    (work,) = service.bot_work(made["table_id"])
    assert work.reference == catalog.pin("dice_circuit", "fixture")
    choice = service.choose_bot(work)
    assert choice.action in work.actions and service.apply_bot(work, choice)
    with service.store.transaction() as db:
        initial = service.store.checked(
            db.execute("SELECT payload,checksum FROM bootstrap").fetchone()
        )
    assert initial["participants"][0]["bot_ref"] == expected[-1]["reference"]


def test_pinned_alias_restart_missing_and_resource_status_do_not_move_cursors(tmp_path):
    api, catalog, service = setup(tmp_path)
    made = service.create_game(GameConfig("dice_circuit", "base", 1), ["fixture"])
    service.start_game(made["table_id"], made["creator_credential"])
    (work,) = service.bot_work(made["table_id"])
    catalog.register(
        "fixture", api.NumpySource(*artifact(tmp_path / "b", 1, "Changed policy"))
    )
    assert service.bot_work(made["table_id"])[0].reference == work.reference
    empty = PlayService(tmp_path / "data")
    before = empty.get_view(made["table_id"])
    with pytest.raises(api.BotUnavailable):
        empty.choose_bot(work)
    empty.refresh_bot_resources(made["table_id"], force=True)
    after = empty.get_view(made["table_id"])
    assert before["observation"] == after["observation"]
    assert after["seats"][0]["availability"]["status"] == "unavailable"
    assert after["seats"][0]["availability"]["reference"] == api.reference_data(
        work.reference
    )
    assert service.choose_bot(work).action in work.actions
    service.refresh_bot_resources(made["table_id"], force=True)
    assert (
        service.get_view(made["table_id"])["seats"][0]["availability"]["status"]
        == "available"
    )


def test_blocked_resolver_never_holds_database_transaction(tmp_path):
    _, catalog, service = setup(tmp_path)
    made = service.create_game(GameConfig("dice_circuit", "base", 1), ["fixture"])
    entered, release = threading.Event(), threading.Event()
    original = catalog.resolve

    def blocked(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)

    catalog.resolve = blocked
    with ThreadPoolExecutor(2) as pool:
        starting = pool.submit(
            service.start_game, made["table_id"], made["creator_credential"]
        )
        try:
            assert entered.wait(3)
            assert pool.submit(
                service.create_game, GameConfig("dice_circuit", "base", 1), ["human"]
            ).result(3)
        finally:
            release.set()
        assert starting.result()["status"] == "playing"


def test_incompatible_injected_pin_rejects_before_persistence(tmp_path):
    api, catalog, service = setup(tmp_path)
    original = catalog.pin

    def pin(*args):
        ref = original(*args)
        return replace(
            ref, compatibility=replace(ref.compatibility, rules_version="999")
        )

    catalog.pin = pin
    with pytest.raises(ValueError):
        service.create_game(GameConfig("dice_circuit", "base", 1), ["fixture"])
    with service.store.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM tables").fetchone()[0] == 0


def test_legacy_builtin_migrates_once_to_known_baseline_and_preserves_credentials(
    tmp_path,
):
    api = catalog_api()
    service = PlayService(tmp_path, seed_factory=lambda: 0)
    made = service.create_game(
        GameConfig("dice_circuit", "base", 2), ["greedy", "human"]
    )
    joined = service.join_game(made["table_id"], 1)
    with service.store.transaction(True) as db:
        state = service.store.load(db, made["table_id"])
        for seat in state["seats"]:
            seat.pop("bot_ref", None)
            seat.pop("display_name", None)
        service.store.save(db, made["table_id"], state)
    first, second = PlayService(tmp_path), PlayService(tmp_path)
    with ThreadPoolExecutor(2) as pool:
        views = list(
            pool.map(
                lambda s: s.get_view(made["table_id"], joined["credential"]),
                (first, second),
            )
        )
    assert {**views[0], 'server_time': views[1]['server_time']} == views[1]
    ref = views[0]["seats"][0]["bot_ref"]
    assert ref["behavior_version"] == "1"
    assert ref == api.reference_data(first.bot_catalog.pin("dice_circuit", "greedy"))


def test_reference_matching_stale_work_and_independent_passive_actors(tmp_path):
    api, catalog, service = setup(tmp_path)
    made = service.create_game(GameConfig("dice_circuit", "base", 3), ["fixture"] * 3)
    table = made["table_id"]
    service.start_game(table, made["creator_credential"])
    for _ in range(200):
        works = service.bot_work(table)
        if len(works) >= 2:
            a, b = works[:2]
            choice_a, choice_b = service.choose_bot(a), service.choose_bot(b)
            assert service.apply_bot(a, choice_a)
            assert service.apply_bot(b, choice_b), (
                "An unrelated passive seat invalidated eligible work"
            )
            assert not service.apply_bot(a, choice_a)
            return
        for work in works:
            service.apply_bot(work, service.choose_bot(work))
    pytest.fail("No independently eligible passive decisions")


def test_resource_checks_ignore_hidden_progress_and_version_mismatched_errors(tmp_path):
    api, catalog, service = setup(tmp_path)
    made = service.create_game(GameConfig("dice_circuit", "base", 3), ["fixture"] * 3)
    table = made["table_id"]
    service.start_game(table, made["creator_credential"])
    ref = catalog.pin("dice_circuit", "fixture")
    before = service.get_view(table)
    original = catalog.resolve
    calls = []

    def unavailable(reference, seed):
        calls.append(reference)
        raise api.BotUnavailable(reference)

    catalog.resolve = unavailable
    service.refresh_bot_resources(table, force=True)
    after = service.get_view(table)
    assert calls == [ref] * 3  # Includes seats with no eligible observation.
    assert before["observation"] == after["observation"]
    assert all(
        item["availability"]["status"] == "unavailable" for item in after["seats"]
    )
    calls.clear()
    service.refresh_bot_resources(table)
    assert not calls, "Unavailable resources bypassed polling backoff"
    catalog.resolve = lambda reference, seed: (_ for _ in ()).throw(
        api.BotUnavailable(replace(reference, identity="0" * 64))
    )
    service.refresh_bot_resources(table, force=True)
    assert {**service.get_view(table), 'server_time': after['server_time']} == after
    catalog.resolve = original
    service.refresh_bot_resources(table, force=True)
    assert all(
        item["availability"]["status"] == "available"
        for item in service.get_view(table)["seats"]
    )


def test_completed_model_replay_has_no_provider_dependency_and_frozen_name(tmp_path):
    api, catalog, service = setup(tmp_path)
    made = service.create_game(GameConfig("dice_circuit", "base", 1), ["fixture"])
    table = made["table_id"]
    service.start_game(table, made["creator_credential"])
    for _ in range(300):
        if service.get_result(table)["result"] is not None:
            break
        for work in service.bot_work(table):
            service.apply_bot(work, service.choose_bot(work))
    assert service.get_result(table)["result"] is not None
    expected = service.get_replay(table)
    without_provider = PlayService(tmp_path / "data")
    assert without_provider.get_replay(table) == expected
    assert (
        without_provider.get_view(table)["seats"][0]["display_name"]
        == "Synthetic policy"
    )
    with service.store.transaction(True) as db:
        state = service.store.load(db, table)
        state["seats"][0]["display_name"] = "Changed name"
        service.store.save(db, table, state)
    with pytest.raises(ValueError, match="participants"):
        without_provider.get_replay(table)


@pytest.mark.parametrize("status", ["playing", "completed"])
def test_legacy_runtime_and_completed_record_remain_honestly_unknown(tmp_path, status):
    catalog_api()
    service = PlayService(tmp_path, seed_factory=lambda: 0)
    made = service.create_game(GameConfig("dice_circuit", "base", 1), ["random"])
    table = made["table_id"]
    service.start_game(table, made["creator_credential"])
    if status == "completed":
        for _ in range(300):
            if service.get_result(table)["result"] is not None:
                break
            for work in service.bot_work(table):
                service.apply_bot(work, service.choose_bot(work))
    from boardcraft.server.storage import encode, digest

    with service.store.transaction(True) as db:
        state = service.store.load(db, table)
        for key in ("bot_ref", "display_name", "availability"):
            state["seats"][0].pop(key)
        service.store.save(db, table, state)
        initial = service.store.checked(
            db.execute("SELECT payload,checksum FROM bootstrap").fetchone()
        )
        del initial["participants"]
        payload = encode(initial)
        db.execute(
            "UPDATE bootstrap SET payload=?,checksum=?", (payload, digest(payload))
        )
    restarted = PlayService(tmp_path)
    if status == "playing":
        assert restarted.bot_work(table)[0].reference.behavior_version == "1"
    else:
        assert restarted.get_replay(table)["position"] == 0
        assert restarted.get_view(table)["seats"][0]["bot_ref"] is None


def test_legacy_unknown_and_unsupported_baseline_do_not_substitute(
    tmp_path, monkeypatch
):
    api = catalog_api()
    service = PlayService(tmp_path, seed_factory=lambda: 0)
    made = service.create_game(GameConfig("dice_circuit", "base", 1), ["random"])
    table = made["table_id"]
    with service.store.transaction(True) as db:
        state = service.store.load(db, table)
        state["seats"][0].pop("bot_ref")
        service.store.save(db, table, state)
    module = service.module("dice_circuit")
    original = module.bot_info
    monkeypatch.setattr(
        module,
        "bot_info",
        lambda: tuple(replace(item, behavior_version="2") for item in original()),
    )
    with pytest.raises(api.BotUnavailable):
        service.get_view(table)
    with service.store.transaction(True) as db:
        state = service.store.load(db, table)
        assert "bot_ref" not in state["seats"][0]
        state["seats"][0]["kind"] = "unknown-model"
        service.store.save(db, table, state)
    with pytest.raises(ValueError, match="legacy"):
        service.get_view(table)


@pytest.mark.parametrize("operation", ["list_bots", "pin", "resolve"])
def test_provider_failures_are_sanitized_at_service_boundary(tmp_path, operation):
    _, catalog, service = setup(tmp_path)

    def failure(*args):
        raise ValueError("provider-secret-path")

    setattr(catalog, operation, failure)
    with pytest.raises(ValueError) as error:
        service.create_game(GameConfig("dice_circuit", "base", 1), ["fixture"])
    assert "provider-secret-path" not in str(error.value)


def test_inflight_alias_rebind_keeps_original_work_reference(tmp_path):
    api, catalog, service = setup(tmp_path)
    made = service.create_game(GameConfig("dice_circuit", "base", 1), ["fixture"])
    table = made["table_id"]
    service.start_game(table, made["creator_credential"])
    (work,) = service.bot_work(table)
    original = catalog.resolve
    entered, release = threading.Event(), threading.Event()

    def blocked(reference, seed):
        entered.set()
        assert release.wait(5)
        return original(reference, seed)

    catalog.resolve = blocked
    with ThreadPoolExecutor(1) as pool:
        choosing = pool.submit(service.choose_bot, work)
        try:
            assert entered.wait(3)
            catalog.register(
                "fixture",
                api.NumpySource(
                    *artifact(tmp_path / "replacement", 1, "Changed policy")
                ),
            )
            assert catalog.pin("dice_circuit", "fixture") != work.reference
        finally:
            release.set()
        assert service.apply_bot(work, choosing.result())
    assert service.get_view(table)["seats"][0]["bot_ref"] == api.reference_data(
        work.reference
    )


@pytest.mark.parametrize("error_kind", ["unavailable", "incompatible"])
@pytest.mark.parametrize("matches", [True, False])
@pytest.mark.parametrize("operation", ["create", "start", "refresh"])
def test_typed_provider_failures_rebuilt_without_private_messages(
    tmp_path, error_kind, matches, operation
):
    api, catalog, service = setup(tmp_path)
    from fastapi.testclient import TestClient
    from boardcraft.server.api import create_app

    expected_type = (
        api.BotUnavailable if error_kind == "unavailable" else api.BotIncompatible
    )
    reference = catalog.pin("dice_circuit", "fixture")
    error_reference = reference if matches else replace(reference, identity="0" * 64)

    class ProviderFailure(expected_type):
        def __init__(self):
            super().__init__(error_reference)
            self.args = ("private-provider-path-for-test",)

    made = service.create_game(GameConfig("dice_circuit", "base", 1), ["fixture"])
    table = made["table_id"]

    def fail(*args):
        raise ProviderFailure()

    catalog.resolve = fail
    if operation == "refresh":
        before = service.get_view(table)
        service.refresh_bot_resources(table, force=True)
        after = service.get_view(table)
        assert before["observation"] == after["observation"]
        if matches:
            assert after["seats"][0]["availability"] == {
                "reference": api.reference_data(reference),
                "status": "unavailable",
            }
        else:
            assert {**after, 'server_time': before['server_time']} == before
        assert "private-provider-path-for-test" not in json.dumps(after)
    else:
        action = (
            (
                lambda: service.create_game(
                    GameConfig("dice_circuit", "base", 1), ["fixture"]
                )
            )
            if operation == "create"
            else (lambda: service.start_game(table, made["creator_credential"]))
        )
        with pytest.raises(expected_type) as error:
            action()
        assert type(error.value) is expected_type
        assert error.value.reference == error_reference
        assert str(error.value) in (
            "Selected bot version is unavailable",
            "Selected bot version is incompatible",
        )
        with TestClient(create_app(service, run_bots=False)) as http:
            result = (
                http.post(
                    "/api/tables",
                    json={
                        "config": {
                            "game_id": "dice_circuit",
                            "variant": "base",
                            "players": 1,
                        },
                        "seats": ["fixture"],
                    },
                )
                if operation == "create"
                else http.post(
                    f"/api/tables/{table}/start",
                    headers={"Authorization": "Bearer " + made["creator_credential"]},
                )
            )
        assert result.status_code == 400
        assert "private-provider-path-for-test" not in result.text
        assert result.json()["detail"] == str(error.value)
        assert service.get_view(table)["status"] == "lobby"
