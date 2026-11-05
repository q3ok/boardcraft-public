"""Seat-owned model decisions and durable note-only checkpoints."""
import copy
import importlib
from dataclasses import replace

import numpy as np
import pytest
from boardcraft.contracts import GameConfig
from boardcraft.registry import get_game
from boardcraft.games.rentcraft.inference.profiles import MINI, V7, resolve_profile
from boardcraft.games.rentcraft.inference.policy import NumpyPolicy, weight_shapes


def policy(profile_id=MINI, bias=0):
    profile = resolve_profile(profile_id)
    net = {"family": "rentcraft-legacy-mlp-v1", "hidden": [2], "activation": "relu", "selection": "greedy"}
    arrays = {k: np.zeros(s, np.float32) for k, s in weight_shapes(profile, net).items()}
    arrays["pi.bias"][0] = bias
    return profile, NumpyPolicy(profile, net, arrays)


def bot(profile_id=MINI, bias=0):
    spec = importlib.util.find_spec("boardcraft.games.rentcraft.inference.bot")
    assert spec, "Rentcraft model bot is missing"
    cls = importlib.import_module(spec.name).RentcraftInferenceBot
    profile, weights = policy(profile_id, bias)
    return cls(profile, weights, 7)


def fresh():
    return get_game("rentcraft").new(GameConfig("rentcraft", "base", 2), 19)


@pytest.mark.parametrize("profile_id", [MINI, V7])
def test_fresh_empty_history_greedy_choice_and_rng_untouched(profile_id):
    engine = fresh()
    seat = engine.decision().player
    player = bot(profile_id)
    before = engine.snapshot()
    choice = player.choose(engine.observe(seat), engine.legal_actions(seat))
    assert choice.action.kind == "roll"
    # The decision is recorded with the model's leading choices; an artifact without a value head reports no value.
    rows = choice.meta["probabilities"]
    assert rows[0]["action"] == "Roll the dice" and "value" not in choice.meta
    assert all(0 <= row["probability"] <= 1 for row in rows) and sum(row["probability"] for row in rows) <= 1.0001
    assert engine.snapshot() == before
    other = get_game("rentcraft").inference_bot(*policy(profile_id), seed=999)
    assert other.choose(engine.observe(seat), engine.legal_actions(seat)) == choice
    engine.apply(seat, choice.action, engine.decision().id)


def test_profile_crossbinding_and_wrong_legal_observation_are_refused():
    player = bot()
    cls = type(player)
    with pytest.raises(ValueError):
        cls(resolve_profile(V7), player.policy, 7)
    engine = fresh()
    seat = engine.decision().player
    before = player.save_state()
    for observation, legal in [(engine.observe(seat), ()), (engine.observe(1-seat), engine.legal_actions(seat)),
                               (replace(engine.observe(seat), game_id="dice_circuit"), engine.legal_actions(seat))]:
        with pytest.raises(ValueError):
            player.choose(observation, legal)
        assert player.save_state() == before


def test_missing_history_and_first_binding_after_start_are_refused():
    engine = fresh()
    seat = engine.decision().player
    player = bot()
    observation = engine.observe(seat)
    missing = copy.deepcopy(observation.data)
    missing.pop("model_context")
    with pytest.raises(ValueError):
        player.choose(replace(observation, data=missing), engine.legal_actions(seat))
    engine.apply(seat, engine.legal_actions(seat)[0], engine.decision().id)
    seat = engine.decision().player
    with pytest.raises(ValueError, match="start"):
        bot().choose(engine.observe(seat), engine.legal_actions(seat))


def test_initialized_nonacting_seat_can_first_choose_later():
    engine = fresh()
    players = [bot(), bot()]
    for seat, player in enumerate(players):
        player.initialize(engine.observe(seat))
    seen = set()
    for _ in range(20):
        seat = engine.decision().player
        choice = players[seat].choose(engine.observe(seat), engine.legal_actions(seat))
        engine.apply(seat, choice.action, engine.decision().id)
        seen.add(seat)
        if len(seen) == 2:
            break
    assert seen == {0, 1}


def test_note_checkpoint_roundtrip_foreign_seat_model_profile_and_malformed_rejection():
    engine = fresh()
    seat = engine.decision().player
    player = bot(V7)
    player.choose(engine.observe(seat), engine.legal_actions(seat))
    saved = player.save_state()
    assert saved["seat"] == seat
    restored = bot(V7)
    restored.load_state(copy.deepcopy(saved))
    assert restored.save_state() == saved
    for foreign in [bot(MINI), bot(V7, bias=1)]:
        with pytest.raises(ValueError):
            foreign.load_state(saved)
    for field, value in [("version", True), ("seat", 4), ("selection", "sample"), ("extra", 0),
                         ("notes", {}), ("notes", {"draft": {}})]:
        damaged = copy.deepcopy(saved)
        damaged[field] = value
        before = restored.save_state()
        with pytest.raises(ValueError):
            restored.load_state(damaged)
        assert restored.save_state() == before
    saved["notes"]["turn"] = -999
    with pytest.raises(ValueError):
        restored.load_state(saved)
    wrong = copy.deepcopy(player.save_state())
    wrong["seat"] = 1 - seat
    with pytest.raises(ValueError):
        restored.load_state(wrong)


def test_internal_drafts_never_escape_and_notes_survive_next_decision(monkeypatch):
    engine = fresh()
    seat = engine.decision().player
    player = bot(V7)
    script = iter([178, 210, 220, 178, 210, 219])
    def pick(self, vector, mask):
        index = next(script)
        assert mask[index]
        return index
    monkeypatch.setattr(NumpyPolicy, "best", pick)
    before = engine.snapshot()
    choice = player.choose(engine.observe(seat), engine.legal_actions(seat))
    assert choice.action.kind == "propose_trade" and choice.action.params["give_cash"] == 25
    assert engine.snapshot() == before
    saved = player.save_state()
    assert "draft" not in saved["notes"] and "opens" not in saved["notes"]
    assert saved["notes"]["pending"] is not None
    assert len(saved["notes"]["records"]) == 1
    clone = bot(V7)
    clone.load_state(saved)
    assert clone.save_state() == saved
    engine.apply(seat, choice.action, engine.decision().id)
    assert "abandoned_draft" not in str(engine.observe(seat).data)


def test_sixty_four_internal_calls_fail_without_state_or_game_mutation(monkeypatch):
    engine = fresh()
    seat = engine.decision().player
    player = bot(V7)
    before, saved = engine.snapshot(), player.save_state()
    calls = []
    def unfinished(self, vector, mask):
        calls.append(1)
        return 178 if len(calls) == 1 else 210
    monkeypatch.setattr(NumpyPolicy, "best", unfinished)
    with pytest.raises(RuntimeError, match="finish"):
        player.choose(engine.observe(seat), engine.legal_actions(seat))
    assert len(calls) == 64
    assert player.save_state() == saved
    assert engine.snapshot() == before


def sent_and_rejected_notes(monkeypatch):
    engine = fresh()
    seat = engine.decision().player
    player = bot(V7)
    script = iter([178, 210, 220, 178, 210, 219, 0])
    def pick(self, vector, mask):
        selected = next(script)
        assert mask[selected]
        return selected
    monkeypatch.setattr(NumpyPolicy, "best", pick)
    selected = player.choose(engine.observe(seat), engine.legal_actions(seat))
    engine.apply(seat, selected.action, engine.decision().id)
    responder = engine.decision().player
    reject = next(a for a in engine.legal_actions(responder) if a.kind == "reject_trade")
    engine.apply(responder, reject, engine.decision().id)
    player.choose(engine.observe(seat), engine.legal_actions(seat))
    return player


def test_checkpoint_preserves_order_origin_steps_outcome_after_reload(monkeypatch):
    player = sent_and_rejected_notes(monkeypatch)
    saved = player.save_state()
    rows = [row for _, row in saved["notes"]["records"]]
    assert [(r["kind"], r["origin"], r["steps"], r["outcome"]) for r in rows] == [
        ("abandoned_draft", "empty", 1, ""), ("offer", "empty", 1, "rejected")]
    restored = bot(V7)
    restored.load_state(saved)
    assert restored.save_state() == saved
    saved["notes"]["records"][0][1]["amount"] = 999
    assert restored.save_state() != saved


@pytest.mark.parametrize("damage", ["record-action", "private-id", "accepted-private", "abandoned-outcome", "missing-revision"])
def test_semantically_malformed_note_state_is_rejected_atomically(monkeypatch, damage):
    player = sent_and_rejected_notes(monkeypatch)
    saved = player.save_state()
    damaged = copy.deepcopy(saved)
    notes = damaged["notes"]
    if damage == "record-action": notes["records"][1][1]["amount"] = 999
    elif damage == "private-id": notes["records"][0][0] = 1
    elif damage == "accepted-private": notes["accepted"][0]["kind"] = "abandoned_draft"
    elif damage == "abandoned-outcome": notes["records"][0][1]["outcome"] = "accepted"
    elif damage == "missing-revision": notes["revision"] = None
    with pytest.raises(ValueError):
        player.load_state(damaged)
    assert player.save_state() == saved


@pytest.mark.parametrize("damage", ["all", "suffix"])
def test_checkpoint_missing_accepted_ledger_rows_is_rejected_atomically(monkeypatch, damage):
    player = sent_and_rejected_notes(monkeypatch)
    saved = player.save_state()
    damaged = copy.deepcopy(saved)
    if damage == "all": damaged["notes"]["records"] = []
    else: damaged["notes"]["records"].pop()
    with pytest.raises(ValueError):
        player.load_state(damaged)
    assert player.save_state() == saved


def retained_note_checkpoint(monkeypatch):
    from boardcraft.games.rentcraft.actions import Phase
    from boardcraft.games.rentcraft.state import TradeWindow
    engine = fresh()
    seat = engine.decision().player
    player = bot(V7)
    player.initialize(engine.observe(seat))
    script = iter([178, 220, 1] * 70)
    def pick(self, vector, mask):
        selected = next(script)
        assert mask[selected]
        return selected
    monkeypatch.setattr(NumpyPolicy, "best", pick)
    for serial in range(1, 71):
        state = engine.game.state
        state.phase = Phase.TRADE_WINDOW
        state.window = TradeWindow("request", None, [seat], "restore", Phase.PRE_ROLL, None, serial)
        chosen = player.choose(engine.observe(seat), engine.legal_actions(seat))
        engine.apply(seat, chosen.action, engine.decision().id)
    return player


def test_checkpoint_retention_boundary_restores_exact_order_and_suffix(monkeypatch):
    player = retained_note_checkpoint(monkeypatch)
    saved = player.save_state()
    rows = saved["notes"]["records"]
    assert len(rows) == len(saved["notes"]["accepted"]) == 64
    assert [row[1]["kind"] for row in rows] == ["pass_window", "abandoned_draft"] * 32
    restored = bot(V7)
    restored.load_state(saved)
    assert restored.save_state() == saved
    damaged = copy.deepcopy(saved)
    # Remove an interior accepted record while keeping the private tail.
    damaged["notes"]["records"].pop(2)
    with pytest.raises(ValueError):
        restored.load_state(damaged)
    assert restored.save_state() == saved
