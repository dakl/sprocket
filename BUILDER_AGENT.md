# Building parts with Sprocket

This file is for an agent that uses Sprocket to model a part: a case, a
bracket, or a model of a bought board. To change Sprocket itself, read
`CLAUDE.md` instead.

Read the two Orange Pi examples first. They show most of what is below:

- `examples/make_orangepi_zero_3w.py` models a bought board with 16 bodies.
- `examples/make_orangepi_case.py` models a printed case around that board,
  with the board as a reference body.

## Two ways to work

1. **Write a generator script (use this for a real part).** A Python script
   writes the `.sprocket` file. The script is the source. Put every dimension
   in it, with a comment that says where the number came from. Keep the script
   next to its output, and run the script again after every change.
2. **Edit a live document through MCP.** Use this to make small changes to a
   document that is open in the app, or to ask questions about it. See
   [MCP](#mcp).

If you edit a generated file through MCP or the app, the next run of the
script removes those edits. Move each edit into the script.

## The document

A `.sprocket` file is JSON:

```json
{
  "format_version": 2,
  "features": [],
  "rollback_index": 0,
  "parameters": {"pcb_l": 65},
  "appearances": [],
  "bodies": {"pcb": {"name": "PCB", "color": "#2d68b0"}},
  "revision": 0,
  "log": []
}
```

- The engine replays `features[:rollback_index]` in order. A script sets
  `rollback_index` to `len(features)`.
- Each feature has a unique `id` and a `type`. Other features refer to it by
  that id.
- A string that starts with `=` is an expression on the parameters, for
  example `"=pcb_l-hole_inset"`. Use parameters for every dimension that
  another dimension depends on. The app shows them in its Parameters panel.

## Ops

The registered ops are `sketch`, `extrude`, `extrude_face`, `revolve`, `loft`,
`sweep`, `boolean`, `fillet`, `chamfer`, `shell`, `hole`, `draft`, `split`,
`mirror`, `pattern`, `thread`, `reference`, and the construction ops `plane`,
`axis` and `point`. This list can change. To get the live list with the JSON
Schema of each op, run this command in `engine/`:

```sh
uv run --extra mcp sprocket schemas
```

Read the schema of an op before you use it. Each field has a description.

### Sketches and extrudes

- `plane` is `"XY"`, `"XZ"` or `"YZ"`, or `{"name": "XY", "offset": 7}`, or
  `{"origin": [x, y, z], "normal": [0, 0, 1], "x_dir": [1, 0, 0]}`.
- A sketch with one `rect` is the short form. For other shapes, use
  `entities` (lines, circles and arcs, each with an `id`).
- An extrude takes `profile` (a sketch id: everything in it) or `profiles`
  (regions picked by the entity ids that enclose them, with `bounded_by`).
- `distance` is in mm, and its sign sets the direction.

### Bodies

- A solid feature with no `base` starts a new body. The body id is the id of
  that feature.
- A feature with a `base` continues the body of its base. The body's shape is
  the result of its latest feature.
- **Chain the bases.** Each feature cuts or adds to the result of its own
  `base`. If three `hole` features all have `base: "pcb"`, only the last one
  stays in the part, and there is no error. Give the second `base: "<first>"`,
  and the third `base: "<second>"`.
- A `boolean` or `pattern` uses up the body of its `tool`.
- Give each body a name and a colour in `bodies`. Colour single faces with
  `appearances`: each entry is a `color` and a `select` selector. Later entries
  win.

### Selectors

`fillet`, `chamfer`, `shell`, `draft` and `appearances` find faces and edges
with a selector. A selector is calculated again on each rebuild, so it does not
break when the face numbers change.

```json
{"select": [{"op": "edges"}, {"op": "at_end", "axis": "Z", "end": "max"}], "expected_count": 4}
```

The first step is `faces`, `edges` or `vertices`. The steps after it make the
set smaller: `filter_by`, `sort_by`, `slice`, `first`, `last`, `nearest_to`,
`nearest_each`, `at_end`, `new_since` and `within`. The full grammar is
`SELECTOR_DESCRIPTION` in `engine/src/sprocket_engine/selectors.py`.

- Add `expected_count`. If the rebuild finds a different number of items, the
  feature fails. Without it, the op can round or cut the wrong edges with no
  error.
- `new_since` (only what one feature made) and `at_end` are more stable than a
  point. Use `nearest_each` when you know the centre of each face.

### Bought parts: `reference`

A `reference` feature brings in another `.sprocket` file or a STEP file as one
body. The app shows it translucent, and the engine does not export it. Use it
to design around a board, as a boolean `tool`, or for a clash check.

- `file` is relative to the folder of the document.
- `rotate` is degrees about world X, then Y, then Z. `translate` is in mm and
  comes after the rotation. To put a part's centre on a point, translate by
  that point minus the rotated centre of the part in its own file.

## Numbers you have not measured

Mark each dimension in the script as one of: a datasheet or spec value, a
measurement (with the tool), an estimate from a photo, or a placeholder. The
Orange Pi scripts use `# spec`, `# UNMEASURED`, `# ESTIMATE` and
`# PLACEHOLDER`. Do not draw a plausible shape for a face that you have not
measured. Leave it as a clear placeholder, and tell the user which numbers
need calipers.

## Check the part

Do these checks after each change, before you tell the user the part is done.

1. **Build it.** In `engine/`:

   ```sh
   uv run --extra mcp sprocket build <doc.sprocket> --step part.step --stl-dir <existing dir>
   ```

   The command lists the bodies it made. A feature that fails does not stop the
   build: the engine exports the part up to that feature and names the feature.
   Read the output for that name.

2. **Check volumes and clashes in code.** Load the document the way the engine
   does, so that the parameters are resolved:

   ```python
   import itertools, sys
   from pathlib import Path
   from sprocket_engine.document import Document
   from sprocket_engine.replay import find_bodies, replay

   doc = Document.load(Path(sys.argv[1])).for_replay()
   _, ctx = replay(doc, return_context=True)
   bodies = find_bodies(doc["features"], ctx)
   for a, b in itertools.combinations(bodies, 2):
       volume = (a["shape"] & b["shape"]).volume
       if volume > 1e-6:
           print("clash", a["id"], b["id"], round(volume, 3))
   ```

   Run it with `uv run --directory <sprocket>/engine --extra mcp python check.py
   <doc>`. Each body has `id`, `shape` and `reference`. Compare
   `shape.volume` and `shape.bounding_box()` with the values you calculated by
   hand. That is how the lost holes in the chained-base trap above are found.
   Do not pass the raw JSON to `replay()`. It cannot read the `=` expressions.

3. **Look at it.** In `engine/`:

   ```sh
   uv run --extra mcp sprocket web <doc.sprocket> --port 8765 --no-autosave
   ```

   Then open `http://127.0.0.1:8765`. `--no-autosave` stops the viewer from
   writing to a generated file. If you hide a body in the viewer, that is a
   document edit.

   - Reference bodies are faint. Do not use a screenshot to check where a
     reference is. Print its bounding box from step 2.
   - For a screenshot, keep the mouse off the model. The viewer highlights the
     face under the pointer, and the highlight can stay after the pointer
     moves. Scroll zooms toward the centre of the view, not toward the pointer,
     so you can scroll over empty space.

## MCP

`sprocket mcp` gives an agent the document that is open in a running app or
daemon:

```sh
uv run --directory <sprocket>/engine --extra mcp sprocket mcp            # the newest running app
uv run --directory <sprocket>/engine --extra mcp sprocket mcp --socket <path>
```

- The engine makes one `add_<op>` tool for each registered op from that op's
  schema. An op that is added later gets a tool automatically.
- The document tools include `get_document`, `get_timeline`, `get_bodies`,
  `apply_edit`, `set_parameter`, `set_rollback`, `set_body`,
  `set_appearances`, `export`, and the `sketch_*` tools for single sketch
  entities and constraints.
- To ask about the geometry, use `describe_geometry`, then `measure`, then
  `render_view`. `check_clearance` and `section` are also available.
  `get_selection` returns what the user picked in the app, and `highlight`
  shows the user a face or an edge.
- The app and MCP share one undo history. The user can undo your edit.
