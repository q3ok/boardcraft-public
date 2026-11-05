"""The installed distribution contains all resources used by local play."""
import json
import re
import subprocess
import zipfile


def test_installed_resources_and_declared_dependencies(installed_runtime):
    runtime = installed_runtime
    code = r"""
import json
from importlib.metadata import distribution
from importlib.resources import files
from importlib.util import find_spec
from boardcraft.registry import get_game, list_games
from boardcraft.inference.catalog import RuntimeBotCatalog
package = files('boardcraft')
rules = package.joinpath('games/dice_circuit/content/rules.en.md').read_text(encoding='utf-8')
sheet = json.loads(package.joinpath('games/dice_circuit/data/base-sheet.json').read_text(encoding='utf-8'))
assert rules == get_game('dice_circuit').rules_text()
assert sheet == get_game('dice_circuit').sheet_spec()
assert sheet['resourceSlots'] == 7
assert {info.game_id for info in list_games()} == {'dice_circuit', 'rentcraft'}
rent = get_game('rentcraft')
assert package.joinpath('games/rentcraft/content/rules.en.md').read_text(encoding='utf-8') == rent.rules_text()
assert len(rent.board_spec()['spaces']) == 40
catalog = RuntimeBotCatalog()
descriptors = catalog.list_bots('rentcraft')
assert len({bot.choice_id for bot in descriptors}) == len(descriptors) == 35
for bot in descriptors:
    assert catalog.resolve(bot.reference, 7).save_state()['options'] == {}
assert all(find_spec(name) is None for name in ('torch', 'boardcraft_training', 'rentcraft'))
static = package.joinpath('server/static')
scripts = [path.read_text(encoding='utf-8') for path in static.joinpath('assets').iterdir() if path.name.endswith('.js')]
assert any('Rentcraft' in script for script in scripts), 'The packaged interface must offer the Rentcraft board'
assert 'type="module"' in static.joinpath('index.html').read_text(encoding='utf-8')
assert static.joinpath('boardcraft-emblem.png').read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
assert 'MIT' in static.joinpath('THIRD-PARTY-LICENSES.txt').read_text(encoding='utf-8')
print(json.dumps(distribution('boardcraft').requires))
"""
    result = subprocess.run([runtime['python'], '-I', '-B', '-c', code], cwd=runtime['cwd'],
                            env=runtime['env'], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    requirements = json.loads(result.stdout)
    assert {re.split(r'[<>=; ]', item)[0] for item in requirements} == {'numpy', 'fastapi', 'uvicorn', 'mcp', 'pytest', 'build'}
    with zipfile.ZipFile(runtime['wheel']) as wheel:
        names = wheel.namelist()
        assert not any(name.endswith(('.map', '.pdf', '.pt', '.pth', '.ckpt', '.safetensors')) for name in names)
        assert not any('/node_modules/' in name or '/tests/' in name for name in names)
