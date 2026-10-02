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
