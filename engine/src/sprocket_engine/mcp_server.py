"""MCP over stdio, in front of a running daemon.

This holds no state. Every tool is a JSON-RPC call to the daemon on its
socket, so an agent edits the same document the app has open, and the user
watches the part change while the agent works. A second engine here would put
the agent in a private copy nobody can see, which is the one thing this
milestone exists to avoid.

The per-op tools are generated from the op registry's JSON Schemas. An op
added later is agent-callable the moment it registers, with its own typed
parameters, without anything in this file changing. That is why the schemas
live on the ops rather than in a table here.

`render_view` returns the PNG itself rather than a path. An agent that cannot
see its own work will confidently report a part it has never looked at.
"""

import asyncio
import base64
import json
import tempfile
from pathlib import Path

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from .ops import REGISTRY
from .rpc import Client, RpcError

SERVER_NAME = "sprocket"
SERVER_VERSION = "0.1.0"

AUTHOR = "agent:mcp"

INSTRUCTIONS = """Sprocket is a parametric CAD modeller. A document is an ordered list of
features replayed in order, exactly like a Fusion 360 timeline.

Work in this order: get_timeline to see what is there, preview_feature to
check a feature builds, then add_<op> to commit it. After any change, call
render_view and look at the image, then measure to check the numbers.

Every feature is named by an id, and later features reference earlier ones by
that id, never by a face or edge number. Face handles like f3 from
describe_geometry are valid only for the revision you read them at.

Edits are optimistic. Pass the base_revision you last read, and a rejected
edit comes back with the current document so you can rebase."""

# Neither belongs to the op, so they are added to every generated tool.
EDIT_PROPERTIES = {
    "index": {
        "type": "integer",
        "description": "where in the timeline to insert it (default: at the rollback bar)",
    },
    "base_revision": {
        "type": "integer",
        "description": "the revision you last read; omit only when nothing else is editing",
    },
}


def op_tool_schema(schema: dict) -> dict:
    """An op's schema as tool parameters.

    `type` goes: the tool name already fixes it, and an agent that has to
    restate it gets it wrong. `id` stops being required, because an agent
    inventing unique names across a long session collides with itself and the
    daemon can name a feature perfectly well.
    """
    properties = {
        name: value for name, value in schema.get("properties", {}).items() if name != "type"
    }
    required = [name for name in schema.get("required", []) if name not in ("type", "id")]
    # `description` goes too: it is already the tool's own description, and an
    # agent pays for every copy of it in the listing.
    dropped = ("properties", "required", "type", "description")
    tool_schema = {key: value for key, value in schema.items() if key not in dropped}
    tool_schema.update(
        {
            "type": "object",
            "properties": {**properties, **EDIT_PROPERTIES},
            "required": required,
        }
    )
    return tool_schema


def _tool(name: str, description: str, properties: dict, required: list[str] | None = None):
    return types.Tool(
        name=name,
        description=description,
        input_schema={
            "type": "object",
            "properties": properties,
            "required": required or [],
        },
    )


HANDLES = {
    "type": "array",
    "items": {"type": "string"},
    "description": "face or edge handles from describe_geometry, e.g. ['f2', 'e7']",
}


def document_tools() -> list[types.Tool]:
    return [
        _tool("get_document", "The whole document: features, parameters, rollback index, revision.", {}),
        _tool(
            "get_timeline",
            "The feature list in order, with which features are built and who added each one. Start here.",
            {},
        ),
        _tool(
            "get_profiles",
            "The closed regions of one sketch, each named by the ids of the entities that bound it. "
            "Copy a region's bounded_by (and inside, if two regions share it) into an extrude's profiles.",
            {"feature": {"type": "string", "description": "id of a sketch feature"}},
            ["feature"],
        ),
        _tool(
            "describe_geometry",
            "Every face and edge of the built part, with a handle and a sentence describing it.",
            {},
        ),
        _tool(
            "get_selection",
            "What the person has selected in the viewer right now: face and edge handles with a sentence "
            "each, and sketch ids. Read it when they say 'this' or 'these'. If stale is true the part was "
            "rebuilt since they picked, so ask them to pick again.",
            {},
        ),
        _tool(
            "resolve_selection",
            "Turns face or edge handles into a selector that survives edits, for a fillet's edges or an "
            "extrude_face's face. With no handles it uses the person's current selection. The reply's "
            "strategy says how robust it is: new_since and structure follow the geometry, position only "
            "follows where it is.",
            {"handles": HANDLES},
        ),
        _tool(
            "highlight",
            "Shows faces, edges or sketches in the person's viewer so they can see what you mean. "
            "An empty list clears it. It does not change their selection.",
            {
                "handles": HANDLES,
                "sketches": {"type": "array", "items": {"type": "string"}, "description": "sketch feature ids"},
            },
        ),
        _tool(
            "measure",
            "Volume, area and bounding box of the whole part, or of the handles you name. "
            "Two handles give the distance between their centres.",
            {"handles": HANDLES},
        ),
        _tool(
            "render_view",
            "A picture of the built part. Look at it after every change.",
            {
                "camera": {
                    "type": "string",
                    "description": "iso, front, back, left, right, top or bottom",
                    "default": "iso",
                },
                "highlight": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "face handles to paint orange, e.g. ['f0']",
                },
                "width": {"type": "integer", "default": 640},
                "height": {"type": "integer", "default": 480},
            },
        ),
        _tool(
            "preview_feature",
            "Builds a feature without committing it, and reports whether it would work. "
            "Use this before adding anything you are not sure about.",
            {
                "feature": {"type": "object", "description": "a complete feature, including its type"},
                "index": EDIT_PROPERTIES["index"],
            },
            ["feature"],
        ),
        _tool(
            "set_rollback",
            "Moves the rollback bar. Features at or after this index stop being built.",
            {"index": {"type": "integer"}, "base_revision": EDIT_PROPERTIES["base_revision"]},
            ["index"],
        ),
        _tool(
            "set_parameter",
            "Sets a named parameter. Features refer to it as '=name', and an expression "
            "like '=width / 2' may reference other parameters. A null value removes it.",
            {
                "name": {"type": "string"},
                "value": {
                    "description": "a number, or an expression string starting with '='",
                    "type": ["number", "string", "null"],
                },
                "base_revision": EDIT_PROPERTIES["base_revision"],
            },
            ["name"],
        ),
        _tool(
            "apply_edit",
            "The general edit path: a list of insert, update, delete, move, set_rollback, set_parameter or "
            "edit_sketch edits applied as one unit. Prefer the add_* tools for adding features.",
            {
                "edits": {"type": "array", "items": {"type": "object"}},
                "base_revision": EDIT_PROPERTIES["base_revision"],
            },
            ["edits"],
        ),
        _tool(
            "export",
            "Writes the built part to STEP, STL, or the viewer's mesh format.",
            {
                "step": {"type": "string", "description": "path to write STEP to"},
                "stl": {"type": "string", "description": "path to write STL to"},
                "mesh": {"type": "string", "description": "path to write the viewer mesh to"},
            },
        ),
    ]


SKETCH_TOOL_PROPERTIES = {
    "feature": {"type": "string", "description": "id of a sketch feature made of lines, arcs and circles"},
    "base_revision": EDIT_PROPERTIES["base_revision"],
}
CONSTRAINT_ID = {"type": "string", "description": "a constraint id from the sketch, such as 'c3'"}
ENTITY_ID = {"type": "string", "description": "an entity id from the sketch, such as 'e2'"}
CHANGES = {"type": "object", "description": "the fields to set. The id and the kind cannot change."}


def sketch_tools() -> list[types.Tool]:
    """Edits inside one sketch. Each one solves the sketch and returns the solved geometry."""
    # Prefixed `sketch_` because every tool that starts with `add_` is an op.
    common = "The sketch is solved after the edit, and the reply holds the solved sketch and its degrees of freedom (dof). "
    return [
        _tool(
            "sketch_add_constraint",
            common + "A constraint with no solution is refused, and the reply names the ones that conflict.",
            {**SKETCH_TOOL_PROPERTIES, "constraint": {"type": "object", "description": "{kind, and a, b or e, and value for a dimension}"}},
            ["feature", "constraint"],
        ),
        _tool(
            "sketch_update_constraint",
            common + "Use it to change a dimension's value.",
            {**SKETCH_TOOL_PROPERTIES, "constraint_id": CONSTRAINT_ID, "changes": CHANGES},
            ["feature", "constraint_id", "changes"],
        ),
        _tool(
            "sketch_remove_constraint",
            common,
            {**SKETCH_TOOL_PROPERTIES, "constraint_id": CONSTRAINT_ID},
            ["feature", "constraint_id"],
        ),
        _tool(
            "sketch_add_entity",
            common + "Join it to the rest with sketch_add_constraint.",
            {**SKETCH_TOOL_PROPERTIES, "entity": {"type": "object", "description": "a line {p0, p1}, an arc {c, r, a0, a1} or a circle {c, r}, with kind"}},
            ["feature", "entity"],
        ),
        _tool(
            "sketch_update_entity",
            common + "The solver moves the entity to the nearest place that obeys the constraints.",
            {**SKETCH_TOOL_PROPERTIES, "entity_id": ENTITY_ID, "changes": CHANGES},
            ["feature", "entity_id", "changes"],
        ),
        _tool(
            "sketch_remove_entity",
            common + "Constraints that mention the entity go with it.",
            {**SKETCH_TOOL_PROPERTIES, "entity_id": ENTITY_ID},
            ["feature", "entity_id"],
        ),
    ]


def op_tools() -> list[types.Tool]:
    """One tool per registered op, described by its own schema.

    The description comes from the schema rather than the apply function's
    docstring. A docstring is written for whoever maintains the op, and the
    first one I generated a tool from opened "Operands are feature ids,
    because ...", which tells an agent nothing about what a boolean does.
    """
    return [
        types.Tool(
            name=f"add_{name}",
            description=op.schema.get("description", f"Adds a {name} feature to the timeline."),
            input_schema=op_tool_schema(op.schema),
        )
        for name, op in sorted(REGISTRY.items())
    ]


def _next_id(client: Client, op_name: str) -> str:
    """A short id the agent did not have to invent, unique in this document."""
    existing = {feature.get("id") for feature in client.call("get_document")["features"]}
    index = 1
    while f"{op_name}{index}" in existing:
        index += 1
    return f"{op_name}{index}"


SKETCH_TOOL_NAMES = {tool.name for tool in sketch_tools()}


def call(client: Client, name: str, arguments: dict) -> list:
    """Runs one tool against the daemon and returns MCP content."""
    if name in SKETCH_TOOL_NAMES:
        return _json_content(client.call(name[len("sketch_"):], author=AUTHOR, **arguments))

    if name.startswith("add_"):
        op_name = name[len("add_"):]
        if op_name not in REGISTRY:
            raise ValueError(f"no op named {op_name!r}")
        feature = {key: value for key, value in arguments.items() if key not in EDIT_PROPERTIES}
        feature["type"] = op_name
        feature.setdefault("id", _next_id(client, op_name))
        edit = {"op": "insert", "feature": feature}
        if "index" in arguments:
            edit["index"] = arguments["index"]
        return _json_content(
            client.call(
                "apply_edit",
                edits=[edit],
                base_revision=arguments.get("base_revision"),
                author=AUTHOR,
            )
        )

    if name == "render_view":
        return _render(client, arguments)

    if name in ("apply_edit", "set_rollback", "set_parameter"):
        return _json_content(client.call(name, author=AUTHOR, **arguments))

    if name in ("get_document", "get_timeline", "get_profiles", "describe_geometry", "get_selection", "resolve_selection", "highlight", "measure", "preview_feature", "export"):
        return _json_content(client.call(name, **arguments))

    raise ValueError(f"no tool named {name!r}")


def _render(client: Client, arguments: dict) -> list:
    """Renders to a temporary file, then hands back the bytes.

    The daemon writes PNGs to a path because the Swift app wants a file on
    disk. An agent wants pixels in the reply, and a path it would have to read
    back is a tool call it may not have a tool for.
    """
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "view.png"
        info = client.call("render_view", path=str(path), **arguments)
        data = base64.b64encode(path.read_bytes()).decode()
    summary = dict(info)
    summary.pop("path", None)
    return [
        types.ImageContent(type="image", data=data, mime_type="image/png"),
        types.TextContent(type="text", text=json.dumps(summary)),
    ]


def _json_content(result) -> list:
    return [types.TextContent(type="text", text=json.dumps(result, indent=1, default=str))]


def build_server(client: Client) -> Server:
    async def on_list_tools(ctx, params):
        return types.ListToolsResult(tools=document_tools() + sketch_tools() + op_tools())

    async def on_call_tool(ctx, params):
        try:
            # The client is blocking, and blocking the event loop would stall
            # the stdio transport for the whole of a replay.
            content = await asyncio.to_thread(call, client, params.name, params.arguments or {})
            return types.CallToolResult(content=content)
        except RpcError as exc:
            # A refused edit is the agent's to fix, not a transport failure, so
            # it comes back as tool content with the daemon's own message and
            # whatever state the daemon attached.
            payload = {"error": str(exc), "code": exc.code}
            if exc.data:
                payload["data"] = exc.data
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=json.dumps(payload, indent=1, default=str))],
                is_error=True,
            )
        except Exception as exc:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=f"{type(exc).__name__}: {exc}")],
                is_error=True,
            )

    return Server(
        SERVER_NAME,
        version=SERVER_VERSION,
        instructions=INSTRUCTIONS,
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )


def run(socket_path: Path) -> int:
    client = Client(Path(socket_path))
    server = build_server(client)

    async def main():
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    try:
        asyncio.run(main())
    finally:
        client.close()
    return 0
