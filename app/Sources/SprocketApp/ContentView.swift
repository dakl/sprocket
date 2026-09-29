import SwiftUI

struct ContentView: View {
    enum Status {
        case starting
        case ready
        case failed(String)
    }

    @State private var status: Status = .starting

    // SPROCKET_DOCUMENT opens a different part, so a throwaway document can be
    // edited without risking the one in examples/.
    private let document = ProcessInfo.processInfo.environment["SPROCKET_DOCUMENT"]
        .map { URL(fileURLWithPath: $0) }
        ?? RepoPaths.examplesDir.appendingPathComponent("box.json")

    var body: some View {
        Group {
            switch status {
            case .starting:
                ProgressView("Starting the engine\u{2026}")
            case .ready:
                WebView(
                    url: RepoPaths.viewerDir.appendingPathComponent("index.html"),
                    readAccessURL: RepoPaths.viewerDir
                )
            case .failed(let message):
                ScrollView {
                    Text(message)
                        .font(.system(.body, design: .monospaced))
                        .padding()
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
        }
        .frame(minWidth: 640, minHeight: 480)
        .task {
            await start()
        }
    }

    /// The first start imports build123d, so it runs off the main thread. The
    /// daemon has written the viewer's files by the time it accepts a
    /// connection, so the page has a part to load when it opens.
    private func start() async {
        EngineDaemon.shared.onExit = { status = .failed("The engine stopped. Quit and reopen the app.") }
        do {
            try await Task.detached { [document] in
                try EngineDaemon.shared.start(document: document, viewerDir: RepoPaths.viewerDir)
            }.value
            status = .ready
        } catch {
            status = .failed("Could not start the engine: \(error.localizedDescription)")
        }
    }
}
