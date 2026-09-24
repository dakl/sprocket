import SwiftUI

@main
struct SprocketApp: App {
    init() {
        // Redirected stdout is fully buffered, not line-buffered, so log
        // output otherwise only appears once the process exits.
        setvbuf(stdout, nil, _IONBF, 0)
    }

    var body: some Scene {
        WindowGroup("Sprocket") {
            ContentView()
        }
    }
}
