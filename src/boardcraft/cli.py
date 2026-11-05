"""Local player and agent entry points with optional transport imports."""
import argparse
import os


def main():
    for name in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
        os.environ[name] = '1'
    parser = argparse.ArgumentParser(prog='boardcraft', description='Play Boardcraft locally.')
    commands = parser.add_subparsers(dest='command', required=True)
    serve = commands.add_parser('serve', help='Run the local game server.')
    serve.add_argument('--host', default='127.0.0.1')
    serve.add_argument('--port', type=int, default=8000)
    serve.add_argument('--data-dir', default=None)
    mcp = commands.add_parser('mcp', help='Run the local MCP stdio server.')
    mcp.add_argument('--data-dir', default=None)
    mcp.add_argument('--server-url', default=None, help='Relay gameplay MCP to an existing HTTP server origin.')
    for command in (serve, mcp):
        command.add_argument('--model-source', default=None, metavar='MANIFEST',
            help='Explicit local policy source manifest. Use the same source inventory for processes sharing a data directory.')
    args = parser.parse_args()
    if args.command == 'mcp' and args.server_url is not None:
        if args.data_dir is not None or args.model_source is not None:
            parser.error('--server-url cannot be combined with --data-dir or --model-source')
        from boardcraft.mcp.http import HttpPlayService
        try:
            service = HttpPlayService(args.server_url)
        except ValueError as error:
            parser.error(str(error))
        import anyio
        from boardcraft.mcp.server import run_stdio
        async def relay():
            await run_stdio(service, run_bots=False)
        anyio.run(relay)
        return
    from boardcraft.server.service import PlayService
    try:
        from boardcraft.inference.catalog import default_catalog
        service = PlayService(args.data_dir, bot_catalog=default_catalog(args.model_source))
    except ValueError as error:
        parser.error(str(error))
    if args.command == 'serve':
        import uvicorn
        from boardcraft.server.api import create_app
        from importlib.resources import files
        uvicorn.run(create_app(service, static_dir=files('boardcraft.server').joinpath('static')), host=args.host, port=args.port, access_log=False, limit_concurrency=64)
    else:
        import anyio
        from boardcraft.mcp.server import run_stdio
        anyio.run(run_stdio, service)
