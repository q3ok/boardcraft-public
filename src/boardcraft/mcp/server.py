"""Official SDK stdio adapter using the same durable application service."""
import json
import anyio
from mcp.server import Server
from mcp.server.stdio import stdio_server
import mcp.types as types
from pydantic import ValidationError
from boardcraft.server.schemas import ActionInput, CommandInput, CreateInput, IdempotencyInput, Input, JoinInput, TableListInput, WaitInput
from .http import RelayError
from pydantic import Field, StrictInt
from boardcraft.server.errors import ServiceError, error_view, invalid_arguments


class TableInput(Input):
    table_id: str = Field(min_length=1, max_length=128)
    credential: str | None = Field(default=None, max_length=256)


class TableJoin(TableInput, JoinInput):
    pass


class TableAction(TableInput, ActionInput):
    pass


class TableStart(TableInput, IdempotencyInput):
    pass


class TableWait(TableInput, WaitInput):
    pass


class TableCommand(TableInput, CommandInput):
    pass


class EventInput(TableInput):
    cursor: StrictInt = Field(default=0, ge=0)
    limit: StrictInt = Field(default=100, ge=1, le=200)


class ReplayInput(TableInput):
    position: StrictInt = Field(default=0, ge=0)


class GameInput(Input):
    game_id: str = Field(min_length=1, max_length=80)


def create_server(service):
    operations = {
        'list_games': (Input, 'Discover games, variants, seat limits and builtin bot kinds. Use list_bots for available choices.', lambda a: service.list_games()),
        'list_bots': (GameInput, 'Discover compatible local bot choices and their exact immutable references. Models always choose the best-scoring legal move (greedy).', lambda a: service.list_bots(a.game_id)),
        'get_board': (GameInput, 'Read the public board specification.', lambda a: service.get_board(a.game_id)),
        'get_settings': (GameInput, 'Read table, live play and builtin player settings schemas.', lambda a: service.get_settings(a.game_id)),
        'submit_command': (TableCommand, 'Submit an advertised parameterized command with decision_token (or legacy expected_revision); use apply_action for enumerated choices. Reuse idempotency_key to recover confirmation.', lambda a: service.submit_command(**a.model_dump())),
        'get_rules': (GameInput, 'Read the canonical English rules.', lambda a: service.get_rules(a.game_id)),
        'list_tables': (TableListInput, 'Discover explicitly listed tables on this server.', lambda a: service.list_tables(**a.model_dump())),
        'create_game': (CreateInput, 'Create a lobby. Save its creator credential. Optional seat_names (one per seat, null for the default) names any seat, agent and human seats included; a name set here is kept when someone joins. Optional secret idempotency_key (32..256 printable characters, >=128 random bits) recovers confirmation for 24 hours; keep it like a credential.', lambda a: service.create_game(a.config.game_config(), a.model_dump()["seats"], live=a.live, visibility=a.visibility, idempotency_key=a.idempotency_key, **({'seat_names': a.seat_names} if a.seat_names is not None else {}))),
        'join_game': (TableJoin, "Occupy one open human or agent seat. Save its separate credential. Optional name (1..40 letters, digits, spaces and . ' _ -) is your display name at the table, used only when the table creator did not name the seat (the creator's name wins); the response returns the display_name in use. A secret idempotency_key recovers the same credential for 24 hours.", lambda a: service.join_game(a.table_id, a.seat, idempotency_key=a.idempotency_key, **({'name': a.name} if a.name is not None else {}))),
        'start_game': (TableStart, 'Start a ready lobby using its creator credential. Reuse idempotency_key to recover confirmation.', lambda a: service.start_game(a.table_id, a.credential, idempotency_key=a.idempotency_key)),
        'observe_game': (TableInput, 'Read one coherent envelope with permitted observation, decision, actions, commands, tokens and timing. Each seat has its own credential; omit it for the observer.', lambda a: service.get_view(a.table_id, a.credential)),
        'wait_for_decision': (TableWait, 'Wait up to 25 seconds: actionable for a seat decision or command; changed with after_change_token for permitted view changes. Returns envelope and terminal reason. Reads never renew deadlines.', lambda a: service.wait_for_decision(**a.model_dump())),
        'legal_actions': (TableInput, 'Read your decision and legal semantic actions with English labels and typed parameters.', lambda a: service.get_actions(a.table_id, a.credential)),
        'apply_action': (TableAction, 'Submit an enumerated legal action using its seat credential, decision ID and optional decision_token. Use submit_command for advertised parameterized commands. Reuse idempotency_key to recover confirmation.', lambda a: service.apply_action(**a.model_dump())),
        'get_events': (EventInput, 'Reconnect using your perspective-local event cursor.', lambda a: service.get_events(a.table_id, a.credential, a.cursor, a.limit)),
        'get_result': (TableInput, 'Read the final result when the game is complete.', lambda a: service.get_result(a.table_id, a.credential)),
        'get_replay': (ReplayInput, 'Read a completed game at a viewer-local position, starting at bootstrap zero. Hidden-only actions have no position.', lambda a: service.get_replay(a.table_id, a.credential, a.position)),
    }
    from boardcraft.server.tournaments import TournamentInput, StartTournamentInput, RevisionInput, FitTournamentInput
    class TournamentId(Input):
        tournament_id: str = Field(pattern='^[0-9a-f]{32}$')
    class TournamentMutation(TournamentId, RevisionInput):
        credential: str | None = Field(default=None,max_length=256)
    class TournamentStart(TournamentMutation, StartTournamentInput):
        pass
    class TournamentFit(TournamentMutation, FitTournamentInput):
        pass
    class TournamentReplay(TournamentId):
        match_id: str = Field(pattern='^[0-9a-f]{64}$')
        position: StrictInt = Field(default=0,ge=0)
    operations.update({
        'tournament_schema':(Input,'Read explicit public scheduling and rating control schemas.',lambda a:service.tournament_schema()),
        'list_tournaments':(Input,'List saved tournaments without dispatching.',lambda a:service.list_tournaments()),
        'create_tournament':(TournamentInput,'Save and pin an explicit immutable schedule; start: true also starts it in the same call.',lambda a:service.create_tournament(a.model_dump(exclude={'lineups'} if a.lineups is None else None))),
        'get_tournament':(TournamentId,'Read progress without dispatching or restarting.',lambda a:service.get_tournament(a.tournament_id)),
        'start_tournament':(TournamentStart,'Explicitly start the saved hash and revision with creator capability.',lambda a:service.start_tournament(a.tournament_id,a.credential,a.spec_sha,a.revision)),
        'restart_tournament':(TournamentStart,'Explicitly retry unfinished original matches; preserve seeds and references.',lambda a:service.restart_tournament(a.tournament_id,a.credential,a.spec_sha,a.revision)),
        'stop_tournament':(TournamentMutation,'Stop queued work and drain actual inflight execution and publication.',lambda a:service.stop_tournament(a.tournament_id,a.credential,a.revision)),
        'delete_tournament':(TournamentMutation,'Delete only this stopped tournament and its unshared owned records.',lambda a:service.delete_tournament(a.tournament_id,a.credential,a.revision)),
        'fit_tournament_ratings':(TournamentFit,'Explicit batch fit on the completed-result snapshot with chosen controls.',lambda a:service.fit_tournament_ratings(a.tournament_id,a.credential,a.revision,a.fit.model_dump())),
        'get_tournament_ratings':(TournamentId,'Read saved fit diagnostics; never refit on a read.',lambda a:service.get_tournament_ratings(a.tournament_id)),
        'get_tournament_match_replay':(TournamentReplay,'Read permitted observer replay frames, never a complete local record.',lambda a:service.get_tournament_match_replay(a.tournament_id,a.match_id,a.position)),
    })
    from boardcraft.server.library_schemas import FitRankingsInput, RankingListInput, RankingInput, ReplayListInput
    operations.update({
        'fit_rankings': (FitRankingsInput, 'Explicitly fit verified selected local tournament results with full controls.', lambda a: service.fit_rankings(**a.model_dump())),
        'list_rankings': (RankingListInput, 'Read bounded saved comparisons; never fit on a read.', lambda a: service.list_rankings(**a.model_dump())),
        'get_ranking': (RankingInput, 'Read the exact saved comparison selected by its result hash.', lambda a: service.get_ranking(**a.model_dump())),
        'list_replays': (ReplayListInput, 'Discover completed listed tables and local tournament matches.', lambda a: service.list_replays(**a.model_dump())),
    })
    limiter = anyio.CapacityLimiter(16)
    active = 0
    active_waits = 0

    def tool_error(error):
        data = error_view(error)
        return types.CallToolResult(content=[types.TextContent(type='text', text=data['message'])],
                                    structured_content=data, isError=True)

    async def list_tools(context, params):
        return types.ListToolsResult(tools=[types.Tool(name=name, description=description, inputSchema=model.model_json_schema())
                                           for name, (model, description, _) in operations.items()])

    async def call_tool(context, params):
        nonlocal active, active_waits
        waiting = params.name == 'wait_for_decision'
        if (active_waits >= 32 if waiting else active >= 16):
            return tool_error(ServiceError('Server is busy; retry the operation', code='busy'))
        if waiting:
            active_waits += 1
        else:
            active += 1
        try:
            if params.name not in operations:
                raise ServiceError('Unknown play operation; use list_tools.', code='unknown_tool')
            if len(json.dumps(params.arguments or {})) > 65536:
                raise ServiceError('Request body is too large', code='invalid_arguments', constraints={'max_bytes': 65536})
            model, _, function = operations[params.name]
            arguments = model.model_validate(params.arguments or {})
            result = (await function(arguments) if waiting else
                      await anyio.to_thread.run_sync(lambda: function(arguments), limiter=limiter))
            # Structured content must be an object in handshake-era MCP clients.
            structured = result if isinstance(result, dict) else {'result': result}
            return types.CallToolResult(content=[types.TextContent(type='text', text=json.dumps(result, allow_nan=False))],
                                        structured_content=structured)
        except RelayError as error:
            return tool_error(ServiceError(str(error), code=error.code, argument='credential' if error.code == 'credential' else None))
        except ValidationError as error:
            return tool_error(invalid_arguments(error, model))
        except ValueError as error:
            return tool_error(error)
        except Exception:
            return tool_error(ServiceError('The operation could not be completed; retry or contact the server operator.', code='internal_error'))
        finally:
            if waiting:
                active_waits -= 1
            else:
                active -= 1
    return Server('boardcraft', version='0.1.0', on_list_tools=list_tools, on_call_tool=call_tool)


async def run_stdio(service, run_bots=True):
    server = create_server(service)
    worker = None
    if run_bots:
        from boardcraft.server.bots import BotExecutor
        worker = BotExecutor(service)
        worker.start()
    try:
        async with stdio_server() as (reader, writer):
            await server.run(reader, writer, server.create_initialization_options())
    finally:
        if worker:
            worker.close()
        if run_bots:
            service.close_tournaments()
