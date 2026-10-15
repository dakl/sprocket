import AppKit

/// One document's window: a spinner while its engine starts, then the viewer,
/// or the reason the engine would not start.
///
/// Windows open as tabs of the front window (`tabbingMode = .preferred`), the
/// way Fusion keeps its documents. A tab dragged out becomes a window.
final class DocumentWindowController: NSWindowController, NSWindowDelegate {
    private var viewer: ViewerWebView?

    init() {
        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1280, height: 800),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.minSize = NSSize(width: 640, height: 480)
        window.tabbingMode = .preferred
        window.tabbingIdentifier = "sprocket-document"
        super.init(window: window)
        window.delegate = self
        shouldCascadeWindows = true
        showStarting()
    }

    required init?(coder: NSCoder) { fatalError("not used") }

    private func showStarting() {
        let spinner = NSProgressIndicator()
        spinner.style = .spinning
        spinner.startAnimation(nil)
        let label = NSTextField(labelWithString: "Starting the engine\u{2026}")
        let stack = NSStackView(views: [spinner, label])
        stack.orientation = .vertical
        setCentered(stack)
    }

    func showViewer(_ viewer: ViewerWebView) {
        self.viewer = viewer
        window?.contentView = viewer.webView
        if window?.isKeyWindow == true { (document as? SprocketDocument)?.engine.markFrontmost() }
    }

    func showFailure(_ message: String) {
        viewer = nil
        let text = NSTextView()
        text.string = message
        text.font = .monospacedSystemFont(ofSize: NSFont.systemFontSize, weight: .regular)
        text.isEditable = false
        text.textContainerInset = NSSize(width: 16, height: 16)
        let scroll = NSScrollView()
        scroll.documentView = text
        scroll.hasVerticalScroller = true
        text.autoresizingMask = [.width]
        window?.contentView = scroll
    }

    private func setCentered(_ view: NSView) {
        let container = NSView()
        view.translatesAutoresizingMaskIntoConstraints = false
        container.addSubview(view)
        NSLayoutConstraint.activate([
            view.centerXAnchor.constraint(equalTo: container.centerXAnchor),
            view.centerYAnchor.constraint(equalTo: container.centerYAnchor),
        ])
        window?.contentView = container
    }

    /// The window in front is the one an agent's MCP server connects to next.
    func windowDidBecomeKey(_ notification: Notification) {
        (document as? SprocketDocument)?.engine.markFrontmost()
    }

    /// The + in the tab bar.
    override func newWindowForTab(_ sender: Any?) {
        NSDocumentController.shared.newDocument(sender)
    }

    @objc func exportSTEP(_ sender: Any?) { (document as? SprocketDocument)?.export("step") }
    @objc func exportSTL(_ sender: Any?) { (document as? SprocketDocument)?.export("stl") }
    @objc func export3MF(_ sender: Any?) { (document as? SprocketDocument)?.export("3mf") }
}
