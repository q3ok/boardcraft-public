"""Explicit compatibility baselines; deliberate Dice revisions update these constants."""
from dataclasses import replace
import hashlib

import numpy as np

from boardcraft.contracts import GameConfig
from boardcraft.inference.catalog import RuntimeBotCatalog, descriptor_data, policy_reference, reference_data
from boardcraft.inference.identity import canonical_tensor_digest
from boardcraft.inference.model import NumpyPolicy
from boardcraft.inference.spec import digest, identities
from boardcraft.play.records import ParticipantRecord, record_data
from boardcraft.play.runner import run_game
from boardcraft.registry import get_game


# Captured from the working tree, including the explicit bonus phase and encoder with 3,697 features.
EXPECTED_DICE_INPUT_SIZE = 3697
EXPECTED_DICE_OUTPUT_SIZE = 301
EXPECTED_CURRENT_DICE_BASELINE = {
    "action_bytes": "c4d8fd08a5ed20446d01687a6c4bd384d4d494c9d1123f1431eb0fe7061c1b39",
    "descriptors": "9276e6d77c6af15f8d5837cbad11f513fcf90cd7e4d6b33f82a390d8f31107d3",
    "encoded_bytes": "92bb49f44a65b14f03d291edf4720308082d26c524865f3ed27d36dae3680905",
    "encoder_bytes": "61f19c7616f233ed70c1503bbd7a4f5f226179e53aa0e3eec0190c1d3b3bd010",
    "features": 3697,
    "identities": {
        "action_id": "dice-circuit-actions",
        "action_layout_hash": "c4d8fd08a5ed20446d01687a6c4bd384d4d494c9d1123f1431eb0fe7061c1b39",
        "action_version": "1",
        "encoder_id": "dice-circuit-visible",
        "encoder_layout_hash": "61f19c7616f233ed70c1503bbd7a4f5f226179e53aa0e3eec0190c1d3b3bd010",
        "encoder_version": "1",
        "game_id": "dice_circuit",
        "input_size": 3697,
        "observation_version": "1",
        "output_size": 301,
        "rules_version": "1"
    },
    "initial_rng_and_state": "a1b0d4769be2a4654f93f12c455fad18943d43d6f7c379c7d21ca9a3333bdf22",
    "logits_bytes": "e86b6fdbf708927efcc488bf9c376dd16267e0d0ae1326ce6c6716136ac5f4d1",
    "policy": "7b9ebe139e178cba013278fa0b3b73c2814e37819a3f24f1527e214b8d54c3af",
    "record_v1_bytes": "529c872cfc2a573826db30787882a667c46919b5cdec3acb887e8ad2e883ceae",
    "record_v2_bytes": "189a8f77c8dac8aecb6d54b1cc65f898591e9520c0e35a19efff642220bc625e",
    "reference_bytes": "8a82550d6b63d5cd89dc3dabf6823b0b32aaae0fd12da3b475f525dfe56cb586"
}
EXPECTED_RENTCRAFT_RULE_PINS = {
    "absurd-bonjour-mixed-auctions":  "3e807ebc66ccb1e287638992f83edf6498f6f92d6f7de613eb57dbe4d2ba08f7",
    "absurd-calculator":  "92c042412a4a86c9e4d25b1a85577133eb5adfdb6b3fb4dfa2c4094d81bfd988",
    "absurd-gpt-style":  "5f431f387eb8a2ba2e788202a1e259c4b658901bd53ef63a12f7951ff034ba48",
    "absurd-investor":  "cf51bca152ff5fdca2712cc22b8079577b8f47d6a5e8bac89ca0b4a8cbe021e6",
    "absurd-resistant-bonjour-b":  "ff750f61ec2fa8c6b8af085339715460e7ff1a4ffa239d00050ffe4045f97e64",
    "absurd-resistant-bonjour-c":  "28a5ea793230b5d6a262e1120135efd552ee2ab3c75c458fd3276f600ccbd5e1",
    "absurd-tactician":  "225f295c63622978564d6c782baa86e24b1727e5d01a885cdb85df7900cbe536",
    "absurd-wary-calculator":  "6d05e4fc9c5d68007c8873863446f41b4e48bcbf724d0fd1ff4186aa58da661e",
    "bonjour-a":  "b132ea6588c590e19b228fb21cde8b34888ae73e201810f74d602961fefec061",
    "bonjour-b":  "c13ece50a5b0f1c85075e91c2445f84a050cd55c525d9192ce6769823b014435",
    "bonjour-c":  "b94c7c4a1c744e24b16798cce96b2aa998dbef6018ac23ef1c5a476e2b0a1a98",
    "bonjour-calculator":  "01f9c4be813c3b8eea5e25c943c46cb67e136fba7b1f8ac8982e1f81cdd77436",
    "bonjour-calculator-auctions":  "571fb088dbd3c5f83862e5876c890f731cc78d12efbc43c5094b2fca389a03d6",
    "bonjour-calculator-pure":  "7f737d5e82fb9e563496a5ac1fe69075f6c27284995de6382a052a88581277ce",
    "bonjour-mixed-auctions":  "96c428214935639786c471735127bfbdbcf5342373b87dc6bebe96d24eaf9591",
    "calculator":  "fdc3cc73e3bdf637d347bbf4904c6d3647e0bd2888d658061b94ff610356ce09",
    "cautious":  "dbe14e05a48b887e8c2cb7b00389a894d312c37289b6fa79cd16bb981c363f76",
    "collector":  "0ce106ed7a96fc4ce5fb42549e27079f5433c3bf0d32e2ae314d41a791c589da",
    "configured-miser":  "f0ffd822de6a876b4a94d203c45c2b85649776cf3a126d7b48c1e921269f66ee",
    "controller":  "e213c5434f5d32739005623d6f7abc307faef2ec96e2941c848d67bc61a190d5",
    "fast-simulator":  "2c7dded7cff39303b8de9f08c06206ba10b9c3ee13d029c9002ff0b0d0a02a3f",
    "gpt-style":  "b8140314533503692d0379c898f1963a7b7e42bfcd357718db36d7f4ebca49ee",
    "hustler":  "0a39609164655d90df08d709e2642350080d06e0c11422cb65832e5b8cccdbb0",
    "investor":  "5402f82aa57239406daf9c4438a8bea58400bf46110df84f010e1e1c1f1c3d8d",
    "master":  "bb1df829b54dadc120651531596b3ef00a5c09ae25f62d2cac41a96e47d398e4",
    "miser":  "8e099a78dabd7b3332bd25f7c822114a226ba471e8541da65c47eb13986e3bb2",
    "random":  "7b3833b88a5054221a10790bfe24b27fc63460ce94910e885b3e904486a3681d",
    "resistant-bonjour-a":  "6b719d14400522eb33c123429839f7d72fa31c8384b32f330ac9e426b6a7d3c8",
    "resistant-bonjour-b":  "2eabff9e95083b0dc5cec02606b2dab727620bd9ffab61db993b1bf01c756b80",
    "resistant-bonjour-b-plus-windows":  "1c323d361f51816ac949b71de96603e7697fbffb7291de01a67924b4f4590a54",
    "resistant-bonjour-c":  "7ad1ad649d160bd5d6fec1c2651859495be941e980889d80a04d3896a4713454",
    "resistant-bonjour-c-plus-windows":  "059aaf39e63872600124996d00a42148ed1d30c2f3573b25483ffda8e2a79d1c",
    "simulator":  "0d2d0560c8429cfdb77c319883477b8e8adaa0173f89b17260fefb3506875625",
    "sol-sets":  "81121500237de7bd903d0389e280db42f5aa0d3fbafb6c2d03bcc3c4b2bf20d2",
    "tactician":  "fa10344308431211e7e27e2ded5ac9588d7ab7ed1a285e0491cc6873bcf91360",
    "wary-calculator":  "9efafacbe53a55a9ca0e8bb445c42b296b3c961922f6a5074e2fd4b4f6083e98"
}


def test_current_dice_bytes_hashes_records_and_rng():
    module = get_game("dice_circuit")
    encoder = module.encoder()
    spec, actions = encoder.encoder_spec(), encoder.action_spec()
    weights = {"pi.weight": np.zeros((EXPECTED_DICE_OUTPUT_SIZE, EXPECTED_DICE_INPUT_SIZE), np.float32),
               "pi.bias": np.arange(EXPECTED_DICE_OUTPUT_SIZE, dtype=np.float32) / 100}
    network = {"family": "mlp", "hidden": [], "activation": "relu"}
    policy = NumpyPolicy(encoder, network, weights)
    from boardcraft.inference.catalog import compatibility
    # Retain the historical reference fingerprint; new executable play identities
    # are greedy and covered by test_selection.
    ref = policy_reference(compatibility(encoder), network, canonical_tensor_digest(weights), sampling_version="numpy-choice-v1")
    catalog = RuntimeBotCatalog()
    engine = module.new(GameConfig("dice_circuit", "base", 2), 917)
    decision = engine.decision()
    vector = encoder.encode(engine.observe(decision.player))
    record = run_game(module, GameConfig("dice_circuit", "base", 1), 3, [module.bot("random", 5)])
    historical = replace(record, version="1", participants=(ParticipantRecord(0, "unknown"),))
    actual = {
        "features": spec["flat_size"], "encoder_bytes": digest(spec), "action_bytes": digest(actions),
        "identities": identities(encoder), "policy": canonical_tensor_digest(weights),
        "reference_bytes": digest(reference_data(ref)),
        "descriptors": digest([descriptor_data(d) for d in catalog.list_bots("dice_circuit")]),
        "encoded_bytes": hashlib.sha256(vector.tobytes()).hexdigest(),
        "logits_bytes": hashlib.sha256(policy.logits(vector).tobytes()).hexdigest(),
        "record_v1_bytes": digest(record_data(historical)), "record_v2_bytes": digest(record_data(record)),
        "initial_rng_and_state": digest(engine.snapshot()),
    }
    assert actual == EXPECTED_CURRENT_DICE_BASELINE


def test_all_35_current_rentcraft_rule_pins_and_nondefault_options():
    catalog = RuntimeBotCatalog()
    actual = {d.choice_id: reference_data(d.reference) for d in catalog.list_bots("rentcraft")}
    assert len(actual) == 35
    # A real strategy option must keep its own canonical identity and wire representation.
    actual["configured-miser"] = reference_data(catalog.pin_builtin("rentcraft", "miser", {"buy_reserve": 123}))
    hashes = {kind: digest(data) for kind, data in actual.items()}
    assert hashes == EXPECTED_RENTCRAFT_RULE_PINS
