import AppKit
import SwiftUI
import UniformTypeIdentifiers

/// Which document is open, and the File menu's actions on it.
///
/// The engine owns the document and writes every edit to its file as it
/// happens (see rpc.py), so there is no unsaved state and no Save for a titled
/// document. A new document is the exception: it has no name yet, so it lives
/// under Application Support until Save As gives it one. That file is what the
/// next launch reopens, so an untitled document is never lost either.
@MainActor
final class DocumentStore: ObservableObject {
    @Published private(set) var url: URL?
    @Published private(set) var recents: [URL] = []
    /// False until the engine is up. The menu items wait for it.
    @Published var ready = false

    private static let lastDocumentKey = "lastDocument"

    /// The app is not bundled, so the type has no Info.plist declaration and
    /// macOS makes one up from the extension. A file on disk gets exactly that
    /// made-up type. Adding `conformingTo: .json` makes a different one that the
    /// file does not conform to, and the open panel greys the file out.
    private static let documentType = UTType(filenameExtension: "sprocket") ?? .data

    private static var untitledDirectory: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Sprocket/Untitled", isDirectory: true)
    }

    private static func isUntitled(_ url: URL) -> Bool {
        url.deletingLastPathComponent().standardizedFileURL == untitledDirectory.standardizedFileURL
    }

    init() {
        refreshRecents()
    }

    var isUntitled: Bool { url.map(Self.isUntitled) ?? true }

    var title: String {
        guard let url, !Self.isUntitled(url) else { return "Untitled" }
        return url.deletingPathExtension().lastPathComponent
    }

    // MARK: - Starting up

    /// The file the engine starts on: a `.sprocket` path on the command line
    /// (`swift run SprocketApp part.sprocket`), else SPROCKET_DOCUMENT, which
    /// lets a throwaway part be edited without risking the one in examples/,
    /// else the document last open, else a new one. Only a `.sprocket` argument
    /// counts, because macOS adds flags such as `-NSDocumentRevisionsDebugMode`.
    func startupDocument() -> URL {
        let chosen: URL
        if let path = CommandLine.arguments.dropFirst().first(where: { $0.hasSuffix(".sprocket") }) {
            chosen = URL(fileURLWithPath: path)
        } else if let path = ProcessInfo.processInfo.environment["SPROCKET_DOCUMENT"] {
            chosen = URL(fileURLWithPath: path)
        } else if let last = UserDefaults.standard.string(forKey: Self.lastDocumentKey),
                  FileManager.default.fileExists(atPath: last) {
            chosen = URL(fileURLWithPath: last)
        } else {
            chosen = Self.makeUntitled()
        }
        url = chosen
        return chosen
    }

    /// A last document that will not load would fail every launch, so a failed
    /// start forgets it and the next one begins on a new document.
    func forgetLastDocument() {
        UserDefaults.standard.removeObject(forKey: Self.lastDocumentKey)
    }

    // MARK: - File menu

    func newDocument() {
        let fresh = Self.untitledURL()
        Task {
            await perform("create a document") {
                _ = try await EngineDaemon.shared.call("new_document", ["path": fresh.path])
                self.adopt(fresh)
            }
        }
    }

    func open() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [Self.documentType]
        panel.allowsMultipleSelection = false
        guard panel.runModal() == .OK, let chosen = panel.url else { return }
        open(chosen)
    }

    func open(_ chosen: URL) {
        Task {
            await perform("open \(chosen.lastPathComponent)") {
                _ = try await EngineDaemon.shared.call("open_document", ["path": chosen.path])
                self.adopt(chosen)
            }
        }
    }

    func saveAs() {
        let panel = NSSavePanel()
        panel.allowedContentTypes = [Self.documentType]
        panel.nameFieldStringValue = isUntitled ? "Untitled.sprocket" : (url?.lastPathComponent ?? "Untitled.sprocket")
        if let url, !isUntitled { panel.directoryURL = url.deletingLastPathComponent() }
        guard panel.runModal() == .OK, let target = panel.url else { return }
        let previous = url
        Task {
            await perform("save the document") {
                _ = try await EngineDaemon.shared.call("save_as", ["path": target.path])
                self.adopt(target)
                // The untitled file was only ever a holding place.
                if let previous, Self.isUntitled(previous) { try? FileManager.default.removeItem(at: previous) }
            }
        }
    }

    /// `format` is "step" or "stl", which is also the extension and the
    /// engine's parameter name.
    func export(_ format: String) {
        let panel = NSSavePanel()
        panel.allowedContentTypes = [UTType(filenameExtension: format) ?? .data]
        panel.nameFieldStringValue = title + "." + format
        guard panel.runModal() == .OK, let target = panel.url else { return }
        Task {
            await perform("export \(format.uppercased())") {
                _ = try await EngineDaemon.shared.call("export", [format: target.path])
            }
        }
    }

    func clearRecents() {
        NSDocumentController.shared.clearRecentDocuments(nil)
        refreshRecents()
    }

    // MARK: - Internals

    private func adopt(_ opened: URL) {
        url = opened
        UserDefaults.standard.set(opened.path, forKey: Self.lastDocumentKey)
        if !Self.isUntitled(opened) {
            NSDocumentController.shared.noteNewRecentDocumentURL(opened)
        }
        refreshRecents()
    }

    private func refreshRecents() {
        recents = NSDocumentController.shared.recentDocumentURLs
            .filter { FileManager.default.fileExists(atPath: $0.path) }
    }

    /// Runs `work`, and tells the user in the engine's own words if it fails.
    private func perform(_ what: String, _ work: () async throws -> Void) async {
        do {
            try await work()
        } catch {
            let alert = NSAlert()
            alert.alertStyle = .warning
            alert.messageText = "Could not \(what)"
            alert.informativeText = error.localizedDescription
            alert.runModal()
        }
    }

    private static func untitledURL() -> URL {
        let stamp = DateFormatter()
        stamp.dateFormat = "yyyyMMdd-HHmmss"
        try? FileManager.default.createDirectory(at: untitledDirectory, withIntermediateDirectories: true)
        return untitledDirectory.appendingPathComponent("Untitled-\(stamp.string(from: Date())).sprocket")
    }

    /// The daemon needs a file that exists, so the first document is written here.
    /// Later ones go through `new_document`, which writes its own.
    private static func makeUntitled() -> URL {
        let fresh = untitledURL()
        FileManager.default.createFile(atPath: fresh.path, contents: Data("{\"features\": []}\n".utf8))
        return fresh
    }
}

extension EngineDaemon {
    func call(_ method: String, _ params: [String: Any]) async throws -> [String: Any] {
        try await withCheckedThrowingContinuation { continuation in
            call(method: method, params: params) { result in
                continuation.resume(with: result.mapError { $0 as Error })
            }
        }
    }
}
