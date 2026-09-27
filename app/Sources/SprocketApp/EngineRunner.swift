import Foundation

struct EngineBuildResult {
    let success: Bool
    let output: String
    /// The id of the first feature that no longer builds, or nil. Editing a
    /// sketch can leave a later op unbuildable, and the engine then exports the
    /// part up to that point instead of failing the whole build (see cli.py's
    /// _build_document). The viewer marks this feature so the user can see
    /// which op was dropped.
    let failedFeature: String?
}

/// Runs the engine's `serve` subcommand (see engine/src/sprocket_engine/cli.py)
/// as one long-lived subprocess instead of spawning a fresh `build` process
/// per edit. Importing build123d (OCP's OpenCascade bindings) costs 2-3s,
/// so the old spawn-per-call approach made every op edit feel laggy. A
/// process that already has it imported turns a build back into a plain
/// replay. Requests and responses are one JSON object per line over
/// stdin/stdout, matching cli.py's `_serve`.
///
/// Subprocess isolation stays, for the reason the spec calls out: OCCT can
/// segfault on bad geometry, and a crash here should cost one failed
/// build, not the app. `shutdown()` is called on app exit so the engine
/// doesn't outlive the window.
final class EngineRunner {
    static let shared = EngineRunner()

    private var process: Process?
    private var stdin: FileHandle?
    private var stdout: FileHandle?
    private var readBuffer = Data()

    private init() {}

    func build(document: URL, meshOut: URL) -> EngineBuildResult {
        let request: [String: Any] = ["document": document.path, "mesh": meshOut.path]
        guard let requestLine = try? JSONSerialization.data(withJSONObject: request) else {
            return EngineBuildResult(success: false, output: "failed to encode build request", failedFeature: nil)
        }
        // One retry against a freshly spawned engine: enough to shrug off a
        // one-off OCCT segfault without masking a build that fails every time.
        return attemptBuild(requestLine, retriesLeft: 1)
    }

    func shutdown() {
        teardown()
    }

    private func attemptBuild(_ requestLine: Data, retriesLeft: Int) -> EngineBuildResult {
        if ensureRunning(), let result = send(requestLine) {
            return result
        }
        teardown()
        if retriesLeft > 0 {
            return attemptBuild(requestLine, retriesLeft: retriesLeft - 1)
        }
        return EngineBuildResult(success: false, output: "engine process is not responding", failedFeature: nil)
    }

    private func ensureRunning() -> Bool {
        if let process, process.isRunning {
            return true
        }

        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        process.arguments = [
            "uv", "run", "--directory", RepoPaths.engineDir.path,
            "sprocket", "serve",
        ]

        let stdinPipe = Pipe()
        let stdoutPipe = Pipe()
        process.standardInput = stdinPipe
        process.standardOutput = stdoutPipe
        // Kept off the line protocol on purpose: a traceback on stderr would
        // otherwise sit next to JSON responses on the same stream. Build
        // failures already come back as `success: false` from _serve.
        process.standardError = Pipe()

        do {
            try process.run()
        } catch {
            return false
        }

        self.process = process
        self.stdin = stdinPipe.fileHandleForWriting
        self.stdout = stdoutPipe.fileHandleForReading
        self.readBuffer = Data()
        return true
    }

    private func send(_ requestLine: Data) -> EngineBuildResult? {
        guard let stdin, let stdout else { return nil }

        var payload = requestLine
        payload.append(0x0A)
        do {
            try stdin.write(contentsOf: payload)
        } catch {
            return nil
        }

        guard let responseLine = readLine(from: stdout),
              let json = try? JSONSerialization.jsonObject(with: responseLine) as? [String: Any],
              let success = json["success"] as? Bool,
              let output = json["output"] as? String
        else {
            return nil
        }
        return EngineBuildResult(success: success, output: output, failedFeature: json["failed_feature"] as? String)
    }

    /// Reads up to the next `\n` from `handle`, blocking on `availableData`
    /// as needed. Returns nil if the pipe closes (the process exited)
    /// before a full line arrives.
    private func readLine(from handle: FileHandle) -> Data? {
        while true {
            if let newlineIndex = readBuffer.firstIndex(of: 0x0A) {
                let line = readBuffer[..<newlineIndex]
                readBuffer.removeSubrange(...newlineIndex)
                return line
            }
            let chunk = handle.availableData
            if chunk.isEmpty {
                return nil
            }
            readBuffer.append(chunk)
        }
    }

    private func teardown() {
        process?.terminate()
        process = nil
        stdin = nil
        stdout = nil
        readBuffer = Data()
    }
}
