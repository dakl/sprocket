import SwiftUI
import WebKit

/// Loads viewer/index.html and grants it read access to the viewer directory,
/// so it can fetch part.mesh, features.json and schemas.json next to it.
///
/// The viewer talks to the engine through one WKScriptMessageHandler named
/// "sprocket". Its only message is `{type: "rpc", id, method, params}`, which
/// this forwards to the daemon, and the reply goes back through the page's own
/// window.__rpcReply. The viewer decides what a click means (viewer/edits.js).
/// Nothing here does.
struct WebView: NSViewRepresentable {
    let url: URL
    let readAccessURL: URL

    func makeCoordinator() -> Coordinator {
        Coordinator()
    }

    func makeNSView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        // WKWebView treats each file:// URL as its own origin, so the
        // viewer's fetch() of part.mesh/features.json next to it is blocked
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
        context.coordinator.webView = webView
        webView.loadFileURL(url, allowingReadAccessTo: readAccessURL)
        return webView
    }

    func updateNSView(_ webView: WKWebView, context: Context) {}

    final class Coordinator: NSObject, WKScriptMessageHandler {
        weak var webView: WKWebView? {
            didSet {
                // Any client's edit, the viewer's own or an agent's, arrives
                // here as a revision. The viewer refetches the files in place,
                // which keeps the camera where the user left it.
                EngineDaemon.shared.onDocumentChanged = { [weak self] revision in
                    self?.webView?.evaluateJavaScript("window.reloadPart && window.reloadPart(\(revision));") { _, error in
                        if let error { print("[viewer] reloadPart failed: \(error)") }
                    }
                }
            }
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

            guard type == "rpc",
                  let id = body["id"] as? Int,
                  let method = body["method"] as? String
            else { return }
            let params = body["params"] as? [String: Any] ?? [:]
            EngineDaemon.shared.call(method: method, params: params) { [weak self] result in
                self?.reply(id: id, result: result)
            }
        }

        /// Hands the daemon's answer to window.__rpcReply. Encoded as JSON text
        /// and passed as a string literal, so nothing in it is ever run as code.
        private func reply(id: Int, result: Result<[String: Any], EngineError>) {
            let script: String
            switch result {
            case .success(let value):
                guard let data = try? JSONSerialization.data(withJSONObject: value),
                      let json = String(data: data, encoding: .utf8)
                else { return }
                script = "window.__rpcReply(\(id), JSON.parse(\(Self.literal(json))));"
            case .failure(let error):
                script = "window.__rpcReply(\(id), null, \(Self.literal(error.message)));"
            }
            DispatchQueue.main.async {
                self.webView?.evaluateJavaScript(script) { _, error in
                    if let error { print("[viewer] rpc reply failed: \(error)") }
                }
            }
        }

        private static func literal(_ text: String) -> String {
            let data = try? JSONSerialization.data(withJSONObject: [text])
            let array = data.flatMap { String(data: $0, encoding: .utf8) } ?? "[\"\"]"
            return String(array.dropFirst().dropLast())
        }
    }
}
