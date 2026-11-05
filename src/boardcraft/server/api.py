"""Optional HTTP adapter for the shared local play service."""
from contextlib import asynccontextmanager
import re
from fastapi import FastAPI, Header, Query, Request
import anyio
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.exceptions import RequestValidationError
from .bots import BotExecutor
from .schemas import ActionInput, CommandInput, CreateInput, IdempotencyInput, JoinInput, WaitInput
from .views import AccessDenied, Conflict
from .errors import error_view, invalid_arguments


class RequestBounds:
    def __init__(self, app):
        self.app, self.active = app, 0

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        if self.active >= 64:
            return await JSONResponse({'detail': 'Server is busy'}, status_code=503)(scope, receive, send)
        self.active += 1
        try:
            body = bytearray()
            while True:
                message = await receive()
                if message['type'] == 'http.disconnect':
                    return
                chunk = message.get('body', b'')
                if len(body) + len(chunk) > 65536:
                    return await JSONResponse({'detail': 'Request body is too large'}, status_code=413)(scope, receive, send)
                body.extend(chunk)
                if not message.get('more_body'):
                    break
            consumed = False
            async def bounded_receive():
                nonlocal consumed
                if not consumed:
                    consumed = True
                    return {'type': 'http.request', 'body': bytes(body), 'more_body': False}
                return await receive()
            await self.app(scope, bounded_receive, send)
        finally:
            self.active -= 1


def credential(authorization):
    if authorization is None:
        return None
    if not authorization.startswith('Bearer ') or len(authorization) > 263:
        raise AccessDenied('Invalid authorization header')
    return authorization[7:]


def create_app(service, *, run_bots=True, static_dir=None):
    try:
        from boardcraft.mcp.transport import create_mcp_transport
    except ImportError as error:
        raise RuntimeError('The MCP runtime is missing; install boardcraft[serve] with its mcp requirement') from error
    mcp_route, mcp_serving = create_mcp_transport(service)

    @asynccontextmanager
    async def lifespan(app):
        worker = BotExecutor(service) if run_bots else None
        if worker:
            worker.start()
        # Rankings are kept by the server: on startup it fits any completed line-up tournament not fitted yet.
        import threading
        recover = getattr(service, 'recover_tournaments', None)
        if recover:
            threading.Thread(target=lambda: _quietly(recover), name='tournament-recovery', daemon=True).start()
        # The bot lists (and the bundled models behind them) are read once at startup, not by the first page.
        warm = getattr(service, 'warm_up', None)
        if warm:
            threading.Thread(target=lambda: _quietly(warm), name='catalog-warm-up', daemon=True).start()
        try:
            async with mcp_serving():
                yield
        finally:
            if worker:
                worker.close()
            service.close_tournaments()
    def _quietly(work):
        try:
            work()
        except Exception:  # noqa: BLE001 -- recovery is repeated on the next start
            pass

    app = FastAPI(title='Boardcraft', lifespan=lifespan)
    app.router.routes.append(mcp_route)
    app.add_middleware(RequestBounds)

    @app.exception_handler(RequestValidationError)
    async def invalid_schema(request, error):
        data = error_view(invalid_arguments(error))
        return JSONResponse({'detail': data['message'], 'isError': True, 'structuredContent': data}, status_code=422)

    @app.exception_handler(ValueError)
    async def invalid(request, error):
        status = 403 if isinstance(error, AccessDenied) else 409 if isinstance(error, Conflict) else 400
        data = error_view(error)
        return JSONResponse({'detail': data['message'], 'isError': True, 'structuredContent': data}, status_code=status)

    @app.get('/api/games')
    def games():
        return service.list_games()

    @app.get('/api/games/{game_id}/rules', response_class=PlainTextResponse)
    def rules(game_id: str):
        return service.get_rules(game_id)

    @app.get('/api/games/{game_id}/bots')
    def bots(game_id: str):
        return service.list_bots(game_id)

    @app.get('/api/games/{game_id}/sheet')
    def sheet(game_id: str):
        return service.get_sheet(game_id)

    @app.get('/api/games/{game_id}/settings')
    def settings(game_id: str):
        return service.get_settings(game_id)

    @app.get('/api/games/{game_id}/board')
    def board(game_id: str):
        return service.get_board(game_id)

    @app.post('/api/tables')
    def create(body: CreateInput):
        return service.create_game(body.config.game_config(), body.model_dump()["seats"], live=body.live, visibility=body.visibility, idempotency_key=body.idempotency_key, **({'seat_names': body.seat_names} if body.seat_names is not None else {}))

    @app.get('/api/tables')
    def tables(game_id: str | None = None, status: str | None = None, offset: str = '0', limit: str = '25'):
        if not re.fullmatch(r'[0-9]+', offset) or not re.fullmatch(r'[0-9]+', limit):
            raise ValueError('Invalid table page bounds')
        return service.list_tables(game_id=game_id, status=status, offset=int(offset), limit=int(limit))

    @app.post('/api/tables/{table_id}/join')
    def join(table_id: str, body: JoinInput):
        return service.join_game(table_id, body.seat, idempotency_key=body.idempotency_key, **({'name': body.name} if body.name is not None else {}))

    @app.post('/api/tables/{table_id}/start')
    def start(table_id: str, body: IdempotencyInput | None = None, authorization: str | None = Header(default=None)):
        return service.start_game(table_id, credential(authorization), idempotency_key=None if body is None else body.idempotency_key)

    @app.get('/api/tables/{table_id}/view')
    def view(table_id: str, authorization: str | None = Header(default=None)):
        return service.get_view(table_id, credential(authorization))

    @app.post('/api/tables/{table_id}/wait')
    async def wait(table_id: str, body: WaitInput, request: Request, authorization: str | None = Header(default=None)):
        result = None
        failure = None
        async with anyio.create_task_group() as group:
            async def disconnected():
                while True:
                    if (await request.receive())['type'] == 'http.disconnect':
                        group.cancel_scope.cancel()
                        return
            group.start_soon(disconnected)
            try:
                result = await service.wait_for_decision(table_id, credential(authorization), **body.model_dump())
            except ValueError as error:
                failure = error
            finally:
                group.cancel_scope.cancel()
        if failure is not None:
            raise failure
        return result

    @app.get('/api/tables/{table_id}/actions')
    def actions(table_id: str, authorization: str | None = Header(default=None)):
        return service.get_actions(table_id, credential(authorization))

    @app.post('/api/tables/{table_id}/actions')
    def apply(table_id: str, body: ActionInput, authorization: str | None = Header(default=None)):
        return service.apply_action(table_id, credential(authorization), **body.model_dump())

    @app.post('/api/tables/{table_id}/commands')
    def command(table_id: str, body: CommandInput, authorization: str | None = Header(default=None)):
        return service.submit_command(table_id, credential(authorization), **body.model_dump())

    @app.get('/api/tables/{table_id}/events')
    def events(table_id: str, cursor: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=200), authorization: str | None = Header(default=None)):
        return service.get_events(table_id, credential(authorization), cursor, limit)

    @app.get('/api/tables/{table_id}/result')
    def result(table_id: str, authorization: str | None = Header(default=None)):
        return service.get_result(table_id, credential(authorization))

    @app.get('/api/tables/{table_id}/replay/timeline')
    def replay_timeline(table_id: str, authorization: str | None = Header(default=None)):
        return service.get_replay_timeline(table_id, credential(authorization))

    @app.get('/api/tables/{table_id}/replay')
    def replay(table_id: str, position: int = Query(0, ge=0), authorization: str | None = Header(default=None)):
        return service.get_replay(table_id, credential(authorization), position)

    from .table_admin import register_table_admin_routes
    register_table_admin_routes(app, service, credential)
    from .tournaments import register_tournament_routes
    register_tournament_routes(app, service, credential)
    from .knockout import register_knockout_routes
    register_knockout_routes(app, service, credential)
    from .library import register_library_routes
    register_library_routes(app, service)
    if static_dir is not None:
        from .static_files import PageStaticFiles
        app.mount('/', PageStaticFiles(directory=static_dir, html=True), name='play')
    return app
