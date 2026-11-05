"""Bounded gameplay HTTP relay with per-request capabilities and no local runtime."""
import json
import math
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from boardcraft.contracts import config_data
from boardcraft.server.errors import remote_error


MESSAGES = {
    'unavailable': 'Game or table unavailable on this Boardcraft server. Check the configured server and table.',
    'credential': 'This game credential does not permit the requested operation.',
    'conflict': 'The game changed. Read its current state before acting again.',
    'request': 'The game request is invalid. Read the current rules and legal actions.',
    'busy': 'Boardcraft is busy. Read the current state before retrying an operation.',
    'connection': 'Boardcraft server is unavailable or confirmation was lost. Check the server and read state before retrying a change.',
    'response': 'The configured server returned an invalid Boardcraft response.',
}
HTTP_CODES = {400: 'unavailable', 404: 'unavailable', 401: 'credential', 403: 'credential',
              409: 'conflict', 422: 'request', 429: 'busy', 503: 'busy'}
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class RelayError(ValueError):
    """A fixed public message, independent of remote error bodies and secrets."""
    def __init__(self, code):
        self.code = code
        super().__init__(MESSAGES[code])


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def segment(value):
    return quote(value, safe='')


def reject_constant(value):
    raise ValueError('Invalid JSON constant')


def finite_float(value):
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError('Invalid JSON number')
    return parsed


# Nesting bound independent of the interpreter's recursion limit (deeper input is rejected on every Python).
MAX_JSON_DEPTH = 256


def load_json(text):
    data = json.loads(text, parse_constant=reject_constant, parse_float=finite_float)
    stack = [(data, 1)]
    while stack:
        value, depth = stack.pop()
        if isinstance(value, (dict, list)):
            if depth > MAX_JSON_DEPTH:
                raise ValueError('JSON nesting too deep')
            stack.extend((child, depth + 1) for child in (value.values() if isinstance(value, dict) else value))
    return data


class HttpPlayService:
    def __init__(self, server_url, *, timeout=30.0, opener=None):
        try:
            if (not isinstance(server_url, str) or any(ord(char) <= 32 for char in server_url)
                    or any(char in server_url for char in ('\\', '?', '#'))):
                raise ValueError
            url = urlsplit(server_url)
            if (url.scheme not in ('http', 'https') or not url.hostname or url.path not in ('', '/')
                    or url.username is not None or url.password is not None
                    or url.port is not None and not 1 <= url.port <= 65535):
                raise ValueError
            if type(timeout) not in (float, int) or not math.isfinite(timeout) or timeout <= 0:
                raise ValueError
        except (ValueError, TypeError):
            raise ValueError('A valid HTTP or HTTPS server origin and positive timeout are required') from None
        self.server_url = server_url.rstrip('/')
        self.timeout = timeout
        self._opener = build_opener(NoRedirect()) if opener is None else opener

    def _request(self, method, path, *, credential=None, body=None, query=None, text=False):
        headers = {'Accept': 'text/plain' if text else 'application/json'}
        if credential is not None:
            if (not isinstance(credential, str) or not credential or len(credential) > 256
                    or any(not 33 <= ord(char) <= 126 for char in credential)):
                raise RelayError('credential')
            headers['Authorization'] = 'Bearer ' + credential
        data = None
        if body is not None:
            try:
                data = json.dumps(body, ensure_ascii=False, allow_nan=False).encode('utf-8')
            except (ValueError, TypeError, UnicodeError, RecursionError):
                raise RelayError('request') from None
            if len(data) > 65536:
                raise RelayError('request')
            headers['Content-Type'] = 'application/json; charset=utf-8'
        url = self.server_url + path
        if query:
            url += '?' + urlencode({key: value for key, value in query.items() if value is not None})
        try:
            request = Request(url, data=data, headers=headers, method=method)
            with self._opener.open(request, timeout=self.timeout) as response:
                if response.status != 200:
                    raise RelayError(HTTP_CODES.get(response.status, 'response'))
                content_type = response.headers.get('Content-Type', '').split(';', 1)[0].strip().lower()
                if content_type != ('text/plain' if text else 'application/json'):
                    raise RelayError('response')
                # A bounded HTTPResponse.read does not raise on every premature EOF.
                expected_length = getattr(response, 'length', None)
                payload = response.read(MAX_RESPONSE_BYTES + 1)
                if (len(payload) > MAX_RESPONSE_BYTES
                        or expected_length is not None and len(payload) != expected_length):
                    raise RelayError('response')
                decoded = payload.decode('utf-8')
                return decoded if text else load_json(decoded)
        except RelayError:
            raise
        except HTTPError as error:
            diagnostic = None
            try:
                if (error.fp is not None and error.headers.get('Content-Type', '').split(';', 1)[0].strip().lower() == 'application/json'):
                    payload = error.read(8193)
                    if len(payload) <= 8192:
                        diagnostic = remote_error(load_json(payload.decode('utf-8')))
            except (ValueError, TypeError, UnicodeError, OSError, HTTPException, RecursionError):
                pass
            finally:
                if error.fp is not None:
                    error.close()
            if diagnostic is not None:
                raise diagnostic from None
            raise RelayError(HTTP_CODES.get(error.code, 'response')) from None
        except (URLError, OSError, TimeoutError):
            raise RelayError('connection') from None
        except (ValueError, TypeError, UnicodeError, HTTPException, RecursionError):
            raise RelayError('response') from None

    def list_games(self):
        return self._request('GET', '/api/games')

    def list_bots(self, game_id):
        return self._request('GET', f'/api/games/{segment(game_id)}/bots')

    def get_board(self, game_id):
        return self._request('GET', f'/api/games/{segment(game_id)}/board')

    def get_settings(self, game_id):
        return self._request('GET', f'/api/games/{segment(game_id)}/settings')

    def get_rules(self, game_id):
        return self._request('GET', f'/api/games/{segment(game_id)}/rules', text=True)

    def list_tables(self, *, game_id=None, status=None, offset=0, limit=25):
        return self._request('GET', '/api/tables', query=dict(game_id=game_id, status=status, offset=offset, limit=limit))

    def create_game(self, config, seats, *, live=None, visibility='invite', idempotency_key=None, seat_names=None):
        body = dict(config=config_data(config), seats=seats, live={} if live is None else live, visibility=visibility)
        if seat_names is not None: body['seat_names'] = seat_names
        if idempotency_key is not None: body['idempotency_key'] = idempotency_key
        return self._request('POST', '/api/tables', body=body)

    def join_game(self, table_id, seat, *, idempotency_key=None, name=None):
        return self._request('POST', f'/api/tables/{segment(table_id)}/join', body={'seat': seat, **({'name': name} if name is not None else {}), **({'idempotency_key': idempotency_key} if idempotency_key is not None else {})})

    def start_game(self, table_id, credential, *, idempotency_key=None):
        return self._request('POST', f'/api/tables/{segment(table_id)}/start', credential=credential, body={} if idempotency_key is None else {'idempotency_key': idempotency_key})

    def get_view(self, table_id, credential=None):
        return self._request('GET', f'/api/tables/{segment(table_id)}/view', credential=credential)

    def get_actions(self, table_id, credential=None):
        return self._request('GET', f'/api/tables/{segment(table_id)}/actions', credential=credential)

    def apply_action(self, table_id, credential, decision_id, action_id, *, decision_token=None, idempotency_key=None):
        return self._request('POST', f'/api/tables/{segment(table_id)}/actions', credential=credential,
                             body=dict(decision_id=decision_id, action_id=action_id, **{key: value for key, value in dict(decision_token=decision_token, idempotency_key=idempotency_key).items() if value is not None}))

    def submit_command(self, table_id, credential, expected_revision, kind, params, decision_id=None, *, decision_token=None, idempotency_key=None):
        return self._request('POST', f'/api/tables/{segment(table_id)}/commands', credential=credential,
                             body=dict(expected_revision=expected_revision, kind=kind, params=params, decision_id=decision_id,
                                       **{key: value for key, value in dict(decision_token=decision_token, idempotency_key=idempotency_key).items() if value is not None}))

    async def wait_for_decision(self, table_id, credential=None, *, mode='actionable', after_change_token=None, timeout=25.):
        import asyncio
        import io
        import ssl
        from http.client import HTTPResponse
        from boardcraft.server.errors import ServiceError
        from boardcraft.server.schemas import WaitInput
        body = WaitInput(mode=mode, after_change_token=after_change_token, timeout=timeout).model_dump()
        headers = {'Accept': 'application/json'}
        if credential is not None:
            if (not isinstance(credential, str) or not credential or len(credential) > 256
                    or any(not 33 <= ord(char) <= 126 for char in credential)):
                raise RelayError('credential')
            headers['Authorization'] = 'Bearer ' + credential
        async def exchange():
            origin = urlsplit(self.server_url)
            secure = origin.scheme == 'https'
            reader, writer = await asyncio.open_connection(origin.hostname, origin.port or (443 if secure else 80),
                                                            ssl=ssl.create_default_context() if secure else None)
            try:
                payload = json.dumps(body, allow_nan=False).encode('utf-8')
                headers.update(Host=origin.netloc, Connection='close', **{'Content-Type': 'application/json', 'Content-Length': str(len(payload))})
                head = f'POST /api/tables/{segment(table_id)}/wait HTTP/1.1\r\n' + ''.join(f'{key}: {value}\r\n' for key, value in headers.items()) + '\r\n'
                writer.write(head.encode('ascii') + payload)
                await writer.drain()
                wire = bytearray()
                while True:
                    chunk = await reader.read(65536)
                    if not chunk: break
                    wire.extend(chunk)
                    if len(wire) > MAX_RESPONSE_BYTES + 65536:
                        raise RelayError('response')
                class BufferSocket:
                    def makefile(self, *args, **kwargs): return io.BytesIO(wire)
                with HTTPResponse(BufferSocket()) as response:
                    response.begin()
                    expected = response.length
                    payload = response.read(MAX_RESPONSE_BYTES + 1)
                    if len(payload) > MAX_RESPONSE_BYTES or expected is not None and len(payload) != expected:
                        raise RelayError('response')
                    if response.headers.get('Content-Type', '').split(';', 1)[0].strip().lower() != 'application/json':
                        raise RelayError(HTTP_CODES.get(response.status, 'response'))
                    data = load_json(payload.decode('utf-8'))
                    if response.status != 200:
                        diagnostic = remote_error(data) if len(payload) <= 8192 else None
                        if diagnostic is not None: raise diagnostic
                        raise RelayError(HTTP_CODES.get(response.status, 'response'))
                    return data
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except OSError:
                    pass
        try:
            return await asyncio.wait_for(exchange(), max(self.timeout, timeout + 5.))
        except (RelayError, ServiceError):
            raise
        except (OSError, asyncio.TimeoutError):
            raise RelayError('connection') from None
        except (ValueError, UnicodeError, HTTPException, RecursionError):
            raise RelayError('response') from None

    def get_events(self, table_id, credential=None, cursor=0, limit=100):
        return self._request('GET', f'/api/tables/{segment(table_id)}/events', credential=credential,
                             query=dict(cursor=cursor, limit=limit))

    def get_result(self, table_id, credential=None):
        return self._request('GET', f'/api/tables/{segment(table_id)}/result', credential=credential)

    def get_replay(self, table_id, credential=None, position=0):
        return self._request('GET', f'/api/tables/{segment(table_id)}/replay', credential=credential, query=dict(position=position))

    def tournament_schema(self):
        return self._request('GET', '/api/tournaments/schema')

    def list_tournaments(self):
        return self._request('GET', '/api/tournaments')

    def create_tournament(self, body):
        return self._request('POST', '/api/tournaments', body=body)

    def get_tournament(self, tournament_id):
        return self._request('GET', f'/api/tournaments/{segment(tournament_id)}')

    def start_tournament(self, tournament_id, credential, spec_sha, revision):
        return self._request('POST', f'/api/tournaments/{segment(tournament_id)}/start', credential=credential,
                             body=dict(spec_sha=spec_sha, revision=revision))

    def restart_tournament(self, tournament_id, credential, spec_sha, revision):
        return self._request('POST', f'/api/tournaments/{segment(tournament_id)}/restart', credential=credential,
                             body=dict(spec_sha=spec_sha, revision=revision))

    def stop_tournament(self, tournament_id, credential, revision):
        return self._request('POST', f'/api/tournaments/{segment(tournament_id)}/stop', credential=credential, body=dict(revision=revision))

    def delete_tournament(self, tournament_id, credential, revision):
        return self._request('POST', f'/api/tournaments/{segment(tournament_id)}/delete', credential=credential, body=dict(revision=revision))

    def fit_tournament_ratings(self, tournament_id, credential, revision, fit):
        return self._request('POST', f'/api/tournaments/{segment(tournament_id)}/fit', credential=credential, body=dict(revision=revision, fit=fit))

    def get_tournament_ratings(self, tournament_id):
        return self._request('GET', f'/api/tournaments/{segment(tournament_id)}/ratings')

    def get_tournament_match_replay(self, tournament_id, match_id, position=0):
        return self._request('GET', f'/api/tournaments/{segment(tournament_id)}/matches/{segment(match_id)}/replay', query=dict(position=position))

    def fit_rankings(self, sources, fit):
        return self._request('POST', '/api/rankings/fit', body=dict(sources=sources, fit=fit))

    def list_rankings(self, **filters):
        return self._request('GET', '/api/rankings', query=filters)

    def get_ranking(self, comparison_id, result_sha):
        return self._request('GET', '/api/rankings/' + segment(comparison_id), query=dict(result_sha=result_sha))

    def list_replays(self, **filters):
        # ``won`` is a flag in the address: sent only when it is set.
        query = {key: (('1' if value else None) if type(value) is bool else value) for key, value in filters.items()}
        return self._request('GET', '/api/replays', query=query)
