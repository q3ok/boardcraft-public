"""Executable numerical limits precede optional imports; libraries stay inert."""
import os
import subprocess
import sys
import pytest


VARIABLES = ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS')


@pytest.mark.parametrize('arguments', (['--help'], ['serve']))
def test_entrypoint_overrides_inherited_threads_before_optional_import(arguments):
    source = '''
import importlib.abc
import os
import sys
from boardcraft.cli import main
names = ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS')
class ReachedOptionalImport(Exception):
    pass
class CheckImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'boardcraft.server.service' or fullname.split('.')[0] in ('numpy', 'fastapi', 'uvicorn', 'mcp'):
            assert all(os.environ[name] == '1' for name in names), 'Thread limits were not set before optional imports'
            raise ReachedOptionalImport()
sys.meta_path.insert(0, CheckImports())
sys.argv = ['boardcraft'] + ARGUMENTS
try:
    main()
except ReachedOptionalImport:
    assert ARGUMENTS == ['serve']
except SystemExit as error:
    assert error.code == 0 and ARGUMENTS == ['--help']
assert all(os.environ[name] == '1' for name in names), 'Inherited thread limits survived the executable entrypoint'
'''.replace('ARGUMENTS', repr(arguments))
    env = dict(os.environ, **{name: '32' for name in VARIABLES})
    result = subprocess.run([sys.executable, '-I', '-B', '-c', source], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


def test_library_import_preserves_inherited_thread_settings():
    source = '''
import os
import sys
import boardcraft
import boardcraft.cli
import boardcraft.registry
import boardcraft.play
names = ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS')
assert all(os.environ[name] == '32' for name in names)
assert not ({'numpy', 'fastapi', 'uvicorn', 'mcp'} & set(sys.modules))
'''
    env = dict(os.environ, **{name: '32' for name in VARIABLES})
    result = subprocess.run([sys.executable, '-I', '-B', '-c', source], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


def test_relay_cli_never_imports_local_runtime_or_catalog(tmp_path):
    source = '''
import importlib.abc
import sys
import types
from boardcraft.cli import main
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in ('boardcraft.server.service', 'boardcraft.server.storage', 'boardcraft.server.bots', 'boardcraft.inference.catalog'):
            raise AssertionError('Relay imported local runtime: ' + fullname)
sys.meta_path.insert(0, Guard())
seen = []
async def run_stdio(service, run_bots=True):
    assert service.__class__.__name__ == 'HttpPlayService'
    assert run_bots is False
    seen.append(True)
adapter = types.ModuleType('boardcraft.mcp.server')
adapter.run_stdio = run_stdio
sys.modules['boardcraft.mcp.server'] = adapter
sys.argv = ['boardcraft', 'mcp', '--server-url', 'http://localhost:8000']
main()
assert seen == [True]
'''
    result = subprocess.run([sys.executable, '-I', '-B', '-c', source], cwd=tmp_path, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert list(tmp_path.iterdir()) == []


def test_actual_relay_mcp_server_import_and_tool_catalog_are_runtime_free(tmp_path):
    source = '''
import asyncio
import importlib.abc
import sys
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in ('boardcraft.server.service', 'boardcraft.server.storage', 'boardcraft.server.bots', 'boardcraft.inference.catalog', 'boardcraft.registry'):
            raise AssertionError('Relay imported local runtime: ' + fullname)
sys.meta_path.insert(0, Guard())
from boardcraft.mcp.server import create_server
from boardcraft.mcp.http import HttpPlayService
from mcp.client import Client
async def run():
    async with Client(create_server(HttpPlayService('http://localhost:8000'))) as client:
        assert 'list_tables' in {t.name for t in (await client.list_tools()).tools}
asyncio.run(run())
'''
    result = subprocess.run([sys.executable, '-I', '-B', '-c', source], cwd=tmp_path, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('extra', [['--data-dir', 'unused'], ['--model-source', 'unused'], ['--server-url', 'ftp://localhost']])
def test_relay_cli_rejects_conflicts_before_local_import(monkeypatch, extra):
    import builtins
    from boardcraft.cli import main
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        assert name not in ('boardcraft.server.service', 'boardcraft.inference.catalog'), 'Validation imported local runtime'
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', guarded)
    monkeypatch.setattr(sys, 'argv', ['boardcraft', 'mcp', '--server-url', 'http://localhost:8000', *extra])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
