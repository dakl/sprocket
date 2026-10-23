import AppKit

final class AppDelegate: NSObject, NSApplicationDelegate {
    private static let openDocumentsKey = "openDocuments"
    /// Launched on one named file (a test run, a throwaway part). Quitting it
    /// then leaves the list of documents to reopen as it was.
    private var launchedOnNamedFile = false

    // A plain `swift run` binary has no app bundle, so macOS never grants it a
    // regular activation policy on its own - its window exists but never comes
    // to the front. applicationDidFinishLaunching is the documented point to
    // force both explicitly.
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        // A bare binary has no bundle icon, so set the Dock icon from app/AppIcon.png.
        // The Dock draws it as is, so the squircle is in the picture (see make_icon.py).
        if let iconURL = AppPaths.devIcon, let icon = NSImage(contentsOf: iconURL) {
            NSApp.applicationIconImage = icon
        }
        NSApp.activate(ignoringOtherApps: true)
        EngineDaemon.removeStaleSockets()

        let recovered = offerRecovery()
        let toOpen = startupDocuments().filter { url in !recovered.contains { $0.path == url.path }
        }
        for url in toOpen {
            NSDocumentController.shared.openDocument(withContentsOf: url, display: true) {
                _, _, error in
                if let error { NSApp.presentError(error) }
            }
        }
        if recovered.isEmpty && toOpen.isEmpty {
            NSDocumentController.shared.newDocument(nil)
        }
        Updater.shared.start()
    }

    /// Sprocket > Settings…, Cmd+,.
    @objc func showSettings(_ sender: Any?) {
        SettingsWindowController.shared.showWindow(sender)
        SettingsWindowController.shared.window?.makeKeyAndOrderFront(sender)
    }

    /// Launch opens what was open at the last quit, not a blank window.
    func applicationShouldOpenUntitledFile(_ sender: NSApplication) -> Bool { false }

    /// NSDocumentController has already asked about every unsaved document by
    /// now, so what is left open is either saved or let go.
    func applicationWillTerminate(_ notification: Notification) {
        let documents = NSDocumentController.shared.documents.compactMap { $0 as? SprocketDocument }
        let named = documents.compactMap(\.fileURL).map(\.path)
        if !launchedOnNamedFile { UserDefaults.standard.set(named, forKey: Self.openDocumentsKey) }
        for document in documents { document.shutDown() }
    }

    // MARK: - Startup

    /// A `.sprocket` path on the command line (`swift run SprocketApp part.sprocket`),
    /// else SPROCKET_DOCUMENT, which lets a throwaway part be edited without risking
    /// the one in examples/, else whatever was open at the last quit. Only a
    /// `.sprocket` argument counts, because macOS adds flags such as
    /// `-NSDocumentRevisionsDebugMode`.
    private func startupDocuments() -> [URL] {
        if let path = CommandLine.arguments.dropFirst().first(where: { $0.hasSuffix(".sprocket") })
            ?? ProcessInfo.processInfo.environment["SPROCKET_DOCUMENT"]
        {
            launchedOnNamedFile = true
            return [URL(fileURLWithPath: path)]
        }
        let paths =
            UserDefaults.standard.stringArray(forKey: Self.openDocumentsKey)
            // The single-window app kept one path under this key.
            ?? UserDefaults.standard.string(forKey: "lastDocument").map { [$0] }
            ?? []
        return paths.filter { FileManager.default.fileExists(atPath: $0) }.map {
            URL(fileURLWithPath: $0)
        }
    }

    /// Recovery files whose process is gone are edits a crash kept from being
    /// saved. Offers them back, each in its own window, still unsaved. Returns
    /// the files reopened that way, so startup does not open them twice.
    private func offerRecovery() -> [URL] {
        let manager = FileManager.default
        let orphans = Recovery.orphans(in: AppPaths.recoveryDir)
        guard !orphans.isEmpty else { return [] }

        let alert = NSAlert()
        alert.messageText =
            orphans.count == 1
            ? "Sprocket quit with unsaved changes in a document."
            : "Sprocket quit with unsaved changes in \(orphans.count) documents."
        alert.informativeText =
            "Restore them to keep working on them. They stay unsaved until you save them."
        alert.addButton(withTitle: "Restore")
        alert.addButton(withTitle: "Discard")
        guard alert.runModal() == .alertFirstButtonReturn else {
            for file in orphans { try? manager.removeItem(at: file) }
            return []
        }

        var reopened: [URL] = []
        for file in orphans {
            if let target = Recovery.restoreTarget(for: file, untitledDir: AppPaths.untitledDir),
                let document = try? NSDocumentController.shared.makeDocument(
                    withContentsOf: target, ofType: SprocketDocument.typeName) as? SprocketDocument
            {
                open(document, restoring: file)
                reopened.append(target)
            } else if let document = try? NSDocumentController.shared.makeUntitledDocument(
                ofType: SprocketDocument.typeName) as? SprocketDocument
            {
                open(document, restoring: file)
            }
            // The crashed run's holding file for an untitled document is no use now.
            if let path = Recovery.documentPath(in: file),
                Recovery.isHoldingFile(path, untitledDir: AppPaths.untitledDir)
            {
                try? manager.removeItem(at: path)
            }
        }
        return reopened
    }

    private func open(_ document: SprocketDocument, restoring file: URL) {
        document.pendingRecovery = file
        NSDocumentController.shared.addDocument(document)
        document.makeWindowControllers()
        document.showWindows()
    }
}
