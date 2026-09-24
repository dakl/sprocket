import Foundation

struct EngineBuildResult {
    let success: Bool
    let output: String
}

/// Runs the engine's own CLI as a subprocess (see engine/src/sprocket_engine/cli.py).
/// This is the M1 interface: no daemon, no JSON-RPC. The subprocess-per-call
/// approach mirrors the spec's "engine never embedded, always supervised"
/// rule — a crash here costs one failed build, not the app.
enum EngineRunner {
    static func build(document: URL, stlOut: URL) -> EngineBuildResult {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        process.arguments = [
            "uv", "run", "--directory", RepoPaths.engineDir.path,
            "sprocket", "build", document.path,
            "--stl", stlOut.path,
        ]

        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe

        do {
            try process.run()
        } catch {
            return EngineBuildResult(success: false, output: "failed to launch uv: \(error.localizedDescription)")
        }

        process.waitUntilExit()
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        let output = String(data: data, encoding: .utf8) ?? ""
        return EngineBuildResult(success: process.terminationStatus == 0, output: output)
    }
}
