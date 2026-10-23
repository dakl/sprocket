import Foundation

/// Where the app finds its viewer files and its engine.
///
/// Run from a source checkout (`swift run`), it uses the repo and `uv run`.
/// Run from a `.app` built by `app/package.sh --bundle`, it uses the Python and
/// viewer files inside the bundle. The test is the bundle's own extension.
enum AppPaths {
    static let isBundled: Bool = Bundle.main.bundleURL.pathExtension == "app"

    /// Dev only: `#filePath` is fixed when the app compiles.
    private static let repoRoot: URL = {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()  // AppPaths.swift -> SprocketApp/
            .deletingLastPathComponent()  // -> Sources/
            .deletingLastPathComponent()  // -> app/
            .deletingLastPathComponent()  // -> repo root
    }()

    private static var resources: URL { Bundle.main.resourceURL! }

    /// The folder the viewer page loads from, and the engine writes `part.mesh`,
    /// `features.json` and `schemas.json` into. A signed bundle is read-only, so
    /// a bundled app serves a copy under Caches instead.
    static let viewerDir: URL = {
        guard isBundled else { return repoRoot.appendingPathComponent("viewer") }
        let caches = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
        return caches.appendingPathComponent("dev.sprocket.app/viewer")
    }()

    /// Copies the bundle's viewer files into `viewerDir`. A fresh copy on every
    /// launch means a new app version never serves old viewer files. Does nothing
    /// when run from a checkout.
    static func prepareViewerDir() throws {
        guard isBundled else { return }
        let manager = FileManager.default
        try? manager.removeItem(at: viewerDir)
        try manager.createDirectory(at: viewerDir.deletingLastPathComponent(), withIntermediateDirectories: true)
        try manager.copyItem(at: resources.appendingPathComponent("viewer"), to: viewerDir)
    }

    private static var support: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Sprocket", isDirectory: true)
    }

    /// Where a new document lives until Save gives it a name. The daemon needs a
    /// file to open, and nothing is written there after the first empty one.
    static var untitledDir: URL { support.appendingPathComponent("Untitled", isDirectory: true) }

    /// Unsaved edits, one file per open document, written by its daemon
    /// (`--recovery`). One left behind by a dead process means a crash.
    static var recoveryDir: URL { support.appendingPathComponent("Recovery", isDirectory: true) }

    /// Each open document's `part.mesh`, `features.json` and `schemas.json`, in a
    /// folder of its own. The window's web view reads them through
    /// ViewerSchemeHandler, which serves everything else from `viewerDir`.
    static var documentDataRoot: URL {
        FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("dev.sprocket.app/documents", isDirectory: true)
    }

    /// Where the bundled Python writes its bytecode. By default it writes
    /// `__pycache__` folders inside the app, which breaks the app's signature.
    static var pycacheDir: URL {
        FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("dev.sprocket.app/pycache", isDirectory: true)
    }

    /// The executable and arguments that start `sprocket daemon`.
    static var engineLaunch: (executable: URL, arguments: [String]) {
        if isBundled {
            return (resources.appendingPathComponent("python/bin/python3"), ["-m", "sprocket_engine"])
        }
        let engineDir = repoRoot.appendingPathComponent("engine")
        return (URL(fileURLWithPath: "/usr/bin/env"), ["uv", "run", "--directory", engineDir.path, "sprocket"])
    }

    /// A bundle gets its Dock icon from `AppIcon.icns`, so only a checkout needs this.
    static var devIcon: URL? {
        isBundled ? nil : repoRoot.appendingPathComponent("app/AppIcon.png")
    }
}
