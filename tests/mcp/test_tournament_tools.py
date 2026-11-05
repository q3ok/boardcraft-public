"""Tournament MCP operations use the same dormant service."""

import asyncio
import json

from boardcraft.mcp.server import create_server
from boardcraft.server.service import PlayService
from mcp.client import Client


def test_mcp_tournament_create_status_delete(tmp_path):
    service = PlayService(tmp_path)

    async def run():
        async with Client(create_server(service)) as client:
            names = {t.name for t in (await client.list_tools()).tools}
            assert {
                "create_tournament",
                "start_tournament",
                "get_tournament",
                "stop_tournament",
                "restart_tournament",
                "delete_tournament",
                "fit_tournament_ratings",
                "get_tournament_ratings",
                "get_tournament_match_replay",
            } <= names
            body = {
                "name": "Fixture",
                "config": {"game_id": "dice_circuit", "variant": "base", "players": 1},
                "rules_version": "1",
                "engine_version": "1",
                "seats": ["random"],
                "seeds": [11],
                "sampler_seeds": [[21]],
                "rotations": [0],
                "match_count": 1,
                "schedule_cap": 1,
                "max_inflight": 1,
                "max_record_bytes": 1000000,
                "max_decisions": 1000,
            }
            response = await client.call_tool("create_tournament", body)
            assert not response.is_error
            made = json.loads(response.content[0].text)
            response = await client.call_tool(
                "get_tournament", {"tournament_id": made["tournament_id"]}
            )
            assert not response.is_error
            assert json.loads(response.content[0].text)["status"] == "pending"
            response = await client.call_tool(
                "delete_tournament",
                {
                    "tournament_id": made["tournament_id"],
                    "credential": made["creator_credential"],
                    "revision": 0,
                },
            )
            assert not response.is_error

    asyncio.run(run())
