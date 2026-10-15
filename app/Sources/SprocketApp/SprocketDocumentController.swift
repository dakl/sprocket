import AppKit

/// Tells AppKit's document machinery about `.sprocket` in code. A bundled app
/// would declare the type in its Info.plist, but `swift run` has no bundle, and
/// without a declaration NSDocumentController knows no document class at all.
/// The first instance made becomes `NSDocumentController.shared`, so main.swift
/// makes this one before anything asks.
final class SprocketDocumentController: NSDocumentController {
    override var defaultType: String? { SprocketDocument.typeName }
    override var documentClassNames: [String] { [NSStringFromClass(SprocketDocument.self)] }

    override func documentClass(forType typeName: String) -> AnyClass? {
        SprocketDocument.self
    }

    override func displayName(forType typeName: String) -> String? {
        "Sprocket document"
    }

    override func typeForContents(of url: URL) throws -> String {
        guard url.pathExtension == "sprocket" else {
            throw EngineError(message: "\(url.lastPathComponent) is not a .sprocket file")
        }
        return SprocketDocument.typeName
    }

    override func runModalOpenPanel(_ openPanel: NSOpenPanel, forTypes types: [String]?) -> Int {
        openPanel.allowedContentTypes = [SprocketDocument.contentType]
        return super.runModalOpenPanel(openPanel, forTypes: nil)
    }
}
