import SwiftUI

struct ContentView: View {
    enum Status {
        case starting
        case ready
        case failed(String)
    }

    @EnvironmentObject private var store: DocumentStore
    @State private var status: Status = .starting

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
        .navigationTitle(store.title)
        .task {
            await start()
        }
    }

    /// The first start imports build123d, so it runs off the main thread. The
    /// daemon has written the viewer's files by the time it accepts a
    /// connection, so the page has a part to load when it opens.
    private func start() async {
        EngineDaemon.shared.onExit = { status = .failed("The engine stopped. Quit and reopen the app.") }
        let document = store.startupDocument()
        do {
            try await Task.detached {
                try EngineDaemon.shared.start(document: document, viewerDir: RepoPaths.viewerDir)
            }.value
            status = .ready
            store.ready = true
        } catch {
            store.forgetLastDocument()
            status = .failed("Could not start the engine: \(error.localizedDescription)")
        }
    }
}
