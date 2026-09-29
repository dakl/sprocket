import AppKit
import SwiftUI

// A plain `swift run` binary has no app bundle, so macOS never grants it a
// regular activation policy on its own - its window exists but never comes
// to the front. applicationDidFinishLaunching is the documented point to
// force both explicitly (doing it in App.init() is too early).
final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
    }
}

@main
struct SprocketApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate

    init() {
        // Redirected stdout is fully buffered, not line-buffered, so log
        // output otherwise only appears once the process exits.
        setvbuf(stdout, nil, _IONBF, 0)

        // The engine runs as a long-lived `sprocket daemon` subprocess
        // (see EngineDaemon), so it needs an explicit stop or it outlives
        // the window.
        NotificationCenter.default.addObserver(
            forName: NSApplication.willTerminateNotification,
            object: nil,
            queue: .main
        ) { _ in
            EngineDaemon.shared.shutdown()
        }
    }

    var body: some Scene {
        WindowGroup("Sprocket") {
            ContentView()
        }
    }
}
