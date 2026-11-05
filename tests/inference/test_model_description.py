"""Optional bounded English text is validated at both artifact entry points."""
import json
import asyncio
from dataclasses import replace

import pytest

from boardcraft.inference.catalog import RuntimeBotCatalog, NumpySource, descriptor_data
from boardcraft.inference.model import load_game_artifact
from boardcraft.registry import get_game
from test_runtime import rent_artifact
from test_catalog import artifact


@pytest.mark.parametrize("game_id", ["dice_circuit", "rentcraft"])
@pytest.mark.parametrize("description", [None, "A compact opponent (32x32), trained with PPO. Uses the teacher \"Rule bot + C\".", "A" * 600])
def test_optional_description_survives_loading_and_catalog(tmp_path, game_id, description):
    paths = rent_artifact(tmp_path) if game_id == "rentcraft" else artifact(tmp_path)
    meta, weights = paths[:2]
    data = json.loads(meta.read_text())
    data["display_name"] = "mini-evo5"
    if description is not None:
        data["description"] = description
    meta.write_text(json.dumps(data), encoding="utf-8")
    loaded = load_game_artifact(meta, weights, get_game(game_id))
    assert loaded.metadata.get("description") == description
    catalog = RuntimeBotCatalog({"fixture": NumpySource(meta, weights)})
    descriptor, = [d for d in catalog.list_bots(game_id) if d.kind == "model"]
    assert descriptor.description == description
    payload = descriptor_data(descriptor)
    if description is None:
        assert "description" not in payload
    else:
        assert payload["description"] == description
    with pytest.raises(ValueError):
        descriptor_data(replace(descriptor, description="<script>unsafe</script>"))


@pytest.mark.parametrize("game_id", ["dice_circuit", "rentcraft"])
@pytest.mark.parametrize("bad", [None, 3, {}, "", " A", "A ", "A\nB", "A\tB", "A" * 601,
    "<b>HTML</b>", "A/path", "A\\path", "A: file", "A@example", "Za\u017c\u00f3\u0142\u0107", "A\u2014B"])
def test_invalid_description_is_rejected(tmp_path, game_id, bad):
    paths = rent_artifact(tmp_path) if game_id == "rentcraft" else artifact(tmp_path)
    meta, weights = paths[:2]
    data = json.loads(meta.read_text())
    data["description"] = bad
    meta.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        load_game_artifact(meta, weights, get_game(game_id))


def test_local_description_reaches_rest_mcp_and_lobby_discovery(tmp_path):
    from fastapi.testclient import TestClient
    from mcp.client import Client
    from boardcraft.mcp.server import create_server
    from boardcraft.server.api import create_app
    from boardcraft.server.service import PlayService
    meta, weights, _, _ = rent_artifact(tmp_path)
    data = json.loads(meta.read_text())
    data.update(display_name="mini-evo5", description="A compact opponent (32x32).")
    meta.write_text(json.dumps(data), encoding="utf-8")
    catalog = RuntimeBotCatalog({"fixture": NumpySource(meta, weights)})
    service = PlayService(tmp_path / "store", bot_catalog=catalog)
    expected = [descriptor_data(d) for d in catalog.list_bots("rentcraft")]
    # This same REST discovery list supplies the lobby's optional description.
    assert service.list_bots("rentcraft")[-1] == expected[-1]
    with TestClient(create_app(service, run_bots=False)) as client:
        response = client.get("/api/games/rentcraft/bots")
        assert response.status_code == 200
        assert response.json()[-1] == expected[-1]
    async def run():
        async with Client(create_server(service)) as client:
            response = await client.call_tool("list_bots", {"game_id": "rentcraft"})
            assert not response.is_error
            assert json.loads(response.content[0].text)[-1] == expected[-1]
    asyncio.run(run())
