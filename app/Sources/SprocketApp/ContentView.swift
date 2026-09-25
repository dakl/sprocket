import SwiftUI

struct ContentView: View {
    enum BuildStatus {
        case building
        case ready
        case failed(String)
    }

    @State private var status: BuildStatus = .building
    // Bumped on every rebuild and appended to the viewer URL as `?t=`, so
    // WKWebView treats the reload as a new page and refetches part.stl
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

    private let document = RepoPaths.examplesDir.appendingPathComponent("box.json")
    private let stlOut = RepoPaths.viewerDir.appendingPathComponent("part.stl")

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
                    onSetRollback: handleSetRollback,
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

    private func rebuild() {
        writeFeaturesSidecar()
        let result = EngineRunner.shared.build(document: document, stlOut: stlOut)
        status = result.success ? .ready : .failed(result.output)
    }

    /// Writes a small `id`/`type`/`active` summary of the document's features
    /// next to part.stl, for the viewer's timeline strip. `active` marks
    /// features before rollback_index, the ones the current build replayed.
    /// Best-effort: a missing or malformed sidecar just leaves the timeline
    /// empty, it never blocks the build.
    private func writeFeaturesSidecar() {
        guard let data = try? Data(contentsOf: document),
              let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let features = root["features"] as? [[String: Any]]
        else { return }

        let rollbackIndex = root["rollback_index"] as? Int ?? features.count
        let summary = features.enumerated().map { index, feature in
            ["id": feature["id"] ?? "", "type": feature["type"] ?? "", "active": index < rollbackIndex]
        }
        guard let output = try? JSONSerialization.data(withJSONObject: summary) else { return }
        try? output.write(to: RepoPaths.viewerDir.appendingPathComponent("features.json"))
    }

    private func handleExtrudeFace(_ payload: ExtrudeFacePayload) {
        performMutation { try appendExtrudeFaceFeature(payload) }
    }

    /// Sets rollback_index, the boundary the engine replays up to. A click on
    /// a live timeline chip rolls back to just before it; a click on a
    /// greyed-out chip rolls forward to just past it.
    private func handleSetRollback(_ index: Int) {
        performMutation { try setRollbackIndex(index) }
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
        // rebuild() must finish writing part.stl before reloadToken changes,
        // or WebView reloads to a URL whose STL is still the previous build.
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
}
