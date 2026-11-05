"""Installed runtime resources remain available without optional services."""
import importlib.util
import json
import subprocess
import sys
from types import ModuleType
from importlib.resources import files


def test_package_import_does_not_load_optional_servers():
    result = subprocess.run(
        [sys.executable, '-I', '-B', '-c',
         "import sys; before = set(sys.modules); import boardcraft; "
         "unexpected = (set(sys.modules) - before) & {'fastapi', 'uvicorn', 'mcp', 'torch'}; "
         "assert not unexpected, f'Package imported optional modules: {sorted(unexpected)}'"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_package_import_check_ignores_preloaded_optional_server(monkeypatch):
    monkeypatch.setitem(sys.modules, 'fastapi', ModuleType('fastapi'))
    test_package_import_does_not_load_optional_servers()


def test_dice_circuit_rules_are_packaged():
    assert importlib.util.find_spec('boardcraft') is not None, 'Standalone boardcraft package is missing'
    rules = files('boardcraft').joinpath('games/dice_circuit/content/rules.en.md').read_text(encoding='utf-8')
    assert '# Dice Circuit' in rules
    assert 'regular opportunity is spent' in rules
    assert 'with no +1 cost' in rules


def test_base_sheet_is_packaged():
    assert importlib.util.find_spec('boardcraft') is not None, 'Standalone boardcraft package is missing'
    sheet = json.loads(files('boardcraft').joinpath('games/dice_circuit/data/base-sheet.json').read_text(encoding='utf-8'))
    assert len(sheet['yellow']['rows']) == 4
    assert sheet['blue']['pointsByCount'][11] == 56
    assert sheet['resourceSlots'] == 7


def test_rentcraft_packaged_rules_board_and_card_decks():
    from boardcraft.registry import get_game

    game = get_game('rentcraft')
    root = files('boardcraft').joinpath('games/rentcraft')
    rules = root.joinpath('content/rules.en.md').read_text(encoding='utf-8')
    assert rules.startswith('# Rentcraft') and rules == game.rules_text()
    board = json.loads(root.joinpath('data/board.en.json').read_text(encoding='utf-8'))
    decks = json.loads(root.joinpath('data/cards.en.json').read_text(encoding='utf-8'))
    spec = game.board_spec()
    assert [space['index'] for space in board] == list(range(40))
    assert [space['name'] for space in board] == [space['name'] for space in spec['spaces']]
    assert set(decks) == {'opportunity', 'community_fund'}
    assert all(len(deck) == 16 for deck in decks.values())
    assert spec['cards'] == decks
