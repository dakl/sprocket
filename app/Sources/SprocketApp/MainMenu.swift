import AppKit

/// The menu bar, built in code because there is no nib. The File items send the
/// standard NSDocument actions (`saveDocument:`, `revertDocumentToSaved:` and so
/// on) up the responder chain, so AppKit enables them and routes them to the
/// document in front.
enum MainMenu {
    static func make() -> NSMenu {
        let bar = NSMenu()
        bar.addItem(submenu(appMenu()))
        bar.addItem(submenu(fileMenu()))
        bar.addItem(submenu(editMenu()))
        bar.addItem(submenu(viewMenu()))
        let window = windowMenu()
        bar.addItem(submenu(window))
        NSApp.windowsMenu = window
        return bar
    }

    private static func appMenu() -> NSMenu {
        let menu = NSMenu(title: "Sprocket")
        menu.addItem(
            withTitle: "About Sprocket",
            action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        menu.addItem(.separator())
        menu.addItem(
            withTitle: "Settings\u{2026}", action: #selector(AppDelegate.showSettings(_:)),
            keyEquivalent: ",")
        menu.addItem(.separator())
        let services = NSMenu(title: "Services")
        menu.addItem(withTitle: "Services", action: nil, keyEquivalent: "").submenu = services
        NSApp.servicesMenu = services
        menu.addItem(.separator())
        menu.addItem(
            withTitle: "Hide Sprocket", action: #selector(NSApplication.hide(_:)),
            keyEquivalent: "h")
        menu.addItem(
            item(
                "Hide Others", #selector(NSApplication.hideOtherApplications(_:)), "h",
                [.command, .option]))
        menu.addItem(
            withTitle: "Show All", action: #selector(NSApplication.unhideAllApplications(_:)),
            keyEquivalent: "")
        menu.addItem(.separator())
        menu.addItem(
            withTitle: "Quit Sprocket", action: #selector(NSApplication.terminate(_:)),
            keyEquivalent: "q")
        return menu
    }

    private static func fileMenu() -> NSMenu {
        let menu = NSMenu(title: "File")
        menu.addItem(
            withTitle: "New", action: #selector(NSDocumentController.newDocument(_:)),
            keyEquivalent: "n")
        menu.addItem(
            withTitle: "Open\u{2026}", action: #selector(NSDocumentController.openDocument(_:)),
            keyEquivalent: "o")
        // NSDocumentController fills a menu that holds a clearRecentDocuments: item.
        let recent = NSMenu(title: "Open Recent")
        recent.addItem(
            withTitle: "Clear Menu",
            action: #selector(NSDocumentController.clearRecentDocuments(_:)), keyEquivalent: "")
        menu.addItem(withTitle: "Open Recent", action: nil, keyEquivalent: "").submenu = recent
        menu.addItem(.separator())
        menu.addItem(
            withTitle: "Close", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        menu.addItem(withTitle: "Save", action: #selector(NSDocument.save(_:)), keyEquivalent: "s")
        menu.addItem(
            item("Save As\u{2026}", #selector(NSDocument.saveAs(_:)), "s", [.command, .shift]))
        menu.addItem(
            withTitle: "Revert to Saved", action: #selector(NSDocument.revertToSaved(_:)),
            keyEquivalent: "")
        menu.addItem(.separator())
        let export = NSMenu(title: "Export")
        export.addItem(
            withTitle: "STEP\u{2026}", action: #selector(DocumentWindowController.exportSTEP(_:)),
            keyEquivalent: "")
        export.addItem(
            withTitle: "STL\u{2026}", action: #selector(DocumentWindowController.exportSTL(_:)),
            keyEquivalent: "")
        export.addItem(
            withTitle: "3MF\u{2026}", action: #selector(DocumentWindowController.export3MF(_:)),
            keyEquivalent: "")
        menu.addItem(withTitle: "Export", action: nil, keyEquivalent: "").submenu = export
        return menu
    }

    /// Undo and Redo reach the web view first, and the viewer handles Cmd+Z
    /// itself, so these only matter inside a text field.
    private static func editMenu() -> NSMenu {
        let menu = NSMenu(title: "Edit")
        menu.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        menu.addItem(item("Redo", Selector(("redo:")), "z", [.command, .shift]))
        menu.addItem(.separator())
        menu.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        menu.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        menu.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        menu.addItem(
            withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        return menu
    }

    private static func viewMenu() -> NSMenu {
        let menu = NSMenu(title: "View")
        menu.addItem(
            item(
                "Enter Full Screen", #selector(NSWindow.toggleFullScreen(_:)), "f",
                [.command, .control]))
        return menu
    }

    private static func windowMenu() -> NSMenu {
        let menu = NSMenu(title: "Window")
        menu.addItem(
            withTitle: "Minimize", action: #selector(NSWindow.performMiniaturize(_:)),
            keyEquivalent: "m")
        menu.addItem(
            withTitle: "Zoom", action: #selector(NSWindow.performZoom(_:)), keyEquivalent: "")
        menu.addItem(.separator())
        menu.addItem(
            withTitle: "Bring All to Front", action: #selector(NSApplication.arrangeInFront(_:)),
            keyEquivalent: "")
        return menu
    }

    private static func submenu(_ menu: NSMenu) -> NSMenuItem {
        let item = NSMenuItem(title: menu.title, action: nil, keyEquivalent: "")
        item.submenu = menu
        return item
    }

    private static func item(
        _ title: String, _ action: Selector, _ key: String, _ modifiers: NSEvent.ModifierFlags
    ) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: key)
        item.keyEquivalentModifierMask = modifiers
        return item
    }
}
