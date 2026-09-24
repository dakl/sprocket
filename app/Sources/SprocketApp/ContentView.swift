import SwiftUI

struct ContentView: View {
    enum BuildStatus {
        case building
        case ready
        case failed(String)
    }

    @State private var status: BuildStatus = .building

    private let document = RepoPaths.examplesDir.appendingPathComponent("box.json")
    private let stlOut = RepoPaths.viewerDir.appendingPathComponent("part.stl")

    var body: some View {
        Group {
            switch status {
            case .building:
                ProgressView("Building part\u{2026}")
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
            let result = EngineRunner.build(document: document, stlOut: stlOut)
            status = result.success ? .ready : .failed(result.output)
        }
    }
}
