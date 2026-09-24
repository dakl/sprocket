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
                    onExtrudeFace: handleExtrudeFace
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
        let result = EngineRunner.build(document: document, stlOut: stlOut)
        status = result.success ? .ready : .failed(result.output)
    }

    /// Writes a small `id`/`type` summary of the document's features next to
    /// part.stl, for the viewer's timeline strip. Best-effort: a missing or
    /// malformed sidecar just leaves the timeline empty, it never blocks the
    /// build.
    private func writeFeaturesSidecar() {
        guard let data = try? Data(contentsOf: document),
              let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let features = root["features"] as? [[String: Any]]
        else { return }

        let summary = features.map { ["id": $0["id"] ?? "", "type": $0["type"] ?? ""] }
        guard let output = try? JSONSerialization.data(withJSONObject: summary) else { return }
        try? output.write(to: RepoPaths.viewerDir.appendingPathComponent("features.json"))
    }

    private func handleExtrudeFace(_ payload: ExtrudeFacePayload) {
        do {
            try appendExtrudeFaceFeature(payload)
        } catch {
            status = .failed("failed to update \(document.lastPathComponent): \(error.localizedDescription)")
            return
        }
        reloadToken = Date().timeIntervalSince1970
        rebuild()
    }

    /// Appends one `extrude_face` feature onto whatever the last feature in
    /// the document is. Good enough for the single-part example document;
    /// picking which feature to push off of is a later concern.
    private func appendExtrudeFaceFeature(_ payload: ExtrudeFacePayload) throws {
        let data = try Data(contentsOf: document)
        guard var root = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              var features = root["features"] as? [[String: Any]],
              let lastID = features.last?["id"] as? String
        else {
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

        let output = try JSONSerialization.data(withJSONObject: root, options: [.prettyPrinted])
        try output.write(to: document)
    }
}
