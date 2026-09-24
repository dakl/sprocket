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

/// Loads viewer/index.html and grants it read access to the viewer
/// directory, so it can fetch part.stl next to it. Registers a
/// WKScriptMessageHandler named "sprocket" so the JS side can post a
/// push/pull commit back to Swift.
struct WebView: NSViewRepresentable {
    let url: URL
    let readAccessURL: URL
    let onExtrudeFace: (ExtrudeFacePayload) -> Void

    func makeCoordinator() -> Coordinator {
        Coordinator(onExtrudeFace: onExtrudeFace)
    }

    func makeNSView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.userContentController.add(context.coordinator, name: "sprocket")
        let webView = WKWebView(frame: .zero, configuration: configuration)
        webView.loadFileURL(url, allowingReadAccessTo: readAccessURL)
        return webView
    }

    func updateNSView(_ webView: WKWebView, context: Context) {
        if webView.url != url {
            webView.loadFileURL(url, allowingReadAccessTo: readAccessURL)
        }
    }

    final class Coordinator: NSObject, WKScriptMessageHandler {
        private let onExtrudeFace: (ExtrudeFacePayload) -> Void

        init(onExtrudeFace: @escaping (ExtrudeFacePayload) -> Void) {
            self.onExtrudeFace = onExtrudeFace
        }

        func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
            guard message.name == "sprocket",
                  let body = message.body as? [String: Any],
                  body["type"] as? String == "extrude_face",
                  let point = body["point"] as? [Double],
                  let normal = body["normal"] as? [Double],
                  let distance = body["distance"] as? Double
            else { return }
            onExtrudeFace(ExtrudeFacePayload(point: point, normal: normal, distance: distance))
        }
    }
}
