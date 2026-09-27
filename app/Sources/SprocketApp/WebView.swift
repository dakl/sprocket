import SwiftUI
import WebKit

/// A push/pull commit posted from the viewer's "OK" button (see
/// viewer/index.html's commitExtrude()). Point and normal are in the
/// engine's own mm coordinates — the viewer never rotates the mesh, so
/// three.js world space already matches build123d space.
struct ExtrudeFacePayload {
    let point: [Double]
    let normal: [Double]
    let distance: Double
}

/// A sketch commit posted from the viewer's sketch mode (see index.html's
/// finishSketch()). `plane` is either the string "XY"/"XZ"/"YZ", or a
/// {origin, normal, x_dir} dict for a plane picked off a part face - passed
/// through untouched, since the engine's sketch op accepts both forms
/// directly. `polygon` is a closed loop of [u, v] points local to that plane.
struct SketchPayload {
    let plane: Any
    let polygon: [[Double]]
}

/// An extrude-the-sketch commit, posted after armSketchExtrude()'s drag (see
/// index.html). `profile` is the id of the sketch feature being extruded;
/// Swift looks up what solid (if any) came before it to fuse/cut into.
struct ExtrudeSketchPayload {
    let profile: String
    let distance: Double
}

/// A sketch edit posted when the viewer's corner or edge drag is released (see
/// index.html's pointerup listener). `id` names an existing sketch feature and
/// `polygon` replaces its points, in the same [u, v] plane-local form
/// SketchPayload uses. The plane never moves, only the profile on it.
struct UpdateSketchPayload {
    let id: String
    let polygon: [[Double]]
}

/// Loads viewer/index.html and grants it read access to the viewer
/// directory, so it can fetch part.stl next to it. Registers a
/// WKScriptMessageHandler named "sprocket" so the JS side can post a
/// push/pull commit back to Swift.
struct WebView: NSViewRepresentable {
    let url: URL
    let readAccessURL: URL
    let onExtrudeFace: (ExtrudeFacePayload) -> Void
    let onAddSketch: (SketchPayload) -> Void
    let onExtrudeSketch: (ExtrudeSketchPayload) -> Void
    let onUpdateSketch: (UpdateSketchPayload) -> Void
    let onSetRollback: (Int) -> Void
    let onNewDocument: () -> Void
    let onUndo: () -> Void
    let onRedo: () -> Void

    func makeCoordinator() -> Coordinator {
        Coordinator(
            onExtrudeFace: onExtrudeFace,
            onAddSketch: onAddSketch,
            onExtrudeSketch: onExtrudeSketch,
            onUpdateSketch: onUpdateSketch,
            onSetRollback: onSetRollback,
            onNewDocument: onNewDocument,
            onUndo: onUndo,
            onRedo: onRedo
        )
    }

    /// Extracts the `t` cache-bust token ContentView appends to `url`, so
    /// updateNSView can tell a real rebuild (token changed) from an
    /// unrelated SwiftUI re-render (token unchanged).
    private func cacheBustToken(of url: URL) -> String? {
        URLComponents(url: url, resolvingAgainstBaseURL: false)?
            .queryItems?.first(where: { $0.name == "t" })?.value
    }

    func makeNSView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        // WKWebView treats each file:// URL as its own origin, so the
        // viewer's fetch() of part.stl/features.json next to it is blocked
        // as cross-origin unless this is set (undocumented WebKit key, no
        // public API for it).
        configuration.setValue(true, forKey: "allowUniversalAccessFromFileURLs")
        configuration.userContentController.add(context.coordinator, name: "sprocket")
        // Forwards console.log/warn/error and uncaught errors to Swift's
        // stdout. This unbundled build has no dev tools attached, so this
        // is the only window into the page's JS.
        let debugScript = """
        (function () {
          function send(level, args) {
            try {
              window.webkit.messageHandlers.sprocket.postMessage({
                type: "log", level: level,
                text: Array.from(args).map(String).join(" ")
              });
            } catch (e) {}
          }
          ["log", "warn", "error"].forEach(function (level) {
            const original = console[level];
            console[level] = function () {
              send(level, arguments);
              original.apply(console, arguments);
            };
          });
          window.onerror = function (message, source, lineno, colno) {
            send("error", [message + " at " + source + ":" + lineno + ":" + colno]);
          };
        })();
        """
        configuration.userContentController.addUserScript(
            WKUserScript(source: debugScript, injectionTime: .atDocumentStart, forMainFrameOnly: true)
        )
        let webView = WKWebView(frame: .zero, configuration: configuration)
        webView.loadFileURL(url, allowingReadAccessTo: readAccessURL)
        context.coordinator.lastToken = cacheBustToken(of: url)
        return webView
    }

    /// Was a full-page reload on every rebuild: the fresh page reset the
    /// three.js camera, so every op edit threw away the user's zoom and
    /// pan. Now only the very first load navigates; a rebuild instead
    /// calls the page's own reloadPart(), which keeps the camera as-is.
    func updateNSView(_ webView: WKWebView, context: Context) {
        let token = cacheBustToken(of: url)
        guard token != context.coordinator.lastToken else { return }
        context.coordinator.lastToken = token

        let tokenLiteral = token.map { "\"\($0)\"" } ?? "null"
        webView.evaluateJavaScript("window.reloadPart && window.reloadPart(\(tokenLiteral));") { _, error in
            if let error {
                print("[viewer] reloadPart failed: \(error)")
            }
        }
    }

    final class Coordinator: NSObject, WKScriptMessageHandler {
        /// The `t` token of the last URL this webView was navigated or
        /// refreshed to. Lets updateNSView tell a real rebuild from a
        /// SwiftUI re-render that didn't change anything.
        var lastToken: String?
        private let onExtrudeFace: (ExtrudeFacePayload) -> Void
        private let onAddSketch: (SketchPayload) -> Void
        private let onExtrudeSketch: (ExtrudeSketchPayload) -> Void
        private let onUpdateSketch: (UpdateSketchPayload) -> Void
        private let onSetRollback: (Int) -> Void
        private let onNewDocument: () -> Void
        private let onUndo: () -> Void
        private let onRedo: () -> Void

        init(
            onExtrudeFace: @escaping (ExtrudeFacePayload) -> Void,
            onAddSketch: @escaping (SketchPayload) -> Void,
            onExtrudeSketch: @escaping (ExtrudeSketchPayload) -> Void,
            onUpdateSketch: @escaping (UpdateSketchPayload) -> Void,
            onSetRollback: @escaping (Int) -> Void,
            onNewDocument: @escaping () -> Void,
            onUndo: @escaping () -> Void,
            onRedo: @escaping () -> Void
        ) {
            self.onExtrudeFace = onExtrudeFace
            self.onAddSketch = onAddSketch
            self.onExtrudeSketch = onExtrudeSketch
            self.onUpdateSketch = onUpdateSketch
            self.onSetRollback = onSetRollback
            self.onNewDocument = onNewDocument
            self.onUndo = onUndo
            self.onRedo = onRedo
        }

        func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
            guard message.name == "sprocket",
                  let body = message.body as? [String: Any],
                  let type = body["type"] as? String
            else { return }

            if type == "log" {
                print("[viewer \(body["level"] ?? "log")] \(body["text"] ?? "")")
                return
            }

            if type == "set_rollback" {
                guard let index = body["index"] as? Int else { return }
                onSetRollback(index)
                return
            }

            if type == "new_document" {
                onNewDocument()
                return
            }

            if type == "undo" {
                onUndo()
                return
            }

            if type == "redo" {
                onRedo()
                return
            }

            if type == "extrude_face" {
                guard let point = body["point"] as? [Double],
                      let normal = body["normal"] as? [Double],
                      let distance = body["distance"] as? Double
                else { return }
                onExtrudeFace(ExtrudeFacePayload(point: point, normal: normal, distance: distance))
                return
            }

            if type == "update_sketch" {
                guard let id = body["id"] as? String, let polygon = body["polygon"] as? [[Double]] else { return }
                onUpdateSketch(UpdateSketchPayload(id: id, polygon: polygon))
                return
            }

            if type == "add_sketch" {
                guard let plane = body["plane"], let polygon = body["polygon"] as? [[Double]] else { return }
                onAddSketch(SketchPayload(plane: plane, polygon: polygon))
                return
            }

            guard type == "extrude_sketch",
                  let profile = body["profile"] as? String,
                  let distance = body["distance"] as? Double
            else { return }
            onExtrudeSketch(ExtrudeSketchPayload(profile: profile, distance: distance))
        }
    }
}
