import AppKit
import UniformTypeIdentifiers
import WebKit

/// One document's viewer: viewer/index.html in a WKWebView, talking to that
/// document's engine.
///
/// The viewer talks to the engine through one WKScriptMessageHandler named
/// "sprocket". Its messages are `{type: "rpc", id, method, params}`, which
/// this forwards to the daemon, with the reply going back through the page's own
/// window.__rpcReply, and `{type: "export", format}`, which asks the document
/// for its export panel. The viewer decides what a click means (viewer/edits.js).
/// Nothing here does.
final class ViewerWebView: NSObject, WKScriptMessageHandler {
    let webView: WKWebView
    private let engine: EngineDaemon
    private let onExport: (String) -> Void
    private var settingsObserver: NSObjectProtocol?
    private let debugScript: WKUserScript

    init(engine: EngineDaemon, dataDir: URL, onExport: @escaping (String) -> Void) {
        self.engine = engine
        self.onExport = onExport
        let configuration = WKWebViewConfiguration()
        // Every window loads the same page, but each reads its own document's
        // part.mesh, features.json and schemas.json. A scheme handler can answer
        // those three names per window, which a file:// page cannot.
        configuration.setURLSchemeHandler(
            ViewerSchemeHandler(viewerDir: AppPaths.viewerDir, dataDir: dataDir),
            forURLScheme: ViewerSchemeHandler.scheme
        )
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
        self.debugScript = WKUserScript(source: debugScript, injectionTime: .atDocumentStart, forMainFrameOnly: true)
        webView = WKWebView(frame: .zero, configuration: configuration)
        super.init()
        // The content controller holds its handler strongly, so it gets a weak proxy.
        configuration.userContentController.add(WeakMessageHandler(self), name: "sprocket")
        bindEngine()
        installUserScripts()
        watchSettings()
        webView.load(URLRequest(url: URL(string: "\(ViewerSchemeHandler.scheme)://viewer/index.html")!))
    }

    /// Any client's edit, the viewer's own or an agent's, arrives here as a
    /// revision. The viewer refetches the files in place, which keeps the
    /// camera where the user left it.
    private func bindEngine() {
        engine.onDocumentChanged = { [weak self] revision, opened, viewerError, json in
            let errorLiteral = viewerError.map { Self.literal($0) } ?? "null"
            // The params go in as a string literal and are parsed, so nothing in them runs as code.
            self?.run("window.reloadPart && window.reloadPart(\(revision), \(opened), \(errorLiteral), JSON.parse(\(Self.literal(json))));", "reloadPart")
        }
        engine.onNotification = { [weak self] method, json in
            self?.run("window.__daemonNotification && window.__daemonNotification(\(Self.literal(method)), JSON.parse(\(Self.literal(json))));", method)
        }
        // What an agent points at. Encoded as JSON, so nothing in it runs as code.
        engine.onHighlight = { [weak self] handles, sketches in
            guard let data = try? JSONSerialization.data(withJSONObject: ["handles": handles, "sketches": sketches]),
                  let json = String(data: data, encoding: .utf8)
            else { return }
            self?.run("window.__highlight && window.__highlight(JSON.parse(\(Self.literal(json))));", "highlight")
        }
    }

    deinit {
        if let settingsObserver { NotificationCenter.default.removeObserver(settingsObserver) }
    }

    /// The debug script, and the settings as window.__sprocketSettings. A user
    /// script's source is fixed once added, so a settings change installs them
    /// again: a reload of the page then reads the new values.
    private func installUserScripts() {
        let controller = webView.configuration.userContentController
        controller.removeAllUserScripts()
        controller.addUserScript(debugScript)
        controller.addUserScript(
            WKUserScript(source: "window.__sprocketSettings = \(AppSettings.viewerJSON());", injectionTime: .atDocumentStart, forMainFrameOnly: true)
        )
    }

    /// The open page gets a change through window.__applySettings. UserDefaults
    /// posts this for any default that changes, not only settings, and sending
    /// the same settings again is harmless.
    private func watchSettings() {
        settingsObserver = NotificationCenter.default.addObserver(
            forName: UserDefaults.didChangeNotification, object: nil, queue: .main
        ) { [weak self] _ in
            guard let self else { return }
            installUserScripts()
            run("window.__applySettings && window.__applySettings(\(AppSettings.viewerJSON()));", "applySettings")
        }
    }

    private func run(_ script: String, _ what: String) {
        webView.evaluateJavaScript(script) { _, error in
            if let error { print("[viewer] \(what) failed: \(error)") }
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

        // The Export button asks for a save panel, which only Swift can open.
        if type == "export", let format = body["format"] as? String {
            onExport(format)
            return
        }

        guard type == "rpc",
              let id = body["id"] as? Int,
              let method = body["method"] as? String
        else { return }
        let params = body["params"] as? [String: Any] ?? [:]
        engine.call(method: method, params: params) { [weak self] result in
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
        DispatchQueue.main.async { self.run(script, "rpc reply") }
    }

    private static func literal(_ text: String) -> String {
        let data = try? JSONSerialization.data(withJSONObject: [text])
        let array = data.flatMap { String(data: $0, encoding: .utf8) } ?? "[\"\"]"
        return String(array.dropFirst().dropLast())
    }
}

private final class WeakMessageHandler: NSObject, WKScriptMessageHandler {
    weak var target: WKScriptMessageHandler?
    init(_ target: WKScriptMessageHandler) { self.target = target }
    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        target?.userContentController(controller, didReceive: message)
    }
}

/// Serves `sprocket://viewer/<path>`: the three files the engine writes for this
/// window from `dataDir`, everything else from the viewer folder. A missing
/// `part.mesh` is a 404, which the viewer reads as an empty document.
final class ViewerSchemeHandler: NSObject, WKURLSchemeHandler {
    static let scheme = "sprocket"
    private static let perDocument: Set<String> = ["part.mesh", "features.json", "schemas.json"]

    private let viewerDir: URL
    private let dataDir: URL

    init(viewerDir: URL, dataDir: URL) {
        self.viewerDir = viewerDir.standardizedFileURL
        self.dataDir = dataDir
    }

    func webView(_ webView: WKWebView, start task: WKURLSchemeTask) {
        guard let url = task.request.url else { return }
        guard let file = Self.file(forPath: url.path, viewerDir: viewerDir, dataDir: dataDir) else {
            return respond(task, url, nil)
        }
        respond(task, url, try? Data(contentsOf: file), type: Self.mimeType(of: file))
    }

    /// The file a request path names: one of the three per-document files from
    /// `dataDir`, anything else from `viewerDir`. Nil for a path that climbs out
    /// of the viewer folder, whatever it says.
    static func file(forPath path: String, viewerDir: URL, dataDir: URL) -> URL? {
        let viewerDir = viewerDir.standardizedFileURL
        let relative = String(path.drop(while: { $0 == "/" }))
        if perDocument.contains(relative) {
            return dataDir.appendingPathComponent(relative)
        }
        let file = viewerDir.appendingPathComponent(relative).standardizedFileURL
        return file.path.hasPrefix(viewerDir.path + "/") ? file : nil
    }

    func webView(_ webView: WKWebView, stop task: WKURLSchemeTask) {}

    private func respond(_ task: WKURLSchemeTask, _ url: URL, _ data: Data?, type: String = "text/plain") {
        let response = HTTPURLResponse(
            url: url,
            statusCode: data == nil ? 404 : 200,
            httpVersion: "HTTP/1.1",
            headerFields: ["Content-Type": type, "Cache-Control": "no-store"]
        )!
        task.didReceive(response)
        task.didReceive(data ?? Data())
        task.didFinish()
    }

    static func mimeType(of file: URL) -> String {
        switch file.pathExtension {
        case "mesh": return "application/octet-stream"
        case "js", "mjs": return "text/javascript"
        default: return UTType(filenameExtension: file.pathExtension)?.preferredMIMEType ?? "application/octet-stream"
        }
    }
}
