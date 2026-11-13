import AppKit
import UniformTypeIdentifiers

/// One open `.sprocket` file: its own engine daemon, its own window and viewer.
///
/// The engine owns the document's content. NSDocument owns the parts a Mac user
/// expects of a file: the title, the Edited dot, Save and Save As, Revert, the
/// "Do you want to save" sheet on close and the review on quit. So this class
/// never reads or writes the file's bytes. Save asks the engine to write
/// (`save`, `save_as`), and the engine's `unsaved` flag drives the Edited dot.
///
/// The daemon runs with `--no-autosave`, so an edit changes nothing on disk
/// until Save. It copies unsaved edits to a recovery file after each change
/// instead, so a crash loses nothing (see AppDelegate.offerRecovery).
final class SprocketDocument: NSDocument {
    /// The app is not bundled in development, so the type has no Info.plist
    /// declaration and macOS makes one up from the extension. A file on disk gets
    /// exactly that made-up type, so the document uses it too.
    static let contentType = UTType(filenameExtension: "sprocket") ?? .data
    static var typeName: String { contentType.identifier }

    let engine = EngineDaemon()
    private let token = UUID().uuidString
    /// Where this document's daemon writes the viewer's three files.
    private lazy var dataDir = AppPaths.documentDataRoot.appendingPathComponent(
        token, isDirectory: true)
    /// `<pid>-<token>.json`, so a launch can tell a crashed run's files from a live one's.
    lazy var recoveryURL = AppPaths.recoveryDir.appendingPathComponent(
        "\(ProcessInfo.processInfo.processIdentifier)-\(token).json"
    )
    /// The file an untitled document's daemon opens, until Save As names it.
    private var holdingURL: URL?
    /// Edits from a crashed run, put back once the engine is up.
    var pendingRecovery: URL?
    private(set) var isEngineReady = false

    override init() {
        super.init()
        // The engine keeps the undo history. Without this, NSDocument's own empty
        // undo manager would answer Edit > Undo before the viewer could.
        hasUndoManager = false
    }

    override class var autosavesInPlace: Bool { false }
    override class var readableTypes: [String] { [typeName] }
    override class var writableTypes: [String] { [typeName] }
    override class func isNativeType(_ type: String) -> Bool { type == typeName }

    override func writableTypes(for saveOperation: NSDocument.SaveOperationType) -> [String] {
        [Self.typeName]
    }

    override func fileNameExtension(
        forType typeName: String, saveOperation: NSDocument.SaveOperationType
    ) -> String? {
        "sprocket"
    }

    override func prepareSavePanel(_ savePanel: NSSavePanel) -> Bool {
        savePanel.allowedContentTypes = [Self.contentType]
        return true
    }

    // MARK: - Reading

    /// The engine reads the file when it starts. A file it cannot load shows the
    /// engine's own message in the window.
    override func read(from url: URL, ofType typeName: String) throws {}

    override func makeWindowControllers() {
        let controller = DocumentWindowController()
        addWindowController(controller)
        start(in: controller)
    }

    /// The first start imports build123d, so it runs off the main thread. The
    /// daemon has written the viewer's files by the time it accepts a
    /// connection, so the page has a part to load when it opens.
    private func start(in controller: DocumentWindowController) {
        let document: URL
        do {
            document = try fileURL ?? makeHoldingFile()
            try FileManager.default.createDirectory(at: dataDir, withIntermediateDirectories: true)
            try FileManager.default.createDirectory(
                at: AppPaths.recoveryDir, withIntermediateDirectories: true)
        } catch {
            controller.showFailure("Could not start the engine: \(error.localizedDescription)")
            return
        }
        engine.onExit = { [weak controller] in
            controller?.showFailure(
                "The engine stopped. Close this window and open the file again.")
        }
        engine.onSaveStateChanged = { [weak self] unsaved, author in
            self?.engineSaveStateChanged(unsaved, author: author)
        }
        let engine = engine
        let dataDir = dataDir
        let recoveryURL = recoveryURL
        Task.detached { [weak self] in
            do {
                try AppPaths.prepareViewerDir()
                try engine.start(document: document, viewerDir: dataDir, recovery: recoveryURL)
                await self?.engineStarted(in: controller)
            } catch {
                await MainActor.run {
                    controller.showFailure(
                        "Could not start the engine: \(error.localizedDescription)")
                }
            }
        }
    }

    @MainActor
    private func engineStarted(in controller: DocumentWindowController) {
        isEngineReady = true
        controller.showViewer(
            ViewerWebView(engine: engine, dataDir: dataDir) { [weak self] format in
                self?.export(format)
            })
        restorePendingRecovery()
    }

    private func restorePendingRecovery() {
        guard let file = pendingRecovery else { return }
        pendingRecovery = nil
        Task {
            do {
                _ = try await engine.call("restore_recovery", ["file": file.path])
                try? FileManager.default.removeItem(at: file)
            } catch {
                presentError(error)
            }
        }
    }

    /// The Edited dot follows the engine: an undo back to what the file holds
    /// clears it, which a change count could not know.
    private func engineSaveStateChanged(_ unsaved: Bool, author: String?) {
        if unsaved != isDocumentEdited {
            updateChangeCount(unsaved ? .changeDone : .changeCleared)
        }
        // The engine reloaded the file after another program wrote it. NSDocument
        // would otherwise refuse the next Save as a file changed behind its back.
        if author == "file", let fileURL {
            fileModificationDate =
                (try? fileURL.resourceValues(forKeys: [.contentModificationDateKey]))?
                .contentModificationDate
        }
    }

    // MARK: - Saving

    /// NSDocument's own safe save writes to a temporary file and swaps it in. The
    /// engine already writes atomically and has to know the final path, so the
    /// write goes straight to it.
    override func writeSafely(
        to url: URL, ofType typeName: String, for saveOperation: NSDocument.SaveOperationType
    ) throws {
        guard isEngineReady else { throw EngineError(message: "the engine has not started yet") }
        switch saveOperation {
        case .saveOperation where url == fileURL:
            _ = try engine.callAndWait("save", [:])
        case .saveOperation, .saveAsOperation:
            _ = try engine.callAndWait("save_as", ["path": url.path])
            discardHoldingFile()
        default:
            throw EngineError(message: "Sprocket cannot save a copy yet. Use Save As.")
        }
    }

    override func revert(toContentsOf url: URL, ofType typeName: String) throws {
        _ = try engine.callAndWait("open_document", ["path": url.path])
    }

    /// Rename and Move in the title bar move the file. The engine has to follow,
    /// or the next Save would write to the old path.
    override func move(to url: URL, completionHandler: ((Error?) -> Void)? = nil) {
        super.move(to: url) { [weak self] error in
            guard let self, error == nil else {
                completionHandler?(error)
                return
            }
            Task {
                do {
                    _ = try await self.engine.call("document_moved", ["path": url.path])
                    completionHandler?(nil)
                } catch {
                    completionHandler?(error)
                }
            }
        }
    }

    override func duplicate() throws -> NSDocument {
        throw EngineError(message: "Sprocket cannot duplicate a document yet. Use Save As.")
    }

    /// `format` is "step", "stl" or "3mf", which is also the extension and the
    /// engine's parameter name.
    func export(_ format: String) {
        guard let window = windowControllers.first?.window else { return }
        let panel = NSSavePanel()
        panel.allowedContentTypes = [UTType(filenameExtension: format) ?? .data]
        panel.nameFieldStringValue = displayName + "." + format
        panel.beginSheetModal(for: window) { [weak self] response in
            guard response == .OK, let target = panel.url, let self else { return }
            Task {
                do {
                    _ = try await self.engine.call("export", [format: target.path])
                } catch {
                    self.presentError(error)
                }
            }
        }
    }

    // MARK: - Closing

    /// By the time this runs the person has saved or chosen Don't Save, so the
    /// recovery copy has done its job.
    override func close() {
        shutDown()
        super.close()
    }

    /// Stops the engine and deletes this document's scratch files. Also run on
    /// quit, when NSDocument does not close its documents.
    func shutDown() {
        engine.shutdown()
        try? FileManager.default.removeItem(at: recoveryURL)
        try? FileManager.default.removeItem(at: dataDir)
        discardHoldingFile()
    }

    private func makeHoldingFile() throws -> URL {
        try FileManager.default.createDirectory(
            at: AppPaths.untitledDir, withIntermediateDirectories: true)
        let url = AppPaths.untitledDir.appendingPathComponent("\(token).sprocket")
        try Data("{\"features\": []}\n".utf8).write(to: url)
        holdingURL = url
        return url
    }

    private func discardHoldingFile() {
        guard let holdingURL else { return }
        try? FileManager.default.removeItem(at: holdingURL)
        self.holdingURL = nil
    }
}
