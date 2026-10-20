// M2 scope: a static viewport for one exported STL. No picking, no
// timeline, no live engine connection yet — see docs/SPEC.md M3+.
// Every colour written in this file is an sRGB hex, the way a CSS colour
// or a design tool states it. The renderer's output encoding is sRGB (see
// renderer setup below), so three.js treats a material colour as linear
// light and encodes it on the way out. Passing a raw hex therefore
// brightens it. srgb() does the one conversion that keeps what you write
// and what you see the same. The view cube renders through cubeRenderer,
// which has no output encoding, so its colours stay raw hexes.
const srgb = (hex) => new THREE.Color(hex).convertSRGBToLinear();
// What a face with no colour from the document is drawn in.
const PART_COLOUR = 0x5b_8d_ee;
const REFERENCE_COLOUR = 0xb0_b4_ba;

const scene = new THREE.Scene();
// Not srgb(): r128 clears the framebuffer with this colour directly and
// skips outputEncoding for it, so converting here would darken it twice.
scene.background = new THREE.Color(0x1e_1e_1e);

// Persistent outlines for sketch features: a bare sketch has no solid to
// show (see sprocket_engine.cli._last_solid_index), so this outline is
// its only visible trace until something extrudes it.
const sketchOutlineGroup = new THREE.Group();
scene.add(sketchOutlineGroup);

// --- Section view ------------------------------------------------------
// A clipping plane across one world axis. three.js keeps what lies on the
// plane normal's side, so the normal points back toward what stays. The cut
// face is the part's own back faces, seen through the cut and drawn flat in
// the section colour: a closed solid shows them only where it is cut open.
const sectionPanelEl = document.getElementById("section-panel");
const sectionOffsetEl = document.getElementById("section-offset");
const sectionValueEl = document.getElementById("section-value");
const toolSectionEl = document.getElementById("tool-section");
const sectionPlane = new THREE.Plane();
const SECTION_AXES = {
  X: new THREE.Vector3(1, 0, 0),
  Y: new THREE.Vector3(0, 1, 0),
  Z: new THREE.Vector3(0, 0, 1),
};
let section = null; // { axis, offset, flip } while the view is on
let sectionCap = null;

function sectionRange(axis) {
  if (!mesh) {
    return [0, 0];
  }
  mesh.geometry.computeBoundingBox();
  const box = mesh.geometry.boundingBox;
  const key = axis.toLowerCase();
  return [box.min[key], box.max[key]];
}

function applySection() {
  const planes = section ? [sectionPlane] : null;
  if (section) {
    const [low, high] = sectionRange(section.axis);
    section.offset = Math.min(high, Math.max(low, section.offset));
    Object.assign(sectionOffsetEl, { min: low, max: high, value: section.offset });
    sectionValueEl.textContent = `${Number(section.offset.toFixed(2))} mm`;
    sectionPlane.normal.copy(SECTION_AXES[section.axis]).multiplyScalar(section.flip ? 1 : -1);
    sectionPlane.constant = section.flip ? -section.offset : section.offset;
    sectionPanelEl
      .querySelectorAll("button[data-axis]")
      .forEach((button) => button.classList.toggle("active", button.dataset.axis === section.axis));
  }
  renderer.localClippingEnabled = Boolean(section);
  const clipped = [
    mesh?.material,
    edgeLines?.material,
    ...threadGroup.children.map((line) => line.material),
  ];
  clipped
    .flat()
    .filter(Boolean)
    .forEach((material) => {
      material.clippingPlanes = planes;
      material.needsUpdate = true;
    });
  if (sectionCap) {
    scene.remove(sectionCap);
    [].concat(sectionCap.material).forEach((material) => material.dispose());
    sectionCap = null;
  }
  if (section && mesh) {
    const cap = new THREE.MeshBasicMaterial({
      color: srgb(0xc8_78_3c),
      side: THREE.BackSide,
      clippingPlanes: planes,
    });
    const none = new THREE.MeshBasicMaterial({ visible: false });
    // Same slots as the part's materials: 1 is a hidden body and 2 a reference body, which get no cap.
    const materials = Array.isArray(mesh.material)
      ? mesh.material.map((_, index) => (index === 1 || index === 2 ? none : cap))
      : cap;
    sectionCap = new THREE.Mesh(mesh.geometry, materials);
    sectionCap.raycast = () => {};
    scene.add(sectionCap);
  }
  toolSectionEl.classList.toggle("active", Boolean(section));
  sectionPanelEl.style.display = section ? "flex" : "none";
}

function setSectionAxis(axis) {
  const [low, high] = sectionRange(axis);
  section = { axis, offset: (low + high) / 2, flip: section ? section.flip : false };
  applySection();
}
toolSectionEl.addEventListener("click", () => {
  if (section) {
    section = null;
    applySection();
  } else {
    setSectionAxis("Z");
  }
});
sectionPanelEl
  .querySelectorAll("button[data-axis]")
  .forEach((button) => button.addEventListener("click", () => setSectionAxis(button.dataset.axis)));
sectionOffsetEl.addEventListener("input", () => {
  section.offset = Number(sectionOffsetEl.value);
  applySection();
});
document.getElementById("section-flip").addEventListener("click", () => {
  section.flip = !section.flip;
  applySection();
});

// Cosmetic threads leave the solid alone, so a helix over the face is
// their only visible trace. Rebuilt on every features.json load.
const threadGroup = new THREE.Group();
scene.add(threadGroup);

// One entry per drawn sketch: { feature, line, fill, handles }. The fill is
// an invisible mesh over the polygon, so a click inside the sketch picks
// it. handles are the corner markers, shown only while it is selected.
let sketchOutlines = [];

// Planes, axes and points the document names: one object each, drawn from
// features.json's `construct` data. Rebuilt on every load, like the sketches.
const constructGroup = new THREE.Group();
scene.add(constructGroup);
let constructObjects = []; // { id, object }
let constructFramed = false; // a document of construct features alone is framed once, since there is no part to frame on
const hiddenConstructIDs = new Set();

const camera = new THREE.PerspectiveCamera(
  45,
  globalThis.innerWidth / globalThis.innerHeight,
  0.1,
  10_000,
);
camera.up.set(0, 0, 1); // Z-up, matching the engine's convention

console.log("renderer init, size", globalThis.innerWidth, globalThis.innerHeight);
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(globalThis.devicePixelRatio || 1);
renderer.setSize(globalThis.innerWidth, globalThis.innerHeight);
renderer.outputEncoding = THREE.sRGBEncoding;
document.body.appendChild(renderer.domElement);
console.log("renderer ready");

const controls = new THREE.OrbitControls(camera, renderer.domElement);

// A studio rig, not a single lamp. The old ambient-plus-one-directional
// pair summed to 1.4 on any face turned towards the key, so the body
// colour's blue channel ran past 1.0 and clipped: every lit face came
// back a flat #6ba5ff with no gradation left in it. These three sum to
// 1.15 at most, and each comes from a different direction, so
// neighbouring faces of a box read as different tones. Measured on
// examples/box.sprocket: face luminance now spans 0.24 to 0.48 with no
// channel at 255. Before, it spanned 0.30 to 0.65 with the top faces
// clipped. Fusion 360 spans 0.18 to 0.32 on the same shape, but it
// separates faces with black edge lines rather than with light.
const hemisphere = new THREE.HemisphereLight(0xff_ff_ff, 0x20_24_2b, 0.18);
scene.add(hemisphere);

const keyLight = new THREE.DirectionalLight(0xff_ff_ff, 0.85);
keyLight.position.set(2, -3, 4); // over the viewer's right shoulder
scene.add(keyLight);

const fillLight = new THREE.DirectionalLight(0xff_ff_ff, 0.12);
fillLight.position.set(-4, 1.5, 0.5); // opposite the key, to lift the shadow side
scene.add(fillLight);

const grid = new THREE.GridHelper(200, 20, 0x44_44_44, 0x33_33_33);
grid.rotation.x = Math.PI / 2; // lie flat in the XY plane
// GridHelper bakes its two colours into a vertex-colour attribute, so it
// cannot take a srgb() Color. Convert the attribute in place instead.
// r128 keeps its sRGB transfer function module-private, so inline it.
const gridColors = grid.geometry.attributes.color.array;
for (let i = 0; i < gridColors.length; i++) {
  const c = gridColors[i];
  gridColors[i] = c < 0.040_45 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}
scene.add(grid);

// The world axes, hidden until the browser tree turns them on. They run as
// far as the grid. While a sketch is open, a visible axis that lies in its
// plane is something to snap to (see sketcher.js).
const AXES = {
  x: { name: "X", direction: new THREE.Vector3(1, 0, 0), colour: 0xff_5a_5a },
  y: { name: "Y", direction: new THREE.Vector3(0, 1, 0), colour: 0x5a_d2_5a },
  z: { name: "Z", direction: new THREE.Vector3(0, 0, 1), colour: 0x5a_9b_ff },
};
const axesVisible = { x: false, y: false, z: false };
const axisLines = {};
for (const [key, axis] of Object.entries(AXES)) {
  const reach = axis.direction.clone().multiplyScalar(100);
  axisLines[key] = new THREE.Line(
    new THREE.BufferGeometry().setFromPoints([reach.clone().negate(), reach]),
    new THREE.LineBasicMaterial({ color: srgb(axis.colour) }),
  );
  axisLines[key].visible = false;
  scene.add(axisLines[key]);
}

function frameCameraOn(object) {
  const box = new THREE.Box3().setFromObject(object);
  const size = box.getSize(new THREE.Vector3());
  const center = box.getCenter(new THREE.Vector3());
  const radius = size.length() * 0.6 || 50;

  camera.position.set(center.x + radius, center.y + radius, center.z + radius);
  camera.near = radius / 100;
  camera.far = radius * 100;
  camera.updateProjectionMatrix();

  controls.target.copy(center);
  controls.update();
}

const params = new URLSearchParams(globalThis.location.search);
const partBase = params.get("part") || "part.mesh";
const featuresBase = "features.json";

let mesh = null;
let edgeLines = null;
// In-flight loads and commands, and the revision on screen. Declared here
// because loadPart() runs at startup, before the transport block below.
let pendingCount = 0;
let loadedRevision = null;
// The newest revision this page has heard of, from a reply or an event, which is what an edit is based on.
let daemonRevision = 0;
const sawRevision = (revision) => {
  if (Number.isInteger(revision) && revision > daemonRevision) {
    daemonRevision = revision;
  }
};

function urlWithCacheBust(base, token) {
  if (!token) {
    return base;
  }
  return `${base + (base.includes("?") ? "&" : "?")}t=${token}`;
}

// --- Mesh file (see engine/src/sprocket_engine/mesh.py) -------------
// uint32 header length, then that many bytes of UTF-8 JSON, then the
// buffers. A buffer's offset is relative to the payload, which starts
// right after the header.
const MESH_ARRAY_TYPES = { float32: Float32Array, uint32: Uint32Array };

function parseMesh(buffer) {
  const headerLength = new DataView(buffer).getUint32(0, true);
  const headerBytes = new Uint8Array(buffer, 4, headerLength);
  // The header is zero-padded to keep the payload 4-aligned. JSON.parse
  // chokes on the padding, so stop at the first NUL.
  const nul = headerBytes.indexOf(0);
  const text = new TextDecoder().decode(nul === -1 ? headerBytes : headerBytes.subarray(0, nul));
  const header = JSON.parse(text);
  // Version 2 adds face_materials; everything else is version 1.
  if (header.format !== "sprocket-mesh" || (header.version !== 1 && header.version !== 2)) {
    throw new Error(`unexpected mesh format ${header.format} v${header.version}`);
  }

  const payloadStart = 4 + headerLength;
  const buffers = {};
  for (const [name, entry] of Object.entries(header.buffers)) {
    const ArrayType = MESH_ARRAY_TYPES[entry.type];
    if (!ArrayType) {
      throw new Error(`unknown buffer type ${entry.type} for ${name}`);
    }
    // A view, not a copy: the whole point of the 4-alignment.
    buffers[name] = new ArrayType(buffer, payloadStart + entry.offset, entry.length);
  }
  return { header: header, buffers: buffers };
}

// One geometry group per B-rep face, in the order the engine's
// shape.faces() returns them. With a single material r128 ignores the
// groups at render time, so they cost one draw call total, not one per
// face. Picking reads them: a group index is a B-rep face index.
function buildPartGeometry(mesh) {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(mesh.buffers.vertices, 3));
  // OCCT's own surface normals. The STL path had to call
  // computeVertexNormals(), which averages triangle normals and so rounds
  // the shading off a curved face.
  geometry.setAttribute("normal", new THREE.BufferAttribute(mesh.buffers.normals, 3));
  geometry.setIndex(new THREE.BufferAttribute(mesh.buffers.indices, 1));

  // faceOfTriangle inverts the groups. A raycast hands back a triangle
  // index and picking needs the face, which would otherwise be a search
  // through the groups on every pointermove. On the geometry rather than in
  // a global so it goes when the geometry is disposed.
  const faceOfTriangle = new Uint32Array(mesh.header.counts.triangles);
  let triangleStart = 0;
  for (let faceIndex = 0; faceIndex < mesh.buffers.triangles_per_face.length; faceIndex++) {
    const triangleCount = mesh.buffers.triangles_per_face[faceIndex];
    geometry.addGroup(triangleStart * 3, triangleCount * 3, 0);
    faceOfTriangle.fill(faceIndex, triangleStart, triangleStart + triangleCount);
    triangleStart += triangleCount;
  }
  geometry.userData.faceOfTriangle = faceOfTriangle;
  // Bodies, in face order (see mesh.py). A hidden body draws with an
  // invisible material and a reference body translucent, and neither is
  // pickable. Material 0 is the part, 1 hidden, 2 reference.
  const faceCount = mesh.buffers.triangles_per_face.length;
  geometry.userData.unpickableFace = new Uint8Array(faceCount);
  geometry.userData.bodies = mesh.header.bodies || [];
  let bodyFaceStart = 0;
  for (const body of geometry.userData.bodies) {
    const materialIndex = body.reference ? 2 : body.visible === false ? 1 : 0;
    for (let face = bodyFaceStart; face < bodyFaceStart + body.faces; face++) {
      geometry.groups[face].materialIndex = materialIndex;
      if (materialIndex) {
        geometry.userData.unpickableFace[face] = 1;
      }
    }
    bodyFaceStart += body.faces;
  }
  // Finishes: one more material per distinct (roughness, metalness), from
  // index 3. Only drawn part faces take one; hidden and reference keep theirs.
  geometry.userData.finishes = [];
  const finishes = mesh.buffers.face_materials;
  if (finishes) {
    const indexOf = new Map();
    for (let face = 0; face < faceCount; face++) {
      const roughness = finishes[face * 2];
      if (roughness < 0 || geometry.groups[face].materialIndex !== 0) {
        continue;
      }
      const key = `${roughness},${finishes[face * 2 + 1]}`;
      if (!indexOf.has(key)) {
        indexOf.set(key, 3 + geometry.userData.finishes.length);
        geometry.userData.finishes.push([roughness, finishes[face * 2 + 1]]);
      }
      geometry.groups[face].materialIndex = indexOf.get(key);
    }
  }
  geometry.userData.faceFinishes = finishes || null;
  const faceColours = mesh.buffers.face_colors;
  if (faceColours) {
    // One colour per vertex, because ocp-tessellate gives every face its
    // own vertices. 0xFFFFFFFF means the document named no colour.
    const colours = new Float32Array(mesh.buffers.vertices.length);
    const indices = mesh.buffers.indices;
    for (let triangle = 0; triangle < faceOfTriangle.length; triangle++) {
      const hex = faceColours[faceOfTriangle[triangle]];
      const colour = srgb(hex === 0xff_ff_ff_ff ? PART_COLOUR : hex);
      for (let corner = 0; corner < 3; corner++) {
        const vertex = indices[triangle * 3 + corner] * 3;
        colours[vertex] = colour.r;
        colours[vertex + 1] = colour.g;
        colours[vertex + 2] = colour.b;
      }
    }
    geometry.setAttribute("color", new THREE.BufferAttribute(colours, 3));
  }
  geometry.computeBoundingBox();
  geometry.computeBoundingSphere();
  return geometry;
}

/// A soft studio environment for finished faces, made once: a sky
/// gradient with two bright panels, prefiltered by PMREM. Faces with no
/// finish do not use it, so their shading is as before.
let environmentTexture = null;
function environmentMap() {
  if (environmentTexture) {
    return environmentTexture;
  }
  const room = new THREE.Scene();
  const sky = new THREE.SphereGeometry(50, 32, 16);
  const tones = new Float32Array(sky.attributes.position.count * 3);
  for (let k = 0; k < sky.attributes.position.count; k++) {
    const up = (sky.attributes.position.getZ(k) / 50 + 1) / 2; // +Z is up, as in the viewport
    const tone = 0.08 + 0.55 * up * up;
    tones.set([tone, tone, tone * 1.05], k * 3);
  }
  sky.setAttribute("color", new THREE.BufferAttribute(tones, 3));
  room.add(
    new THREE.Mesh(sky, new THREE.MeshBasicMaterial({ vertexColors: true, side: THREE.BackSide })),
  );
  for (const [x, y, z] of [
    [30, 20, 25],
    [-35, -10, 15],
  ]) {
    const panel = new THREE.Mesh(
      new THREE.PlaneGeometry(30, 18),
      new THREE.MeshBasicMaterial({ color: 0xff_ff_ff, side: THREE.DoubleSide }),
    );
    panel.position.set(x, y, z);
    panel.lookAt(0, 0, 0);
    room.add(panel);
  }
  const generator = new THREE.PMREMGenerator(renderer);
  environmentTexture = generator.fromScene(room, 0.02).texture;
  generator.dispose();
  return environmentTexture;
}

/// The materials that draw part faces: the plain one and every finish.
function partMaterials() {
  const all = [].concat(mesh.material);
  return all.filter((_, index) => index === 0 || index >= 3);
}

function disposePart() {
  if (mesh) {
    scene.remove(mesh);
    mesh.geometry.dispose();
    [].concat(mesh.material).forEach((material) => material.dispose());
    mesh = null;
  }
  if (edgeLines) {
    scene.remove(edgeLines);
    edgeLines.geometry.dispose();
    [].concat(edgeLines.material).forEach((material) => material.dispose());
    edgeLines = null;
  }
}

function loadPart(token, options) {
  const refit = Boolean(options?.refit);
  const path = urlWithCacheBust(partBase, token);
  return trackPending(
    fetch(path)
      // file:// fetches report status 0, not 200, so response.ok is always
      // false there.
      .then((response) => {
        if (!response.ok && response.status !== 0) {
          throw new Error(`HTTP ${response.status}`);
        }
        return response.arrayBuffer();
      })
      .then((buffer) => {
        const parsed = parseMesh(buffer);
        const counts = parsed.header.counts;
        console.log(
          "mesh loaded",
          path,
          `${counts.faces} faces,`,
          `${counts.triangles} triangles,`,
          `${counts.edges} edges`,
        );

        // Replace, don't add: a rebuild reuses this scene instead of
        // reloading the page, so the old mesh must go or it doubles up.
        const hadPart = Boolean(mesh);
        disposePart();

        // metalness 0: a metal surface takes its colour from reflections,
        // and this material has no environment map, so any metalness above 0
        // only drains the body colour towards black. Finished faces get
        // their own material with one (see environmentMap).
        //
        // polygonOffset pushes the body away from the camera by a hair so
        // the edge lines below, which sit exactly on the body's boundary,
        // win the depth test instead of flickering against it.
        const material = new THREE.MeshStandardMaterial({
          color: parsed.buffers.face_colors ? 0xff_ff_ff : srgb(PART_COLOUR),
          vertexColors: Boolean(parsed.buffers.face_colors),
          metalness: 0,
          roughness: 0.55,
          polygonOffset: true,
          polygonOffsetFactor: 1,
          polygonOffsetUnits: 1,
        });
        const partGeometry = buildPartGeometry(parsed);
        const bodies = partGeometry.userData.bodies;
        const finishes = partGeometry.userData.finishes;
        const layered =
          finishes.length > 0 || bodies.some((body) => body.reference || body.visible === false);
        const hiddenMaterial = new THREE.MeshBasicMaterial({ visible: false });
        const referenceMaterial = new THREE.MeshStandardMaterial({
          color: srgb(REFERENCE_COLOUR),
          transparent: true,
          opacity: 0.28,
          depthWrite: false,
          metalness: 0,
          roughness: 0.8,
        });
        // A finish is the part material with its own roughness and
        // metalness, lit by a generated environment so a metal has
        // something to reflect.
        const finishMaterials = finishes.map(([roughness, metalness]) => {
          const finished = material.clone();
          finished.roughness = roughness;
          finished.metalness = metalness;
          finished.envMap = environmentMap();
          finished.envMapIntensity = 0.9;
          return finished;
        });
        mesh = new THREE.Mesh(
          partGeometry,
          layered ? [material, hiddenMaterial, referenceMaterial, ...finishMaterials] : material,
        );
        mesh.userData.counts = counts;
        mesh.userData.faceColours = parsed.buffers.face_colors || null;
        scene.add(mesh);

        // The B-rep edge curves, already tessellated into consecutive point
        // pairs. This is the difference that makes the viewport read like a
        // CAD view: Fusion 360 separates faces with dark edge lines rather
        // than with lighting, and measured against its shading its face
        // tones span only about 0.14 of luma.
        const edgeGeometry = new THREE.BufferGeometry();
        edgeGeometry.setAttribute("position", new THREE.BufferAttribute(parsed.buffers.edges, 3));
        // srgb() like every other main-scene colour. Being unlit buys no
        // exemption: outputEncoding encodes the final fragment whether a
        // material samples lights or not. Measured, a raw hex here comes
        // out #2a2c2f rather than #14181f, light enough to read as a grey
        // seam instead of an edge.
        const edgeMaterial = new THREE.LineBasicMaterial({ color: srgb(0x14_18_1f) });
        // Edges follow their body: one group per body, material as for faces.
        const unpickableEdge = new Uint8Array(parsed.buffers.segments_per_edge.length);
        if (layered) {
          let edge = 0;
          let segment = 0;
          for (const body of bodies) {
            const materialIndex = body.reference ? 2 : body.visible === false ? 1 : 0;
            let segments = 0;
            for (let k = edge; k < edge + body.edges; k++) {
              segments += parsed.buffers.segments_per_edge[k];
            }
            edgeGeometry.addGroup(segment * 2, segments * 2, materialIndex);
            if (materialIndex) {
              unpickableEdge.fill(1, edge, edge + body.edges);
            }
            edge += body.edges;
            segment += segments;
          }
        }
        edgeLines = new THREE.LineSegments(
          edgeGeometry,
          layered
            ? [
                edgeMaterial,
                new THREE.LineBasicMaterial({ visible: false }),
                new THREE.LineBasicMaterial({
                  color: srgb(REFERENCE_COLOUR),
                  transparent: true,
                  opacity: 0.35,
                }),
              ]
            : edgeMaterial,
        );
        edgeLines.userData.unpickableEdge = unpickableEdge;
        scene.add(edgeLines);

        // The same ID map faces have, for edges: segment k belongs to B-rep
        // edge edgeOfSegment[k], and edge e owns segments edgeStart[e] up to
        // edgeStart[e + 1], in shape.edges() order (see mesh.py).
        const segmentsPerEdge = parsed.buffers.segments_per_edge;
        const edgeStart = new Uint32Array(segmentsPerEdge.length + 1);
        const edgeOfSegment = new Uint32Array(counts.segments);
        segmentsPerEdge.forEach((segmentCount, edgeIndex) => {
          edgeStart[edgeIndex + 1] = edgeStart[edgeIndex] + segmentCount;
          edgeOfSegment.fill(edgeIndex, edgeStart[edgeIndex], edgeStart[edgeIndex + 1]);
        });
        edgeLines.userData.edgeStart = edgeStart;
        edgeLines.userData.edgeOfSegment = edgeOfSegment;

        // The first solid of a document has nothing to stay framed on.
        if (refit || !hadPart) {
          frameCameraOn(mesh);
        }
        applyVisibility();
        renderBrowser();
        applySection();
        // Shell, Fillet and Hole enable on `mesh`, which was null until now.
        updateToolbarState();
        // Planes and axes are drawn to the part's size.
        renderConstructs(browserFeatures);
      })
      .catch((error) => {
        // Also fires for a genuinely empty document (rolled back before the
        // first feature): the engine deletes the mesh file rather than
        // export nothing, so clear the stale part instead of leaving the
        // last-built one on screen.
        document.title = "Sprocket viewer";
        console.log("no mesh to load (empty document, or a real load failure)", path, error);
        disposePart();
        renderBrowser();
        updateToolbarState();
      }),
  );
}

// --- Timeline (M3) --------------------------------------------------
// A sidecar JSON next to part.mesh, written by the Swift app on every
// rebuild: a plain array of {id, type}, one per feature in the document.
function loadFeatures(token) {
  return trackPending(
    fetch(urlWithCacheBust(featuresBase, token))
      // file:// fetches report status 0, not 200, so response.ok is always
      // false in the app. Treat 0 as success or the timeline never loads
      // outside a local server.
      .then((response) => (response.ok || response.status === 0 ? response.json() : []))
      .then((features) => {
        // A rebuild replaces every feature object, so a selection held from
        // before it would point at a stale one. The sketch selection is
        // re-resolved by id instead of dropped, so editing a corner leaves
        // the same sketch selected and its handles up for the next drag.
        browserFeatures = features;
        measureAsked = ""; // a rebuild changes what the same handles measure
        const selectedSketchID = selectedSketch?.id;
        selectedSketch = null;
        selectedPatch = null;
        clearEdgeSelection();
        if (modifyTool) {
          cancelModify(); // its selector was made against the old build
        }
        if (extrudeArmed) {
          cancelExtrude(); // its face is gone, and the arrow with it
        }
        cancelPick();
        selectionChanged();
        hideStatus();
        renderTimeline(features);
        renderProperties();
        renderSketchOutlines(features);
        updateToolbarState(); // Extrude and Revolve are live once there is a region to pick
        renderConstructs(features);
        renderThreads(features);
        refreshParameters();
        const stillThere = features.find(
          (feature) =>
            feature.id === selectedSketchID && feature.type === "sketch" && feature.active,
        );
        if (stillThere) {
          selectSketchForExtrude(stillThere);
        }
        reportFailedFeature(features);
        renderBrowser();
      })
      .catch((error) => console.error("failed to load features.json", error)),
  );
}

// Called from Swift (see WebView.swift) after every op edit, instead of
// reloading the page: refetches part.mesh/features.json in place so the
// camera position and zoom the user set survive a rebuild.
//
// `opened` means the engine swapped in another document. Its revision can
// equal the old one's, so its files are fetched under a fresh URL, and the
// selection, a sketch in progress and hidden sketches belonged to the old one.
//
// `info` is the rest of the daemon's `document_changed` (author, changes),
// when the change came from somewhere else. Swift and the event stream
// both pass it; the viewer's own edits call this without it.
globalThis.reloadPart = (token, opened, viewerError, info) => {
  loadedRevision = token;
  sawRevision(token);
  showMeshError(viewerError);
  if (opened) {
    resetForNewDocument();
  }
  // A new document, or the same revision rebuilt because a referenced file
  // changed: either way the old URL would serve what is already shown.
  const fetchToken = opened || info?.refresh ? `${token}-${Date.now()}` : token;
  // A rebuild renumbers faces and edges, so what an agent pointed at is gone.
  showAgentHighlight({});
  // The daemon has already carried the selection over to the new handles
  // (rpc.py's _carry_selection). Ask for it before the reload clears the
  // page's own copy, and keep that clearing from being reported back as
  // "nothing selected".
  const selection =
    opened || !canCallDaemon()
      ? Promise.resolve(null)
      : rpcCall("get_selection", {}).catch(() => null);
  restoringSelection = true;
  const loads = [
    loadPart(fetchToken, { refit: Boolean(opened) }),
    loadFeatures(fetchToken),
    selection,
  ];
  trackPending(
    Promise.all(loads)
      .then(([, , view]) => {
        if (token !== loadedRevision) {
          return; // a newer reload owns the screen
        }
        applyDaemonSelection(view);
        showChangeCue(ViewerLive.changeCue(info));
      })
      .finally(() => {
        if (token !== loadedRevision) {
          return;
        }
        restoringSelection = false;
        reportSelection();
      }),
  );
  // The app and the browser both come through here. A sketch being edited
  // may be the thing that changed, and the sketcher checks.
  if (!opened && sketchStage === "draw") {
    sketcher.remoteChanged();
  }
};

const canCallDaemon = () => hasSwiftBridge() || /^https?:$/.test(globalThis.location.protocol);

/// Selects what the daemon says is selected, if it belongs to the part on
/// screen, without telling the daemon again.
function applyDaemonSelection(view) {
  const picked = ViewerLive.selectionToApply(view, loadedRevision);
  if (!picked || !mesh) {
    return;
  }
  if (picked.face !== null && mesh.geometry.groups[picked.face] && !selectedSketch) {
    selectedPatch = facePatch(picked.face);
    setHighlight(selectedPatch, 0xff_dd_55);
  }
  if (
    picked.edge !== null &&
    edgeLines &&
    edgeLines.userData.edgeStart[picked.edge + 1] !== undefined
  ) {
    selectEdge(picked.edge);
  }
  updateToolbarState();
  renderBrowser();
}

/// "Updated by agent:…" in the status line for a moment, and a short glow
/// on each timeline row the change touched.
function showChangeCue(cue) {
  if (!cue) {
    return;
  }
  showNotice(cue.text, 2500);
  for (const id of cue.featureIds) {
    const chip = timelineEl.querySelector(`.timeline-chip[data-feature-id="${CSS.escape(id)}"]`);
    if (!chip) {
      continue;
    }
    chip.classList.remove("timeline-chip-changed");
    void chip.offsetWidth; // restart the animation when the same row changes twice
    chip.classList.add("timeline-chip-changed");
  }
}

// The file changed on disk while this window held edits it does not have.
// Neither side is dropped without the person choosing.
const conflictEl = document.getElementById("conflict");
function showConflict(params) {
  if (!params || params.resolved || !params.message) {
    conflictEl.style.display = "none";
    return;
  }
  document.getElementById("conflict-text").textContent = params.message;
  conflictEl.style.display = "flex";
}
for (const [button, keep] of [
  ["conflict-file", "file"],
  ["conflict-mine", "mine"],
]) {
  document.getElementById(button).addEventListener("click", () => {
    conflictEl.style.display = "none";
    trackPending(
      rpcCall("resolve_conflict", { keep }).then((result) => {
        sawRevision(result.revision);
        if (result.revision !== loadedRevision) {
          globalThis.reloadPart(result.revision);
        }
      }),
    );
  });
}

// Swift forwards the daemon's other notifications here; the browser gets
// them from the event stream below.
globalThis.__daemonNotification = (method, params) => {
  if (method === "document_conflict") {
    showConflict(params);
  }
};

/// The engine could not write the part as a mesh, so it deleted the mesh
/// and sent the reason. Without this the screen shows nothing and says
/// nothing, which looks like the file did not open.
function showMeshError(text) {
  meshErrorEl.textContent = text ? `Could not draw the part: ${text}` : "";
  meshErrorEl.style.display = text ? "block" : "none";
}

function resetForNewDocument() {
  if (extrudeArmed) {
    cancelExtrude();
  }
  if (modifyTool) {
    cancelModify();
  }
  if (sketchStage) {
    exitSketchMode();
  }
  hiddenSketchIDs.clear();
  clearSelection();
}

loadPart(params.get("t"), { refit: true });
loadFeatures(params.get("t"));
// The revision on screen at start, so a face handle can be sent to the
// daemon before the first change arrives. After this script has run,
// because the transport below is declared further down.
Promise.resolve().then(() => {
  if (!canCallDaemon()) {
    return;
  }
  trackPending(
    rpcCall("get_timeline", {})
      .then((timeline) => {
        sawRevision(timeline.revision);
        if (loadedRevision === null) {
          loadedRevision = timeline.revision;
        }
      })
      .catch(() => {}),
  );
});

// A chip's click sets rollback_index: an active chip rolls back to just
// before it; a greyed-out one rolls forward to just past it. Cmd+Z /
// Cmd+Shift+Z undo/redo this and every extrude, via Swift's undo stack.
const FEATURE_ICONS = {
  sketch:
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 21h8" /><path d="m15 5 4 4" /><path d="M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z" /></svg>',
  extrude:
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m18 9-6-6-6 6" /><path d="M12 3v14" /><path d="M5 21h14" /></svg>',
  extrude_face: "↕",
  shell:
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="15" rx="1" /><path d="M7 5v11h10V5" /></svg>',
  hole: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="1" /><circle cx="12" cy="12" r="3" /></svg>',
  pattern:
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="6" height="6" /><rect x="15" y="3" width="6" height="6" /><rect x="3" y="15" width="6" height="6" /><rect x="15" y="15" width="6" height="6" /></svg>',
  mirror:
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v18" stroke-dasharray="2 2" /><path d="M9 6L4 18h5z" /><path d="M15 6l5 12h-5z" /></svg>',
  sweep:
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20V12a8 8 0 0 1 8-8h8" /><ellipse cx="4" cy="20" rx="3" ry="1.5" /></svg>',
  loft: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="12" cy="19" rx="8" ry="2.5" /><ellipse cx="12" cy="5" rx="4" ry="1.5" /><path d="M4 19 8 5" /><path d="M20 19 16 5" /></svg>',
  split:
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="3" width="16" height="7" rx="1" /><rect x="4" y="14" width="16" height="7" rx="1" /><path d="M2 12h20" stroke-dasharray="2 2" /></svg>',
  draft:
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 20h18" /><path d="M6 20 9 5h6l3 15" /></svg>',
  thread:
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8 3v18" /><path d="M16 3v18" /><path d="m8 6 8 3" /><path d="m8 11 8 3" /><path d="m8 16 8 3" /></svg>',
  plane:
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m3 9 6-4 12 4-6 4z" /><path d="m3 16 6-4 12 4-6 4z" stroke-dasharray="2 2" /></svg>',
  axis: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20 20 4" stroke-dasharray="3 3" /><circle cx="4" cy="20" r="1.5" /><circle cx="20" cy="4" r="1.5" /></svg>',
  point:
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="2" /><path d="M12 3v4" /><path d="M12 17v4" /><path d="M3 12h4" /><path d="M17 12h4" /></svg>',
  reference:
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="6" width="16" height="12" rx="1" stroke-dasharray="3 3" /></svg>',
  // The same drawings as the toolbar buttons for these ops.
  boolean:
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="9" cy="12" r="6" /><circle cx="15" cy="12" r="6" /></svg>',
  revolve:
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="12" cy="12" rx="5" ry="9" /><path d="M3 12h18" /></svg>',
  fillet:
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20V10a6 6 0 0 1 6-6h10" /><path d="M4 10V4h6" stroke-dasharray="2 2" /></svg>',
  chamfer:
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20v-8l8-8h8" /><path d="M4 12V4h8" stroke-dasharray="2 2" /></svg>',
};

function renderTimeline(features) {
  const timeline = document.getElementById("timeline");
  timeline.querySelectorAll(".timeline-chip").forEach((chip) => chip.remove());
  features.forEach((feature, index) => {
    const chip = document.createElement("span");
    chip.className =
      "timeline-chip" +
      (feature.active ? "" : " timeline-chip-inactive") +
      (feature.failed ? " timeline-chip-failed" : "");
    // Icons only. The id and type are in the tooltip.
    chip.title = `${feature.id} (${feature.type})${feature.failed ? " - no longer applies" : ""}`;
    const icon = FEATURE_ICONS[feature.type] || "?";
    chip.innerHTML = `<span class="timeline-icon">${icon}</span>`;
    chip.dataset.featureId = feature.id;
    const edit = document.createElement("span");
    edit.className = "timeline-edit";
    edit.title = "Edit parameters";
    edit.innerHTML =
      '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h10M18 7h2M4 17h2M10 17h10" /><circle cx="16" cy="7" r="2" /><circle cx="8" cy="17" r="2" /></svg>';
    edit.addEventListener("click", (event) => {
      event.stopPropagation();
      openProperties(feature.id);
    });
    chip.appendChild(edit);
    chip.addEventListener("dblclick", () => {
      if (feature.type === "sketch" && feature.active && feature.feature.entities) {
        editSketch(feature);
      }
    });
    chip.addEventListener("click", () => {
      // A sketch is inert (see cli.py's _last_solid_index) - clicking one
      // selects it, for extruding or for dragging its corners, instead of
      // rolling back to before it. Rolling back past a sketch shows the
      // same part, so the chip after it is the one that does that.
      if (feature.type === "sketch" && feature.active) {
        selectSketchForExtrude(feature);
        return;
      }
      setRollback(feature.active ? index : index + 1);
    });
    timeline.appendChild(chip);
  });
  if (!draggingPlayhead) {
    placePlayhead(features.filter((feature) => feature.active).length);
  }
}

// --- Playhead -----------------------------------------------------------
// Drag it along the timeline and the part is rebuilt up to that point, so
// the model can be watched being made. Its position is a rollback index:
// the number of chips on its left.
const timelineEl = document.getElementById("timeline");
const playheadEl = document.getElementById("playhead");
let draggingPlayhead = false;
let scrubTarget = null;
let scrubbing = false;

function timelineChips() {
  return Array.from(timelineEl.querySelectorAll(".timeline-chip"));
}

/// Puts the playhead in the gap after `index` chips. offsetLeft is measured
/// from #timeline's padding edge in scroll coordinates, the same as the
/// playhead's own `left`.
function placePlayhead(index) {
  const chips = timelineChips();
  if (chips.length === 0) {
    playheadEl.style.display = "none";
    return;
  }
  const after = (chip) => chip.offsetLeft + chip.offsetWidth;
  const x =
    index === 0
      ? chips[0].offsetLeft - 3
      : index >= chips.length
        ? after(chips.at(-1)) + 3
        : (after(chips[index - 1]) + chips[index].offsetLeft) / 2;
  playheadEl.style.display = "block";
  playheadEl.style.left = `${x}px`;
}

/// How many chips lie left of the pointer: the rollback index it points at.
function indexAtPointer(clientX) {
  const x = clientX - timelineEl.getBoundingClientRect().left + timelineEl.scrollLeft;
  return timelineChips().filter((chip) => chip.offsetLeft + chip.offsetWidth / 2 < x).length;
}

/// Asks for `index`, one request at a time. A drag moves faster than the
/// engine rebuilds, so while one is running only the newest target is kept.
function scrubTo(index) {
  scrubTarget = index;
  if (scrubbing) {
    return;
  }
  scrubbing = true;
  const next = () => {
    const target = scrubTarget;
    return setRollback(target).finally(() => {
      if (scrubTarget !== target) {
        return next();
      }
      scrubbing = false;
    });
  };
  next();
}

playheadEl.addEventListener("pointerdown", (event) => {
  draggingPlayhead = true;
  playheadEl.setPointerCapture(event.pointerId);
  event.preventDefault();
});
playheadEl.addEventListener("pointermove", (event) => {
  if (!draggingPlayhead) {
    return;
  }
  const index = indexAtPointer(event.clientX);
  placePlayhead(index);
  scrubTo(index);
});
const endPlayheadDrag = () => {
  if (!draggingPlayhead) {
    return;
  }
  draggingPlayhead = false;
  placePlayhead(browserFeatures.filter((feature) => feature.active).length);
};
playheadEl.addEventListener("pointerup", endPlayheadDrag);
playheadEl.addEventListener("pointercancel", endPlayheadDrag);
globalThis.addEventListener("resize", () =>
  placePlayhead(browserFeatures.filter((feature) => feature.active).length),
);

/// Names the op the engine could not apply, if any. Editing a sketch can
/// leave a later op unbuildable - a face selector matching nothing, a cut
/// that misses - and the part on screen is then the prefix before it, so
/// the user has to be told rather than left wondering what vanished.
function reportFailedFeature(features) {
  const failed = features.find((feature) => feature.failed);
  if (!failed) {
    warningEl.style.display = "none";
    return;
  }
  warningEl.textContent =
    failed.id +
    " (" +
    failed.type +
    ") no longer applies after the change. The part shown stops before it.";
  warningEl.style.display = "block";
}

// --- Properties form --------------------------------------------------
// One form per feature, built from that op's JSON Schema. `schemas.json`
// is written by the engine (served live by `sprocket web`, generated into
// this folder by `make app`), so an op registered tomorrow gets a form
// without a viewer change.
const propertiesEl = document.getElementById("properties");
let editingFeatureId = null;
let schemas = null;

function loadSchemas() {
  if (schemas) {
    return Promise.resolve(schemas);
  }
  return trackPending(
    fetch("schemas.json").then((response) => {
      if (!response.ok && response.status !== 0) {
        throw new Error(`no schemas.json (HTTP ${response.status})`);
      }
      return response.json();
    }),
  ).then((loaded) => (schemas = loaded));
}

function openProperties(featureId) {
  editingFeatureId = featureId;
  loadSchemas()
    .then(renderProperties)
    .catch((error) => {
      editingFeatureId = null;
      console.error("cannot open the parameter form", error);
    });
}

function closeProperties() {
  editingFeatureId = null;
  renderProperties();
}

function renderProperties() {
  const entry = editingFeatureId && browserFeatures.find((item) => item.id === editingFeatureId);
  if (!entry?.feature || !schemas?.[entry.type]) {
    editingFeatureId = null;
    propertiesEl.hidden = true;
    return;
  }
  propertiesEl.hidden = false;
  ParamsForm.render(propertiesEl, {
    schema: schemas[entry.type],
    feature: entry.feature,
    onCommit: (feature) => sendCommand({ type: "update_feature", id: entry.id, feature: feature }),
    onClose: closeProperties,
  });
}

// --- Transport ------------------------------------------------------
// A gesture becomes edits (edits.js), and edits go to the daemon's
// JSON-RPC methods. The two transports differ only in how a call reaches
// it. Inside the Swift app it is a WKWebView message that Swift forwards to
// the daemon socket and answers through globalThis.__rpcReply. Over http(s) it
// is a POST to `sprocket web`. From a file:// page with no bridge there is
// nothing to call.
//
// sendCommand returns a promise that rejects with the engine's reason.
let nextRpcId = 1;
const pendingRpc = new Map();

function trackPending(promise) {
  pendingCount++;
  return promise.finally(() => {
    pendingCount--;
  });
}

const hasSwiftBridge = () => Boolean(globalThis.webkit?.messageHandlers?.sprocket);

// Called from Swift with the daemon's reply to a call made below.
globalThis.__rpcReply = (id, result, errorMessage) => {
  const call = pendingRpc.get(id);
  if (!call) {
    return;
  }
  pendingRpc.delete(id);
  if (errorMessage) {
    call.reject(new Error(errorMessage));
  } else {
    call.resolve(result);
  }
};

function rpcCall(method, params) {
  if (hasSwiftBridge()) {
    const id = nextRpcId++;
    return new Promise((resolve, reject) => {
      pendingRpc.set(id, { resolve, reject });
      globalThis.webkit.messageHandlers.sprocket.postMessage({ type: "rpc", id, method, params });
    });
  }
  return fetch("/rpc", {
    method: "POST",
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method, params }),
  })
    .then((response) => response.json())
    .then((body) => {
      if (body.error) {
        throw new Error(body.error.message);
      }
      return body.result;
    });
}

/// The document as the viewer last loaded it, which is what edits.js reads.
function documentState() {
  return {
    features: browserFeatures.map((entry) => entry.feature),
    rollbackIndex: browserFeatures.filter((entry) => entry.active).length,
  };
}

// A gesture nobody awaits, such as a timeline click, still has to say why the
// engine refused it.
globalThis.addEventListener("unhandledrejection", (event) => {
  if (!(event.reason instanceof Error)) {
    return;
  }
  warningEl.textContent = event.reason.message;
  warningEl.style.display = "block";
});

function sendCommand(message) {
  if (!hasSwiftBridge() && !/^https?:$/.test(globalThis.location.protocol)) {
    console.log("(no Swift bridge and no server, dropping)", message);
    return Promise.resolve();
  }
  let call;
  if (message.type === "undo" || message.type === "redo") {
    call = () => rpcCall(message.type, {});
  } else if (message.type === "set_appearances") {
    // Face colours: the same call an agent makes, so it is undoable and logged.
    call = () =>
      rpcCall("set_appearances", {
        appearances: message.appearances,
        author: "viewer",
        base_revision: message.base_revision,
      });
  } else if (message.type === "set_visibility") {
    // View state, not an edit: undoable, but no log entry or timeline row.
    call = () => rpcCall("set_visibility", { id: message.id, visible: message.visible });
  } else {
    let edits;
    try {
      edits = ViewerEdits.editsFor(documentState(), message);
    } catch (error) {
      return Promise.reject(error);
    }
    const based =
      message.base_revision === undefined ? {} : { base_revision: message.base_revision };
    call = () => rpcCall("apply_edit", { edits, author: "viewer", ...based });
  }
  showStatus("Rebuilding…");
  return trackPending(
    call()
      .then((result) => {
        sawRevision(result.revision);
        if (result.changed !== false && result.revision !== loadedRevision) {
          globalThis.reloadPart(result.revision);
        } else {
          hideStatus();
        }
        // The engine keeps every "=param" field the edit left alone. A field
        // the edit changed now holds a number, so say which links went.
        if (result.unsolved?.length) {
          showNotice(
            "Sketch " +
              result.unsolved.join(", ") +
              " was not solved again: the engine has no constraint solver, so its geometry did not follow the parameter",
          );
        }
        if (result.unlinked?.length) {
          const fields = result.unlinked.map((item) => `${item.field} (${item.was})`).join(", ");
          showNotice(`Replaced parameter link: ${fields}`);
        }
      })
      .catch((error) => {
        hideStatus();
        throw error;
      }),
  );
}

// An edit made somewhere else, such as an agent on the daemon socket,
// arrives here. The viewer's own edits arrive twice, once as the reply to
// its POST and once as this event, so a revision already loaded is skipped.
if (/^https?:$/.test(globalThis.location.protocol)) {
  const events = new EventSource("/events");
  events.addEventListener("highlight", (event) =>
    showAgentHighlight(JSON.parse(event.data).params),
  );
  events.addEventListener("document_changed", (event) => {
    const params = JSON.parse(event.data).params;
    const { revision, opened, viewer_error } = params;
    sawRevision(revision);
    if (opened || params.refresh || revision !== loadedRevision) {
      globalThis.reloadPart(revision, opened, viewer_error, params);
    } else {
      showMeshError(viewer_error);
    }
    if (params.conflict) {
      showConflict(params.conflict);
    }
  });
  events.addEventListener("document_conflict", (event) =>
    showConflict(JSON.parse(event.data).params),
  );
}

function setRollback(index) {
  return sendCommand({ type: "set_rollback", index: index });
}

// --- Push/pull picking (M3) ---------------------------------------
// A "patch" is one B-rep face: geometry group `faceIndex`, a contiguous
// run of triangles in shape.faces() order (see mesh.py). It used to be a
// guess. With an STL there was no face information at all, so a click
// flood-filled from the hit triangle across shared edges into neighbours
// whose winding normals matched to within a tolerance. That found flat
// faces only, merged two coplanar faces that happened to touch, and
// needed a rounded-coordinate map to decide which triangles were even
// neighbours. OCCT knows the answer, so this reads it.

/// The B-rep face under a raycast hit: which triangles are that face, and
/// the surface normal there.
///
/// `normal` is OCCT's own node normal, not a cross product of the hit
/// triangle's edges. On a planar face the two agree, measured to 1e-6 on
/// examples/box.sprocket, so the extrude axis is unchanged. On a curved face
/// the node normal is the surface's and the winding normal is one facet's.
function facePatchAt(hit) {
  const patch = facePatch(mesh.geometry.userData.faceOfTriangle[hit.faceIndex], hit.faceIndex);
  patch.anchorPoint = hit.point.clone();
  return patch;
}

/// Face `faceIndex` as a patch. Its normal is read at `triangle`, or at
/// its first triangle; its anchor is that triangle's first corner.
function facePatch(faceIndex, triangle) {
  const geometry = mesh.geometry;
  const normals = geometry.attributes.normal;
  const group = geometry.groups[faceIndex];
  const corner = geometry.index.getX((triangle === undefined ? group.start / 3 : triangle) * 3);
  const position = geometry.attributes.position;
  return {
    anchorPoint: new THREE.Vector3(
      position.getX(corner),
      position.getY(corner),
      position.getZ(corner),
    ),
    faceIndex: faceIndex,
    firstTriangle: group.start / 3,
    triangleCount: group.count / 3,
    normal: new THREE.Vector3(normals.getX(corner), normals.getY(corner), normals.getZ(corner)),
  };
}

/// A point that is on face `faceIndex`: the centroid of its largest
/// triangle. The centroid of the whole face can sit off the face, when it
/// has a hole or a concave outline.
function faceCentre(faceIndex) {
  const geometry = mesh.geometry;
  const group = geometry.groups[faceIndex];
  const position = geometry.attributes.position;
  const a = new THREE.Vector3();
  const b = new THREE.Vector3();
  const c = new THREE.Vector3();
  let best = null;
  let bestArea = -1;
  for (let t = group.start / 3; t < (group.start + group.count) / 3; t++) {
    const [i0, i1, i2] = triangleCorners(t);
    a.fromBufferAttribute(position, i0);
    b.fromBufferAttribute(position, i1);
    c.fromBufferAttribute(position, i2);
    const area = b.clone().sub(a).cross(c.clone().sub(a)).length();
    if (area > bestArea) {
      bestArea = area;
      best = a.clone().add(b).add(c).divideScalar(3);
    }
  }
  return best;
}

/// The vertex indices of triangle `t`'s three corners.
function triangleCorners(t) {
  const index = mesh.geometry.index;
  return [index.getX(t * 3), index.getX(t * 3 + 1), index.getX(t * 3 + 2)];
}

function edgeKey(a, b) {
  return a < b ? `${a}|${b}` : `${b}|${a}`;
}

/// A patch's outside edges: used by one of its triangles rather than
/// shared by two.
///
/// By vertex index, with no coordinate rounding. Within one B-rep face
/// OCCT's triangulation shares its nodes, so two triangles that meet reuse
/// the same index. The old flood fill could not assume that, because a
/// patch could span faces and the tessellation splits vertices between
/// them. Measured on examples/box.sprocket: all 18 faces share their interior
/// edges by index, and every boundary vertex has exactly two boundary
/// edges, so the loops below always close. Pinned by
/// test_a_faces_triangulation_is_index_shared_and_its_boundary_closes.
function boundaryEdges(patch) {
  const counts = new Map();
  const edges = [];
  const lastTriangle = patch.firstTriangle + patch.triangleCount;
  for (let t = patch.firstTriangle; t < lastTriangle; t++) {
    const [i0, i1, i2] = triangleCorners(t);
    for (const edge of [
      [i0, i1],
      [i1, i2],
      [i2, i0],
    ]) {
      edges.push(edge);
      const key = edgeKey(edge[0], edge[1]);
      counts.set(key, (counts.get(key) || 0) + 1);
    }
  }
  return edges.filter(([a, b]) => counts.get(edgeKey(a, b)) === 1);
}

// Walk the boundary edges into ordered loops of vertex indices, so
// consecutive entries are connected. A face with a hole has more than one
// loop: two of box.sprocket's 18 do.
function orderLoops(edges) {
  const adj = new Map();
  for (const [a, b] of edges) {
    if (!adj.has(a)) {
      adj.set(a, []);
    }
    if (!adj.has(b)) {
      adj.set(b, []);
    }
    adj.get(a).push(b);
    adj.get(b).push(a);
  }
  const used = new Set();
  const loops = [];
  for (const [a, b] of edges) {
    const startKey = edgeKey(a, b);
    if (used.has(startKey)) {
      continue;
    }
    used.add(startKey);
    const loop = [a];
    let cur = b;
    while (cur !== a) {
      loop.push(cur);
      const next = adj.get(cur).find((n) => !used.has(edgeKey(n, cur)));
      if (next === undefined) {
        break;
      }
      used.add(edgeKey(next, cur));
      cur = next;
    }
    loops.push(loop);
  }
  return loops;
}

// Preview geometry: the patch itself, copied and pushed out along the
// normal (the "cap"), plus a wall of quads connecting the original
// boundary loop to its pushed-out copy. offsetFlags marks which
// vertices move, so a drag only rewrites a position buffer - no rebuild.
function buildPreviewArrays(patch) {
  const geometry = mesh.geometry;
  const pos = geometry.attributes.position;
  const index = geometry.index;
  const positions = [];
  const offsets = [];

  // The cap: the face's own triangles, every vertex of which moves.
  const lastCorner = (patch.firstTriangle + patch.triangleCount) * 3;
  for (let c = patch.firstTriangle * 3; c < lastCorner; c++) {
    const i = index.getX(c);
    positions.push(pos.getX(i), pos.getY(i), pos.getZ(i));
    offsets.push(1);
  }

  // The wall: two triangles per boundary edge, from the edge to the copy
  // of it that moves with the cap.
  for (const loop of orderLoops(boundaryEdges(patch))) {
    for (let i = 0; i < loop.length; i++) {
      const a = loop[i];
      const b = loop[(i + 1) % loop.length];
      for (const [vertex, offset] of [
        [a, 0],
        [b, 0],
        [b, 1],
        [a, 0],
        [b, 1],
        [a, 1],
      ]) {
        positions.push(pos.getX(vertex), pos.getY(vertex), pos.getZ(vertex));
        offsets.push(offset);
      }
    }
  }

  return { positions: new Float32Array(positions), offsets: new Float32Array(offsets) };
}

const raycaster = new THREE.Raycaster();
const highlightMaterial = new THREE.MeshBasicMaterial({
  color: srgb(0xff_aa_00),
  transparent: true,
  opacity: 0.35,
  side: THREE.DoubleSide,
  depthTest: true,
});
const highlightMesh = new THREE.Mesh(new THREE.BufferGeometry(), highlightMaterial);
highlightMesh.visible = false;
scene.add(highlightMesh);

/// A face's triangles as positions, nudged off the surface so a highlight
/// does not z-fight the body.
function patchPositions(patch) {
  const geometry = mesh.geometry;
  const pos = geometry.attributes.position;
  const normals = geometry.attributes.normal;
  const index = geometry.index;
  const positions = [];
  const lastCorner = (patch.firstTriangle + patch.triangleCount) * 3;
  for (let c = patch.firstTriangle * 3; c < lastCorner; c++) {
    const i = index.getX(c);
    // Nudge off the surface so the highlight doesn't z-fight the body.
    // Along each vertex's own normal, not one normal for the whole patch:
    // on a curved face a single direction lifts one side of the highlight
    // clear and buries the other.
    positions.push(
      pos.getX(i) + normals.getX(i) * 0.05,
      pos.getY(i) + normals.getY(i) * 0.05,
      pos.getZ(i) + normals.getZ(i) * 0.05,
    );
  }
  return positions;
}

function setHighlight(patch, color) {
  const positions = patchPositions(patch);
  highlightMesh.geometry.dispose();
  highlightMesh.geometry = new THREE.BufferGeometry();
  highlightMesh.geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  highlightMaterial.color.copy(srgb(color));
  highlightMesh.visible = true;
}

function clearHighlight() {
  highlightMesh.visible = false;
}

// --- What an agent points at -----------------------------------------
// `highlight` from an agent paints faces and edges green, apart from the
// person's own orange selection, which it never touches.
const agentFaces = new THREE.Mesh(
  new THREE.BufferGeometry(),
  new THREE.MeshBasicMaterial({
    color: srgb(0x5b_e0_a0),
    transparent: true,
    opacity: 0.45,
    side: THREE.DoubleSide,
  }),
);
const agentEdges = new THREE.LineSegments(
  new THREE.BufferGeometry(),
  new THREE.LineBasicMaterial({ color: srgb(0x5b_e0_a0), depthTest: false }),
);
agentEdges.renderOrder = 2;
agentFaces.visible = false;
agentEdges.visible = false;
scene.add(agentFaces, agentEdges);
let agentPointedAt = { handles: [], sketches: [] };

function appendAll(target, source) {
  for (const value of source) {
    target.push(value);
  }
}

/// `pointed` is {handles, sketches}: `f2` and `e7` as describe_geometry names them.
function showAgentHighlight(pointed) {
  agentPointedAt = { handles: pointed.handles || [], sketches: pointed.sketches || [] };
  const facePositions = [];
  const edgePositions = [];
  for (const handle of agentPointedAt.handles) {
    const index = Number(handle.slice(1));
    if (handle[0] === "f" && mesh) {
      const group = mesh.geometry.groups[index];
      if (group) {
        appendAll(
          facePositions,
          patchPositions({ firstTriangle: group.start / 3, triangleCount: group.count / 3 }),
        );
      }
    } else if (handle[0] === "e" && edgeLines) {
      const { edgeStart } = edgeLines.userData;
      if (edgeStart && edgeStart[index + 1] !== undefined) {
        appendAll(
          edgePositions,
          edgeLines.geometry.attributes.position.array.slice(
            edgeStart[index] * 6,
            edgeStart[index + 1] * 6,
          ),
        );
      }
    }
  }
  for (const [object, positions] of [
    [agentFaces, facePositions],
    [agentEdges, edgePositions],
  ]) {
    object.geometry.dispose();
    object.geometry = new THREE.BufferGeometry();
    object.geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
    object.visible = positions.length > 0;
  }
}
// The app's Swift side calls this when the daemon sends a highlight.
globalThis.__highlight = showAgentHighlight;

/// A pointer event as normalised device coordinates, which is what
/// Raycaster.setFromCamera takes.
function pointerNDC(event) {
  const rect = renderer.domElement.getBoundingClientRect();
  return new THREE.Vector2(
    ((event.clientX - rect.left) / rect.width) * 2 - 1,
    -((event.clientY - rect.top) / rect.height) * 2 + 1,
  );
}

/// Raycast hits on the part, leaving out hidden and reference bodies.
function partHits() {
  const geometry = mesh.geometry;
  return raycaster
    .intersectObject(mesh)
    .filter(
      (hit) => !geometry.userData.unpickableFace[geometry.userData.faceOfTriangle[hit.faceIndex]],
    );
}

/// The part steps aside for a Modify tool's preview (see
/// showModifyPreview), but a click in the tool still picks on it.
const partPickable = () => mesh && (mesh.visible || modifyPreview !== null);

function raycastFace(event) {
  if (!partPickable()) {
    return null;
  }
  raycaster.setFromCamera(pointerNDC(event), camera);
  const hits = partHits();
  return hits.length > 0 ? hits[0] : null;
}

// --- Edge picking ---------------------------------------------------
// A B-rep edge is a run of the edge line segments (see the ID map built in
// loadPart). The selection keeps the edge's index for the viewer and its
// midpoint for anything that has to name the edge to the engine, because
// documents refer to geometry by position, never by index.
const EDGE_PICK_PIXELS = 6;
const edgeHighlight = new THREE.LineSegments(
  new THREE.BufferGeometry(),
  new THREE.LineBasicMaterial({ color: srgb(0xff_aa_00), depthTest: false }),
);
edgeHighlight.renderOrder = 2;
edgeHighlight.visible = false;
scene.add(edgeHighlight);

/// The edge line segment under the pointer, or null. Lines are hit-tested
/// without depth, so an edge on the far side of the part is dropped when a
/// face stands clearly in front of it.
function raycastEdge(event) {
  if (!edgeLines || !(edgeLines.visible || (modifyPreview !== null && partPickable()))) {
    return null;
  }
  raycaster.setFromCamera(pointerNDC(event), camera);
  const previous = raycaster.params.Line.threshold;
  raycaster.params.Line.threshold = worldRadius(EDGE_PICK_PIXELS, controls.target);
  const unpickable = edgeLines.userData.unpickableEdge;
  const hits = raycaster
    .intersectObject(edgeLines)
    .filter((hit) => !unpickable[edgeLines.userData.edgeOfSegment[Math.floor(hit.index / 2)]]);
  raycaster.params.Line.threshold = previous;
  if (hits.length === 0) {
    return null;
  }
  const faceHit = raycastFace(event);
  if (
    faceHit &&
    faceHit.distance < hits[0].distance - 2 * worldRadius(EDGE_PICK_PIXELS, hits[0].point)
  ) {
    return null;
  }
  return hits[0];
}

function edgeIndexOfHit(hit) {
  return edgeLines.userData.edgeOfSegment[Math.floor(hit.index / 2)];
}

/// A plain pick selects one edge. With `additive` (Shift), the edge joins
/// the selection, or leaves it if it was in: a fillet takes many edges.
function selectEdge(edgeIndex, additive) {
  if (additive && selectedEdges.includes(edgeIndex)) {
    selectedEdges = selectedEdges.filter((index) => index !== edgeIndex);
  } else if (additive) {
    selectedEdges = [...selectedEdges, edgeIndex];
  } else {
    selectedEdges = [edgeIndex];
  }
  const { edgeStart } = edgeLines.userData;
  const positions = edgeLines.geometry.attributes.position.array;
  // Two vertices of three floats per segment.
  const runs = selectedEdges.map((index) =>
    positions.slice(edgeStart[index] * 6, edgeStart[index + 1] * 6),
  );
  const joined = new Float32Array(runs.reduce((total, run) => total + run.length, 0));
  let offset = 0;
  for (const run of runs) {
    joined.set(run, offset);
    offset += run.length;
  }
  edgeHighlight.geometry.dispose();
  edgeHighlight.geometry = new THREE.BufferGeometry();
  edgeHighlight.geometry.setAttribute("position", new THREE.BufferAttribute(joined, 3));
  edgeHighlight.visible = selectedEdges.length > 0;
}

function clearEdgeSelection() {
  selectedEdges = [];
  edgeHighlight.visible = false;
}

/// The middle of an edge's middle segment: a point that is on the edge
/// even when the edge is a curve.
function edgeMidpoint(edgeIndex) {
  const { edgeStart } = edgeLines.userData;
  const positions = edgeLines.geometry.attributes.position.array;
  const segment = Math.floor((edgeStart[edgeIndex] + edgeStart[edgeIndex + 1] - 1) / 2);
  return new THREE.Vector3(
    (positions[segment * 6] + positions[segment * 6 + 3]) / 2,
    (positions[segment * 6 + 1] + positions[segment * 6 + 4]) / 2,
    (positions[segment * 6 + 2] + positions[segment * 6 + 5]) / 2,
  );
}

const hintEl = document.getElementById("hint");
const panelEl = document.getElementById("extrude-panel");
const distanceEl = document.getElementById("handle-value");
const statusEl = document.getElementById("status");
const warningEl = document.getElementById("warning");
const meshErrorEl = document.getElementById("mesh-error");

// --- Drag handles ---------------------------------------------------
// While a tool is open, an arrow (a distance) or a ring (an angle) shows
// where its value acts, as in Fusion. Dragging it sets the value, and the
// box beside it shows the value and takes a typed one. One handle at a
// time. A tool describes its handle with showLinearHandle or
// showAngularHandle and gets each new value through onChange. The math is
// in handles.js.
const HANDLE_PIXELS = 46; // the arrow's length
const RING_PIXELS = 70; // a ring's radius when it has none of its own
const KNOB_PIXELS = 7;
// { kind: "linear", origin, direction, value, rule, unit, onChange, object, pointAt? }, or
// { kind: "angular", centre, axis, reference, radius, value, rule, unit, onChange, object }.
// Vectors are THREE.Vector3; `rule` is {min?, max?} as ViewerHandles.clamp takes it.
let handle = null;
let handleDrag = null; // while the pointer holds the handle: {offset} for an arrow, {unwrapped, offset} for a ring
let handleTyped = false; // the box holds what the user typed, so a value from elsewhere must not overwrite it

function showLinearHandle(spec) {
  hideHandle();
  const object = ViewerHandles.createArrow(THREE);
  scene.add(object);
  handle = { kind: "linear", unit: "mm", rule: {}, ...spec, object };
  showHandleBox();
}

function showAngularHandle(spec) {
  hideHandle();
  const object = ViewerHandles.createRing(THREE);
  scene.add(object);
  handle = {
    kind: "angular",
    unit: "°",
    rule: { min: 1, max: 360 },
    radius: null,
    ...spec,
    object,
  };
  showHandleBox();
}

function hideHandle() {
  if (handle) {
    scene.remove(handle.object);
    handle.object.traverse((part) => {
      if (part.geometry) {
        part.geometry.dispose();
      }
      if (part.material) {
        part.material.dispose();
      }
    });
  }
  handle = null;
  handleDrag = null;
  handleTyped = false;
  distanceEl.style.display = "none";
  if (document.activeElement === distanceEl) {
    distanceEl.blur();
  }
}

/// The box opens with the caret in it, so a typed value needs no click,
/// unless the spec says `focus: false`.
function showHandleBox() {
  handleTyped = false;
  distanceEl.value = ViewerHandles.format(handle.value, handle.unit);
  distanceEl.style.display = "block";
  placeHandle();
  if (handle.focus === false) {
    return;
  }
  distanceEl.focus();
  distanceEl.select();
}

/// A value from somewhere other than the handle: a panel field, or a
/// reselect that moved the handle's tool.
function setHandleValue(value) {
  if (!handle || !Number.isFinite(value)) {
    return;
  }
  handle.value = value;
  if (!handleTyped) {
    distanceEl.value = ViewerHandles.format(value, handle.unit);
  }
}

/// Where the arrow starts. The value point rides on the value, so it sits
/// on the face being pushed or the bottom of a hole, and the arrow starts
/// there. With `pointAt` the arrow ends there instead, pointing at what it
/// acts on: a fillet or chamfer arrow points at its edge.
function arrowBase() {
  const at = handle.origin.clone().addScaledVector(handle.direction, handle.value);
  return handle.pointAt
    ? at.addScaledVector(handle.direction, -worldRadius(HANDLE_PIXELS, at))
    : at;
}

/// The ring's radius in mm: its own, or a fixed size on screen.
function ringRadius() {
  return handle.radius || worldRadius(RING_PIXELS, handle.centre);
}

function ringPoint(degrees) {
  const radians = (degrees * Math.PI) / 180;
  const side = handle.axis.clone().cross(handle.reference);
  return handle.centre
    .clone()
    .addScaledVector(handle.reference, Math.cos(radians) * ringRadius())
    .addScaledVector(side, Math.sin(radians) * ringRadius());
}

/// The point the value box sits beside: the arrow's free end, or the ring's knob.
function handleTip() {
  if (handle.kind === "angular") {
    return ringPoint(handle.value);
  }
  const base = arrowBase();
  return handle.pointAt
    ? base
    : base.addScaledVector(handle.direction, worldRadius(HANDLE_PIXELS, base));
}

/// Puts the handle where its value says, at a fixed size on screen, and the
/// box beside it. Runs every frame, so a zoom or an orbit keeps it in step.
function placeHandle() {
  if (!handle) {
    return;
  }
  const { object } = handle;
  if (handle.kind === "linear") {
    const base = arrowBase();
    object.position.copy(base);
    object.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), handle.direction);
    object.scale.setScalar(worldRadius(HANDLE_PIXELS, base));
  } else {
    const radius = ringRadius();
    const side = handle.axis.clone().cross(handle.reference);
    object.position.copy(handle.centre);
    object.quaternion.setFromRotationMatrix(
      new THREE.Matrix4().makeBasis(handle.reference, side, handle.axis),
    );
    object.scale.setScalar(radius);
    const { arc, spoke, knob, ball } = object.userData;
    const points = ViewerHandles.arcPoints(handle.value).map(
      ([x, y, z]) => new THREE.Vector3(x, y, z),
    );
    arc.geometry.dispose();
    arc.geometry = new THREE.BufferGeometry().setFromPoints(points);
    spoke.geometry.dispose();
    spoke.geometry = new THREE.BufferGeometry().setFromPoints([
      new THREE.Vector3(0, 0, 0),
      new THREE.Vector3(1, 0, 0),
    ]);
    const end = points.at(-1);
    const knobSize = worldRadius(KNOB_PIXELS, ringPoint(handle.value)) / radius;
    knob.position.copy(end);
    knob.scale.setScalar(knobSize);
    ball.position.copy(end);
    ball.scale.setScalar(knobSize * 2);
  }
  object.updateMatrixWorld(true);
  camera.updateMatrixWorld();
  const ndc = handleTip().project(camera);
  const rect = renderer.domElement.getBoundingClientRect();
  const onScreen = ndc.z < 1;
  distanceEl.style.visibility = onScreen ? "visible" : "hidden";
  distanceEl.style.left = `${rect.left + ((ndc.x + 1) / 2) * rect.width + 14}px`;
  distanceEl.style.top = `${rect.top + ((1 - ndc.y) / 2) * rect.height - 12}px`;
}

function handleUnderPointer(event) {
  if (!handle) {
    return false;
  }
  placeHandle();
  raycaster.setFromCamera(pointerNDC(event), camera);
  return raycaster.intersectObjects(handle.object.userData.grab, false).length > 0;
}

function setHandleHover(hovered) {
  if (!handle) {
    return;
  }
  handle.object.userData.material.color.copy(
    srgb(hovered ? ViewerHandles.HOVER_COLOUR : ViewerHandles.HANDLE_COLOUR),
  );
}

/// Where the pointer is along the arrow's axis (mm), or round the ring's
/// axis (degrees, 0 to 360). Null when the view is edge-on to either.
function handleReading(event) {
  raycaster.setFromCamera(pointerNDC(event), camera);
  const { ray } = raycaster;
  if (handle.kind === "linear") {
    return ViewerHandles.axisParameter(
      ray.origin.toArray(),
      ray.direction.toArray(),
      handle.origin.toArray(),
      handle.direction.toArray(),
    );
  }
  const hit = new THREE.Vector3();
  const plane = new THREE.Plane().setFromNormalAndCoplanarPoint(handle.axis, handle.centre);
  if (!ray.intersectPlane(plane, hit)) {
    return null;
  }
  return ViewerHandles.angleAbout(
    hit.toArray(),
    handle.centre.toArray(),
    handle.axis.toArray(),
    handle.reference.toArray(),
  );
}

/// Grabbing keeps the offset between the pointer and the value, so the
/// handle does not jump to the pointer on the first move.
function beginHandleDrag(event) {
  const reading = handleReading(event);
  if (reading === null) {
    return false;
  }
  handleDrag =
    handle.kind === "linear"
      ? { offset: handle.value - reading }
      : {
          unwrapped: ViewerHandles.unwrapAngle(handle.value, reading),
          offset: handle.value - ViewerHandles.unwrapAngle(handle.value, reading),
        };
  handleTyped = false;
  controls.enabled = false;
  renderer.domElement.style.cursor = "grabbing";
  return true;
}

function dragHandle(event) {
  const reading = handleReading(event);
  if (reading === null) {
    return;
  }
  let value;
  let perPixel;
  if (handle.kind === "linear") {
    value = reading + handleDrag.offset;
    perPixel = worldRadius(1, arrowBase());
  } else {
    handleDrag.unwrapped = ViewerHandles.unwrapAngle(handleDrag.unwrapped, reading);
    value = handleDrag.unwrapped + handleDrag.offset;
    perPixel = (180 / Math.PI) * (worldRadius(1, handle.centre) / ringRadius());
  }
  value = ViewerHandles.clamp(
    ViewerHandles.snap(value, ViewerHandles.snapStep(perPixel)),
    handle.rule,
  );
  if (value === handle.value) {
    return;
  }
  handle.value = value;
  distanceEl.value = ViewerHandles.format(value, handle.unit);
  handle.onChange(value);
}

function endHandleDrag() {
  handleDrag = null;
  controls.enabled = true;
  renderer.domElement.style.cursor = "default";
  // Back to the box, so a typed value or Enter follows a drag directly.
  if (handle) {
    distanceEl.focus();
    distanceEl.select();
  }
}

// The box takes "5", "5 mm" or "-3": parseFloat stops at the unit.
distanceEl.addEventListener("input", () => {
  if (!handle) {
    return;
  }
  const value = Number.parseFloat(distanceEl.value);
  if (!Number.isFinite(value)) {
    return;
  }
  handleTyped = true;
  handle.value = value;
  handle.onChange(value);
});
// A drag that leaves the canvas still moves the handle, and a release
// anywhere ends it.
globalThis.addEventListener("pointermove", (event) => {
  if (handleDrag) {
    dragHandle(event);
  }
});
globalThis.addEventListener("pointerup", () => {
  if (handleDrag) {
    endHandleDrag();
  }
});

// --- Picking for a tool -----------------------------------------------
// A tool started with nothing selected first asks for its input: what it
// accepts lights up under the pointer, and a click picks it and opens the
// tool. Inside an open tool the same hover shows what a click would
// reselect; Shift adds edges, faces or regions where the tool takes many.
const PICKS = {
  extrude: { accepts: ["region", "face"], prompt: "Extrude: pick a face or a sketch region" },
  fillet: { accepts: ["edge"], prompt: "Fillet: pick an edge. Shift adds more" },
  chamfer: { accepts: ["edge"], prompt: "Chamfer: pick an edge. Shift adds more" },
  hole: { accepts: ["face"], prompt: "Hole: pick the face to drill" },
  shell: { accepts: ["face"], prompt: "" },
  revolve: { accepts: ["region"], prompt: "Revolve: pick a sketch region" },
};
const TOOL_BUTTONS = () => ({
  extrude: toolExtrudeEl,
  fillet: toolFilletEl,
  chamfer: toolChamferEl,
  hole: toolHoleEl,
  revolve: toolRevolveEl,
});
let pickTool = null; // the tool waiting for its first pick
let toolPress = null; // where a press landed in pick mode or an open tool, so a drag (orbit) is not a click
let hoverRegion = null; // {sketch, region}: the region under the pointer, drawn brighter
let hovered = null; // what showHover lit last, for the test hook: {kind, face | edge | sketch and region}
const hoverMesh = new THREE.Mesh(
  new THREE.BufferGeometry(),
  new THREE.MeshBasicMaterial({
    color: srgb(0xff_aa_00),
    transparent: true,
    opacity: 0.35,
    side: THREE.DoubleSide,
  }),
);
hoverMesh.visible = false;
const hoverEdge = new THREE.LineSegments(
  new THREE.BufferGeometry(),
  new THREE.LineBasicMaterial({ color: srgb(0xff_cc_66), depthTest: false }),
);
hoverEdge.renderOrder = 2;
hoverEdge.visible = false;
scene.add(hoverMesh, hoverEdge);

/// The kind of tool open now, for what a click in the view reselects.
function openToolKind() {
  if (pickTool) {
    return pickTool;
  }
  if (extrudeArmed) {
    return "extrude";
  }
  return modifyTool ? modifyTool.kind : null;
}

function startPick(kind) {
  cancelPick();
  clearSelection();
  pickTool = kind;
  const button = TOOL_BUTTONS()[kind];
  if (button) {
    button.classList.add("active");
  }
  showHint(`${PICKS[kind].prompt}. Esc cancels.`);
  updateToolbarState();
}

function cancelPick() {
  if (!pickTool) {
    return;
  }
  const button = TOOL_BUTTONS()[pickTool];
  if (button) {
    button.classList.remove("active");
  }
  pickTool = null;
  clearHover();
  hideHint();
  updateToolbarState();
}

function clearHover() {
  hovered = null;
  hoverMesh.visible = false;
  hoverEdge.visible = false;
  if (hoverRegion) {
    hoverRegion = null;
    renderSketchSelection();
  }
}

/// What a click at `event` would pick for a tool that accepts `accepts`:
/// {kind: "region", feature, regionIndex}, {kind: "edge", edge} or
/// {kind: "face", hit}. In that order, as the idle click tests them: a
/// sketch sits on the surface, and an edge borders a face.
function pickAt(event, accepts) {
  if (accepts.includes("region")) {
    const sketchHit = raycastSketch(event);
    if (sketchHit) {
      const faceInFront = raycastFace(event);
      const tolerance = worldRadius(SKETCH_PICK_PIXELS, sketchHit.point);
      if (!faceInFront || faceInFront.distance >= sketchHit.distance - tolerance) {
        return { kind: "region", feature: sketchHit.feature, regionIndex: sketchHit.regionIndex };
      }
    }
  }
  if (accepts.includes("edge")) {
    const edgeHit = raycastEdge(event);
    if (edgeHit) {
      return { kind: "edge", edge: edgeIndexOfHit(edgeHit) };
    }
  }
  if (accepts.includes("face")) {
    const hit = raycastFace(event);
    if (hit) {
      return { kind: "face", hit };
    }
  }
  return null;
}

function showHover(pick) {
  hoverMesh.visible = false;
  hoverEdge.visible = false;
  hovered = !pick
    ? null
    : pick.kind === "face"
      ? { kind: "face", face: mesh.geometry.userData.faceOfTriangle[pick.hit.faceIndex] }
      : pick.kind === "edge"
        ? { kind: "edge", edge: pick.edge }
        : { kind: "region", sketch: pick.feature.id, region: pick.regionIndex };
  const region =
    pick && pick.kind === "region" ? { sketch: pick.feature.id, region: pick.regionIndex } : null;
  if (JSON.stringify(region) !== JSON.stringify(hoverRegion)) {
    hoverRegion = region;
    renderSketchSelection();
  }
  if (!pick) {
    return;
  }
  if (pick.kind === "face") {
    hoverMesh.geometry.dispose();
    hoverMesh.geometry = new THREE.BufferGeometry();
    hoverMesh.geometry.setAttribute(
      "position",
      new THREE.Float32BufferAttribute(patchPositions(facePatchAt(pick.hit)), 3),
    );
    hoverMesh.visible = true;
  } else if (pick.kind === "edge") {
    const { edgeStart } = edgeLines.userData;
    const run = edgeLines.geometry.attributes.position.array.slice(
      edgeStart[pick.edge] * 6,
      edgeStart[pick.edge + 1] * 6,
    );
    hoverEdge.geometry.dispose();
    hoverEdge.geometry = new THREE.BufferGeometry();
    hoverEdge.geometry.setAttribute("position", new THREE.BufferAttribute(run, 3));
    hoverEdge.visible = true;
  }
}

/// Makes `pick` the selection, as an idle click on it would. `additive`
/// (Shift) adds an edge or a region, or takes it out again.
function applyPick(pick, additive) {
  if (pick.kind === "region") {
    selectSketchForExtrude(pick.feature, pick.regionIndex, additive);
  } else if (pick.kind === "edge") {
    selectedPatch = null;
    selectedSketch = null;
    clearHighlight();
    renderSketchSelection();
    selectEdge(pick.edge, additive);
    selectionChanged();
  } else {
    clearEdgeSelection();
    selectedPatch = facePatchAt(pick.hit);
    selectedSketch = null;
    setHighlight(selectedPatch, 0xff_dd_55);
    renderSketchSelection();
    selectionChanged();
  }
}

/// The direction a fillet or chamfer arrow points from edge `edgeIndex`:
/// away from the material, between the normals of the two faces that meet
/// there, and square to the edge. Those faces are the ones with mesh nodes
/// on both ends of the edge's middle segment; OCCT tessellates an edge and
/// its faces from the same nodes.
function edgeOutward(edgeIndex) {
  const { edgeStart } = edgeLines.userData;
  const positions = edgeLines.geometry.attributes.position.array;
  const segment = Math.floor((edgeStart[edgeIndex] + edgeStart[edgeIndex + 1] - 1) / 2);
  const a = new THREE.Vector3().fromArray(positions, segment * 6);
  const b = new THREE.Vector3().fromArray(positions, segment * 6 + 3);
  const tangent = b.clone().sub(a).normalize();
  const geometry = mesh.geometry;
  const position = geometry.attributes.position;
  const normals = geometry.attributes.normal;
  const corner = new THREE.Vector3();
  const sum = new THREE.Vector3();
  const tolerance = 1e-3;
  geometry.groups.forEach((group) => {
    let nearA = -1;
    let nearB = -1;
    for (let c = group.start; c < group.start + group.count; c++) {
      const vertex = geometry.index.getX(c);
      corner.fromBufferAttribute(position, vertex);
      if (nearA < 0 && corner.distanceTo(a) < tolerance) {
        nearA = vertex;
      }
      if (nearB < 0 && corner.distanceTo(b) < tolerance) {
        nearB = vertex;
      }
      if (nearA >= 0 && nearB >= 0) {
        break;
      }
    }
    if (nearA >= 0 && nearB >= 0) {
      sum.add(new THREE.Vector3().fromBufferAttribute(normals, nearA));
    }
  });
  sum.addScaledVector(tangent, -sum.dot(tangent));
  if (sum.lengthSq() < 1e-8) {
    // No faces found, or two that cancel: point at the viewer instead.
    camera.getWorldDirection(sum).negate();
    sum.addScaledVector(tangent, -sum.dot(tangent));
  }
  return sum.normalize();
}

// --- Toolbar --------------------------------------------------------
// Only three commands exist so far. Every other toolbar button is a
// disabled placeholder, so the shape of the app is visible before the
// ops behind it are written.
const toolSketchEl = document.getElementById("tool-sketch");
const toolExtrudeEl = document.getElementById("tool-extrude");
const toolFilletEl = document.getElementById("tool-fillet");
const toolChamferEl = document.getElementById("tool-chamfer");
const toolHoleEl = document.getElementById("tool-hole");
const toolRevolveEl = document.getElementById("tool-revolve");
const toolShellEl = document.getElementById("tool-shell");
const toolCombineEl = document.getElementById("tool-combine");
const toolMirrorEl = document.getElementById("tool-mirror");
const toolLinearEl = document.getElementById("tool-linear");
const toolCircularEl = document.getElementById("tool-circular");
const toolAxisEl = document.getElementById("tool-axis");
const toolPointEl = document.getElementById("tool-point");
const toolOffsetPlaneEl = document.getElementById("tool-offset-plane");

/// Extrude acts on the current selection, so it is live only while there
/// is one. Called from every place a selection or a mode changes.
function updateToolbarState() {
  const idle = !extrudeArmed && !modifyTool;
  // With nothing selected these tools ask for their pick (startPick), so
  // they are live whenever there is something to pick.
  const regions = sketchOutlines.some(({ feature, drawn }) => feature.active && drawn);
  toolExtrudeEl.disabled = !idle || !(selectedPatch || selectedSketch || mesh || regions);
  toolFilletEl.disabled = !idle || !mesh;
  toolChamferEl.disabled = toolFilletEl.disabled;
  toolHoleEl.disabled = !idle || !mesh;
  toolRevolveEl.disabled = !idle || !(selectedSketch || regions);
  // Shell works with no selection at all: with no open face it hollows the part.
  toolShellEl.disabled = !idle || !mesh;
  // Bodies: Mirror and the patterns act on the picked one, Combine needs two.
  const free = idle && !sketchStage;
  const bodyPicked = Boolean(free && mesh && pickedBody());
  toolMirrorEl.disabled = toolLinearEl.disabled = toolCircularEl.disabled = !bodyPicked;
  toolCombineEl.disabled = !(free && mesh && partBodies().length >= 2);
  toolAxisEl.disabled = !(free && mesh && (selectedPatch || selectedEdges.length > 0));
  toolPointEl.disabled = toolOffsetPlaneEl.disabled = !free;
  toolSketchEl.classList.toggle("active", Boolean(sketchStage));
}

toolSketchEl.addEventListener("click", () => {
  if (sketchStage || extrudeArmed || modifyTool) {
    return;
  }
  startSketchPickPlane();
});

toolExtrudeEl.addEventListener("click", armCurrentSelection);

// Export: STL, 3MF or STEP. In a browser the engine serves the file and the
// page saves it. In the app the file lives where the person chooses, so the
// message goes to Swift, which opens its own save panel.
const toolExportEl = document.getElementById("tool-export");
const EXPORT_FORMATS = [
  ["STL", "stl"],
  ["3MF", "3mf"],
  ["STEP", "step"],
];
async function exportPart(format) {
  if (!mesh) {
    return showNotice("There is no part to export yet");
  }
  if (hasSwiftBridge()) {
    globalThis.webkit.messageHandlers.sprocket.postMessage({ type: "export", format });
    return;
  }
  try {
    const response = await fetch(`/export?format=${format}`);
    if (!response.ok) {
      throw new Error((await response.json()).error);
    }
    const disposition = response.headers.get("Content-Disposition") || "";
    const link = document.createElement("a");
    link.href = URL.createObjectURL(await response.blob());
    link.download = (/filename="([^"]+)"/.exec(disposition) || [])[1] || `part.${format}`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 10_000);
    showNotice(`Exported ${link.download}`, 3000);
  } catch (error) {
    showNotice(`Export failed: ${error.message}`);
  }
}
toolExportEl.addEventListener("click", () => {
  if (sketchStage || extrudeArmed) {
    return;
  }
  const box = toolExportEl.getBoundingClientRect();
  openMenu(
    box.left,
    box.bottom + 6,
    "Export part as",
    EXPORT_FORMATS.map(([label, format]) => ({ label, action: () => exportPart(format) })),
  );
});

// Clears the document to zero features. No confirmation, because Swift
// snapshots box.sprocket into the same undo stack every other edit uses, so
// Cmd+Z brings the whole part back (see ContentView.performMutation).
document.getElementById("tool-new").addEventListener("click", () => {
  if (sketchStage || extrudeArmed || modifyTool) {
    return;
  }
  sendCommand({ type: "new_document" });
});

// --- Browser tree ---------------------------------------------------
// Visibility only, for now. The state lives here rather than on the
// three.js objects because a rebuild replaces every sketch object and
// the mesh: feature ids outlive them, the objects do not.
const hiddenSketchIDs = new Set();
const collapsedGroups = new Set();
let bodyVisible = true;
let gridVisible = true;
let browserFeatures = [];
let documentParameters = {}; // the document's named parameters, as stored: numbers and "=expressions"

const EYE_ON =
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2.062 12.348a1 1 0 0 1 0-.696 10.75 10.75 0 0 1 19.876 0 1 1 0 0 1 0 .696 10.75 10.75 0 0 1-19.876 0" /><circle cx="12" cy="12" r="3" /></svg>';
const EYE_OFF =
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.733 5.076a10.744 10.744 0 0 1 11.205 6.575 1 1 0 0 1 0 .696 10.747 10.747 0 0 1-1.444 2.49" /><path d="M14.084 14.158a3 3 0 0 1-4.242-4.242" /><path d="M17.479 17.499a10.75 10.75 0 0 1-15.417-5.151 1 1 0 0 1 0-.696 10.75 10.75 0 0 1 4.446-5.143" /><path d="m2 2 20 20" /></svg>';

/// Pushes the browser tree's state onto the scene. Runs after every
/// rebuild too, since renderSketchOutlines makes new objects that default
/// to visible.
function applyVisibility() {
  grid.visible = gridVisible;
  for (const key of Object.keys(AXES)) {
    axisLines[key].visible = axesVisible[key];
  }
  if (mesh) {
    mesh.visible = bodyVisible;
  }
  if (edgeLines) {
    edgeLines.visible = bodyVisible;
  }
  constructObjects.forEach(({ id, object }) => (object.visible = !hiddenConstructIDs.has(id)));
  sketchOutlines.forEach((entry) => {
    const visible = !hiddenSketchIDs.has(entry.feature.id);
    entry.line.visible = visible;
    // The fills are the click targets (see raycastSketch), so hiding them
    // is also what makes a hidden sketch unpickable in the viewport.
    entry.fills.forEach((fill) => (fill.visible = visible));
    if (!visible) {
      entry.handles.visible = false;
    }
  });
}

// Hiding the selected sketch keeps it selected, as Fusion does. Its
// corner handles go with it, and pickSketchPart refuses to grab a hidden
// outline, so the only thing left is Extrude.
function setSketchHidden(id, hidden) {
  if (hidden) {
    hiddenSketchIDs.add(id);
  } else {
    hiddenSketchIDs.delete(id);
  }
  applyVisibility();
  renderBrowser();
}

function visibleSketches() {
  return browserFeatures.filter(
    (feature) =>
      feature.type === "sketch" &&
      feature.plane &&
      feature.regions &&
      !hiddenSketchIDs.has(feature.id),
  );
}

function setEverythingVisible(visible) {
  gridVisible = visible;
  for (const key of Object.keys(AXES)) {
    axesVisible[key] = visible;
  }
  bodyVisible = visible;
  hiddenSketchIDs.clear();
  if (!visible) {
    browserFeatures
      .filter((feature) => feature.type === "sketch")
      .forEach((feature) => hiddenSketchIDs.add(feature.id));
  }
  applyVisibility();
  renderBrowser();
}

function toggleGroup(name) {
  if (collapsedGroups.has(name)) {
    collapsedGroups.delete(name);
  } else {
    collapsedGroups.add(name);
  }
  renderBrowser();
}

function renderBrowser() {
  const tree = document.getElementById("browser-tree");
  tree.innerHTML = "";

  function addRow(options) {
    const row = document.createElement("div");
    row.className = `browser-row${options.selected ? " selected" : ""}`;
    row.style.paddingLeft = `${6 + options.depth * 14}px`;

    const caret = document.createElement("span");
    caret.className = `browser-caret${options.group ? " clickable" : ""}`;
    caret.textContent = options.group ? (collapsedGroups.has(options.group) ? "▸" : "▾") : "";
    if (options.group) {
      caret.addEventListener("click", () => toggleGroup(options.group));
    }
    row.appendChild(caret);

    const eye = document.createElement("span");
    eye.className = `browser-eye${options.visible ? "" : " off"}`;
    eye.innerHTML = options.noEye ? "" : options.visible ? EYE_ON : EYE_OFF;
    eye.title = options.noEye ? "" : options.visible ? "Hide" : "Show";
    if (options.noEye) {
      eye.style.width = "14px";
    } else {
      eye.addEventListener("click", options.onToggle);
    }
    row.appendChild(eye);

    const label = document.createElement("span");
    label.className = `browser-label${options.onClick ? " clickable" : ""}${options.dim ? " dim" : ""}`;
    label.textContent = options.label;
    if (options.onClick) {
      label.addEventListener("click", options.onClick);
    }
    row.appendChild(label);

    if (options.onColor) {
      // The browser's own colour picker. A change sends one set_body edit.
      const swatch = document.createElement("input");
      swatch.type = "color";
      swatch.className = "browser-swatch";
      swatch.title = "Body colour";
      swatch.value = options.color || `#${PART_COLOUR.toString(16).padStart(6, "0")}`;
      swatch.addEventListener("change", () => options.onColor(swatch.value));
      row.appendChild(swatch);
    }

    if (options.onContextMenu) {
      row.addEventListener("contextmenu", (event) => {
        event.preventDefault();
        options.onContextMenu(event);
      });
    }

    tree.appendChild(row);
  }

  const sketches = browserFeatures.filter(
    (feature) => feature.type === "sketch" && feature.plane && feature.regions,
  );
  const anyAxisVisible = Object.values(axesVisible).some(Boolean);
  const anythingVisible =
    gridVisible || anyAxisVisible || (Boolean(mesh) && bodyVisible) || visibleSketches().length > 0;

  addRow({
    depth: 0,
    group: "root",
    label: "Document",
    visible: anythingVisible,
    onToggle: () => setEverythingVisible(!anythingVisible),
  });
  if (collapsedGroups.has("root")) {
    return;
  }

  addRow({
    depth: 1,
    label: "Grid",
    visible: gridVisible,
    onToggle: () => {
      gridVisible = !gridVisible;
      applyVisibility();
      renderBrowser();
    },
  });

  addRow({
    depth: 1,
    group: "origin",
    label: "Origin",
    visible: anyAxisVisible,
    onToggle: () => {
      for (const key of Object.keys(AXES)) {
        axesVisible[key] = !anyAxisVisible;
      }
      applyVisibility();
      renderBrowser();
    },
  });
  if (!collapsedGroups.has("origin")) {
    for (const [key, axis] of Object.entries(AXES)) {
      addRow({
        depth: 2,
        label: `${axis.name} axis`,
        visible: axesVisible[key],
        onToggle: () => {
          axesVisible[key] = !axesVisible[key];
          applyVisibility();
          renderBrowser();
        },
      });
    }
  }

  addRow({
    depth: 1,
    group: "bodies",
    label: "Bodies",
    dim: !mesh,
    visible: Boolean(mesh) && bodyVisible,
    onToggle: () => {
      if (!mesh) {
        return;
      }
      bodyVisible = !bodyVisible;
      applyVisibility();
      renderBrowser();
    },
  });
  // One row per body, from the mesh header. The eye is the body's saved
  // `visible` view state: undoable, but not an edit. Hidden bodies still export.
  if (mesh && !collapsedGroups.has("bodies")) {
    const bodies = mesh.geometry.userData.bodies;
    if (bodies.length === 0) {
      addRow({
        depth: 2,
        label: "Body1",
        visible: bodyVisible,
        onToggle: () => {
          bodyVisible = !bodyVisible;
          applyVisibility();
          renderBrowser();
        },
      });
    }
    for (const body of bodies) {
      addRow({
        depth: 2,
        label: body.name + (body.reference ? " (reference)" : ""),
        visible: body.visible !== false,
        dim: body.reference,
        onToggle: () =>
          sendCommand({ type: "set_visibility", id: body.id, visible: body.visible === false }),
        selected: !body.reference && selectedBodyId === body.id,
        onClick: body.reference ? null : () => selectBody(body.id),
        color: body.color,
        onColor: body.reference
          ? null
          : (color) => sendCommand({ type: "set_body", id: body.id, color }),
        onContextMenu: (event) => openBodyMenu(event.clientX, event.clientY, body),
      });
    }
  }

  addRow({
    depth: 1,
    group: "parameters",
    label: "Parameters",
    noEye: true,
    dim: Object.keys(documentParameters).length === 0,
  });
  if (!collapsedGroups.has("parameters")) {
    renderParameterRows(tree);
  }

  const constructs = browserFeatures.filter((feature) => feature.construct);
  if (constructs.length > 0) {
    addRow({
      depth: 1,
      group: "construction",
      label: "Construction",
      visible: constructs.some((feature) => !hiddenConstructIDs.has(feature.id)),
      onToggle: () => {
        const showing = constructs.some((feature) => !hiddenConstructIDs.has(feature.id));
        constructs.forEach((feature) =>
          showing ? hiddenConstructIDs.add(feature.id) : hiddenConstructIDs.delete(feature.id),
        );
        applyVisibility();
        renderBrowser();
      },
    });
    if (!collapsedGroups.has("construction")) {
      constructs.forEach((feature) =>
        addRow({
          depth: 2,
          label: `${feature.id} (${feature.type})`,
          dim: !feature.active,
          visible: !hiddenConstructIDs.has(feature.id),
          onToggle: () => {
            if (hiddenConstructIDs.has(feature.id)) {
              hiddenConstructIDs.delete(feature.id);
            } else {
              hiddenConstructIDs.add(feature.id);
            }
            applyVisibility();
            renderBrowser();
          },
        }),
      );
    }
  }

  addRow({
    depth: 1,
    group: "sketches",
    label: "Sketches",
    dim: sketches.length === 0,
    visible: visibleSketches().length > 0,
    onToggle: () => {
      const showing = visibleSketches().length > 0;
      sketches.forEach((feature) => setSketchHidden(feature.id, showing));
    },
  });
  if (collapsedGroups.has("sketches")) {
    return;
  }
  sketches.forEach((feature) => {
    const hidden = hiddenSketchIDs.has(feature.id);
    addRow({
      depth: 2,
      label: feature.id,
      // A rolled-past sketch is greyed in the timeline too: it is not part
      // of the document prefix the build replayed.
      dim: !feature.active,
      selected: Boolean(selectedSketch && selectedSketch.id === feature.id),
      visible: !hidden,
      onToggle: () => setSketchHidden(feature.id, !hidden),
      onClick: feature.active && !hidden ? () => selectSketchForExtrude(feature) : null,
    });
  });
}

/// The document's named parameters, one row each: a box that takes a number
/// or an expression over the others, and a way to remove it. A last row adds
/// one. A change goes through the same set_parameter edit an agent makes,
/// so it is undoable and logged, and a refused one (a cycle, a name in use
/// by an expression) leaves the box as it was.
function renderParameterRows(tree) {
  const row = (build) => {
    const element = document.createElement("div");
    element.className = "browser-row parameter-row";
    element.style.paddingLeft = `${6 + 2 * 14}px`;
    build(element);
    tree.appendChild(element);
  };
  const field = (className, value, placeholder, title) => {
    const input = document.createElement("input");
    input.className = `browser-param ${className}`;
    input.spellcheck = false;
    input.autocomplete = "off";
    input.value = value;
    input.placeholder = placeholder;
    input.title = title;
    // Typing here must not reach the viewer's own key shortcuts.
    input.addEventListener("keydown", (event) => event.stopPropagation());
    return input;
  };
  const apply = (name, value, restore) =>
    sendCommand({ type: "set_parameter", name, value }).catch((error) => {
      showNotice(`Parameter ${name}: ${error.message}`);
      if (restore) {
        restore();
      }
    });
  for (const [name, raw] of Object.entries(documentParameters)) {
    row((element) => {
      const label = document.createElement("span");
      label.className = "browser-label";
      label.textContent = name;
      label.title = `${name}: use it in a field as =${name}`;
      const input = field(
        "",
        String(raw),
        "value",
        "A number, or an expression such as =width * 2",
      );
      const commit = () => {
        if (input.value.trim() === String(raw)) {
          return;
        }
        apply(name, input.value, () => (input.value = String(raw)));
      };
      input.addEventListener("change", commit);
      input.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          input.blur();
        }
        if (event.key === "Escape") {
          input.value = String(raw);
          input.blur();
        }
      });
      const remove = document.createElement("span");
      remove.className = "browser-button";
      remove.textContent = "×";
      remove.title = `Remove ${name}`;
      remove.addEventListener("click", () => apply(name, null));
      element.append(label, input, remove);
    });
  }
  row((element) => {
    const name = field("name", "", "new name", "A name: letters, digits and underscores");
    const value = field("", "", "value", "A number, or an expression such as =width * 2");
    const add = document.createElement("span");
    add.className = "browser-button";
    add.textContent = "+";
    add.title = "Add the parameter";
    const commit = () => {
      if (!name.value.trim() && !value.value.trim()) {
        return;
      }
      apply(name.value.trim(), value.value, null).then(() => {});
    };
    for (const input of [name, value]) {
      input.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          commit();
        }
      });
    }
    add.addEventListener("click", commit);
    element.append(name, value, add);
  });
}

/// Reads the document's parameters, which features.json does not carry.
function refreshParameters() {
  if (!canCallDaemon()) {
    return Promise.resolve();
  }
  return rpcCall("get_document", {})
    .then((document) => {
      sawRevision(document.revision);
      const next = document.parameters || {};
      if (JSON.stringify(next) === JSON.stringify(documentParameters)) {
        return;
      }
      // A box being typed in keeps its text: the rebuild that follows an add repaints around it.
      documentParameters = next;
      renderBrowser();
    })
    .catch(() => {});
}

function showHint(text) {
  hintEl.textContent = text;
  hintEl.style.display = "block";
}
function hideHint() {
  hintEl.style.display = "none";
}

/// A selection changed: the toolbar's Extrude button and the browser tree
/// follow it. Extrude is the toolbar button or the E key, and Escape clears.
function selectionChanged() {
  updateToolbarState();
  renderBrowser();
  reportSelection();
  updateMeasure();
}

// The measure readout: a face's area, an edge's length, a face and an edge's
// distance and angle, or with nothing selected each body's volume. The
// daemon measures the B-rep, so a page with no daemon shows nothing.
const measureEl = document.getElementById("measure-readout");
const toolMeasureEl = document.getElementById("tool-measure");
let measureOn = false;
let measureAsked = "";
function toggleMeasure() {
  measureOn = !measureOn;
  toolMeasureEl.classList.toggle("active", measureOn);
  measureAsked = "";
  if (measureOn) {
    updateMeasure();
  } else {
    measureEl.style.display = "none";
  }
}
toolMeasureEl.addEventListener("click", toggleMeasure);
const millimetres = (value) => String(Number(value.toFixed(2)));
function updateMeasure() {
  if (!measureOn || !canCallDaemon()) {
    return;
  }
  const handles = [
    selectedPatch && `f${selectedPatch.faceIndex}`,
    ...selectedEdges.map((index) => `e${index}`),
  ].filter(Boolean);
  const key = JSON.stringify(handles);
  if (key === measureAsked) {
    return;
  }
  measureAsked = key;
  if (handles.length > 2) {
    measureEl.style.display = "none"; // the daemon measures one or two things
    return;
  }
  rpcCall("measure", handles.length > 0 ? { handles } : {})
    .then((result) => {
      if (key !== measureAsked) {
        return;
      }
      measureEl.textContent = measureLines(result, handles).join("\n");
      measureEl.style.display = measureEl.textContent ? "block" : "none";
    })
    .catch(() => {
      measureEl.style.display = "none";
    });
}
function measureLines(result, handles) {
  if (handles.length === 2) {
    const lines = [`Distance ${millimetres(result.distance)} mm`];
    if (result.angle !== undefined) {
      lines.push(`Angle ${millimetres(result.angle)}°`);
    }
    return lines;
  }
  if (handles.length === 1) {
    return result.area !== undefined
      ? [`Area ${millimetres(result.area)} mm²`]
      : [`Length ${millimetres(result.length)} mm`];
  }
  const bodies = result.bodies || [];
  if (bodies.length === 1) {
    return [`Volume ${millimetres(bodies[0].volume)} mm³`];
  }
  return bodies.map((body) => `${body.name}  ${millimetres(body.volume)} mm³`);
}

// The daemon holds the selection so an agent can read it ("fillet these").
// Handles are the ones describe_geometry gives: f<i> and e<i>, taken at the
// revision this page has loaded, because a rebuild renumbers them.
let reportedSelection = "";
let restoringSelection = false; // a reload is putting the daemon's selection back
function reportSelection() {
  if (restoringSelection) {
    return;
  }
  if (!hasSwiftBridge() && !/^https?:$/.test(globalThis.location.protocol)) {
    return;
  }
  const handles = [
    selectedPatch && `f${selectedPatch.faceIndex}`,
    ...selectedEdges.map((index) => `e${index}`),
  ].filter(Boolean);
  const sketches = selectedSketch ? [selectedSketch.id] : [];
  const params = { handles, sketches };
  if (Number.isInteger(loadedRevision)) {
    params.revision = loadedRevision;
  }
  const key = JSON.stringify(params);
  if (key === reportedSelection) {
    return;
  }
  reportedSelection = key;
  rpcCall("set_selection", params).catch(() => {});
}

/// Arms whichever of the two selections is live. The E key and the
/// toolbar's Extrude button both come through here.
/// With nothing selected, it asks for a face or a region first.
function armCurrentSelection() {
  if (extrudeArmed || modifyTool || sketchStage) {
    return;
  }
  cancelPick();
  if (selectedPatch) {
    armExtrude();
  } else if (selectedSketch) {
    armSketchExtrude(selectedSketch);
  } else if (!toolExtrudeEl.disabled) {
    startPick("extrude");
  }
}

function clearSelection() {
  selectedPatch = null;
  selectedSketch = null;
  clearEdgeSelection();
  renderer.domElement.style.cursor = "default";
  clearHighlight();
  selectionChanged();
  hideHint();
  renderSketchSelection();
}
function showStatus(text) {
  statusEl.textContent = text;
  statusEl.style.display = "block";
}
// The rebuild this was showing has landed: features.json is what arrives
// last, so loadFeatures clears it.
let noticeUntil = 0;
function hideStatus() {
  if (Date.now() < noticeUntil) {
    return;
  }
  statusEl.style.display = "none";
}
/// A status line that outlives the rebuild it came with, for six seconds.
function showNotice(text, milliseconds) {
  const shown = milliseconds || 6000;
  showStatus(text);
  noticeUntil = Date.now() + shown;
  setTimeout(hideStatus, shown + 100);
}

let selectedBodyId = null; // a body row picked in the browser tree: what Mirror, the patterns and Combine act on
let selectedPatch = null; // facePatchAt()'s result, plus anchorPoint
let selectedEdges = []; // B-rep edge indices, in shape.edges() order; Shift adds to them
let selectedRegions = []; // indices into selectedSketch.regions that Extrude will push
let selectedSketch = null; // a features.json entry: any replayed sketch, picked in the 3D view or via its timeline chip
// A live corner or edge drag on the selected sketch:
// { feature, plane, kind, indices, polygon, lastUV }. polygon is a working
// copy in the plane's uv, posted to Swift on release.
let sketchEditDrag = null;
let extrudeArmed = false;
let modifyTool = null; // the open Modify tool: see armModify
let armedMode = "face"; // "face" | "sketch" - which of the two below armExtrude() armed
let previewState = null; // { basePositions, offsetFlags, previewMesh, previewGeometry, distance }
let sketchExtrudeRegions = []; // the regions armSketchExtrude() is pushing
let sketchExtrudeFeature = null; // the features.json entry armSketchExtrude() is dragging

renderer.domElement.addEventListener("pointermove", (event) => {
  if (sketchStage === "draw") {
    sketcher.pointerMove(event);
    return;
  }
  if (sketchStage === "pick-plane") {
    if (!mesh) {
      clearHighlight();
      return;
    }
    const hit = raycastFace(event);
    if (!hit) {
      clearHighlight();
      return;
    }
    setHighlight(facePatchAt(hit), 0xff_aa_00);
    return;
  }
  if (sketchEditDrag) {
    dragSketchEdit(event);
    return;
  }
  if (handleDrag) {
    return; // the window's handler moves the handle
  }
  // Over the handle: it lights up and offers a grab, and nothing below it
  // is hovered.
  if (handleUnderPointer(event)) {
    setHandleHover(true);
    clearHover();
    renderer.domElement.style.cursor = "grab";
    return;
  }
  if (handle) {
    setHandleHover(false);
    renderer.domElement.style.cursor = "default";
  }
  const tool = openToolKind();
  if (tool) {
    const accepts = PICKS[tool] ? PICKS[tool].accepts : [];
    showHover(accepts.length > 0 ? pickAt(event, accepts) : null);
    return;
  }
  // A grab cursor over a corner or an edge of the selected sketch: the only
  // cue, besides the corner markers, that it can be dragged.
  renderer.domElement.style.cursor =
    selectedSketch && pickSketchPart(event, selectedSketch) ? "move" : "default";
  if (selectedPatch) {
    return; // keep the selection highlight until a new pick
  }
  const hit = raycastFace(event);
  if (!hit) {
    clearHighlight();
    return;
  }
  setHighlight(facePatchAt(hit), 0xff_aa_00);
});

// Grabbing the viewport cancels a camera move, unless the move is the one
// that puts you square on to a sketch plane.
function cancelCameraMove() {
  if (controls.enableRotate) {
    cameraMove = null;
  }
}
renderer.domElement.addEventListener("pointerdown", cancelCameraMove);

// Wheel zoom, in place of OrbitControls' own (see wheel-zoom.js for why).
// It listens on window in the capture phase and stops the event there, so
// the controls' wheel listener on the canvas never runs. Middle-drag dolly
// is still the controls'. The app sets globalThis.__sprocketSettings before the
// page loads and calls globalThis.__applySettings when Settings change.
let zoomSensitivity = WheelZoom.clampSensitivity(globalThis.__sprocketSettings?.zoomSensitivity);
globalThis.__applySettings = (settings) => {
  if (settings && "zoomSensitivity" in settings) {
    zoomSensitivity = WheelZoom.clampSensitivity(settings.zoomSensitivity);
  }
};
globalThis.addEventListener(
  "wheel",
  (event) => {
    if (event.target !== renderer.domElement) {
      return;
    }
    event.stopPropagation();
    event.preventDefault();
    if (!controls.enabled || !controls.enableZoom) {
      return;
    }
    cancelCameraMove();
    const scale = WheelZoom.distanceScale(
      event.deltaY,
      event.deltaMode,
      renderer.domElement.clientHeight,
      zoomSensitivity,
    );
    const offset = camera.position.clone().sub(controls.target).multiplyScalar(scale);
    offset.setLength(
      Math.min(controls.maxDistance, Math.max(controls.minDistance, offset.length())),
    );
    camera.position.copy(controls.target).add(offset);
    controls.update();
  },
  { capture: true, passive: false },
);

renderer.domElement.addEventListener("pointerdown", (event) => {
  // The right button pans the camera, and a right click without a drag
  // opens the context menu (below). Neither picks.
  if (event.button === 2) {
    return;
  }
  if (sketchStage === "pick-plane") {
    const hit = raycastFace(event);
    if (!hit) {
      return;
    }
    sketchOnFace(facePatchAt(hit), hit.point);
    return;
  }
  if (sketchStage === "draw") {
    sketcher.pointerDown(event);
    return;
  }
  if (handle && handleUnderPointer(event) && beginHandleDrag(event)) {
    return;
  }
  // In pick mode or an open tool, a press only picks if it is let go
  // without a drag: a drag orbits the camera (see the pointerup below).
  if (openToolKind()) {
    toolPress = { x: event.clientX, y: event.clientY };
    return;
  }
  // Editing the selected sketch outranks picking anything new: a press on
  // one of its corners or edges starts a drag rather than a reselection.
  if (selectedSketch) {
    const pick = pickSketchPart(event, selectedSketch);
    if (pick) {
      beginSketchEdit(pick);
      return;
    }
  }
  // The sketch is tested first: its outline sits on the part surface, so a
  // face hit at the same pixel would otherwise always win.
  // A sketch behind a face is not under the pointer: without this test the
  // outline of a sketch on the underside of a part took every click on a
  // side wall in front of it. A sketch lying on the visible surface is at
  // the same distance as the face, so the tolerance keeps it pickable.
  const sketchHit = raycastSketch(event);
  if (sketchHit) {
    const faceInFront = raycastFace(event);
    const tolerance = worldRadius(SKETCH_PICK_PIXELS, sketchHit.point);
    if (!faceInFront || faceInFront.distance >= sketchHit.distance - tolerance) {
      selectSketchForExtrude(sketchHit.feature, sketchHit.regionIndex, event.shiftKey);
      return;
    }
  }
  const edgeHit = raycastEdge(event);
  if (edgeHit) {
    selectedPatch = null;
    selectedSketch = null;
    clearHighlight();
    renderSketchSelection();
    selectEdge(edgeIndexOfHit(edgeHit), event.shiftKey);
    hideHint();
    selectionChanged();
    return;
  }
  const hit = raycastFace(event);
  if (!hit) {
    clearSelection();
    return;
  }
  clearEdgeSelection();
  selectedPatch = facePatchAt(hit);
  selectedPatch.anchorPoint = hit.point.clone();
  selectedSketch = null;
  setHighlight(selectedPatch, 0xff_dd_55);
  renderSketchSelection();
  hideHint();
  selectionChanged();
});

// --- Right-click menu and colour picker ---------------------------------
// A right click on a face (a press and release without a drag, so a
// right-drag still pans) opens a menu for that face and its body. Colours
// go through the daemon's own calls: set_appearances for a face,
// set_body for a body. So they are undoable, logged, and an agent sees them.
const contextMenuEl = document.getElementById("context-menu");
const colourPopoverEl = document.getElementById("colour-popover");
const colourHexEl = document.getElementById("colour-hex");
const colourNativeEl = document.getElementById("colour-native");
const RIGHT_CLICK_SLOP = 4; // pixels the pointer may move and still count as a click
let rightPress = null;
let colourTarget = null; // what the popover's colour goes to: a function of "#rrggbb"

function menuOpen() {
  return contextMenuEl.style.display === "block" || colourPopoverEl.style.display === "block";
}
function closeMenus() {
  contextMenuEl.style.display = "none";
  colourPopoverEl.style.display = "none";
  colourTarget = null;
}

/// Shows `element` at (x, y), kept inside the window.
function placeAt(element, x, y) {
  element.style.display = "block";
  const width = element.offsetWidth;
  const height = element.offsetHeight;
  element.style.left = `${Math.max(4, Math.min(x, globalThis.innerWidth - width - 4))}px`;
  element.style.top = `${Math.max(4, Math.min(y, globalThis.innerHeight - height - 4))}px`;
}

/// `items` is [{label, action}]; a null entry is left out.
function openMenu(x, y, title, items) {
  closeMenus();
  contextMenuEl.innerHTML = "";
  const heading = document.createElement("div");
  heading.className = "menu-title";
  heading.textContent = title;
  contextMenuEl.appendChild(heading);
  for (const item of items.filter(Boolean)) {
    const button = document.createElement("button");
    button.setAttribute("role", "menuitem");
    button.textContent = item.label;
    button.addEventListener("click", () => {
      contextMenuEl.style.display = "none";
      item.action();
    });
    contextMenuEl.appendChild(button);
  }
  placeAt(contextMenuEl, x, y);
}

function loadRecentColours() {
  try {
    const stored = JSON.parse(localStorage.getItem("sprocket.recentColours") || "[]");
    return Array.isArray(stored) ? stored.filter((item) => ViewerColours.normaliseHex(item)) : [];
  } catch {
    return [];
  }
}
function saveRecentColours(list) {
  try {
    localStorage.setItem("sprocket.recentColours", JSON.stringify(list));
  } catch {
    // Private mode or blocked storage: the list just does not persist.
  }
}

function swatchButton(hex, name) {
  const button = document.createElement("button");
  button.className = "swatch";
  button.style.background = hex;
  button.title = name ? `${name} ${hex}` : hex;
  button.setAttribute("aria-label", button.title);
  button.dataset.hex = hex;
  button.addEventListener("click", () => pickColour(hex));
  return button;
}

/// The popover: palette, hex box, the system picker, recent colours, and
/// finishes. `finish` is {current, onPick(id or null)}, or null to hide them.
function openColourPicker(x, y, title, current, onPick, finish) {
  closeMenus();
  colourTarget = onPick;
  colourPopoverEl.querySelector(".popover-title").textContent = title;
  const palette = colourPopoverEl.querySelector(".palette");
  palette.innerHTML = "";
  for (const item of ViewerColours.PALETTE) {
    palette.appendChild(swatchButton(item.hex, item.name));
  }
  const recent = loadRecentColours();
  const recentEl = colourPopoverEl.querySelector(".recent");
  recentEl.innerHTML = "";
  for (const hex of recent) {
    recentEl.appendChild(swatchButton(hex));
  }
  colourPopoverEl.querySelector(".recent-label").style.display =
    recent.length > 0 ? "block" : "none";
  const finishesEl = colourPopoverEl.querySelector(".finishes");
  finishesEl.innerHTML = "";
  colourPopoverEl.querySelector(".finish-label").style.display = finish ? "block" : "none";
  if (finish) {
    for (const item of [...ViewerColours.MATERIALS, { id: null, name: "None" }]) {
      const button = document.createElement("button");
      button.textContent = item.name;
      button.dataset.material = item.id || "";
      if ((finish.current || null) === item.id) {
        button.className = "current";
      }
      button.addEventListener("click", () => {
        closeMenus();
        finish.onPick(item.id);
      });
      finishesEl.appendChild(button);
    }
  }
  const start =
    ViewerColours.normaliseHex(current) || `#${PART_COLOUR.toString(16).padStart(6, "0")}`;
  colourHexEl.value = start;
  colourHexEl.classList.remove("invalid");
  colourNativeEl.value = start;
  placeAt(colourPopoverEl, x, y);
  colourHexEl.focus();
  colourHexEl.select();
}

function pickColour(colour) {
  const hex = ViewerColours.normaliseHex(colour);
  if (!hex || !colourTarget) {
    return;
  }
  const target = colourTarget;
  saveRecentColours(ViewerColours.pushRecent(loadRecentColours(), hex));
  closeMenus();
  target(hex);
}

function applyHexBox() {
  const hex = ViewerColours.normaliseHex(colourHexEl.value);
  colourHexEl.classList.toggle("invalid", !hex);
  if (hex) {
    pickColour(hex);
  }
}
document.getElementById("colour-apply").addEventListener("click", applyHexBox);
colourHexEl.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    applyHexBox();
  }
});
colourHexEl.addEventListener("input", () => {
  const hex = ViewerColours.normaliseHex(colourHexEl.value);
  colourHexEl.classList.toggle("invalid", !hex && colourHexEl.value.length >= 4);
  if (hex) {
    colourNativeEl.value = hex;
  }
});
colourNativeEl.addEventListener("change", () => pickColour(colourNativeEl.value));

// A press anywhere else closes them; one inside does not.
globalThis.addEventListener(
  "pointerdown",
  (event) => {
    if (!menuOpen()) {
      return;
    }
    if (contextMenuEl.contains(event.target) || colourPopoverEl.contains(event.target)) {
      return;
    }
    closeMenus();
  },
  true,
);

/// The body that owns face `faceIndex`, from the mesh header's face counts.
function bodyOfFace(faceIndex) {
  let start = 0;
  for (const body of mesh.geometry.userData.bodies) {
    if (faceIndex < start + body.faces) {
      return body;
    }
    start += body.faces;
  }
  return null;
}

/// Colours or finishes face `faceIndex` (`change` is {color?, material?}),
/// or clears what reaches it when `change` is null. The daemon names the
/// entries on the face and a box that picks it out alone.
async function setFaceAppearance(faceIndex, change) {
  const info = await rpcCall("get_face_appearance", { handle: `f${faceIndex}` });
  const current = await rpcCall("get_document", {});
  const shown =
    globalThis.__tcad.faceColour(faceIndex) ||
    bodyOfFace(faceIndex)?.color ||
    `#${PART_COLOUR.toString(16).padStart(6, "0")}`;
  const appearances = change
    ? ViewerColours.withFaceAppearance(current.appearances, info, change, shown)
    : ViewerColours.withoutFaceColour(current.appearances, info);
  return sendCommand({ type: "set_appearances", appearances, base_revision: current.revision });
}

function bodyFinish(body) {
  return {
    current: body.material || null,
    onPick: (material) => sendCommand({ type: "set_body", id: body.id, material }),
  };
}

function openBodyMenu(x, y, body) {
  const hidden = body.visible === false;
  openMenu(x, y, body.name, [
    body.reference
      ? null
      : {
          label: "Colour…",
          action: () =>
            openColourPicker(
              x,
              y,
              `${body.name} colour`,
              body.color,
              (hex) => sendCommand({ type: "set_body", id: body.id, color: hex }),
              bodyFinish(body),
            ),
        },
    {
      label: hidden ? "Show" : "Hide",
      action: () => sendCommand({ type: "set_visibility", id: body.id, visible: hidden }),
    },
  ]);
}

async function openFaceMenu(event) {
  if (sketchStage || extrudeArmed || modifyTool || !mesh) {
    return;
  }
  const hit = raycastFace(event);
  if (!hit) {
    return;
  }
  const x = event.clientX;
  const y = event.clientY;
  const faceIndex = mesh.geometry.userData.faceOfTriangle[hit.faceIndex];
  const body = bodyOfFace(faceIndex);
  // The handle is only good for the part the daemon has now.
  const info =
    loadedRevision === daemonRevision
      ? await trackPending(rpcCall("get_face_appearance", { handle: `f${faceIndex}` })).catch(
          () => null,
        )
      : null;
  const clear = ViewerColours.clearLabel(info);
  const faceColour = globalThis.__tcad.faceColour(faceIndex) || body?.color;
  const ownEntry = info && [...info.entries].reverse().find((entry) => entry.faces === 1);
  const faceFinish = {
    current: ownEntry?.material || null,
    onPick: (material) => setFaceAppearance(faceIndex, { material }),
  };
  openMenu(x, y, `${body ? `${body.name} · ` : ""}face f${faceIndex}`, [
    {
      label: "Colour…",
      action: () =>
        openColourPicker(
          x,
          y,
          "Face colour",
          faceColour,
          (hex) => setFaceAppearance(faceIndex, { color: hex }),
          info ? faceFinish : null,
        ),
    },
    body && !body.reference
      ? {
          label: "Body colour…",
          action: () =>
            openColourPicker(
              x,
              y,
              `${body.name} colour`,
              body.color,
              (hex) => sendCommand({ type: "set_body", id: body.id, color: hex }),
              bodyFinish(body),
            ),
        }
      : null,
    body
      ? {
          label: "Hide body",
          action: () => sendCommand({ type: "set_visibility", id: body.id, visible: false }),
        }
      : null,
    clear ? { label: clear, action: () => setFaceAppearance(faceIndex, null) } : null,
  ]);
}

renderer.domElement.addEventListener("contextmenu", (event) => event.preventDefault());
renderer.domElement.addEventListener("pointerdown", (event) => {
  rightPress = event.button === 2 ? { x: event.clientX, y: event.clientY } : null;
});
renderer.domElement.addEventListener("pointerup", (event) => {
  if (event.button !== 2 || !rightPress) {
    return;
  }
  const moved = Math.hypot(event.clientX - rightPress.x, event.clientY - rightPress.y);
  rightPress = null;
  if (moved <= RIGHT_CLICK_SLOP) {
    openFaceMenu(event);
  }
});

// Ends a corner or edge drag and sends the new profile to Swift, which
// rewrites the sketch in place and replays everything after it (see
// ContentView's updateSketchPolygon). Not on the canvas: releasing outside
// it would otherwise leave the drag live.
globalThis.addEventListener("pointerup", () => {
  if (sketchStage === "draw") {
    sketcher.pointerUp();
  }
  if (!sketchEditDrag) {
    return;
  }
  const { feature, polygon } = sketchEditDrag;
  sketchEditDrag = null;
  controls.enabled = true;
  hideHint();
  sendCommand({ type: "update_sketch", id: feature.id, polygon: polygon });
});

// Selects a sketch feature, from a click on the sketch in the 3D view or on
// its timeline chip. A selected sketch is what E extrudes, and what a
// corner or edge drag edits - the user's own rule is "a sketch stays flat
// until you select it and choose extrude."
function selectSketchForExtrude(feature, regionIndex, additive) {
  if (extrudeArmed) {
    return;
  }
  const sameSketch = selectedSketch && selectedSketch.id === feature.id;
  const all = feature.regions.map((_, index) => index);
  if (regionIndex === undefined) {
    selectedRegions = all;
  } else if (additive && sameSketch) {
    selectedRegions = selectedRegions.includes(regionIndex)
      ? selectedRegions.filter((index) => index !== regionIndex)
      : [...selectedRegions, regionIndex];
  } else {
    selectedRegions = [regionIndex];
  }
  selectedSketch = feature;
  selectedPatch = null;
  clearEdgeSelection();
  clearHighlight();
  renderSketchSelection();
  hideHint();
  showHint(
    feature.polygon
      ? `Sketch ${feature.id} selected, drag a corner or edge to change it`
      : "Sketch " +
          feature.id +
          ": " +
          selectedRegions.length +
          " of " +
          all.length +
          " regions picked. Shift adds more, E extrudes.",
  );
  selectionChanged();
}
// How close, in screen pixels, the pointer must be to a corner or an edge
// to grab it. In pixels rather than mm so it holds at any zoom.
const SKETCH_PICK_PIXELS = 10;

/// Where the pointer sits on `plane`, in the plane's own [u, v], or null
/// when the ray runs parallel to it. The plane is infinite on purpose: a
/// corner can be dragged outside the polygon it belongs to.
function pointerOnSketchPlane(event, plane) {
  raycaster.setFromCamera(pointerNDC(event), camera);
  const normal = new THREE.Vector3().crossVectors(plane.xDir, plane.yDir).normalize();
  const hit = new THREE.Vector3();
  const infinite = new THREE.Plane().setFromNormalAndCoplanarPoint(normal, plane.origin);
  if (!raycaster.ray.intersectPlane(infinite, hit)) {
    return null;
  }
  const local = hit.sub(plane.origin);
  return [local.dot(plane.xDir), local.dot(plane.yDir)];
}

/// SKETCH_PICK_PIXELS as a distance in mm at `point`. A pick radius fixed
/// in mm would be unreachable zoomed out and swallow the whole sketch
/// zoomed in.
function worldRadius(pixels, point) {
  const viewDistance = camera.position.distanceTo(point);
  const worldPerPixel =
    (2 * Math.tan((camera.fov * Math.PI) / 360) * viewDistance) / renderer.domElement.clientHeight;
  return pixels * worldPerPixel;
}

function pointToSegment([px, py], [ax, ay], [bx, by]) {
  const dx = bx - ax;
  const dy = by - ay;
  const lengthSq = dx * dx + dy * dy;
  const t = lengthSq ? Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / lengthSq)) : 0;
  return Math.hypot(px - ax - t * dx, py - ay - t * dy);
}

/// What the pointer is over on `feature`'s outline: { kind, indices, uv,
/// plane } for a corner or an edge, else null. Corners win over edges at
/// equal distance, since a corner sits on two of them.
function pickSketchPart(event, feature) {
  if (!feature.plane || !feature.polygon) {
    return null;
  }
  if (hiddenSketchIDs.has(feature.id)) {
    return null;
  }
  const plane = resolveStoredPlane(feature.plane);
  const uv = pointerOnSketchPlane(event, plane);
  if (!uv) {
    return null;
  }
  const world = plane.origin
    .clone()
    .addScaledVector(plane.xDir, uv[0])
    .addScaledVector(plane.yDir, uv[1]);
  const polygon = feature.polygon;

  let best = { distance: worldRadius(SKETCH_PICK_PIXELS, world), kind: null, indices: null };
  polygon.forEach(([u, v], index) => {
    const distance = Math.hypot(u - uv[0], v - uv[1]);
    if (distance < best.distance) {
      best = { distance, kind: "corner", indices: [index] };
    }
  });
  if (!best.kind) {
    polygon.forEach((_, index) => {
      const next = (index + 1) % polygon.length;
      const distance = pointToSegment(uv, polygon[index], polygon[next]);
      if (distance < best.distance) {
        best = { distance, kind: "edge", indices: [index, next] };
      }
    });
  }
  return best.kind ? { kind: best.kind, indices: best.indices, uv: uv, plane: plane } : null;
}

function beginSketchEdit(pick) {
  sketchEditDrag = {
    feature: selectedSketch,
    plane: pick.plane,
    kind: pick.kind,
    indices: pick.indices,
    polygon: selectedSketch.polygon.map(([u, v]) => [u, v]),
    lastUV: pick.uv,
  };
  controls.enabled = false;
  showHint(pick.kind === "corner" ? "Dragging a corner" : "Dragging an edge");
}

function dragSketchEdit(event) {
  const uv = pointerOnSketchPlane(event, sketchEditDrag.plane);
  if (!uv) {
    return;
  }
  let du = uv[0] - sketchEditDrag.lastUV[0];
  let dv = uv[1] - sketchEditDrag.lastUV[1];
  if (sketchEditDrag.kind === "edge") {
    // An edge moves perpendicular to itself, so it stays parallel and a
    // dragged rectangle stays a rectangle. Dragging along the edge does
    // nothing, the same as in Fusion.
    const [a, b] = sketchEditDrag.indices.map((index) => sketchEditDrag.polygon[index]);
    const length = Math.hypot(b[0] - a[0], b[1] - a[1]);
    if (length > 1e-9) {
      const nu = -(b[1] - a[1]) / length;
      const nv = (b[0] - a[0]) / length;
      const along = du * nu + dv * nv;
      du = along * nu;
      dv = along * nv;
    }
  }
  sketchEditDrag.indices.forEach((index) => {
    sketchEditDrag.polygon[index][0] += du;
    sketchEditDrag.polygon[index][1] += dv;
  });
  sketchEditDrag.lastUV = uv;
  redrawSketchEdit();
}

/// Redraws the dragged sketch from its working polygon, so the outline and
/// its handles follow the pointer before the engine has rebuilt anything.
/// The invisible fill is left stale: nothing raycasts it mid-drag, and the
/// rebuild rebuilds it anyway.
function redrawSketchEdit() {
  const entry = sketchOutlines.find(({ feature }) => feature.id === sketchEditDrag.feature.id);
  if (!entry) {
    return;
  }
  const { plane, polygon } = sketchEditDrag;
  const points = polygon.map(([u, v]) =>
    plane.origin.clone().addScaledVector(plane.xDir, u).addScaledVector(plane.yDir, v),
  );
  entry.line.geometry.dispose();
  entry.line.geometry = new THREE.BufferGeometry().setFromPoints(points.concat([points[0]]));
  entry.handles.geometry.dispose();
  entry.handles.geometry = new THREE.BufferGeometry().setFromPoints(points);
}

/// Opens extrude on the selected face. The preview starts at `distance`
/// (0 on a fresh start, so nothing shows until the arrow moves), and the
/// arrow on the face is what sets it.
function armExtrude(distance = 0) {
  armedMode = "face";
  const { positions: basePositions, offsets: offsetFlags } = buildPreviewArrays(selectedPatch);
  const previewGeometry = new THREE.BufferGeometry();
  previewGeometry.setAttribute(
    "position",
    new THREE.Float32BufferAttribute(basePositions.slice(), 3),
  );
  const previewMaterial = new THREE.MeshStandardMaterial({
    color: srgb(0x5b_e0_a0),
    transparent: true,
    opacity: 0.55,
    side: THREE.DoubleSide,
  });
  const previewMesh = new THREE.Mesh(previewGeometry, previewMaterial);
  scene.add(previewMesh);

  previewState = { basePositions, offsetFlags, previewMesh, previewGeometry, distance };
  openExtrude(selectedPatch.anchorPoint, selectedPatch.normal);
}

// Opens extrude on the selected sketch's picked regions (see
// selectSketchForExtrude). There is no mesh patch to push: the "face" is
// the regions' own flat outline, and its normal is the sketch plane's.
function armSketchExtrude(feature, distance = 0) {
  const plane = resolveStoredPlane(feature.plane);
  const axis = new THREE.Vector3().crossVectors(plane.xDir, plane.yDir).normalize();
  const picked = selectedRegions.map((index) => feature.regions[index]);
  if (picked.length === 0) {
    return;
  }
  armedMode = "sketch";
  // The arrow stands on the first picked region's interior point, which is
  // inside it even when the outline is concave.
  const anchor = worldOfPlane(plane, picked[0].inside);

  const previewMaterial = new THREE.MeshStandardMaterial({
    color: srgb(0x5b_e0_a0),
    transparent: true,
    opacity: 0.55,
    side: THREE.DoubleSide,
  });
  const previewMesh = new THREE.Mesh(new THREE.BufferGeometry(), previewMaterial);
  scene.add(previewMesh);

  sketchExtrudeFeature = feature;
  sketchExtrudeRegions = picked;
  previewState = { previewMesh, previewGeometry: previewMesh.geometry, distance };
  openExtrude(anchor, axis);
}

/// What both arms share: the panel, and the arrow at `anchor` along `axis`.
/// The camera stays free, so you can orbit to see the arrow side on.
function openExtrude(anchor, axis) {
  extrudeArmed = true;
  hideHint();
  selectionChanged();
  panelEl.style.display = "flex";
  setExtrudeDistance(previewState.distance);
  showLinearHandle({
    origin: anchor.clone(),
    direction: axis.clone().normalize(),
    value: previewState.distance,
    onChange: setExtrudeDistance,
  });
}

// Builds a prism from the sketch's own polygon, extruded along its plane
// normal by `distance` (negative goes the other way, previewing a cut).
function buildSketchExtrudePreviewGeometry(feature, regions, distance) {
  const plane = resolveStoredPlane(feature.plane);
  const shapes = regions.map((region) => {
    const shape = new THREE.Shape(region.outline.map(([u, v]) => new THREE.Vector2(u, v)));
    region.holes.forEach((hole) =>
      shape.holes.push(new THREE.Path(hole.map(([u, v]) => new THREE.Vector2(u, v)))),
    );
    return shape;
  });
  const depth = Math.max(Math.abs(distance), 0.001);
  const geometry = new THREE.ExtrudeGeometry(shapes, {
    depth,
    bevelEnabled: false,
    curveSegments: 1,
  });
  if (distance < 0) {
    geometry.translate(0, 0, -depth);
  }
  const normal = new THREE.Vector3().crossVectors(plane.xDir, plane.yDir).normalize();
  const basis = new THREE.Matrix4().makeBasis(plane.xDir, plane.yDir, normal);
  basis.setPosition(plane.origin);
  geometry.applyMatrix4(basis);
  return geometry;
}

/// The preview at `distance`, from the arrow or the box. At 0 there is
/// nothing to show.
function setExtrudeDistance(distance) {
  previewState.distance = distance;
  previewState.previewMesh.visible = distance !== 0;
  if (armedMode === "sketch") {
    const geometry = buildSketchExtrudePreviewGeometry(
      sketchExtrudeFeature,
      sketchExtrudeRegions,
      distance,
    );
    previewState.previewMesh.geometry.dispose();
    previewState.previewMesh.geometry = geometry;
    previewState.previewGeometry = geometry;
    return;
  }
  const { basePositions, offsetFlags, previewGeometry } = previewState;
  const arr = previewGeometry.attributes.position.array;
  const axis = selectedPatch.normal;
  for (let i = 0; i < offsetFlags.length; i++) {
    const o = offsetFlags[i] * distance;
    arr[i * 3] = basePositions[i * 3] + axis.x * o;
    arr[i * 3 + 1] = basePositions[i * 3 + 1] + axis.y * o;
    arr[i * 3 + 2] = basePositions[i * 3 + 2] + axis.z * o;
  }
  previewGeometry.attributes.position.needsUpdate = true;
  previewGeometry.computeVertexNormals();
}

function cancelExtrude() {
  scene.remove(previewState.previewMesh);
  previewState.previewGeometry.dispose();
  previewState.previewMesh.material.dispose();
  previewState = null;
  extrudeArmed = false;
  armedMode = "face";
  sketchExtrudeFeature = null;
  hideHandle();
  clearHover();
  panelEl.style.display = "none";
  updateToolbarState();
}

/// A click in the view while extrude is open: a face or a region moves the
/// extrude there, keeping the distance. Shift adds or removes a region of
/// the same sketch.
function reselectExtrude(pick, additive) {
  const distance = previewState.distance;
  cancelExtrude();
  applyPick(pick, additive && pick.kind === "region");
  if (selectedPatch) {
    armExtrude(distance);
  } else if (selectedSketch && selectedRegions.length > 0) {
    armSketchExtrude(selectedSketch, distance);
  }
}

function commitExtrude() {
  if (previewState.distance === 0) {
    showNotice("Drag the arrow or type a distance first.", 3000);
    return;
  }
  const message =
    armedMode === "sketch"
      ? {
          type: "extrude_sketch",
          profiles: sketchExtrudeRegions.map((region) => ({
            sketch: sketchExtrudeFeature.id,
            bounded_by: region.bounded_by,
            inside: region.inside,
          })),
          distance: previewState.distance,
        }
      : {
          type: "extrude_face",
          point: selectedPatch.anchorPoint.toArray(),
          normal: selectedPatch.normal.toArray(),
          distance: previewState.distance,
        };
  const faceHandle = armedMode === "sketch" ? null : `f${selectedPatch.faceIndex}`;
  cancelExtrude();
  if (!faceHandle) {
    sendCommand(message);
    return;
  }
  // The engine turns the picked face into a selector that follows the part
  // through later edits. If it cannot, the face is named by where it was clicked.
  trackPending(
    rpcCall("resolve_selection", { handles: [faceHandle] }).then(
      (answer) => sendCommand({ ...message, face: answer.selector }),
      () => sendCommand(message),
    ),
  );
}

document.getElementById("extrude-ok").addEventListener("click", () => {
  if (extrudeArmed) {
    commitExtrude();
  }
});
document.getElementById("extrude-cancel").addEventListener("click", () => {
  if (extrudeArmed) {
    cancelExtrude();
  }
});

// --- Modify tools: fillet, chamfer, shell, hole -----------------------
// The same path as an agent's: resolve_selection turns the picked handles
// into a selector, preview_feature builds the feature against a copy of
// the document (with the mesh, drawn here over the real part), and OK
// sends it as one edit. The fields and the feature they make are in
// modify.js.
const modifyPanelEl = document.getElementById("modify-panel");
const modifyFieldsEl = document.getElementById("modify-fields");
const modifyInfoEl = document.getElementById("modify-info");
const modifyOkEl = document.getElementById("modify-ok");
let modifyPreview = null; // { mesh, lines }: the previewed part, drawn over the real one. A construct tool's is {mesh: group, lines: null}
const NOT_SOLIDS = ["sketch", "plane", "axis", "point"]; // features that build no body
const PREVIEW_DELAY_MS = 200;

/// The bodies of the part on screen, reference bodies left out.
function partBodies() {
  return mesh ? mesh.geometry.userData.bodies.filter((body) => !body.reference) : [];
}

/// The body Mirror, a pattern or Combine acts on: the one picked in the
/// browser tree, else the one owning the picked edges or face, else the only
/// body there is. Null when it is not clear.
function pickedBody() {
  const bodies = partBodies();
  const named = bodies.find((body) => body.id === selectedBodyId);
  if (named) {
    return named;
  }
  let owner = null;
  if (selectedEdges.length > 0) {
    owner = bodyOfEdge(selectedEdges[0]);
  } else if (selectedPatch) {
    owner = bodyOfFace(selectedPatch.faceIndex);
  }
  if (owner && !owner.reference) {
    return owner;
  }
  return bodies.length === 1 ? bodies[0] : null;
}

function selectBody(id) {
  selectedBodyId = id;
  updateToolbarState();
  renderBrowser();
}

/// The newest feature that makes a solid: the base when the mesh header lists no bodies.
function tipFeatureId() {
  const solids = browserFeatures.filter(
    (entry) => entry.active && !NOT_SOLIDS.includes(entry.type),
  );
  return solids.length > 0 ? solids.at(-1).id : null;
}

/// The body that owns edge `edgeIndex`, from the mesh header's edge counts.
function bodyOfEdge(edgeIndex) {
  let start = 0;
  for (const body of mesh.geometry.userData.bodies) {
    if (edgeIndex < start + body.edges) {
      return body;
    }
    start += body.edges;
  }
  return null;
}

/// The body a Modify tool works on, from what is picked: the body of the
/// selected edges or of the selected face. With nothing picked and one
/// body, that body. {body} or {error}.
function modifyBody(kind) {
  const bodies = mesh.geometry.userData.bodies;
  let owners = [];
  if ((kind === "fillet" || kind === "chamfer") && selectedEdges.length > 0) {
    owners = selectedEdges.map(bodyOfEdge);
  } else if (selectedPatch) {
    owners = [bodyOfFace(selectedPatch.faceIndex)];
  } else if (bodies.filter((body) => !body.reference).length === 1) {
    owners = [bodies.find((body) => !body.reference)];
  } else {
    return { error: "Pick a face of the body to shell first." };
  }
  const ids = new Set(owners.map((body) => body?.id));
  if (ids.size !== 1) {
    return { error: "Pick edges of one body." };
  }
  const body = owners[0];
  if (!body) {
    return { error: "That pick is not on a body." };
  }
  if (body.reference) {
    return { error: `${body.name} is a reference body.` };
  }
  return { body };
}

/// Triangle index ranges (start, count) of faces first..first+count-1.
/// The mesh groups are one per face, in body order.
function faceTriangleRange(first, count) {
  const groups = mesh.geometry.groups;
  const last = groups[first + count - 1];
  return { start: groups[first].start, count: last.start + last.count - groups[first].start };
}

/// The vertices of the triangles in `range`, passed to `visit(x, y, z)`.
function visitVertices(range, visit) {
  const index = mesh.geometry.index;
  const position = mesh.geometry.attributes.position;
  for (let i = range.start; i < range.start + range.count; i++) {
    const corner = index.getX(i);
    visit(position.getX(corner), position.getY(corner), position.getZ(corner));
  }
}

/// What a Modify tool sizes its starting values by: `body`'s bounding box,
/// and for a hole the two sides of the picked face.
function modifyScale(kind, body) {
  const bodies = mesh.geometry.userData.bodies;
  let firstFace = 0;
  for (const other of bodies) {
    if (other === body) {
      break;
    }
    firstFace += other.faces;
  }
  const range =
    bodies.length > 0
      ? faceTriangleRange(firstFace, body.faces)
      : { start: 0, count: mesh.geometry.index.count };
  const box = new THREE.Box3();
  visitVertices(range, (x, y, z) => box.expandByPoint(new THREE.Vector3(x, y, z)));
  const size = box.getSize(new THREE.Vector3());
  const scale = { box: [size.x, size.y, size.z] };
  if (kind === "hole" && selectedPatch) {
    const normal = selectedPatch.normal;
    const across =
      Math.abs(normal.x) < 0.9 ? new THREE.Vector3(1, 0, 0) : new THREE.Vector3(0, 1, 0);
    const u = across.sub(normal.clone().multiplyScalar(across.dot(normal))).normalize();
    const v = normal.clone().cross(u);
    const low = [Number.POSITIVE_INFINITY, Number.POSITIVE_INFINITY];
    const high = [Number.NEGATIVE_INFINITY, Number.NEGATIVE_INFINITY];
    const point = new THREE.Vector3();
    visitVertices(faceTriangleRange(selectedPatch.faceIndex, 1), (x, y, z) => {
      point.set(x, y, z);
      [point.dot(u), point.dot(v)].forEach((value, axis) => {
        low[axis] = Math.min(low[axis], value);
        high[axis] = Math.max(high[axis], value);
      });
    });
    scale.face = [high[0] - low[0], high[1] - low[1]];
  }
  return scale;
}

/// `options` is what a reselect carries over: {values} keeps the panel's
/// values, {openFaces} is the shell's list of faces to open.
async function armModify(kind, options = {}) {
  if (modifyTool || extrudeArmed || sketchStage) {
    return;
  }
  cancelPick();
  if (kind !== "revolve" && !mesh) {
    return;
  }
  // Nothing picked yet: ask for it, and open the tool on the first pick.
  const needsPick =
    ((kind === "fillet" || kind === "chamfer") && selectedEdges.length === 0) ||
    (kind === "hole" && !selectedPatch) ||
    (kind === "revolve" && !selectedSketch);
  if (needsPick && !(kind === "revolve" && toolRevolveEl.disabled)) {
    return startPick(kind);
  }
  let base = null;
  let scale = null;
  if (kind !== "revolve") {
    if (mesh.geometry.userData.bodies.length === 0) {
      base = tipFeatureId(); // an engine that sends no body list
    } else {
      if (kind === "hole" && !selectedPatch) {
        return showNotice("Click the face to drill first.");
      }
      const found = modifyBody(kind);
      if (found.error) {
        return showNotice(found.error);
      }
      base = found.body.tip;
      scale = modifyScale(kind, found.body);
    }
    if (!base) {
      return;
    }
  }
  const target = { base };
  let handles = [];
  if (kind === "revolve") {
    if (!selectedSketch) {
      return showNotice("Select a sketch first.");
    }
    target.profile = selectedSketch.id;
  } else if (kind === "fillet" || kind === "chamfer") {
    handles = selectedEdges.map((index) => `e${index}`);
  } else if (kind === "hole") {
    if (!selectedPatch) {
      return showNotice("Click the face to drill first.");
    }
    target.point = selectedPatch.anchorPoint.toArray();
    target.normal = selectedPatch.normal.toArray();
  }
  const openFaces =
    kind !== "shell" ? [] : options.openFaces || (selectedPatch ? [selectedPatch.faceIndex] : []);
  if (kind === "shell") {
    handles = openFaces.map((face) => `f${face}`);
  }
  const values = options.values ? { ...options.values } : ViewerModify.defaults(kind, scale);
  const tool = {
    kind,
    target,
    values,
    openFaces,
    seq: 0,
    ok: false,
    dirty: false,
    timer: null,
    inflight: null,
    feature: null,
  };
  if (kind === "revolve") {
    // Where the ring starts: the picked region's interior point, swept round the axis.
    const region = selectedSketch.regions[selectedRegions.length > 0 ? selectedRegions[0] : 0];
    if (region) {
      tool.profilePoint = worldOfPlane(resolveStoredPlane(selectedSketch.plane), region.inside);
    }
  }
  modifyTool = tool; // claims the tool before the first await, so a second key press does nothing
  updateToolbarState();
  if (handles.length > 0) {
    try {
      const answer = await trackPending(rpcCall("resolve_selection", { handles }));
      if (kind === "shell") {
        target.open = answer.selector;
      } else {
        target.edges = answer.selector;
      }
    } catch (error) {
      if (modifyTool === tool) {
        modifyTool = null;
        updateToolbarState();
      }
      warningEl.textContent = error.message;
      warningEl.style.display = "block";
      return;
    }
  }
  if (modifyTool !== tool) {
    return; // cancelled while the selector was made
  }
  document.getElementById("modify-title").textContent = ViewerModify.TOOLS[kind].title;
  modifyPanelEl.style.display = "flex";
  renderModifyFields();
  const first = modifyFieldsEl.querySelector("input[type=text]");
  if (first) {
    first.focus();
    first.select();
  }
  refreshModifyHandle(true);
  runPreview();
}

// --- A Modify tool's handle -------------------------------------------
// The field each tool's handle drives. The handle and the panel field are
// two views of one value: a drag writes the field, typing in either moves
// the handle. Mirror, Combine, Axis and Point have no size to drag.
const HANDLE_FIELDS = {
  fillet: "radius",
  chamfer: "length",
  shell: "thickness",
  hole: "depth",
  plane: "offset",
  linear: "spacing",
  revolve: "angle",
  circular: "angle",
};
const AXIS_VECTORS = { X: [1, 0, 0], Y: [0, 1, 0], Z: [0, 0, 1] };

/// Puts the open tool's handle where its picks and values say. `focus`
/// moves the caret to the handle's box: true when the tool opens, false
/// when a select or a checkbox changed and keeps the caret.
function refreshModifyHandle(focus) {
  const tool = modifyTool;
  hideHandle();
  if (!tool) {
    return;
  }
  const name = HANDLE_FIELDS[tool.kind];
  const field = name && ViewerModify.TOOLS[tool.kind].fields.find((item) => item.name === name);
  if (!field || !ViewerModify.visible(field, tool.values)) {
    return;
  }
  const place = modifyHandlePlace(tool);
  if (!place) {
    return;
  }
  const value = ViewerModify.parseNumber(tool.values[name]);
  const onChange = (next) => {
    tool.values[name] = String(next);
    const input = document.getElementById(`modify-${name}`);
    if (input) {
      input.value = String(next);
    }
    schedulePreview();
  };
  const spec = { ...place, value: Number.isFinite(value) ? value : 0, onChange, focus };
  if (place.axis) {
    showAngularHandle(spec);
  } else {
    showLinearHandle(spec);
  }
}

function vector(values) {
  return new THREE.Vector3(...values);
}

/// `point` moved onto the line through `origin` along unit `direction`.
function ontoAxis(point, origin, direction) {
  return origin.clone().addScaledVector(direction, point.clone().sub(origin).dot(direction));
}

/// The bounding box of `body`'s faces, or of the whole part when the mesh
/// lists no bodies.
function bodyBox(body) {
  const box = new THREE.Box3();
  const bodies = mesh.geometry.userData.bodies;
  if (!body || bodies.length === 0) {
    return box.setFromObject(mesh);
  }
  let firstFace = 0;
  for (const other of bodies) {
    if (other === body) {
      break;
    }
    firstFace += other.faces;
  }
  visitVertices(faceTriangleRange(firstFace, body.faces), (x, y, z) =>
    box.expandByPoint(new THREE.Vector3(x, y, z)),
  );
  return box;
}

/// Where an axis choice lies: {origin, direction} as Vector3s, or null.
/// `where` holds what pick_axis resolved for a picked edge or face.
function axisOfChoice(choice, where) {
  if (typeof choice === "string" && AXIS_VECTORS[choice]) {
    return { origin: new THREE.Vector3(), direction: vector(AXIS_VECTORS[choice]) };
  }
  const found = choice?.construct
    ? constructObjects.find((item) => item.id === choice.construct)?.info
    : where;
  if (!found?.direction) {
    return null;
  }
  return { origin: vector(found.origin), direction: vector(found.direction).normalize() };
}

/// A ring round `axis` that starts at `point`: its centre is `point`
/// moved onto the axis, and its radius reaches back to `point`.
function ringThrough(point, axis) {
  const centre = ontoAxis(point, axis.origin, axis.direction);
  const out = point.clone().sub(centre);
  const radius = out.length();
  const reference =
    radius > 1e-6
      ? out.divideScalar(radius)
      : vector(ViewerHandles.anyPerpendicular(axis.direction.toArray()));
  return { centre, axis: axis.direction, reference, radius: radius > 1e-6 ? radius : null };
}

/// The handle for `tool`: {origin, direction, rule} for an arrow, or
/// {centre, axis, reference, radius, rule} for a ring. Null when there is
/// nowhere sensible to put one.
function modifyHandlePlace(tool) {
  const positive = { min: 0.01 };
  const { kind, target, values } = tool;
  if (kind === "fillet" || kind === "chamfer") {
    if (selectedEdges.length === 0) {
      return null;
    }
    // From outside the corner, pointing at the last picked edge and into the
    // material: dragging toward the edge rounds or cuts more.
    const edge = selectedEdges.at(-1);
    return {
      origin: edgeMidpoint(edge),
      direction: edgeOutward(edge).negate(),
      rule: positive,
      pointAt: true,
    };
  }
  if (kind === "shell") {
    const sign = values.outward ? 1 : -1;
    if (tool.openFaces.length > 0 && !values.closed) {
      const face = tool.openFaces[0];
      return {
        origin: faceCentre(face),
        direction: facePatch(face).normal.multiplyScalar(sign),
        rule: positive,
      };
    }
    const box = bodyBox(pickedBody());
    const top = box.getCenter(new THREE.Vector3()).setZ(box.max.z);
    return { origin: top, direction: new THREE.Vector3(0, 0, sign), rule: positive };
  }
  if (kind === "hole") {
    return {
      origin: vector(target.point),
      direction: vector(target.normal).negate(),
      rule: positive,
    };
  }
  if (kind === "plane") {
    const choice = target.choices.plane?.[values.plane];
    let plane = null;
    if (typeof choice === "string" && SKETCH_PRINCIPAL_PLANES[choice]) {
      plane = SKETCH_PRINCIPAL_PLANES[choice];
    } else if (choice?.construct) {
      const info = constructObjects.find((item) => item.id === choice.construct)?.info;
      if (info) {
        plane = { origin: vector(info.origin), normal: vector(info.normal) };
      }
    } else if (values.plane === "face" && target.where.face) {
      plane = {
        origin: vector(target.where.face.origin),
        normal: vector(target.where.face.normal),
      };
    }
    if (!plane) {
      return null;
    }
    // On the plane, level with the middle of the part, so the arrow is
    // beside what it offsets rather than out at the plane's origin.
    const normal = plane.normal.clone().normalize();
    const middle = mesh
      ? new THREE.Box3().setFromObject(mesh).getCenter(new THREE.Vector3())
      : plane.origin.clone();
    const origin = middle
      .clone()
      .addScaledVector(normal, -middle.clone().sub(plane.origin).dot(normal));
    return { origin, direction: normal, rule: {} };
  }
  if (kind === "linear") {
    const axis = axisOfChoice(target.choices.direction?.[values.direction], target.where.direction);
    if (!axis) {
      return null;
    }
    // From the middle of the body, so the arrow's base sits where the first copy's middle goes.
    return {
      origin: bodyBox(pickedBody()).getCenter(new THREE.Vector3()),
      direction: axis.direction,
      rule: {},
    };
  }
  if (kind === "circular") {
    const axis = axisOfChoice(target.choices.axis?.[values.axis], target.where.axis);
    if (!axis) {
      return null;
    }
    return {
      ...ringThrough(bodyBox(pickedBody()).getCenter(new THREE.Vector3()), axis),
      rule: { min: 1, max: 360 },
    };
  }
  if (kind === "revolve") {
    const axis = axisOfChoice(values.axis, null);
    if (!axis || !tool.profilePoint) {
      return null;
    }
    return { ...ringThrough(tool.profilePoint, axis), rule: { min: 1, max: 360 } };
  }
  return null;
}

/// A click in the view while a Modify tool is open. A hole moves to the
/// face clicked. Fillet, chamfer and revolve take the new pick, with Shift
/// adding edges; shell opens the face clicked, with Shift adding faces.
/// The values typed so far carry over.
function reselectModify(pick, additive) {
  const tool = modifyTool;
  if (tool.kind === "hole") {
    tool.target.point = pick.hit.point.toArray();
    tool.target.normal = facePatchAt(pick.hit).normal.toArray();
    schedulePreview();
    refreshModifyHandle(false);
    return;
  }
  const values = tool.values;
  let openFaces;
  if (tool.kind === "shell") {
    const face = mesh.geometry.userData.faceOfTriangle[pick.hit.faceIndex];
    if (!additive) {
      openFaces = [face];
    } else {
      openFaces = tool.openFaces.includes(face)
        ? tool.openFaces.filter((item) => item !== face)
        : [...tool.openFaces, face];
    }
  }
  cancelModify();
  applyPick(pick, additive && pick.kind !== "face");
  armModify(tool.kind, { values, openFaces });
}

function renderModifyFields(focusName) {
  const tool = modifyTool;
  modifyFieldsEl.innerHTML = "";
  for (const field of ViewerModify.TOOLS[tool.kind].fields) {
    if (field.onlyWithFace && !tool.target.open) {
      continue;
    }
    if (!ViewerModify.visible(field, tool.values)) {
      continue;
    }
    const label = document.createElement("label");
    label.append(field.label);
    let input;
    if (field.type === "select") {
      input = document.createElement("select");
      const offered = field.dynamic
        ? tool.target.options[field.name] || []
        : field.options.map((option) => ({ value: option, label: option }));
      for (const option of offered) {
        input.add(new Option(option.label, option.value));
      }
      input.value = tool.values[field.name];
    } else {
      input = document.createElement("input");
      input.type = field.type === "checkbox" ? "checkbox" : "text";
      if (field.type === "checkbox") {
        input.checked = tool.values[field.name];
      } else {
        input.value = tool.values[field.name];
        input.autocomplete = "off";
        input.spellcheck = false;
      }
    }
    input.id = `modify-${field.name}`;
    // A text field updates the preview as you type. A select or a checkbox
    // can show or hide other fields, so the row is drawn again.
    input.addEventListener(field.type === "number" ? "input" : "change", () => {
      tool.values[field.name] = field.type === "checkbox" ? input.checked : input.value;
      if (field.type !== "number") {
        renderModifyFields(field.name);
      }
      schedulePreview();
      // The handle follows: its value, or where it stands when a choice moved it.
      if (field.name === HANDLE_FIELDS[tool.kind]) {
        setHandleValue(ViewerModify.parseNumber(input.value));
      } else if (field.type !== "number") {
        refreshModifyHandle(false);
      }
    });
    label.append(input);
    modifyFieldsEl.append(label);
  }
  if (focusName) {
    const again = document.getElementById(`modify-${focusName}`);
    if (again) {
      again.focus();
    }
  }
}

function setModifyInfo(text, bad) {
  modifyInfoEl.textContent = text;
  modifyInfoEl.classList.toggle("bad", Boolean(bad));
}

function schedulePreview() {
  const tool = modifyTool;
  tool.ok = false;
  tool.dirty = true;
  modifyOkEl.disabled = true;
  clearTimeout(tool.timer);
  tool.timer = setTimeout(runPreview, PREVIEW_DELAY_MS);
}

/// Builds the feature against a copy of the document and draws the result.
/// A reply that a newer call has overtaken is dropped.
function runPreview() {
  const tool = modifyTool;
  if (!tool) {
    return Promise.resolve();
  }
  clearTimeout(tool.timer);
  tool.dirty = false;
  const seq = ++tool.seq;
  tool.ok = false;
  modifyOkEl.disabled = true;
  const built = ViewerModify.featureFor(tool.kind, tool.values, tool.target);
  if (built.error) {
    tool.feature = null;
    clearModifyPreview();
    setModifyInfo(built.error, true);
    return Promise.resolve();
  }
  tool.feature = built;
  setModifyInfo("Previewing…");
  tool.inflight = (async () => {
    let result;
    try {
      result = await trackPending(
        rpcCall("preview_feature", {
          feature: { id: "preview", ...built },
          mesh: !ViewerModify.CONSTRUCT_KINDS.includes(tool.kind),
        }),
      );
    } catch (error) {
      result = { would_build: false, reason: error.message };
    }
    if (modifyTool !== tool || seq !== tool.seq) {
      return;
    }
    if (result.would_build && result.construct) {
      showConstructPreview(result.construct);
      tool.ok = true;
      modifyOkEl.disabled = false;
      setModifyInfo(`Preview: ${describeConstruct(result.construct)}. Enter applies.`);
    } else if (result.would_build) {
      showModifyPreview(result.mesh);
      tool.ok = true;
      modifyOkEl.disabled = false;
      setModifyInfo(
        "Preview: " +
          result.counts.faces +
          " faces, volume " +
          millimetres(result.volume) +
          " mm³. Enter applies.",
      );
    } else {
      clearModifyPreview();
      setModifyInfo(result.reason, true);
    }
  })();
  return tool.inflight;
}

function showModifyPreview(data) {
  clearModifyPreview();
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(new Float32Array(data.vertices), 3));
  geometry.setIndex(new THREE.BufferAttribute(new Uint32Array(data.triangles), 1));
  geometry.computeVertexNormals();
  const previewMesh = new THREE.Mesh(
    geometry,
    new THREE.MeshStandardMaterial({
      color: srgb(0x5b_e0_a0),
      transparent: true,
      opacity: 0.8,
      side: THREE.DoubleSide,
    }),
  );
  const lineGeometry = new THREE.BufferGeometry();
  lineGeometry.setAttribute("position", new THREE.BufferAttribute(new Float32Array(data.edges), 3));
  const lines = new THREE.LineSegments(
    lineGeometry,
    new THREE.LineBasicMaterial({ color: srgb(0x0a_3a_26) }),
  );
  scene.add(previewMesh);
  scene.add(lines);
  modifyPreview = { mesh: previewMesh, lines };
  if (!mesh && modifyTool && !modifyTool.framed) {
    modifyTool.framed = true; // the first solid: nothing on screen to look at yet
    frameCameraOn(previewMesh);
  }
  // The real part would show through the green one, so it steps aside.
  if (mesh) {
    mesh.visible = false;
  }
  if (edgeLines) {
    edgeLines.visible = false;
  }
  clearHighlight(); // the picked face's tint would cover a hole or an opening
}

/// A construct feature's preview: the plane, axis or point, drawn bright.
function showConstructPreview(info) {
  clearModifyPreview();
  const object = makeConstructObject(info, true);
  scene.add(object);
  modifyPreview = { mesh: object, lines: null };
  if (!mesh && modifyTool && !modifyTool.framed) {
    modifyTool.framed = true;
    frameCameraOn(object);
  }
}

const roundedNumbers = (values) => values.map((value) => Math.round(value * 100) / 100).join(", ");

function describeConstruct(info) {
  if (info.kind === "plane") {
    return (
      "plane through (" +
      roundedNumbers(info.origin) +
      "), normal (" +
      roundedNumbers(info.normal) +
      ")"
    );
  }
  if (info.kind === "axis") {
    return (
      "axis through (" +
      roundedNumbers(info.origin) +
      "), along (" +
      roundedNumbers(info.direction) +
      ")"
    );
  }
  return `point at (${roundedNumbers(info.at)})`;
}

function clearModifyPreview() {
  if (modifyPreview) {
    for (const item of [modifyPreview.mesh, modifyPreview.lines]) {
      if (!item) {
        continue;
      }
      scene.remove(item);
      item.traverse((part) => {
        if (part.geometry) {
          part.geometry.dispose();
        }
        if (part.material) {
          part.material.dispose();
        }
      });
    }
    modifyPreview = null;
  }
  applyVisibility();
  if (selectedPatch && mesh) {
    setHighlight(selectedPatch, 0xff_dd_55);
  }
}

function cancelModify() {
  if (!modifyTool) {
    return;
  }
  clearTimeout(modifyTool.timer);
  modifyTool = null;
  hideHandle();
  clearHover();
  clearModifyPreview();
  modifyPanelEl.style.display = "none";
  setModifyInfo("");
  if (document.activeElement && modifyPanelEl.contains(document.activeElement)) {
    document.activeElement.blur();
  }
  updateToolbarState();
}

async function commitModify() {
  const tool = modifyTool;
  if (!tool) {
    return;
  }
  if (tool.dirty) {
    runPreview();
  }
  await tool.inflight;
  if (modifyTool !== tool || !tool.ok) {
    return; // the info line says why it would not build
  }
  const feature = tool.feature;
  cancelModify();
  sendCommand({ type: "add_feature", feature });
}

modifyOkEl.addEventListener("click", commitModify);
document.getElementById("modify-cancel").addEventListener("click", cancelModify);
toolFilletEl.addEventListener("click", () => armModify("fillet"));
toolChamferEl.addEventListener("click", () => armModify("chamfer"));
toolHoleEl.addEventListener("click", () => armModify("hole"));
toolRevolveEl.addEventListener("click", () => armModify("revolve"));
toolShellEl.addEventListener("click", () => armModify("shell"));

// --- Construct features and the body tools --------------------------------
// Offset plane, Axis and Point add a plane, axis or point feature. Mirror,
// the two patterns and Combine act on a body picked in the browser tree. They
// share the Modify panel: the fields come from modify.js, and the choices that
// depend on the document (planes, axes, bodies) are made here when the tool opens.

const BODY_KINDS = ["mirror", "linear", "circular"];
const ORIGIN_PLANES = ["XY", "XZ", "YZ"];

/// The planes a tool may start from or mirror across. A picked face comes first, so it is the default.
async function planeChoices(target) {
  const options = [];
  const choices = {};
  if (selectedPatch && canCallDaemon() && loadedRevision === daemonRevision) {
    try {
      const found = await trackPending(
        rpcCall("face_sketch_plane", { handle: `f${selectedPatch.faceIndex}` }),
      );
      options.push({ value: "face", label: "Picked face" });
      choices.face = found.plane;
      target.where.face = found.resolved;
    } catch {
      showNotice("The picked face is not flat, so it is not offered as a plane");
    }
  }
  for (const name of ORIGIN_PLANES) {
    options.push({ value: name, label: `${name} plane` });
    choices[name] = name;
  }
  for (const feature of browserFeatures.filter((entry) => entry.active && entry.type === "plane")) {
    options.push({ value: feature.id, label: `${feature.id} (plane)` });
    choices[feature.id] = { construct: feature.id };
  }
  target.options.plane = options;
  target.choices.plane = choices;
}

/// The axes a tool may use: a picked round face or edge first, then X, Y, Z and the axis features.
async function axisChoices(target, name) {
  const options = [];
  const choices = {};
  const handle =
    selectedEdges.length > 0
      ? `e${selectedEdges.at(-1)}`
      : selectedPatch
        ? `f${selectedPatch.faceIndex}`
        : null;
  if (handle && canCallDaemon() && loadedRevision === daemonRevision) {
    try {
      const found = await trackPending(rpcCall("pick_axis", { handle }));
      options.push({ value: "picked", label: handle[0] === "e" ? "Picked edge" : "Picked face" });
      choices.picked = found.axis;
      target.where[name] = found.resolved;
    } catch {
      // A flat face or a curved edge has no axis: the list is only the others.
    }
  }
  for (const axis of ["X", "Y", "Z"]) {
    options.push({ value: axis, label: `${axis} axis` });
    choices[axis] = axis;
  }
  for (const feature of browserFeatures.filter((entry) => entry.active && entry.type === "axis")) {
    options.push({ value: feature.id, label: `${feature.id} (axis)` });
    choices[feature.id] = { construct: feature.id };
  }
  target.options[name] = options;
  target.choices[name] = choices;
}

async function armConstruct(kind) {
  if (modifyTool || extrudeArmed || sketchStage) {
    return;
  }
  const target = { options: {}, choices: {}, where: {} };
  const bodies = partBodies();
  let body = null;
  if (BODY_KINDS.includes(kind)) {
    body = pickedBody();
    if (!body) {
      return showNotice("Pick a body in the browser first.");
    }
    target.tool = body.tip;
  }
  const tool = {
    kind,
    target,
    values: {},
    seq: 0,
    ok: false,
    dirty: false,
    timer: null,
    inflight: null,
    feature: null,
  };
  modifyTool = tool; // claims the tool before the first await
  updateToolbarState();
  const scale = { options: target.options };
  try {
    if (kind === "plane" || kind === "mirror") {
      await planeChoices(target);
    }
    if (kind === "linear") {
      await axisChoices(target, "direction");
    }
    if (kind === "circular") {
      await axisChoices(target, "axis");
    }
    if (BODY_KINDS.includes(kind) && kind !== "plane") {
      target.options.into = [
        ViewerModify.SAME,
        ...bodies
          .filter((other) => other.id !== body.id)
          .map((other) => ({ value: other.tip, label: other.name })),
      ];
    }
    if (kind === "combine") {
      const offered = bodies.map((other) => ({ value: other.tip, label: other.name }));
      target.options.target = offered;
      target.options.tool = offered;
    }
    if (kind === "axis") {
      const handle =
        selectedEdges.length > 0 ? `e${selectedEdges.at(-1)}` : `f${selectedPatch.faceIndex}`;
      const found = await trackPending(rpcCall("pick_axis", { handle }));
      target.axis = found.axis;
    }
    if (
      kind === "point" &&
      mesh &&
      (selectedEdges.length || selectedPatch) &&
      canCallDaemon() &&
      loadedRevision === daemonRevision
    ) {
      const described = await trackPending(rpcCall("describe_geometry", {}));
      const picked =
        selectedEdges.length > 0
          ? described.edges[selectedEdges.at(-1)]
          : described.faces[selectedPatch.faceIndex];
      if (picked) {
        scale.at = picked.centre.map((value) => Math.round(value * 100) / 100);
      }
    }
    if (kind === "plane" && mesh) {
      const size = new THREE.Box3().setFromObject(mesh).getSize(new THREE.Vector3());
      scale.box = [size.x, size.y, size.z];
    }
  } catch (error) {
    if (modifyTool === tool) {
      modifyTool = null;
      updateToolbarState();
    }
    showNotice(
      kind === "axis"
        ? "That has no axis. Pick a round face, or a straight or circular edge."
        : error.message,
    );
    return;
  }
  if (modifyTool !== tool) {
    return; // cancelled while the choices were made
  }
  tool.values = ViewerModify.defaults(kind, scale);
  if (kind === "combine") {
    const picked = pickedBody() || bodies[0];
    tool.values.target = picked.tip;
    tool.values.tool = (bodies.find((other) => other.tip !== picked.tip) || picked).tip;
  }
  document.getElementById("modify-title").textContent = ViewerModify.TOOLS[kind].title;
  modifyPanelEl.style.display = "flex";
  renderModifyFields();
  const first = modifyFieldsEl.querySelector("input[type=text]");
  if (first) {
    first.focus();
    first.select();
  }
  refreshModifyHandle(true);
  runPreview();
}

toolOffsetPlaneEl.addEventListener("click", () => armConstruct("plane"));
toolAxisEl.addEventListener("click", () => armConstruct("axis"));
toolPointEl.addEventListener("click", () => armConstruct("point"));
toolMirrorEl.addEventListener("click", () => armConstruct("mirror"));
toolLinearEl.addEventListener("click", () => armConstruct("linear"));
toolCircularEl.addEventListener("click", () => armConstruct("circular"));
toolCombineEl.addEventListener("click", () => armConstruct("combine"));

// A click (a press let go without a drag) in pick mode or an open tool:
// the first pick opens the tool, a later one reselects inside it.
renderer.domElement.addEventListener("pointerup", (event) => {
  const press = toolPress;
  toolPress = null;
  if (!press || event.button !== 0) {
    return;
  }
  if (Math.hypot(event.clientX - press.x, event.clientY - press.y) > 4) {
    return;
  }
  const kind = openToolKind();
  if (!kind || !PICKS[kind]) {
    return;
  }
  const pick = pickAt(event, PICKS[kind].accepts);
  if (!pick) {
    return;
  }
  clearHover();
  if (pickTool) {
    cancelPick();
    applyPick(pick, false);
    if (kind === "extrude") {
      armCurrentSelection();
    } else {
      armModify(kind);
    }
  } else if (extrudeArmed) {
    reselectExtrude(pick, event.shiftKey);
  } else {
    reselectModify(pick, event.shiftKey);
  }
});

globalThis.addEventListener("keydown", (event) => {
  if (menuOpen()) {
    if (event.key === "Escape") {
      closeMenus();
    }
    return; // the hex box takes letters; nothing else should act meanwhile
  }
  // The parameter form is a text field: "e" and "s" are letters there, and
  // Cmd+Z undoes the typing rather than the document.
  if (propertiesEl.contains(document.activeElement)) {
    return;
  }
  if (sketchStage === "draw") {
    sketcher.keyDown(event);
    return;
  }
  if (sketchStage) {
    if (event.key === "Escape") {
      cancelSketch();
    }
    return;
  }
  // A Modify tool owns the keyboard: its fields take the letters.
  if (modifyTool) {
    if (event.key === "Escape") {
      event.preventDefault();
      cancelModify();
    } else if (event.key === "Enter") {
      event.preventDefault();
      commitModify();
    }
    return;
  }
  if (pickTool && event.key === "Escape") {
    cancelPick();
    return;
  }
  // Typing in the distance box must not trigger shortcuts: "e" would
  // re-arm and "s" would start a sketch. Enter and Escape still count,
  // since they commit and cancel from the keyboard.
  if (document.activeElement === distanceEl && event.key !== "Enter" && event.key !== "Escape") {
    return;
  }
  if ((event.key === "s" || event.key === "S") && !extrudeArmed) {
    startSketchPickPlane();
  } else if (event.key === "e" || event.key === "E") {
    // Stops the "e" itself landing in the distance box, which arming focuses.
    event.preventDefault();
    armCurrentSelection();
  } else if ((event.metaKey || event.ctrlKey) && "fchr".includes(event.key.toLowerCase())) {
    // Cmd+F and the like belong to the browser.
  } else if (event.key === "f" || event.key === "F") {
    event.preventDefault();
    armModify("fillet");
  } else if (event.key === "c" || event.key === "C") {
    event.preventDefault();
    armModify("chamfer");
  } else if (event.key === "r" || event.key === "R") {
    event.preventDefault();
    armModify("revolve");
  } else if (event.key === "h" || event.key === "H") {
    event.preventDefault();
    armModify("hole");
  } else if (event.key === "i" || event.key === "I") {
    toggleMeasure();
  } else if (event.key === "Escape" && !extrudeArmed) {
    clearSelection();
  } else if (event.key === "Escape" && extrudeArmed) {
    cancelExtrude();
  } else if (event.key === "Enter" && extrudeArmed) {
    commitExtrude();
  } else if (event.metaKey && event.key.toLowerCase() === "z" && !extrudeArmed) {
    event.preventDefault();
    sendCommand({ type: event.shiftKey ? "redo" : "undo" });
  }
});

// --- Sketch mode -----------------------------------------------------
// Two stages: "pick-plane" (click a face, or one of the three principal
// planes) then "draw" (place a rectangle or freeform polygon on that
// plane). On finish, world-space points are projected into the plane's
// own [u, v] basis and posted as {type: "add_sketch", plane, polygon} -
// see sketch.py's PLANE_SCHEMA for the exact shape `plane` must match.
const SKETCH_PRINCIPAL_PLANES = {
  // Bases match build123d's own Plane.XY/XZ/YZ exactly (confirmed
  // against the engine): y_dir is always normal.cross(x_dir).
  XY: {
    origin: new THREE.Vector3(0, 0, 0),
    normal: new THREE.Vector3(0, 0, 1),
    xDir: new THREE.Vector3(1, 0, 0),
  },
  XZ: {
    origin: new THREE.Vector3(0, 0, 0),
    normal: new THREE.Vector3(0, -1, 0),
    xDir: new THREE.Vector3(1, 0, 0),
  },
  YZ: {
    origin: new THREE.Vector3(0, 0, 0),
    normal: new THREE.Vector3(1, 0, 0),
    xDir: new THREE.Vector3(0, 1, 0),
  },
};

let sketchStage = null; // null | "pick-plane" | "draw"
let sketchPlane = null; // { origin, normal, xDir, yDir, ref } - ref is "XY"/"XZ"/"YZ" or null for a picked face
let sketchPlaneHelper = null;
let dimmedMeshState = null; // { transparent, opacity } to restore on exit

const sketchPlanePanelEl = document.getElementById("sketch-plane-panel");
const sketchPlaneOffsetEl = document.getElementById("sketch-plane-offset");
/// The offset typed in the plane panel, in mm. Anything that is not a number is no offset.
const typedPlaneOffset = () => {
  const value = Number.parseFloat(sketchPlaneOffsetEl.value);
  return Number.isFinite(value) ? value : 0;
};
const sketchDrawPanelEl = document.getElementById("sketch-draw-panel");
const sketcher = createSketcher({
  THREE,
  scene,
  camera,
  renderer,
  srgb,
  model: SketchModel,
  solver: SketchSolver,
  rayFor(event) {
    raycaster.setFromCamera(pointerNDC(event), camera);
    return raycaster.ray;
  },
  worldRadius,
  /// The world axes now shown, as {name, direction}.
  visibleAxes: () =>
    Object.entries(AXES)
      .filter(([key]) => axesVisible[key])
      .map(([, axis]) => axis),
  setCameraEnabled(enabled) {
    controls.enabled = enabled;
  },
  cancel: () => cancelSketch(),
  commit(message) {
    exitSketchMode();
    sendCommand(message);
  },
  /// Leaves a sketch whose changes are already in the document.
  close() {
    exitSketchMode();
  },
  updateSketch(id, shape) {
    return sendCommand({
      type: "update_sketch_entities",
      id,
      ...shape,
      base_revision: daemonRevision,
    });
  },
  /// The sketch feature as the document holds it now, or null if it is gone.
  fetchSketch(id) {
    return rpcCall("get_document", {}).then((document) => {
      sawRevision(document.revision);
      const feature = document.features.find(
        (item) => item.id === id && item.type === "sketch" && item.entities,
      );
      return feature
        ? { entities: feature.entities, constraints: feature.constraints || [] }
        : null;
    });
  },
  planePayload: (plane) =>
    plane.payload ||
    plane.ref || {
      origin: plane.origin.toArray(),
      normal: plane.normal.toArray(),
      x_dir: plane.xDir.toArray(),
    },
  pickProjectable,
  /// What projecting `ref` ({sketch, entity} or {handle}) into a sketch on `plane` gives: {source, entity}.
  projectReference(plane, ref) {
    if (ref.handle && loadedRevision !== daemonRevision) {
      return Promise.reject(new Error("the part is still rebuilding, try again"));
    }
    const payload = plane.payload ||
      plane.ref || {
        origin: plane.origin.toArray(),
        normal: plane.normal.toArray(),
        x_dir: plane.xDir.toArray(),
      };
    return rpcCall("project_reference", {
      plane: payload,
      ...(ref.handle ? { handle: ref.handle } : { source: ref }),
    });
  },
  ui: {
    tools: [...sketchDrawPanelEl.querySelectorAll("[data-tool]")],
    constraints: [...sketchDrawPanelEl.querySelectorAll("[data-constraint]")],
    status: document.getElementById("sketch-status"),
    state: document.getElementById("sketch-state"),
    finish: document.getElementById("sketch-finish"),
    toggle: document.getElementById("sketch-constraints-toggle"),
    construction: document.getElementById("sketch-construction"),
    mirror: document.getElementById("sketch-mirror"),
    cancel: document.getElementById("sketch-cancel"),
    labels: document.getElementById("sketch-labels"),
    dimension: document.getElementById("sketch-dimension"),
  },
});
renderer.domElement.addEventListener("dblclick", () => {
  if (sketchStage === "draw") {
    sketcher.doubleClick();
  }
});

// A picked face only gives a normal, not an in-plane x-axis, so one is
// built the same way for JS and Python: cross world-up into the normal,
// falling back to world-X when the normal is nearly vertical.
function computeXDir(normal) {
  const worldUp = new THREE.Vector3(0, 0, 1);
  const ref = Math.abs(normal.dot(worldUp)) > 0.9 ? new THREE.Vector3(1, 0, 0) : worldUp;
  return new THREE.Vector3().crossVectors(ref, normal).normalize();
}

function makeSketchPlane(origin, normal, xDir) {
  const unitNormal = normal.clone().normalize();
  const unitXDir = xDir.clone().normalize();
  const yDir = new THREE.Vector3().crossVectors(unitNormal, unitXDir).normalize();
  return { origin: origin.clone(), normal: unitNormal, xDir: unitXDir, yDir, ref: null };
}

function sketchToLocal(point) {
  const rel = point.clone().sub(sketchPlane.origin);
  return [rel.dot(sketchPlane.xDir), rel.dot(sketchPlane.yDir)];
}

function sketchToWorld(u, v) {
  return sketchPlane.origin
    .clone()
    .addScaledVector(sketchPlane.xDir, u)
    .addScaledVector(sketchPlane.yDir, v);
}

// Rebuilds a plane basis from a stored sketch feature's `plane` field -
// same shapes finishSketch() posts to Swift (a principal-plane name, or a
// {origin, normal, x_dir} dict), so a persisted sketch renders with the
// same basis it was drawn on.
function resolveStoredPlane(planeRef) {
  if (typeof planeRef === "string") {
    const base = SKETCH_PRINCIPAL_PLANES[planeRef];
    return {
      origin: base.origin.clone(),
      xDir: base.xDir.clone(),
      yDir: base.normal.clone().cross(base.xDir).normalize(),
    };
  }
  const origin = new THREE.Vector3(...planeRef.origin);
  const normal = new THREE.Vector3(...planeRef.normal).normalize();
  const xDir = new THREE.Vector3(...planeRef.x_dir).normalize();
  return { origin, xDir, yDir: new THREE.Vector3().crossVectors(normal, xDir).normalize() };
}

// Draws a closed outline for every sketch feature in the timeline, since
// a bare sketch has no solid of its own to show. Rebuilt wholesale on
// every features.json load - cheap, and simpler than diffing.
function worldOfPlane(plane, [u, v]) {
  return plane.origin.clone().addScaledVector(plane.xDir, u).addScaledVector(plane.yDir, v);
}

/// One entity as a run of points in world space.
function entityRun(entity, plane) {
  const arcPoints = (steps, start, end) =>
    Array.from({ length: steps + 1 }, (_, step) => {
      const angle = start + ((end - start) * step) / steps;
      return worldOfPlane(plane, [
        entity.c[0] + entity.r * Math.cos(angle),
        entity.c[1] + entity.r * Math.sin(angle),
      ]);
    });
  if (entity.kind === "line") {
    return [worldOfPlane(plane, entity.p0), worldOfPlane(plane, entity.p1)];
  }
  return entity.kind === "circle"
    ? arcPoints(72, 0, 2 * Math.PI)
    : arcPoints(48, entity.a0, entity.a1);
}

/// Every entity of a drawn sketch as one run of line segments, in world space.
function entityOutlinePoints(feature, plane) {
  const points = [];
  for (const entity of feature.entities || (feature.resolved || feature.feature).entities) {
    const run = entityRun(entity, plane);
    run.slice(1).forEach((point, index) => points.push(run[index], point));
  }
  return points;
}

/// What the Project tool would take at the pointer: the nearest entity of an
/// earlier, shown sketch, else an edge of the part. {ref, segments} or null.
/// `ref` is {sketch, entity} or {handle: "e7"}, which the engine turns into a
/// selector that follows the edge. `segments` are the points to highlight, in pairs.
function pickProjectable(event) {
  const rect = renderer.domElement.getBoundingClientRect();
  const pointer = [event.clientX - rect.left, event.clientY - rect.top];
  camera.updateMatrixWorld();
  const onScreen = (point) => {
    const projected = point.clone().project(camera);
    return [((projected.x + 1) / 2) * rect.width, ((1 - projected.y) / 2) * rect.height];
  };
  // A sketch can only project from the ones before it in the timeline.
  const before = editedSketchId
    ? browserFeatures.findIndex((feature) => feature.id === editedSketchId)
    : browserFeatures.length;
  let best = null;
  browserFeatures.forEach((feature, index) => {
    const shown =
      feature.type === "sketch" &&
      feature.active &&
      feature.plane &&
      !hiddenSketchIDs.has(feature.id);
    if (!shown || index >= before) {
      return;
    }
    const entities = feature.entities || (feature.resolved || feature.feature).entities;
    const plane = resolveStoredPlane(feature.plane);
    for (const entity of entities || []) {
      const run = entityRun(entity, plane);
      for (let step = 0; step + 1 < run.length; step++) {
        const gap = pointToSegment(pointer, onScreen(run[step]), onScreen(run[step + 1]));
        if (gap <= 8 && (!best || gap < best.gap)) {
          best = { gap, ref: { sketch: feature.id, entity: entity.id }, run };
        }
      }
    }
  });
  if (best) {
    return {
      ref: best.ref,
      segments: best.run.slice(1).flatMap((point, index) => [best.run[index], point]),
    };
  }
  const hit = raycastEdge(event);
  if (!hit) {
    return null;
  }
  const edgeIndex = edgeIndexOfHit(hit);
  const { edgeStart } = edgeLines.userData;
  const positions = edgeLines.geometry.attributes.position.array;
  const points = [];
  for (let at = edgeStart[edgeIndex] * 6; at < edgeStart[edgeIndex + 1] * 6; at += 3) {
    points.push(new THREE.Vector3(positions[at], positions[at + 1], positions[at + 2]));
  }
  return { ref: { handle: `e${edgeIndex}` }, segments: points };
}

/// A region as a flat mesh in world space: its outline with its holes cut out.
function regionFillGeometry(region, plane) {
  const shape = new THREE.Shape(region.outline.map(([u, v]) => new THREE.Vector2(u, v)));
  region.holes.forEach((hole) =>
    shape.holes.push(new THREE.Path(hole.map(([u, v]) => new THREE.Vector2(u, v)))),
  );
  const geometry = new THREE.ShapeGeometry(shape);
  // Baked into the geometry rather than set as the mesh's matrix, so
  // raycasting needs no matrixWorld update pass to see it.
  geometry.applyMatrix4(
    new THREE.Matrix4()
      .makeBasis(
        plane.xDir,
        plane.yDir,
        new THREE.Vector3().crossVectors(plane.xDir, plane.yDir).normalize(),
      )
      .setPosition(plane.origin),
  );
  return geometry;
}

/// One helix per thread the engine resolved, one turn per pitch. It sits a
/// little off the face, into the air (inside a hole, outside a shaft), so it
/// does not z-fight with the surface.
function renderThreads(features) {
  threadGroup.clear();
  features
    .filter((feature) => feature.type === "thread" && feature.thread)
    .forEach(({ thread }) => {
      const start = new THREE.Vector3(...thread.start);
      const axis = new THREE.Vector3(...thread.direction).normalize();
      const helper =
        Math.abs(axis.z) < 0.9 ? new THREE.Vector3(0, 0, 1) : new THREE.Vector3(1, 0, 0);
      const u = new THREE.Vector3().crossVectors(axis, helper).normalize();
      const v = new THREE.Vector3().crossVectors(axis, u);
      const radius = thread.radius * (thread.internal ? 0.97 : 1.03);
      const turns = thread.length / thread.pitch;
      const steps = Math.max(24, Math.ceil(turns * 24));
      const points = Array.from({ length: steps + 1 }, (_, step) => {
        const along = step / steps;
        const angle = 2 * Math.PI * turns * along;
        return start
          .clone()
          .addScaledVector(axis, thread.length * along)
          .addScaledVector(u, radius * Math.cos(angle))
          .addScaledVector(v, radius * Math.sin(angle));
      });
      const line = new THREE.Line(
        new THREE.BufferGeometry().setFromPoints(points),
        new THREE.LineBasicMaterial({ color: srgb(0xd0_a0_40) }),
      );
      line.userData.designation = thread.designation;
      threadGroup.add(line);
    });
  if (section) {
    applySection();
  }
}

const CONSTRUCT_COLOUR = 0x4d_d0_e1;

/// How big to draw a plane or an axis: a little more than the part, and not
/// tiny when there is no part yet.
function sceneExtent() {
  if (!mesh) {
    return 60;
  }
  const size = new THREE.Box3().setFromObject(mesh).getSize(new THREE.Vector3());
  return Math.max(40, Math.max(size.x, size.y, size.z) * 1.6);
}

/// A plane, an axis or a point as three.js objects. `info` is the engine's
/// `construct` data. A preview is drawn brighter.
function makeConstructObject(info, preview) {
  const group = new THREE.Group();
  const colour = srgb(preview ? 0x5b_e0_a0 : CONSTRUCT_COLOUR);
  const extent = sceneExtent();
  if (info.kind === "plane") {
    const normal = new THREE.Vector3(...info.normal).normalize();
    const xDir = new THREE.Vector3(...info.x_dir).normalize();
    const yDir = new THREE.Vector3().crossVectors(normal, xDir).normalize();
    // Centred under the part, not at the plane's own origin, which can be far from it.
    const centre = new THREE.Vector3(...info.origin);
    if (mesh) {
      const middle = new THREE.Box3().setFromObject(mesh).getCenter(new THREE.Vector3());
      centre.add(middle.sub(centre).projectOnPlane(normal));
    }
    const fill = new THREE.Mesh(
      new THREE.PlaneGeometry(extent, extent),
      new THREE.MeshBasicMaterial({
        color: colour,
        transparent: true,
        opacity: preview ? 0.25 : 0.12,
        side: THREE.DoubleSide,
        depthWrite: false,
      }),
    );
    const outline = new THREE.LineLoop(
      new THREE.BufferGeometry().setFromPoints(
        [
          [-1, -1],
          [1, -1],
          [1, 1],
          [-1, 1],
        ].map(([u, v]) => new THREE.Vector3((u * extent) / 2, (v * extent) / 2, 0)),
      ),
      new THREE.LineBasicMaterial({ color: colour }),
    );
    for (const item of [fill, outline]) {
      item.quaternion.setFromRotationMatrix(new THREE.Matrix4().makeBasis(xDir, yDir, normal));
      item.position.copy(centre);
      group.add(item);
    }
  } else if (info.kind === "axis") {
    const origin = new THREE.Vector3(...info.origin);
    const direction = new THREE.Vector3(...info.direction).normalize();
    // The axis runs through the part's middle, however far its origin is.
    let along = 0;
    if (mesh) {
      along = new THREE.Box3()
        .setFromObject(mesh)
        .getCenter(new THREE.Vector3())
        .sub(origin)
        .dot(direction);
    }
    const middle = origin.clone().addScaledVector(direction, along);
    group.add(
      new THREE.Line(
        new THREE.BufferGeometry().setFromPoints([
          middle.clone().addScaledVector(direction, -extent / 2),
          middle.clone().addScaledVector(direction, extent / 2),
        ]),
        new THREE.LineDashedMaterial({ color: colour, dashSize: 3, gapSize: 2 }),
      ),
    );
    group.children[0].computeLineDistances();
  } else {
    const points = new THREE.Points(
      new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(...info.at)]),
      new THREE.PointsMaterial({
        color: colour,
        size: 10,
        sizeAttenuation: false,
        depthTest: false,
      }),
    );
    points.renderOrder = 1002;
    group.add(points);
  }
  group.userData.construct = info;
  return group;
}

function renderConstructs(features) {
  constructGroup.clear();
  constructObjects = [];
  for (const feature of features.filter((entry) => entry.construct && entry.active)) {
    const object = makeConstructObject(feature.construct, false);
    constructGroup.add(object);
    constructObjects.push({ id: feature.id, object, info: feature.construct });
  }
  if (!mesh && !constructFramed && constructObjects.length > 0) {
    constructFramed = true;
    frameCameraOn(constructGroup);
  }
  applyVisibility();
}

// Draws every sketch feature in the timeline, since a bare sketch has no
// solid of its own to show. A drawn sketch shows its entities and one fill
// per closed region, which is what a click picks and Extrude pushes. A
// polygon sketch shows its outline and corner handles. Rebuilt wholesale on
// every features.json load: cheap, and simpler than diffing.
function renderSketchOutlines(features) {
  sketchOutlineGroup.clear();
  sketchOutlines = [];
  features
    .filter((feature) => feature.type === "sketch" && feature.plane && feature.regions)
    .forEach((feature) => {
      const plane = resolveStoredPlane(feature.plane);
      const drawn = Boolean(feature.feature.entities);
      const points = drawn
        ? entityOutlinePoints(feature, plane)
        : feature.polygon
          ? feature.polygon.map(([u, v]) => worldOfPlane(plane, [u, v]))
          : [];
      const geometry = new THREE.BufferGeometry().setFromPoints(
        drawn || points.length === 0 ? points : points.concat([points[0]]),
      );
      // A sketch drawn on an existing face sits exactly on the mesh
      // surface, which z-fights with a normal line. depthTest off plus a
      // high renderOrder draws it on top unconditionally instead, so it
      // stays visible regardless of camera angle: the user's explicit
      // requirement for a bare, unextruded sketch.
      const material = new THREE.LineBasicMaterial({
        color: srgb(feature.active ? 0x5b_e0_a0 : 0x66_66_66),
        depthTest: false,
      });
      const line = drawn
        ? new THREE.LineSegments(geometry, material)
        : new THREE.Line(geometry, material);
      line.renderOrder = 999;
      sketchOutlineGroup.add(line);

      // One mesh per region, so a click anywhere inside picks it instead
      // of needing pixel-accurate aim along a 1px line. Faintly visible for
      // a drawn sketch, which is how the regions "light up" once it is
      // finished. A polygon's stays invisible: the outline is the cue.
      const fills = feature.regions.map((region, regionIndex) => {
        const fill = new THREE.Mesh(
          regionFillGeometry(region, plane),
          new THREE.MeshBasicMaterial({
            color: srgb(0x5b_e0_a0),
            transparent: true,
            opacity: 0,
            visible: drawn,
            side: THREE.DoubleSide,
            depthWrite: false,
          }),
        );
        fill.userData.regionIndex = regionIndex;
        sketchOutlineGroup.add(fill);
        return fill;
      });

      // Corner markers, shown only while a polygon sketch is selected. They
      // are the visible cue that a corner can be dragged; the hit test
      // itself works in the plane's uv (see pickSketchPart), not off these
      // points. Fixed pixel size, so they stay grabbable at any zoom.
      const handles = new THREE.Points(
        new THREE.BufferGeometry().setFromPoints(feature.polygon ? points : []),
        new THREE.PointsMaterial({
          color: 0xff_dd_55,
          size: 8,
          sizeAttenuation: false,
          depthTest: false,
        }),
      );
      handles.renderOrder = 1000;
      sketchOutlineGroup.add(handles);
      sketchOutlines.push({ feature, line, fills, handles, drawn });
    });
  renderSketchSelection();
  applyVisibility();
}
/// Recolours every sketch outline and shows the selected one's corner
/// handles: the selected sketch yellow, the rest by whether the build
/// replayed them. Called after any selection change.
function renderSketchSelection() {
  sketchOutlines.forEach(({ feature, line, fills, handles, drawn }) => {
    const selected = selectedSketch && selectedSketch.id === feature.id;
    line.material.color.copy(
      srgb(selected ? 0xff_dd_55 : feature.active ? 0x5b_e0_a0 : 0x66_66_66),
    );
    handles.visible = Boolean(selected) && !hiddenSketchIDs.has(feature.id);
    // A drawn sketch's regions glow faintly, and the picked ones brightly.
    fills.forEach((fill) => {
      const picked = selected && selectedRegions.includes(fill.userData.regionIndex);
      const hovered =
        hoverRegion &&
        hoverRegion.sketch === feature.id &&
        hoverRegion.region === fill.userData.regionIndex;
      fill.material.opacity = !drawn || !feature.active ? 0 : picked ? 0.55 : hovered ? 0.4 : 0.16;
      fill.material.color.copy(srgb(picked ? 0xff_aa_00 : 0x5b_e0_a0));
    });
  });
}
/// The nearest sketch under the pointer, or null. Every replayed sketch
/// qualifies, not just the one at the tip: extruding one appends onto the
/// current tip solid (see ContentView's appendExtrudeSketchFeature), and
/// editing one replays the whole timeline over the new profile. A sketch
/// the build rolled past is skipped - it is not in the document prefix an
/// extrude would be appended to.
function raycastSketch(event) {
  const candidates = sketchOutlines.filter(
    ({ feature, fills }) => feature.active && fills[0]?.visible,
  );
  if (candidates.length === 0) {
    return null;
  }
  raycaster.setFromCamera(pointerNDC(event), camera);
  const hits = raycaster.intersectObjects(candidates.flatMap(({ fills }) => fills));
  if (hits.length === 0) {
    return null;
  }
  const entry = candidates.find(({ fills }) => fills.includes(hits[0].object));
  return entry
    ? {
        feature: entry.feature,
        regionIndex: hits[0].object.userData.regionIndex,
        distance: hits[0].distance,
        point: hits[0].point,
      }
    : null;
}

function buildSketchPlaneHelper(plane) {
  const size = 300;
  const geometry = new THREE.PlaneGeometry(size, size);
  const material = new THREE.MeshBasicMaterial({
    color: srgb(0x5b_e0_a0),
    transparent: true,
    opacity: 0.08,
    side: THREE.DoubleSide,
    depthWrite: false,
  });
  const helperMesh = new THREE.Mesh(geometry, material);
  helperMesh.quaternion.setFromRotationMatrix(
    new THREE.Matrix4().makeBasis(plane.xDir, plane.yDir, plane.normal),
  );
  helperMesh.position.copy(plane.origin);
  return helperMesh;
}

function startSketchPickPlane(options) {
  cancelPick();
  sketchPlaneOffsetEl.value = "0";
  // A face picked first is the plane: pick, then press Sketch.
  if (selectedPatch && mesh) {
    const patch = selectedPatch;
    clearSelection();
    sketchStage = "pick-plane";
    updateToolbarState();
    sketchOnFace(patch, patch.anchorPoint);
    return;
  }
  sketchStage = "pick-plane";
  renderConstructPlaneButtons();
  sketchPlanePanelEl.style.display = "flex";
  updateToolbarState();
}

/// A button for each plane feature, so a sketch can start on one. Its offset
/// box moves the sketch further along the plane's normal, as for XY.
function renderConstructPlaneButtons() {
  const holder = document.getElementById("sketch-plane-constructs");
  holder.replaceChildren();
  for (const entry of constructObjects.filter(({ info }) => info.kind === "plane")) {
    const button = document.createElement("button");
    button.textContent = entry.id;
    button.dataset.construct = entry.id;
    button.title = `Sketch on the plane feature ${entry.id}`;
    button.addEventListener("click", () => {
      if (sketchStage !== "pick-plane") {
        return;
      }
      const normal = new THREE.Vector3(...entry.info.normal).normalize();
      const offset = typedPlaneOffset();
      sketchPlane = makeSketchPlane(
        new THREE.Vector3(...entry.info.origin).addScaledVector(normal, offset),
        normal,
        new THREE.Vector3(...entry.info.x_dir),
      );
      sketchPlane.payload = offset ? { construct: entry.id, offset } : { construct: entry.id };
      sketchPlane.canonical = true; // opened square on, v up, as a principal plane is
      enterSketchDrawStage();
    });
    holder.appendChild(button);
  }
}

/// Starts a sketch on a picked face. The daemon turns the face into a
/// plane reference ({feature, face selector}) that the engine finds again
/// on every rebuild, so the sketch follows the face when an upstream
/// dimension moves it. The sketcher draws in the plane the engine will
/// use. Without a daemon, or on a face that is not flat, the plane is
/// the fixed tangent plane at the click, and the status line says so.
function sketchOnFace(patch, point) {
  const fixed = () => {
    sketchPlane = makeSketchPlane(point, patch.normal, computeXDir(patch.normal));
    enterSketchDrawStage();
  };
  if (!canCallDaemon() || loadedRevision !== daemonRevision) {
    return fixed();
  }
  sketchStage = "resolving-plane";
  trackPending(
    rpcCall("face_sketch_plane", { handle: `f${patch.faceIndex}` })
      .then((found) => {
        if (sketchStage !== "resolving-plane") {
          return;
        }
        const resolved = found.resolved;
        const offset = typedPlaneOffset();
        const normal = new THREE.Vector3(...resolved.normal);
        sketchPlane = makeSketchPlane(
          new THREE.Vector3(...resolved.origin).addScaledVector(normal.clone().normalize(), offset),
          normal,
          new THREE.Vector3(...resolved.x_dir),
        );
        sketchPlane.payload = offset ? { ...found.plane, offset } : found.plane;
        enterSketchDrawStage();
      })
      .catch((error) => {
        if (sketchStage !== "resolving-plane") {
          return;
        }
        showNotice(`Sketch plane is fixed, it will not follow the face: ${error.message}`);
        fixed();
      }),
  );
}

/// Opens the sketcher on `sketchPlane`. Pass an entity sketch's feature to
/// redraw it instead of starting a new one.
function enterSketchDrawStage(feature) {
  sketchStage = "draw";
  clearHighlight();
  hideHint();
  sketchPlanePanelEl.style.display = "none";
  sketchDrawPanelEl.style.display = "flex";
  sketcher.begin(sketchPlane, feature);

  // View the plane from whichever side the camera is already on, and roll
  // to the plane axis nearest the current screen up. Both keep the move
  // to the smallest rotation that still looks at the plane square on.
  //
  // A principal plane is the exception. It always opens the way Fusion does:
  // seen from the side its normal points to, with v up the screen and u to
  // the right (Top for XY, Front for XZ, Right for YZ). From an isometric
  // camera the nearest roll is a coin toss, and a sketch that opens turned
  // on its side or upside down is harder to draw in.
  const up = screenUp();
  const canonical = Boolean(sketchPlane.ref || sketchPlane.canonical);
  const side = canonical
    ? 1
    : camera.position.clone().sub(sketchPlane.origin).dot(sketchPlane.normal) < 0
      ? -1
      : 1;
  const rolls = [sketchPlane.yDir, sketchPlane.xDir];
  const nearestRoll = canonical
    ? sketchPlane.yDir.clone()
    : rolls
        .flatMap((axis) => [axis.clone(), axis.clone().negate()])
        .reduce((best, axis) => (axis.dot(up) > best.dot(up) ? axis : best));
  // A new sketch starts where the click was, at the distance the camera
  // already has. A sketch being redrawn may sit anywhere on its plane, so
  // frame what is already drawn.
  const framing = feature ? framingFor(feature) : null;
  moveCameraTo(
    sketchPlane.normal.clone().multiplyScalar(side),
    nearestRoll,
    framing ? framing.centre : sketchPlane.origin,
    // On a principal plane the camera may be framed tight on a small part,
    // and a sketch is usually wider than that. Back off to about 100 mm of
    // view. On a picked face the user chose the zoom, so keep it.
    framing
      ? framing.distance
      : sketchPlane.ref
        ? Math.max(camera.position.distanceTo(controls.target), 120)
        : undefined,
  );
  controls.enableRotate = false;

  if (mesh) {
    dimmedMeshState = partMaterials().map((item) => ({
      material: item,
      transparent: item.transparent,
      opacity: item.opacity,
    }));
    for (const item of partMaterials()) {
      item.transparent = true;
      item.opacity = 0.25;
    }
  }

  sketchPlaneHelper = buildSketchPlaneHelper(sketchPlane);
  scene.add(sketchPlaneHelper);
}

/// The middle of a drawn sketch's entities on the sketch plane, and a camera
/// distance that keeps all of them in view.
function framingFor(feature) {
  const points = feature.entities.flatMap((entity) =>
    entity.kind === "line"
      ? [entity.p0, entity.p1]
      : [
          [entity.c[0] - entity.r, entity.c[1] - entity.r],
          [entity.c[0] + entity.r, entity.c[1] + entity.r],
        ],
  );
  const us = points.map((point) => point[0]);
  const vs = points.map((point) => point[1]);
  const middle = [(Math.min(...us) + Math.max(...us)) / 2, (Math.min(...vs) + Math.max(...vs)) / 2];
  const size = Math.max(Math.max(...us) - Math.min(...us), Math.max(...vs) - Math.min(...vs));
  return { centre: worldOfPlane(sketchPlane, middle), distance: size * 1.6 + 30 };
}

/// Reopens an entity sketch for redrawing, on the plane it was drawn on.
let editedSketchId = null;
function editSketch(entry) {
  if (sketchStage || extrudeArmed) {
    return;
  }
  // The sketcher works in numbers. The engine puts each "=param"
  // expression back wherever the value did not change (keep_expressions).
  const feature = entry.resolved || entry.feature;
  const stored = resolveStoredPlane(entry.plane);
  const normal = new THREE.Vector3().crossVectors(stored.xDir, stored.yDir).normalize();
  sketchPlane = makeSketchPlane(stored.origin, normal, stored.xDir);
  sketchPlane.ref = typeof entry.plane === "string" ? entry.plane : null;
  // A face or offset plane stays a reference when the sketch is redrawn.
  const raw = entry.feature?.plane;
  if (raw && typeof raw === "object" && (raw.feature || raw.name || raw.construct)) {
    sketchPlane.payload = raw;
  }
  if (raw && typeof raw === "object" && (raw.name || raw.construct)) {
    sketchPlane.canonical = true;
  }
  // The sketcher draws it now, and the saved outline would sit under it.
  editedSketchId = entry.id;
  hiddenSketchIDs.add(entry.id);
  applyVisibility();
  enterSketchDrawStage(feature);
}

function cancelSketch() {
  exitSketchMode();
}

function exitSketchMode() {
  sketcher.end();
  if (editedSketchId) {
    hiddenSketchIDs.delete(editedSketchId);
  }
  editedSketchId = null;
  applyVisibility();
  sketchStage = null;
  sketchPlane = null;
  sketchPlanePanelEl.style.display = "none";
  sketchDrawPanelEl.style.display = "none";
  clearHighlight();
  hideHint();
  controls.enableRotate = true;
  updateToolbarState();

  if (sketchPlaneHelper) {
    scene.remove(sketchPlaneHelper);
    sketchPlaneHelper.geometry.dispose();
    sketchPlaneHelper.material.dispose();
    sketchPlaneHelper = null;
  }
  if (dimmedMeshState) {
    for (const saved of dimmedMeshState) {
      saved.material.transparent = saved.transparent;
      saved.material.opacity = saved.opacity;
    }
  }
  dimmedMeshState = null;
}

["XY", "XZ", "YZ"].forEach((name) => {
  document
    .querySelector(`#sketch-plane-panel button[data-plane="${name}"]`)
    .addEventListener("click", () => {
      if (sketchStage !== "pick-plane") {
        return;
      }
      const def = SKETCH_PRINCIPAL_PLANES[name];
      const offset = typedPlaneOffset();
      sketchPlane = makeSketchPlane(
        def.origin.clone().addScaledVector(def.normal, offset),
        def.normal,
        def.xDir,
      );
      if (offset === 0) {
        sketchPlane.ref = name;
      } else {
        // Stored as the engine's own {name, offset}, and opened like a principal plane.
        sketchPlane.payload = { name, offset };
        sketchPlane.canonical = true;
      }
      enterSketchDrawStage();
    });
});

document.getElementById("sketch-plane-cancel").addEventListener("click", cancelSketch);
sketchPlaneOffsetEl.addEventListener("keydown", (event) => {
  event.stopPropagation();
  if (event.key === "Escape") {
    cancelSketch();
  }
});

// --- View cube ---------------------------------------------------
// A small always-on gizmo showing the main camera's orientation. Its
// own scene/camera/renderer stay in lockstep with the main camera's
// rotation every frame; clicking a face, edge or corner snaps the main
// camera to look straight at it, at the same distance from
// controls.target as before.
//
// Screen left/right placement of FRONT vs RIGHT in the default corner
// view is geometrically fixed by which Y face (+Y or -Y) the camera
// sees: with Z-up and a camera on the +X,+Y,+Z side, +Y projects to
// screen-right and -X/-Y project to screen-left. So +Y = RIGHT (not
// LEFT) is what makes FRONT-left/RIGHT-right match the reference
// ViewCube layout, given DEFAULT_VIEW_DIR below also sits on the +Y side.
const AXIS_VIEWS = [
  // labelRotation counteracts BoxGeometry's default per-face UV
  // orientation, which isn't consistent across faces - without it, some
  // labels bake in reading sideways or upside down. Chosen by eye so
  // every face reads upright at the default isometric view.
  { label: "FRONT", dir: [1, 0, 0], up: [0, 0, 1], labelRotation: -90 },
  { label: "BACK", dir: [-1, 0, 0], up: [0, 0, 1], labelRotation: 90 },
  { label: "RIGHT", dir: [0, 1, 0], up: [0, 0, 1], labelRotation: 180 },
  { label: "LEFT", dir: [0, -1, 0], up: [0, 0, 1], labelRotation: 0 },
  { label: "TOP", dir: [0, 0, 1], up: [0, 1, 0], labelRotation: -90 },
  { label: "BOTTOM", dir: [0, 0, -1], up: [0, 1, 0], labelRotation: 90 },
];
const DEFAULT_VIEW_DIR = new THREE.Vector3(1, 1, 1).normalize();
const DEFAULT_UP = new THREE.Vector3(0, 0, 1);

function makeFaceTexture(label, rotationDeg) {
  const size = 512;
  const c = document.createElement("canvas");
  c.width = c.height = size;
  const ctx = c.getContext("2d");
  ctx.fillStyle = "#e8e8e8";
  ctx.fillRect(0, 0, size, size);
  ctx.strokeStyle = "#999";
  ctx.lineWidth = 12;
  ctx.strokeRect(6, 6, size - 12, size - 12);
  ctx.translate(size / 2, size / 2);
  ctx.rotate((rotationDeg * Math.PI) / 180);
  ctx.fillStyle = "#222";
  ctx.font = "bold 96px -apple-system, sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(label, 0, 0);
  const texture = new THREE.CanvasTexture(c);
  // The cube sits at an angle by default, so most faces are seen edge-on
  // (foreshortened) - without anisotropic filtering, mipmapping blurs
  // that view heavily. maxAnisotropy sharpens it back up.
  texture.anisotropy = cubeRenderer.capabilities.getMaxAnisotropy();
  return texture;
}

// Used for the X/Y/Z axis labels, which float off the cube rather than
// sitting on a face, so a camera-facing sprite is the natural fit there.
function makeLabelSprite(text, color, fontPx, canvasSize, opts) {
  const c = document.createElement("canvas");
  c.width = c.height = canvasSize;
  const ctx = c.getContext("2d");
  ctx.fillStyle = color;
  ctx.font = `bold ${fontPx}px -apple-system, sans-serif`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(text, canvasSize / 2, canvasSize / 2);
  const sprite = new THREE.Sprite(
    new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(c), ...opts }),
  );
  return sprite;
}

// --- Camera moves ---------------------------------------------------
// A view change eases into place instead of jumping, so you can see where
// the model went. The rotation is a quaternion slerp between the old and
// new orientation, which always takes the short way round.
const LOCAL_UP = new THREE.Vector3(0, 1, 0);
const LOCAL_BACK = new THREE.Vector3(0, 0, 1);
const CAMERA_MOVE_MS = 350;
let cameraMove = null;

// The orientation of a camera that sits along `direction` and has `up`
// pointing up the screen.
function viewQuaternion(direction, up) {
  const basis = new THREE.Matrix4().lookAt(direction, new THREE.Vector3(), up);
  return new THREE.Quaternion().setFromRotationMatrix(basis);
}

function screenUp() {
  return LOCAL_UP.clone().applyQuaternion(camera.quaternion);
}

function moveCameraTo(direction, up, target, distance) {
  const startTarget = controls.target.clone();
  const offset = camera.position.clone().sub(startTarget);
  cameraMove = {
    startTime: performance.now(),
    distance: distance || offset.length() || 200,
    startTarget,
    endTarget: (target || startTarget).clone(),
    startQuaternion: viewQuaternion(offset.normalize(), screenUp()),
    endQuaternion: viewQuaternion(direction.clone().normalize(), up),
  };
}

function stepCameraMove() {
  const elapsed = performance.now() - cameraMove.startTime;
  const t = Math.min(1, elapsed / CAMERA_MOVE_MS);
  const eased = t * t * (3 - 2 * t);
  const orientation = cameraMove.startQuaternion.clone().slerp(cameraMove.endQuaternion, eased);
  controls.target.lerpVectors(cameraMove.startTarget, cameraMove.endTarget, eased);
  camera.up.copy(LOCAL_UP).applyQuaternion(orientation);
  camera.position
    .copy(controls.target)
    .addScaledVector(LOCAL_BACK.clone().applyQuaternion(orientation), cameraMove.distance);
  if (t === 1) {
    cameraMove = null;
  }
}

function snapToView(dir, up) {
  moveCameraTo(dir, new THREE.Vector3(up.x, up.y, up.z));
}

const CUBE_SIZE = 132;
const HALF = 0.7; // BoxGeometry(1.4, 1.4, 1.4) half-extent

const cubeContainer = document.getElementById("view-cube");
const cubeCanvas = document.createElement("canvas");
cubeContainer.appendChild(cubeCanvas);

const cubeRenderer = new THREE.WebGLRenderer({ canvas: cubeCanvas, antialias: true, alpha: true });
cubeRenderer.setPixelRatio(globalThis.devicePixelRatio || 1);
cubeRenderer.setSize(CUBE_SIZE, CUBE_SIZE);

const cubeScene = new THREE.Scene();
// BoxGeometry's default material order is +x, -x, +y, -y, +z, -z -
// AXIS_VIEWS above is built in that same order so materialIndex maps
// straight across on click. Every face bakes its label into its own
// texture rather than using a billboard sprite: a sprite always faces
// the camera, so on a foreshortened side face its plane can't coincide
// with the face plane without either floating forward off the cube or
// z-fighting against the face itself. Baked text stays flush by
// construction and rotates with the cube, at the cost of reading
// diagonally rather than horizontally at non-square angles - the same
// trade-off Fusion/SolidWorks ViewCubes make.
const cubeMaterials = AXIS_VIEWS.map(
  (view) => new THREE.MeshBasicMaterial({ map: makeFaceTexture(view.label, view.labelRotation) }),
);
const cubeMesh = new THREE.Mesh(new THREE.BoxGeometry(1.4, 1.4, 1.4), cubeMaterials);
cubeScene.add(cubeMesh);

// Cylinders instead of THREE.AxesHelper: WebGL ignores LineBasicMaterial's
// linewidth, so a fat line needs real geometry. Each one runs from the
// origin to AXIS_LENGTH, well short of the label so the label sits past
// the tip rather than along the shaft.
const AXIS_LENGTH = 1.0;
const AXIS_RADIUS = 0.03;
const AXIS_LABELS = [
  { text: "X", color: "#f44", direction: new THREE.Vector3(1, 0, 0) },
  { text: "Y", color: "#4f4", direction: new THREE.Vector3(0, 1, 0) },
  { text: "Z", color: "#48f", direction: new THREE.Vector3(0, 0, 1) },
];
AXIS_LABELS.forEach(({ text, color, direction }) => {
  const line = new THREE.Mesh(
    new THREE.CylinderGeometry(AXIS_RADIUS, AXIS_RADIUS, AXIS_LENGTH, 12),
    new THREE.MeshBasicMaterial({ color }),
  );
  line.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), direction);
  line.position.copy(direction).multiplyScalar(AXIS_LENGTH / 2);
  cubeScene.add(line);

  const sprite = makeLabelSprite(text, color, 80, 128, { depthTest: false });
  sprite.scale.set(0.3, 0.3, 1);
  sprite.position.copy(direction).multiplyScalar(1.15);
  cubeScene.add(sprite);
});

// Faces (one of nx/ny/nz nonzero), edges (two nonzero) and corners (all
// three nonzero) of the cube each get their own small, clickable region,
// same as a Fusion-style ViewCube - a face region duplicates what
// clicking cubeMesh itself already does, but gives the same visible
// handle/hover affordance the edges and corners have. Direction is just
// the region's own position normalised; the centre (all zero) is skipped.
const REGION_DEFAULT_COLOR = 0xcf_cf_cf;
const REGION_HOVER_COLOR = 0x5b_e0_a0;
const regionMeshes = [];
for (const nx of [-1, 0, 1]) {
  for (const ny of [-1, 0, 1]) {
    for (const nz of [-1, 0, 1]) {
      const nonzero = [nx, ny, nz].filter((c) => c !== 0).length;
      if (nonzero < 1) {
        continue;
      }
      const thin = 0.22;
      const long = 0.62;
      const medium = 0.5; // face-handle footprint: smaller than the full face
      const zeroAxisSize = nonzero === 1 ? medium : long;
      const geometry = new THREE.BoxGeometry(
        nx === 0 ? zeroAxisSize : thin,
        ny === 0 ? zeroAxisSize : thin,
        nz === 0 ? zeroAxisSize : thin,
      );
      const material = new THREE.MeshBasicMaterial({
        color: REGION_DEFAULT_COLOR,
        transparent: true,
        opacity: 0,
      });
      const mesh = new THREE.Mesh(geometry, material);
      // Set each nonzero axis to HALF - thin/2, not HALF, so the box's
      // outer face lands flush on the cube surface instead of poking
      // past it. A zero axis stays at 0 either way.
      const flush = HALF - thin / 2;
      mesh.position.set(nx * flush, ny * flush, nz * flush);
      mesh.userData.viewDir = new THREE.Vector3(nx, ny, nz).normalize();
      regionMeshes.push(mesh);
      cubeScene.add(mesh);
    }
  }
}

const cubeCamera = new THREE.PerspectiveCamera(35, 1, 0.1, 10);

function pickCubeNDC(event) {
  const rect = cubeCanvas.getBoundingClientRect();
  return new THREE.Vector2(
    ((event.clientX - rect.left) / rect.width) * 2 - 1,
    -((event.clientY - rect.top) / rect.height) * 2 + 1,
  );
}

let hoveredRegion = null;
cubeCanvas.addEventListener("pointermove", (event) => {
  const cubeRaycaster = new THREE.Raycaster();
  cubeRaycaster.setFromCamera(pickCubeNDC(event), cubeCamera);
  const hit = cubeRaycaster.intersectObjects(regionMeshes)[0];
  if (hoveredRegion) {
    hoveredRegion.material.color.setHex(REGION_DEFAULT_COLOR);
    hoveredRegion.material.opacity = 0;
  }
  hoveredRegion = hit ? hit.object : null;
  if (hoveredRegion) {
    hoveredRegion.material.color.setHex(REGION_HOVER_COLOR);
    hoveredRegion.material.opacity = 0.85;
  }
});
cubeCanvas.addEventListener("pointerleave", () => {
  if (hoveredRegion) {
    hoveredRegion.material.color.setHex(REGION_DEFAULT_COLOR);
    hoveredRegion.material.opacity = 0;
  }
  hoveredRegion = null;
});

cubeCanvas.addEventListener("click", (event) => {
  const cubeRaycaster = new THREE.Raycaster();
  cubeRaycaster.setFromCamera(pickCubeNDC(event), cubeCamera);
  const hit = cubeRaycaster.intersectObjects([cubeMesh, ...regionMeshes])[0];
  if (!hit) {
    return;
  }
  if (hit.object === cubeMesh) {
    const view = AXIS_VIEWS[hit.face.materialIndex];
    snapToView(new THREE.Vector3(...view.dir), new THREE.Vector3(...view.up));
  } else {
    snapToView(hit.object.userData.viewDir, new THREE.Vector3(0, 0, 1));
  }
});

document.getElementById("view-cube-home").addEventListener("click", () => {
  snapToView(DEFAULT_VIEW_DIR, DEFAULT_UP);
});

function renderViewCube() {
  const dir = camera.position.clone().sub(controls.target).normalize();
  cubeCamera.position.copy(dir).multiplyScalar(4.6);
  cubeCamera.up.copy(camera.up);
  cubeCamera.lookAt(0, 0, 0);
  cubeRenderer.render(cubeScene, cubeCamera);
}

function animate() {
  requestAnimationFrame(animate);
  if (cameraMove) {
    stepCameraMove();
  }
  controls.update();
  placeHandle();
  sketcher.frame();
  renderer.render(scene, camera);
  renderViewCube();
}
animate();
renderBrowser();

globalThis.addEventListener("resize", () => {
  camera.aspect = globalThis.innerWidth / globalThis.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(globalThis.innerWidth, globalThis.innerHeight);
});

// --- Test hooks -----------------------------------------------------
// What a Playwright test or an agent asserts on, read straight from the
// viewer's own state, plus screenPointOf() so a test clicks where the
// model says a face is instead of guessing pixels. Read-only: nothing here
// changes the document, so it is safe in every build.
/// Where B-rep edge `edge` is on screen, and whether it is in front.
function screenPointOfEdge(edge) {
  if (!mesh || !edgeLines || edge >= edgeLines.userData.edgeStart.length - 1) {
    return null;
  }
  camera.updateMatrixWorld();
  const point = edgeMidpoint(edge);
  const ndc = point.clone().project(camera);
  const rect = renderer.domElement.getBoundingClientRect();
  raycaster.setFromCamera(new THREE.Vector2(ndc.x, ndc.y), camera);
  const hits = partHits();
  const visible =
    hits.length === 0 ||
    hits[0].distance >=
      camera.position.distanceTo(point) - 2 * worldRadius(EDGE_PICK_PIXELS, point);
  return {
    x: rect.left + ((ndc.x + 1) / 2) * rect.width,
    y: rect.top + ((1 - ndc.y) / 2) * rect.height,
    visible,
  };
}

globalThis.__tcad = {
  get axes() {
    return { ...axesVisible };
  },
  /// The section view: axis, offset and flip, and how many materials are clipped, or null when off.
  get section() {
    if (!section) {
      return null;
    }
    const clipped = []
      .concat(mesh ? mesh.material : [], edgeLines ? edgeLines.material : [])
      .filter((material) => material.clippingPlanes?.length);
    return { ...section, clipped: clipped.length, cap: Boolean(sectionCap) };
  },
  /// The measure readout's text, empty when it is hidden.
  get measure() {
    return measureEl.style.display === "none" ? "" : measureEl.textContent;
  },
  /// The cosmetic threads drawn: designation and point count of each helix.
  get threads() {
    return threadGroup.children.map((line) => ({
      designation: line.userData.designation,
      points: line.geometry.attributes.position.count,
    }));
  },
  /// False while a mesh, a timeline or a command is still in flight.
  get idle() {
    return pendingCount === 0;
  },
  /// What an agent last pointed at: {handles, sketches}.
  get highlighted() {
    return { handles: [...agentPointedAt.handles], sketches: [...agentPointedAt.sketches] };
  },
  get revision() {
    return loadedRevision;
  },
  get timeline() {
    return browserFeatures.map((entry) => ({
      id: entry.id,
      type: entry.type,
      active: entry.active,
      failed: Boolean(entry.failed),
    }));
  },
  get features() {
    return browserFeatures.map((entry) => entry.feature);
  },
  get selection() {
    return {
      face: selectedPatch ? selectedPatch.faceIndex : null,
      edge: selectedEdges.length > 0 ? selectedEdges.at(-1) : null,
      sketch: selectedSketch ? selectedSketch.id : null,
      regions: selectedSketch ? [...selectedRegions] : [],
    };
  },
  /// The sketcher's own state while it is open: what is drawn, how
  /// constrained it is, and what is selected.
  get sketch() {
    return sketcher.snapshot();
  },
  /// The regions of every sketch, as the engine reported them.
  get regions() {
    return browserFeatures
      .filter((entry) => entry.regions)
      .map((entry) => ({ sketch: entry.id, regions: entry.regions }));
  },
  /// The open Modify tool: its kind, the feature its fields make, and
  /// whether the preview built. Null when none is open.
  get modifying() {
    return modifyTool
      ? {
          kind: modifyTool.kind,
          feature: modifyTool.feature,
          ok: modifyTool.ok,
          preview: Boolean(modifyPreview),
          info: modifyInfoEl.textContent,
        }
      : null;
  },
  /// The plane, axis and point features drawn: id, what they are, and whether they show.
  get constructs() {
    return constructObjects.map(({ id, info, object }) => ({
      id,
      kind: info.kind,
      visible: object.visible,
      info,
    }));
  },
  /// The body picked in the browser tree, if any.
  get selectedBody() {
    return selectedBodyId;
  },
  get selectedEdges() {
    return [...selectedEdges];
  },
  get extruding() {
    return extrudeArmed
      ? { distance: previewState.distance, regions: sketchExtrudeRegions.length }
      : null;
  },
  /// The tool waiting for its first pick, or null.
  get picking() {
    return pickTool;
  },
  /// What a tool's hover lights up under the pointer, or null.
  get hovered() {
    return hovered;
  },
  /// The open drag handle: its kind and value, and where on screen to grab
  /// it and which way to drag (`toward`, one handle length further along).
  /// Null when none shows.
  get handle() {
    if (!handle) {
      return null;
    }
    placeHandle();
    const rect = renderer.domElement.getBoundingClientRect();
    const screen = (point) => {
      const ndc = point.clone().project(camera);
      return {
        x: rect.left + ((ndc.x + 1) / 2) * rect.width,
        y: rect.top + ((1 - ndc.y) / 2) * rect.height,
      };
    };
    let grab;
    let toward;
    if (handle.kind === "linear") {
      const base = arrowBase();
      const length = worldRadius(HANDLE_PIXELS, base);
      grab = base.clone().addScaledVector(handle.direction, length * 0.85);
      toward = base.clone().addScaledVector(handle.direction, length * 1.85);
    } else {
      grab = ringPoint(handle.value);
      toward = ringPoint(handle.value - 45);
    }
    return {
      kind: handle.kind,
      value: handle.value,
      grab: screen(grab),
      toward: screen(toward),
      box: distanceEl.value,
    };
  },
  /// Where [u, v] on the sketch plane is on screen, in page pixels.
  sketchPoint(uv) {
    return sketcher.screenPointOfUV(uv);
  },
  /// Where a sketch's region `index` is on screen: the middle of its interior point.
  regionPoint(sketchId, index) {
    const entry = browserFeatures.find((item) => item.id === sketchId);
    if (!entry?.regions[index]) {
      return null;
    }
    camera.updateMatrixWorld();
    const plane = resolveStoredPlane(entry.plane);
    const ndc = worldOfPlane(plane, entry.regions[index].inside).project(camera);
    const rect = renderer.domElement.getBoundingClientRect();
    return {
      x: rect.left + ((ndc.x + 1) / 2) * rect.width,
      y: rect.top + ((1 - ndc.y) / 2) * rect.height,
    };
  },
  get editing() {
    return editingFeatureId;
  },
  /// True while the camera eases into a new view. A test that clicks before
  /// it stops aims at where things were.
  get cameraMoving() {
    return cameraMove !== null;
  },
  get camera() {
    return { position: camera.position.toArray(), target: controls.target.toArray() };
  },
  get zoomSensitivity() {
    return zoomSensitivity;
  },
  get mesh() {
    return mesh ? { ...mesh.userData.counts } : null;
  },
  /// The bodies in the mesh header: id, name, color, visible, reference.
  get bodies() {
    return mesh ? mesh.geometry.userData.bodies.map((body) => ({ ...body })) : [];
  },
  /// Face `face`'s own colour as "#rrggbb", or null when it has none.
  faceColour(face) {
    const colours = mesh?.userData.faceColours;
    if (!colours || colours[face] === undefined || colours[face] === 0xff_ff_ff_ff) {
      return null;
    }
    return `#${colours[face].toString(16).padStart(6, "0")}`;
  },
  /// The open right-click menu's items, or null.
  get contextMenu() {
    if (contextMenuEl.style.display !== "block") {
      return null;
    }
    return Array.from(contextMenuEl.querySelectorAll("button")).map((button) => button.textContent);
  },
  /// Face `face`'s finish as [roughness, metalness], or null when it has none.
  faceFinish(face) {
    const finishes = mesh?.geometry.userData.faceFinishes;
    if (!finishes || finishes[face * 2] < 0) {
      return null;
    }
    return [finishes[face * 2], finishes[face * 2 + 1]];
  },
  /// The finished materials in use: [{roughness, metalness, hasEnvironment}].
  get finishMaterials() {
    if (!mesh) {
      return [];
    }
    return []
      .concat(mesh.material)
      .slice(3)
      .map((item) => ({
        roughness: item.roughness,
        metalness: item.metalness,
        hasEnvironment: Boolean(item.envMap),
      }));
  },
  /// The status line, when it shows.
  get status() {
    return statusEl.style.display === "none" ? null : statusEl.textContent;
  },

  /// Where B-rep face `face` is on screen, in page pixels, and whether a
  /// click there would land on it rather than on something in front.
  ///
  /// The point is the centroid of the face's largest triangle, not of the
  /// whole face: the centroid of a face with a hole or a concave outline
  /// can sit off the face altogether.
  screenPointOf({ face, edge }) {
    if (edge !== undefined) {
      return screenPointOfEdge(edge);
    }
    if (!mesh?.geometry.groups[face]) {
      return null;
    }
    const geometry = mesh.geometry;
    const best = faceCentre(face);
    // The render loop refreshes these, but it does not run in a hidden tab.
    camera.updateMatrixWorld();
    const ndc = best.clone().project(camera);
    const rect = renderer.domElement.getBoundingClientRect();
    const x = rect.left + ((ndc.x + 1) / 2) * rect.width;
    const y = rect.top + ((1 - ndc.y) / 2) * rect.height;
    raycaster.setFromCamera(new THREE.Vector2(ndc.x, ndc.y), camera);
    const hits = partHits();
    const visible = hits.length > 0 && geometry.userData.faceOfTriangle[hits[0].faceIndex] === face;
    return { x, y, visible };
  },
};
