"""Build and install the distribution independently of the source checkout."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import venv

import pytest


@pytest.fixture(scope='session')
def installed_runtime(tmp_path_factory):
    root = tmp_path_factory.mktemp('installed-runtime')
    source = Path(__file__).resolve().parents[1]
    assert not root.is_relative_to(source), 'Installed checks must run outside the checkout'
    wheels = root / 'wheels'
    subprocess.run([sys.executable, '-I', '-B', '-m', 'build', '--wheel', '--outdir', str(wheels), str(source)],
                   check=True, capture_output=True, text=True)
    wheel, = wheels.glob('*.whl')
    environment = root / 'environment'
    venv.EnvBuilder(with_pip=True).create(environment)
    python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    install = subprocess.run([str(python), '-I', '-B', '-m', 'pip', 'install', f'{wheel}[serve,mcp]'],
                             capture_output=True, text=True)
    assert install.returncode == 0, install.stdout + install.stderr
    outside = tempfile.TemporaryDirectory(prefix='boardcraft-runtime-')
    cwd = Path(outside.name).resolve()
    assert not cwd.is_relative_to(source)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    # The runtime must not need a Node executable, inherited import paths or user packages.
    env['PATH'] = str(python.parent)
    env.pop('PYTHONPATH', None)
    env.pop('PYTHONHOME', None)
    probe = subprocess.run([str(python), '-I', '-B', '-c',
        "import importlib.util,json,pathlib,sys; import boardcraft; "
        "from boardcraft.registry import list_games; list_games(); "
        "from boardcraft.server.api import create_app; from boardcraft.mcp.server import create_server; "
        "assert importlib.util.find_spec('torch') is None; "
        "assert not any(name.split('.')[0] in {'torch','training','boardcraft_private','rentcraft'} for name in sys.modules); "
        "print(json.dumps({'origin':str(pathlib.Path(boardcraft.__file__).resolve()),'prefix':sys.prefix,'version':sys.version}))"],
        cwd=cwd, env=env, capture_output=True, text=True)
    assert probe.returncode == 0, probe.stdout + probe.stderr
    details = json.loads(probe.stdout)
    assert Path(details['origin']).is_relative_to(environment)
    assert not Path(details['origin']).is_relative_to(source)
    print('Installed runtime: ' + json.dumps(dict(details, wheel=str(wheel), cwd=str(cwd))))
    try:
        yield {'python': str(python), 'cwd': cwd, 'env': env, 'wheel': wheel, 'root': root}
    finally:
        outside.cleanup()
