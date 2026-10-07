import SwiftUI

/// The File menu. Save only exists for a document with no name yet, because
/// the engine already writes every edit of a named one to its file.
struct FileCommands: Commands {
    @ObservedObject var store: DocumentStore

    var body: some Commands {
        CommandGroup(replacing: .newItem) {
            Button("New") { store.newDocument() }
                .keyboardShortcut("n")
                .disabled(!store.ready)
            Button("Open\u{2026}") { store.open() }
                .keyboardShortcut("o")
                .disabled(!store.ready)
            Menu("Open Recent") {
                ForEach(store.recents, id: \.self) { recent in
                    Button(recent.lastPathComponent) { store.open(recent) }
                }
                Divider()
                Button("Clear Menu") { store.clearRecents() }
                    .disabled(store.recents.isEmpty)
            }
            .disabled(!store.ready)
        }
        CommandGroup(replacing: .saveItem) {
            Button("Save") { store.saveAs() }
                .keyboardShortcut("s")
                .disabled(!store.ready || !store.isUntitled)
            Button("Save As\u{2026}") { store.saveAs() }
                .keyboardShortcut("s", modifiers: [.command, .shift])
                .disabled(!store.ready)
            Divider()
            Menu("Export") {
                Button("STEP\u{2026}") { store.export("step") }
                Button("STL\u{2026}") { store.export("stl") }
                Button("3MF\u{2026}") { store.export("3mf") }
            }
            .disabled(!store.ready)
        }
    }
}
