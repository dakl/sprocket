import SwiftUI

struct ContentView: View {
    enum BuildStatus {
        case building
        case ready
        case failed(String)
    }

    @State private var status: BuildStatus = .building
    // Bumped on every rebuild and appended to the viewer URL as `?t=`, so
    // WKWebView treats the reload as a new page and refetches part.mesh
    // instead of serving it from cache.
    @State private var reloadToken = Date().timeIntervalSince1970

    // Undo/redo stack: each entry is a full snapshot of box.json's bytes
    // from just before one mutation. Covers both extrude_face appends and
    // rollback_index changes, since both just rewrite box.json. A new
    // mutation clears redoStack, same as any editor (VS Code, Photoshop,
    // git checkout + new commit) — nothing is ever silently dropped without
    // going through this stack first.
    @State private var undoStack: [Data] = []
    @State private var redoStack: [Data] = []

    // SPROCKET_DOCUMENT opens a different part, so a throwaway document can be
    // edited without risking the one in examples/.
    private let document = ProcessInfo.processInfo.environment["SPROCKET_DOCUMENT"]
        .map { URL(fileURLWithPath: $0) }
        ?? RepoPaths.examplesDir.appendingPathComponent("box.json")
    private let meshOut = RepoPaths.viewerDir.appendingPathComponent("part.mesh")

    var body: some View {
        Group {
            switch status {
            case .building:
                ProgressView("Building part\u{2026}")
            case .ready:
                WebView(
                    url: viewerURL,
                    readAccessURL: RepoPaths.viewerDir,
                    onExtrudeFace: handleExtrudeFace,
                    onAddSketch: handleAddSketch,
                    onExtrudeSketch: handleExtrudeSketch,
                    onUpdateSketch: handleUpdateSketch,
                    onUpdateFeature: handleUpdateFeature,
                    onSetRollback: handleSetRollback,
                    onNewDocument: handleNewDocument,
                    onUndo: undo,
                    onRedo: redo
                )
            case .failed(let message):
                ScrollView {
                    Text(message)
                        .font(.system(.body, design: .monospaced))
                        .padding()
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
        }
        .frame(minWidth: 640, minHeight: 480)
        .task {
            rebuild()
        }
    }

    private var viewerURL: URL {
        var components = URLComponents(
            url: RepoPaths.viewerDir.appendingPathComponent("index.html"),
            resolvingAgainstBaseURL: false
        )!
        components.queryItems = [URLQueryItem(name: "t", value: String(reloadToken))]
        return components.url!
    }

    /// Builds before writing the sidecar, not after: which feature no longer
    /// builds is only known once the engine has replayed the document, and the
    /// viewer reads the sidecar to mark it.
    private func rebuild() {
        let result = EngineRunner.shared.build(document: document, meshOut: meshOut)
        writeFeaturesSidecar(failedFeature: result.failedFeature)
        status = result.success ? .ready : .failed(result.output)
    }

    /// Writes a small `id`/`type`/`active` summary of the document's features
    /// next to part.mesh, for the viewer's timeline strip. `active` marks
    /// features before rollback_index, the ones the current build replayed.
    /// A sketch feature also carries its `plane`/`polygon`, so the viewer can
    /// draw its outline - a bare sketch produces no solid (see
    /// sprocket_engine.cli._last_solid_index), so its outline is the only
    /// visible trace of it in the 3D view.
    /// `failedFeature`, when set, marks the one op the engine could not apply,
    /// so the viewer can say which op was dropped from the part on screen.
    /// Best-effort: a missing or malformed sidecar just leaves the timeline
    /// empty, it never blocks the build.
    private func writeFeaturesSidecar(failedFeature: String?) {
        guard let data = try? Data(contentsOf: document),
              let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let features = root["features"] as? [[String: Any]]
        else { return }

        let rollbackIndex = root["rollback_index"] as? Int ?? features.count
        let summary = features.enumerated().map { index, feature -> [String: Any] in
            // `feature` is the whole object, which the viewer's parameter form edits.
            var entry: [String: Any] = [
                "id": feature["id"] ?? "", "type": feature["type"] ?? "", "active": index < rollbackIndex,
                "feature": feature,
            ]
            if feature["type"] as? String == "sketch" {
                entry["plane"] = feature["plane"]
                entry["polygon"] = feature["polygon"]
            }
            if let failedFeature, (feature["id"] as? String) == failedFeature {
                entry["failed"] = true
            }
            return entry
        }
        guard let output = try? JSONSerialization.data(withJSONObject: summary) else { return }
        try? output.write(to: RepoPaths.viewerDir.appendingPathComponent("features.json"))
    }

    private func handleExtrudeFace(_ payload: ExtrudeFacePayload) {
        performMutation { try appendExtrudeFaceFeature(payload) }
    }

    private func handleAddSketch(_ payload: SketchPayload) {
        performMutation { try appendSketchFeature(payload) }
    }

    private func handleExtrudeSketch(_ payload: ExtrudeSketchPayload) {
        performMutation { try appendExtrudeSketchFeature(payload) }
    }

    private func handleUpdateSketch(_ payload: UpdateSketchPayload) {
        performMutation { try updateSketchPolygon(payload) }
    }

    private func handleUpdateFeature(_ payload: UpdateFeaturePayload) {
        performMutation { try replaceFeature(payload) }
    }

    /// Sets rollback_index, the boundary the engine replays up to. A click on
    /// a live timeline chip rolls back to just before it; a click on a
    /// greyed-out chip rolls forward to just past it.
    private func handleSetRollback(_ index: Int) {
        performMutation { try setRollbackIndex(index) }
    }

    /// Replaces the document with an empty one, so the next sketch starts from
    /// nothing. Goes through performMutation like every other edit, so Cmd+Z
    /// restores the whole part.
    private func handleNewDocument() {
        performMutation {
            let root: [String: Any] = [
                "schema_version": 1,
                "units": ["length": "mm"],
                "features": [],
                "rollback_index": 0,
            ]
            let output = try JSONSerialization.data(withJSONObject: root, options: [.prettyPrinted])
            try output.write(to: document)
        }
    }

    /// Runs one document mutation under the undo stack: snapshots box.json
    /// before `mutate` runs, so `undo()` can restore it byte-for-byte
    /// regardless of what the mutation did (append a feature, splice the
    /// rolled-back tail, whatever). Any new mutation clears redoStack.
    private func performMutation(_ mutate: () throws -> Void) {
        guard let previousData = try? Data(contentsOf: document) else { return }
        do {
            try mutate()
        } catch {
            status = .failed("failed to update \(document.lastPathComponent): \(error.localizedDescription)")
            return
        }
        undoStack.append(previousData)
        redoStack.removeAll()
        // rebuild() must finish writing part.mesh before reloadToken changes,
        // or WebView reloads to a URL whose mesh is still the previous build.
        rebuild()
        reloadToken = Date().timeIntervalSince1970
    }

    private func undo() {
        guard let previousData = undoStack.popLast(),
              let currentData = try? Data(contentsOf: document)
        else { return }
        redoStack.append(currentData)
        try? previousData.write(to: document)
        rebuild()
        reloadToken = Date().timeIntervalSince1970
    }

    private func redo() {
        guard let nextData = redoStack.popLast(),
              let currentData = try? Data(contentsOf: document)
        else { return }
        undoStack.append(currentData)
        try? nextData.write(to: document)
        rebuild()
        reloadToken = Date().timeIntervalSince1970
    }

    private func setRollbackIndex(_ index: Int) throws {
        let data = try Data(contentsOf: document)
        guard var root = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let features = root["features"] as? [[String: Any]]
        else {
            throw NSError(domain: "Sprocket", code: 1, userInfo: [NSLocalizedDescriptionKey: "box.json has no features"])
        }

        root["rollback_index"] = max(0, min(index, features.count))
        let output = try JSONSerialization.data(withJSONObject: root, options: [.prettyPrinted])
        try output.write(to: document)
    }

    /// Appends one `extrude_face` feature onto the current rollback tip.
    /// Extruding after a rollback discards the rolled-back tail from
    /// box.json first — replay() only replays a prefix, so a hidden tail
    /// can't sit before a live feature. undo() is what makes this safe.
    private func appendExtrudeFaceFeature(_ payload: ExtrudeFacePayload) throws {
        let data = try Data(contentsOf: document)
        guard var root = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              var features = root["features"] as? [[String: Any]],
              !features.isEmpty
        else {
            throw NSError(domain: "Sprocket", code: 1, userInfo: [NSLocalizedDescriptionKey: "box.json has no features"])
        }

        let rollbackIndex = min(root["rollback_index"] as? Int ?? features.count, features.count)
        features = Array(features.prefix(rollbackIndex))
        guard let lastID = features.last?["id"] as? String else {
            throw NSError(domain: "Sprocket", code: 1, userInfo: [NSLocalizedDescriptionKey: "box.json has no features"])
        }

        let feature: [String: Any] = [
            "id": "f\(features.count + 1)",
            "type": "extrude_face",
            "base": lastID,
            "face": [
                "from": "part",
                "select": [
                    ["op": "faces"],
                    ["op": "nearest_to", "point": payload.point, "normal": payload.normal],
                ],
            ],
            "distance": payload.distance,
        ]
        features.append(feature)
        root["features"] = features
        root["rollback_index"] = features.count

        let output = try JSONSerialization.data(withJSONObject: root, options: [.prettyPrinted])
        try output.write(to: document)
    }

    /// Appends one `sketch` feature onto the current rollback tip. Unlike
    /// extrude_face, a sketch has no `base` reference — it just needs a
    /// plane and a profile — so this only has to truncate the rolled-back
    /// tail, not chase a prior feature's id.
    private func appendSketchFeature(_ payload: SketchPayload) throws {
        let data = try Data(contentsOf: document)
        guard var root = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              var features = root["features"] as? [[String: Any]]
        else {
            throw NSError(domain: "Sprocket", code: 1, userInfo: [NSLocalizedDescriptionKey: "box.json has no features"])
        }

        let rollbackIndex = min(root["rollback_index"] as? Int ?? features.count, features.count)
        features = Array(features.prefix(rollbackIndex))

        let feature: [String: Any] = [
            "id": "f\(features.count + 1)",
            "type": "sketch",
            "plane": payload.plane,
            "polygon": payload.polygon,
        ]
        features.append(feature)
        root["features"] = features
        root["rollback_index"] = features.count

        let output = try JSONSerialization.data(withJSONObject: root, options: [.prettyPrinted])
        try output.write(to: document)
    }

    /// Rewrites one existing sketch's polygon, leaving the rest of the timeline
    /// alone. Every later feature is replayed against the new profile, which is
    /// the point: an extrude on this sketch follows the edited face. A later op
    /// may stop applying, and the engine reports that separately (see
    /// EngineBuildResult.failedFeature).
    private func updateSketchPolygon(_ payload: UpdateSketchPayload) throws {
        let data = try Data(contentsOf: document)
        guard var root = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              var features = root["features"] as? [[String: Any]],
              let index = features.firstIndex(where: { ($0["id"] as? String) == payload.id }),
              features[index]["type"] as? String == "sketch"
        else {
            throw NSError(domain: "Sprocket", code: 1, userInfo: [NSLocalizedDescriptionKey: "no sketch with id \(payload.id)"])
        }

        features[index]["polygon"] = payload.polygon
        root["features"] = features

        let output = try JSONSerialization.data(withJSONObject: root, options: [.prettyPrinted])
        try output.write(to: document)
    }

    /// Swaps one feature for the edited copy the parameter form sent. The
    /// engine validates it against the op's schema when it replays, and a
    /// value it refuses shows up as `failedFeature` like any other broken op.
    private func replaceFeature(_ payload: UpdateFeaturePayload) throws {
        let data = try Data(contentsOf: document)
        guard var root = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              var features = root["features"] as? [[String: Any]],
              let index = features.firstIndex(where: { ($0["id"] as? String) == payload.id })
        else {
            throw NSError(domain: "Sprocket", code: 1, userInfo: [NSLocalizedDescriptionKey: "no feature with id \(payload.id)"])
        }

        features[index] = payload.feature
        root["features"] = features

        let output = try JSONSerialization.data(withJSONObject: root, options: [.prettyPrinted])
        try output.write(to: document)
    }

    /// Finds the id of the solid feature standing just before `index` in the
    /// timeline, skipping back over any sketch features - the same rule
    /// sprocket_engine.cli._last_solid_index applies when picking what to
    /// export. Returns nil when there is no solid yet (this sketch was the
    /// document's first feature).
    private func lastSolidFeatureID(before index: Int, in features: [[String: Any]]) -> String? {
        var i = index
        while i > 0 {
            i -= 1
            if (features[i]["type"] as? String) != "sketch" {
                return features[i]["id"] as? String
            }
        }
        return nil
    }

    /// Appends one `extrude` feature that turns a selected sketch into a
    /// solid. `base`, when there is a prior solid, tells the engine's
    /// extrude op to fuse into it (or cut, for a negative distance) instead
    /// of replacing it - see ops/extrude.py.
    private func appendExtrudeSketchFeature(_ payload: ExtrudeSketchPayload) throws {
        let data = try Data(contentsOf: document)
        guard var root = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              var features = root["features"] as? [[String: Any]]
        else {
            throw NSError(domain: "Sprocket", code: 1, userInfo: [NSLocalizedDescriptionKey: "box.json has no features"])
        }

        let rollbackIndex = min(root["rollback_index"] as? Int ?? features.count, features.count)
        features = Array(features.prefix(rollbackIndex))

        guard features.contains(where: { ($0["id"] as? String) == payload.profile }) else {
            throw NSError(domain: "Sprocket", code: 1, userInfo: [NSLocalizedDescriptionKey: "unknown sketch id \(payload.profile)"])
        }

        var feature: [String: Any] = [
            "id": "f\(features.count + 1)",
            "type": "extrude",
            "profile": payload.profile,
            "distance": payload.distance,
        ]
        // The solid at the tip, not the one standing before the sketch: any
        // sketch in the timeline can be extruded, and the result must fuse into
        // the part as it stands now. Searching back from the sketch instead
        // would name a solid that later features have already consumed, so the
        // extrude would drop their geometry.
        if let base = lastSolidFeatureID(before: features.count, in: features) {
            feature["base"] = base
        }
        features.append(feature)
        root["features"] = features
        root["rollback_index"] = features.count

        let output = try JSONSerialization.data(withJSONObject: root, options: [.prettyPrinted])
        try output.write(to: document)
    }
}
