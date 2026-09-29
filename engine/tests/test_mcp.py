"""The MCP adapter, driven the way an agent drives it.

These go through a real MCP client session over an in-memory transport rather
than calling `call()` directly, because most of what this layer can get wrong
is in the shapes it hands the protocol: a tool schema the client rejects, a
refused edit raised instead of returned, an image that never gets encoded.
"""

import asyncio
import base64
import json

import anyio
import pytest
from mcp import ClientSession
from mcp.shared.memory import create_client_server_memory_streams
from mcp import types

from sprocket_engine.mcp_server import build_server
from sprocket_engine.ops import REGISTRY
from sprocket_engine.rpc import Client

from conftest import running


def drive(daemon, work):
    """Runs one async exchange against a server wired to this daemon.

    The suite is otherwise synchronous, and an async pytest plugin is a
    dependency for four tests. One `asyncio.run` per test costs nothing.
    """
    async def main():
        client = Client(daemon.socket_path)
        server = build_server(client)
        answer = {}
        try:
            async with create_client_server_memory_streams() as (
                (client_read, client_write),
                (server_read, server_write),
            ):
                async with anyio.create_task_group() as tasks:

                    async def serve():
                        await server.run(
                            server_read,
                            server_write,
                            server.create_initialization_options(),
                            raise_exceptions=True,
                        )

                    tasks.start_soon(serve)
                    async with ClientSession(client_read, client_write) as session:
                        await session.initialize()
                        answer["value"] = await work(session)
                    tasks.cancel_scope.cancel()
        finally:
            client.close()
        return answer["value"]

    return asyncio.run(main())


def text_of(result) -> str:
    return "".join(part.text for part in result.content if isinstance(part, types.TextContent))


def test_every_registered_op_is_callable_as_a_tool(daemon):
    """An op added later has to become agent-callable without editing the
    adapter. The generated list is the only thing that makes that true."""
    tools = drive(daemon, lambda session: session.list_tools())
    names = {tool.name for tool in tools.tools}
    assert {f"add_{name}" for name in REGISTRY} <= names
    assert "render_view" in names


def test_a_generated_tool_hides_the_fields_an_agent_should_not_set(daemon):
    """`type` is fixed by the tool name, and an agent inventing unique ids
    across a long session collides with itself."""
    tools = drive(daemon, lambda session: session.list_tools())
    fillet = next(tool for tool in tools.tools if tool.name == "add_fillet")
    assert "type" not in fillet.input_schema["properties"]
    assert "id" not in fillet.input_schema["required"]
    assert "base_revision" in fillet.input_schema["properties"]


def test_every_op_tool_describes_what_the_op_does(daemon):
    """The descriptions came from the apply docstrings once, and add_boolean
    opened "Operands are feature ids, because ...", which tells an agent
    nothing. They come from the schemas now, so check they are all there."""
    tools = drive(daemon, lambda session: session.list_tools())
    for tool in tools.tools:
        if tool.name.startswith("add_"):
            assert tool.description == REGISTRY[tool.name[len("add_"):]].schema["description"]


def test_adding_a_feature_names_it_and_reports_the_build(daemon):
    async def work(session):
        return await session.call_tool(
            "add_fillet", {"base": "e1", "radius": 3, "edges": {"select": [{"op": "edges"}]}}
        )

    result = drive(daemon, work)
    assert result.is_error is not True
    built = json.loads(text_of(result))
    # The agent named no id, so the daemon picked one.
    assert built["built"]["ok"] is True
    assert built["built"]["volume"] < 12000.0


def test_render_view_returns_the_image_itself(daemon):
    """A path would be a file the agent may have no tool to read, and an agent
    that cannot see its work reports a part it has never looked at."""
    async def work(session):
        return await session.call_tool("render_view", {"camera": "iso", "width": 200, "height": 160})

    result = drive(daemon, work)
    image = next(part for part in result.content if isinstance(part, types.ImageContent))
    assert image.mime_type == "image/png"
    assert base64.b64decode(image.data)[:8] == b"\x89PNG\r\n\x1a\n"
    assert json.loads(text_of(result))["width"] == 200


def test_a_refused_edit_comes_back_as_content_not_an_exception(daemon):
    """A stale revision is the agent's to fix. Raising it out of the tool call
    loses the daemon's payload, which is the only thing that lets it rebase."""
    async def work(session):
        await session.call_tool("set_parameter", {"name": "width", "value": 40})
        return await session.call_tool(
            "add_sketch",
            {"plane": "XY", "circle": {"radius": 5}, "base_revision": 0},
        )

    result = drive(daemon, work)
    assert result.is_error is True
    payload = json.loads(text_of(result))
    assert payload["code"] == -32001
    assert payload["data"]["current"] == 1
    assert payload["data"]["document"]["parameters"] == {"width": 40}


def test_a_feature_the_engine_rejects_is_reported_not_raised(daemon):
    async def work(session):
        return await session.call_tool("add_extrude", {"profile": "nope", "distance": 10})

    result = drive(daemon, work)
    assert result.is_error is True
    assert "nope" in text_of(result)


def test_the_document_is_untouched_by_a_preview(daemon, client):
    async def work(session):
        return await session.call_tool(
            "preview_feature",
            {"feature": {"id": "try", "type": "fillet", "base": "e1", "radius": 999,
                         "edges": {"select": [{"op": "edges"}]}}},
        )

    result = drive(daemon, work)
    assert json.loads(text_of(result))["would_build"] is False
    assert [feature["id"] for feature in client.call("get_document")["features"]] == ["s1", "e1"]
