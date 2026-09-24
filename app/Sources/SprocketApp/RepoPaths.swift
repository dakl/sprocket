import Foundation

/// Dev-only path resolution: derives the repo root from this source file's
/// own location at compile time. Packaging (bundling the engine, pointing
/// at a configured path instead) is explicitly deferred — see the spec's
/// Decisions table.
enum RepoPaths {
    static let repoRoot: URL = {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()  // RepoPaths.swift -> SprocketApp/
            .deletingLastPathComponent()  // -> Sources/
            .deletingLastPathComponent()  // -> app/
            .deletingLastPathComponent()  // -> repo root
    }()

    static var engineDir: URL { repoRoot.appendingPathComponent("engine") }
    static var viewerDir: URL { repoRoot.appendingPathComponent("viewer") }
    static var examplesDir: URL { repoRoot.appendingPathComponent("examples") }
}
