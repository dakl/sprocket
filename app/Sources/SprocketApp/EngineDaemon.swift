import Darwin
import Foundation

struct EngineError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

/// Runs `sprocket daemon` (see engine/src/sprocket_engine/rpc.py) as one
/// long-lived subprocess and talks to it over its Unix socket, one JSON-RPC
/// request per line.
///
/// The app holds no editing rules. The viewer works out which edits a click
/// means (viewer/edits.js) and asks for them through `call`. The daemon applies
/// them, keeps the undo history and rewrites the files the viewer reads
/// (`--viewer-dir`), then says so with a `document_changed` notification.
///
/// Subprocess isolation stays, for the reason the spec calls out: OCCT can
/// segfault on bad geometry, and a crash should cost one failed edit, not the
/// app. `shutdown()` runs on app exit so the engine does not outlive the window.
final class EngineDaemon {
    static let shared = EngineDaemon()

    /// Called on the main queue with the new revision after any client's edit.
    /// `opened` is true when the engine swapped in another document. The last
    /// argument is the whole notification's params as JSON (author, changes).
    var onDocumentChanged: ((Int, Bool, String?, String) -> Void)?
    /// Called on the main queue with any other notification the viewer acts
    /// on, such as `document_conflict`: the method and its params as JSON.
    var onNotification: ((String, String) -> Void)?
    /// Called on the main queue when an agent highlights faces, edges or sketches.
    /// An empty pair clears the highlight.
    var onHighlight: (([String], [String]) -> Void)?
    /// Called on the main queue if the connection drops, which means the engine died.
    var onExit: (() -> Void)?

    private var process: Process?
    private var socketPath: String?
    private var descriptor: Int32 = -1
    private let lock = NSLock()
    private var nextID = 1
    private var pending: [Int: (Result<[String: Any], EngineError>) -> Void] = [:]

    private init() {}

    /// Starts the daemon and blocks until it accepts a connection. The daemon
    /// writes the viewer's files before it opens the socket, so a connection
    /// means the viewer has something to load. Call off the main thread: the
    /// first start imports build123d, which takes a few seconds.
    func start(document: URL, viewerDir: URL) throws {
        let path = NSTemporaryDirectory() + "sprocket-\(ProcessInfo.processInfo.processIdentifier).sock"
        // The kernel caps a Unix socket path at about 100 bytes.
        guard path.utf8.count < 100 else {
            throw EngineError(message: "socket path is too long: \(path)")
        }
        try? FileManager.default.removeItem(atPath: path)
        Self.removeStaleSockets()

        let process = Process()
        let launch = AppPaths.engineLaunch
        process.executableURL = launch.executable
        process.arguments = launch.arguments + [
            "daemon", document.path,
            "--socket", path, "--viewer-dir", viewerDir.path,
        ]
        // Both streams go to our own log, and both must be drained: a full
        // pipe would block the engine mid-print.
        let output = Pipe()
        process.standardOutput = output
        process.standardError = output
        output.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            guard !data.isEmpty, let text = String(data: data, encoding: .utf8) else { return }
            print("[engine] \(text)", terminator: "")
        }
        try process.run()
        self.process = process
        self.socketPath = path

        var attempts = 0
        while attempts < 600 {
            if !process.isRunning {
                throw EngineError(message: "the engine exited before it opened its socket (see the [engine] log lines)")
            }
            if let connected = Self.connect(to: path) {
                descriptor = connected
                break
            }
            attempts += 1
            Thread.sleep(forTimeInterval: 0.1)
        }
        guard descriptor >= 0 else {
            throw EngineError(message: "the engine did not open its socket within 60 seconds")
        }
        Thread.detachNewThread { [weak self] in self?.readLoop() }
    }

    /// One JSON-RPC call. The completion runs on a background thread with the
    /// reply's `result`, or an error carrying the engine's own message.
    func call(method: String, params: [String: Any], completion: @escaping (Result<[String: Any], EngineError>) -> Void) {
        lock.lock()
        let id = nextID
        nextID += 1
        pending[id] = completion
        lock.unlock()

        let request: [String: Any] = ["jsonrpc": "2.0", "id": id, "method": method, "params": params]
        guard var line = try? JSONSerialization.data(withJSONObject: request) else {
            finish(id, .failure(EngineError(message: "could not encode the request")))
            return
        }
        line.append(0x0A)
        lock.lock()
        let fd = descriptor
        lock.unlock()
        let written = line.withUnsafeBytes { buffer -> Int in
            var sent = 0
            while sent < buffer.count {
                let count = write(fd, buffer.baseAddress! + sent, buffer.count - sent)
                if count <= 0 { return -1 }
                sent += count
            }
            return sent
        }
        if written < 0 {
            finish(id, .failure(EngineError(message: "the engine is not responding")))
        }
    }

    /// Deletes `sprocket-<pid>.sock` files left by a run that was killed, so
    /// they do not pile up. A socket whose pid is still alive is kept.
    static func removeStaleSockets() {
        let directory = NSTemporaryDirectory()
        let names = (try? FileManager.default.contentsOfDirectory(atPath: directory)) ?? []
        for name in names where name.hasPrefix("sprocket-") && name.hasSuffix(".sock") {
            let digits = name.dropFirst("sprocket-".count).dropLast(".sock".count)
            guard let pid = Int32(digits) else { continue }
            // kill(pid, 0) sends nothing. EPERM means the pid exists but is not ours.
            if kill(pid, 0) == 0 || errno == EPERM { continue }
            try? FileManager.default.removeItem(atPath: directory + name)
        }
    }

    func shutdown() {
        lock.lock()
        let fd = descriptor
        descriptor = -1
        lock.unlock()
        if fd >= 0 { close(fd) }
        process?.terminate()
        process = nil
        if let socketPath { try? FileManager.default.removeItem(atPath: socketPath) }
    }

    // MARK: - Reading

    private func readLoop() {
        var buffer = Data()
        var chunk = [UInt8](repeating: 0, count: 65536)
        while true {
            lock.lock()
            let fd = descriptor
            lock.unlock()
            let count = read(fd, &chunk, chunk.count)
            if count <= 0 { break }
            buffer.append(chunk, count: count)
            while let newline = buffer.firstIndex(of: 0x0A) {
                let line = buffer[buffer.startIndex..<newline]
                buffer.removeSubrange(buffer.startIndex...newline)
                handle(line: Data(line))
            }
        }
        failEverythingPending()
        DispatchQueue.main.async { self.onExit?() }
    }

    private func handle(line: Data) {
        guard let message = try? JSONSerialization.jsonObject(with: line) as? [String: Any] else { return }
        if let id = message["id"] as? Int {
            if let error = message["error"] as? [String: Any] {
                finish(id, .failure(EngineError(message: error["message"] as? String ?? "the engine refused the request")))
            } else {
                finish(id, .success(message["result"] as? [String: Any] ?? [:]))
            }
            return
        }
        if message["method"] as? String == "document_changed",
           let params = message["params"] as? [String: Any],
           let revision = params["revision"] as? Int {
            let opened = params["opened"] as? Bool ?? false
            let viewerError = params["viewer_error"] as? String
            let json = (try? JSONSerialization.data(withJSONObject: params)).flatMap { String(data: $0, encoding: .utf8) } ?? "{}"
            DispatchQueue.main.async { self.onDocumentChanged?(revision, opened, viewerError, json) }
        }
        if let method = message["method"] as? String, method == "document_conflict",
           let params = message["params"] as? [String: Any],
           let data = try? JSONSerialization.data(withJSONObject: params),
           let json = String(data: data, encoding: .utf8) {
            DispatchQueue.main.async { self.onNotification?(method, json) }
        }
        if message["method"] as? String == "highlight", let params = message["params"] as? [String: Any] {
            let handles = params["handles"] as? [String] ?? []
            let sketches = params["sketches"] as? [String] ?? []
            DispatchQueue.main.async { self.onHighlight?(handles, sketches) }
        }
    }

    private func finish(_ id: Int, _ result: Result<[String: Any], EngineError>) {
        lock.lock()
        let completion = pending.removeValue(forKey: id)
        lock.unlock()
        completion?(result)
    }

    private func failEverythingPending() {
        lock.lock()
        let stranded = pending
        pending = [:]
        lock.unlock()
        for completion in stranded.values {
            completion(.failure(EngineError(message: "the engine stopped")))
        }
    }

    // MARK: - Socket

    private static func connect(to path: String) -> Int32? {
        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else { return nil }
        var address = sockaddr_un()
        address.sun_family = sa_family_t(AF_UNIX)
        withUnsafeMutablePointer(to: &address.sun_path) { pointer in
            pointer.withMemoryRebound(to: CChar.self, capacity: 104) { destination in
                _ = strncpy(destination, path, 103)
            }
        }
        let result = withUnsafePointer(to: &address) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.connect(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        if result == 0 { return fd }
        close(fd)
        return nil
    }
}
