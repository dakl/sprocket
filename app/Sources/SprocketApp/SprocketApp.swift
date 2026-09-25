import AppKit
import SwiftUI

@main
struct SprocketApp: App {
    init() {
        // Redirected stdout is fully buffered, not line-buffered, so log
        // output otherwise only appears once the process exits.
        setvbuf(stdout, nil, _IONBF, 0)

        // The engine now runs as a long-lived `sprocket serve` subprocess
        // (see EngineRunner), so it needs an explicit stop or it outlives
        // the window.
        NotificationCenter.default.addObserver(
            forName: NSApplication.willTerminateNotification,
            object: nil,
            queue: .main
        ) { _ in
            EngineRunner.shared.shutdown()
        }
    }

    var body: some Scene {
        WindowGroup("Sprocket") {
            ContentView()
        }
    }
}
