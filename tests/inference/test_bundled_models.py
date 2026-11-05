"""Selected bundled opponents are public, portable NumPy play resources."""
import asyncio
import builtins
import json
import re
import subprocess
import zipfile
from importlib.resources import files

import pytest

from boardcraft.contracts import GameConfig
from boardcraft.inference.catalog import default_catalog, descriptor_data
from boardcraft.registry import get_game


SELECTED = {
    "mini-evo5": "Compact MLP network (32x32) from the earliest Rentcraft model line, trained with reinforcement learning. Uses the older block encoder with generated trade candidates. A light, fast opponent. Always chooses the best-scoring legal move (greedy).",
    "njord-gen0": 'Transformer network (6 layers x 128). Learned the rules and imitated the rule bot "Resistant Bonjour C + Windows", then learned to propose and negotiate trades itself and was refined with PPO. The strongest Rentcraft model at the time of export. Always chooses the best-scoring legal move (greedy).',
    "hodr": "Small transformer network (2 layers x 32), trained with the same teacher and procedure as njord-gen0. In our comparison its playing strength could not be told apart from njord-gen0, at about 20 times fewer parameters. Always chooses the best-scoring legal move (greedy).",
}


def selected_payload(payload):
    return {item["choice_id"]: (item["display_name"], item["description"])
            for item in payload if item["kind"] == "model"}


EXPECTED = {"rentcraft-" + name: (name, description) for name, description in SELECTED.items()}


def test_bundled_catalog_exact_selection_and_numpy_decisions(monkeypatch):
    original = builtins.__import__
    def no_torch(name, *args, **kwargs):
        assert name.split(".")[0] != "torch", "Playing bundled models must not import torch"
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", no_torch)
    catalog = default_catalog()
    descriptors = catalog.list_bots("rentcraft")
    assert selected_payload([descriptor_data(item) for item in descriptors]) == EXPECTED
    module = get_game("rentcraft")
    for item in descriptors:
        if item.kind != "model":
            continue
        bot = catalog.resolve(catalog.pin("rentcraft", item.choice_id), 7)
        engine = module.new(GameConfig("rentcraft", "base", 2), 19)
        decision = engine.decision()
        legal = engine.legal_actions(decision.player)
        choice = bot.choose(engine.observe(decision.player), legal)
        assert choice.action in legal
        engine.apply(decision.player, choice.action, decision.id)


def test_bundled_files_are_only_selected_public_artifacts():
    root = files("boardcraft.models")
    manifest = json.loads(root.joinpath("models.json").read_text(encoding="utf-8"))
    assert manifest["version"] == 1
    assert {row["choice_id"] for row in manifest["models"]} == set(EXPECTED)
    assert len(manifest["models"]) == 3
    assert {path.name for path in root.iterdir()} == {"README.md", "models.json", *EXPECTED}
    for row in manifest["models"]:
        folder = root.joinpath(row["choice_id"])
        assert {path.name for path in folder.iterdir()} == {"metadata.json", "weights.npz"}
        assert row == {"choice_id": row["choice_id"], "metadata": row["choice_id"] + "/metadata.json",
                       "weights": row["choice_id"] + "/weights.npz"}
        raw = folder.joinpath("metadata.json").read_bytes()
        assert b"\r" not in raw
        text = raw.decode("ascii")
        assert not re.search(r"[A-Za-z]:[/\\]|[/\\]|https?:|@|\u2013|\u2014", text.replace('\\"', '"'))
        assert all(word not in text.lower() for word in
                   ("checkpoint", "source_sha", "optimizer", "config_name", "boardcraft_training", "secret"))
        metadata = json.loads(text)
        assert set(metadata) == {"game_id", "rules_version", "observation_version", "encoder_id", "encoder_version",
            "encoder_layout_hash", "action_id", "action_version", "action_layout_hash",
            "input_size", "output_size", "format", "architecture", "weights_sha256",
            "display_name", "description", "value_head"}
        with zipfile.ZipFile(folder.joinpath("weights.npz")) as archive:
            assert not archive.comment
            assert all(re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*\.npy", name)
                       for name in archive.namelist())
            assert all(not item.comment and item.compress_type == zipfile.ZIP_STORED for item in archive.infolist())


def test_descriptions_reach_rest_mcp_and_lobby_data(tmp_path):
    from fastapi.testclient import TestClient
    from mcp.client import Client
    from boardcraft.mcp.server import create_server
    from boardcraft.server.api import create_app
    from boardcraft.server.service import PlayService
    service = PlayService(tmp_path)
    assert selected_payload(service.list_bots("rentcraft")) == EXPECTED
    with TestClient(create_app(service, run_bots=False)) as http:
        response = http.get("/api/games/rentcraft/bots")
        assert response.status_code == 200
        assert selected_payload(response.json()) == EXPECTED
    async def run():
        async with Client(create_server(service)) as client:
            response = await client.call_tool("list_bots", {"game_id": "rentcraft"})
            assert not response.is_error
            assert selected_payload(json.loads(response.content[0].text)) == EXPECTED
    asyncio.run(run())


def test_installed_wheel_includes_bundled_files_and_plays_without_torch(installed_runtime):
    root = files("boardcraft.models")
    with zipfile.ZipFile(installed_runtime["wheel"]) as wheel:
        names = {name for name in wheel.namelist() if name.startswith("boardcraft/models/")}
        expected = {"boardcraft/models/README.md", "boardcraft/models/models.json"}
        expected.update("boardcraft/models/" + choice + "/" + filename
                        for choice in EXPECTED for filename in ("metadata.json", "weights.npz"))
        assert names == expected
        for name in names:
            assert wheel.read(name) == root.joinpath(name.removeprefix("boardcraft/models/")).read_bytes()
    script = '''
import importlib.util, json
from boardcraft.contracts import GameConfig
from boardcraft.registry import get_game
from boardcraft.inference.catalog import default_catalog, descriptor_data
assert importlib.util.find_spec("torch") is None
catalog = default_catalog()
models = [d for d in catalog.list_bots("rentcraft") if d.kind == "model"]
for d in models:
    bot = catalog.resolve(catalog.pin("rentcraft", d.choice_id), 7)
    engine = get_game("rentcraft").new(GameConfig("rentcraft", "base", 2), 19)
    decision = engine.decision()
    legal = engine.legal_actions(decision.player)
    choice = bot.choose(engine.observe(decision.player), legal)
    assert choice.action in legal
    engine.apply(decision.player, choice.action, decision.id)
print(json.dumps([descriptor_data(d) for d in models]))
'''
    result = subprocess.run([installed_runtime["python"], "-I", "-B", "-c", script],
                            cwd=installed_runtime["cwd"], env=installed_runtime["env"],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert selected_payload(json.loads(result.stdout)) == EXPECTED
