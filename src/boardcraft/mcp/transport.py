"""Native MCP Streamable HTTP for the gameplay host, served from the shared tool registry."""
from mcp.server.streamable_http_manager import StreamableHTTPASGIApp, StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import PlainTextResponse
from starlette.routing import Route
import anyio
from .server import create_server

LOOPBACK_HOSTS = ['127.0.0.1:*', 'localhost:*', '[::1]:*']
LOOPBACK_ORIGINS = ['http://127.0.0.1:*', 'http://localhost:*', 'http://[::1]:*']


class PostOnly:
    """Stateless JSON has no session or server-pushed stream, so GET and DELETE answer 405 as the MCP spec allows."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['method'] != 'POST':
            return await PlainTextResponse('Method Not Allowed', 405, {'Allow': 'POST'})(scope, receive, send)
        # JSON-mode SDK responses wait on their response channel rather than
        # reading ASGI disconnects. Own that receive channel after the bounded
        # request body, and cancel the request/transport when its client leaves.
        body = bytearray()
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            body.extend(message.get('body', b''))
            if len(body) > 65536:
                return await PlainTextResponse('Request body is too large', 413)(scope, receive, send)
            if not message.get('more_body'):
                break
        consumed = False
        disconnected = anyio.Event()
        async def buffered_receive():
            nonlocal consumed
            if not consumed:
                consumed = True
                return {'type': 'http.request', 'body': bytes(body), 'more_body': False}
            await disconnected.wait()
            return {'type': 'http.disconnect'}
        async with anyio.create_task_group() as group:
            async def watch():
                while True:
                    if (await receive())['type'] == 'http.disconnect':
                        disconnected.set()
                        group.cancel_scope.cancel()
                        return
            group.start_soon(watch)
            try:
                await self.app(scope, buffered_receive, send)
            finally:
                group.cancel_scope.cancel()


def create_mcp_transport(service):
    """Return the /mcp route and the lifespan context that must wrap its serving.

    State lives in the service, so the transport is stateless JSON and holds no sessions.
    """
    manager = StreamableHTTPSessionManager(
        create_server(service), json_response=True, stateless=True, max_request_body_size=65536,
        security_settings=TransportSecuritySettings(allowed_hosts=LOOPBACK_HOSTS, allowed_origins=LOOPBACK_ORIGINS))
    return Route('/mcp', endpoint=PostOnly(StreamableHTTPASGIApp(manager))), manager.run
