import Foundation
import Testing

@testable import SprocketApp

/// Which file a `sprocket://viewer/<path>` request reads.
struct SchemeHandlerTests {
    let viewer = URL(fileURLWithPath: "/repo/viewer")
    let data = URL(fileURLWithPath: "/caches/documents/ABC")

    func file(_ path: String) -> String? {
        ViewerSchemeHandler.file(forPath: path, viewerDir: viewer, dataDir: data)?.path
    }

    @Test(arguments: ["part.mesh", "features.json", "schemas.json"])
    func theDocumentsOwnFilesComeFromItsDataFolder(name: String) {
        #expect(file("/" + name) == "/caches/documents/ABC/" + name)
    }

    @Test func everythingElseComesFromTheViewerFolder() {
        #expect(file("/index.html") == "/repo/viewer/index.html")
        #expect(file("/vendor/planegcs.js") == "/repo/viewer/vendor/planegcs.js")
    }

    @Test func aDocumentFileNameInASubfolderIsAViewerFile() {
        #expect(file("/test/part.mesh") == "/repo/viewer/test/part.mesh")
    }

    @Test(arguments: ["/../engine/src/sprocket_engine/rpc.py", "/vendor/../../secret", "/..", "/"])
    func aPathOutsideTheViewerFolderIsRefused(path: String) {
        #expect(file(path) == nil)
    }

    @Test func aDotDotThatStaysInsideIsAllowed() {
        #expect(file("/vendor/../index.html") == "/repo/viewer/index.html")
    }

    @Test func aSiblingFolderThatSharesThePrefixIsRefused() {
        // `/repo/viewer-old` starts with `/repo/viewer` but is not inside it.
        #expect(file("/../viewer-old/index.html") == nil)
    }

    @Test func meshAndScriptTypes() {
        #expect(ViewerSchemeHandler.mimeType(of: URL(fileURLWithPath: "/x/part.mesh")) == "application/octet-stream")
        #expect(ViewerSchemeHandler.mimeType(of: URL(fileURLWithPath: "/x/app.js")) == "text/javascript")
        #expect(ViewerSchemeHandler.mimeType(of: URL(fileURLWithPath: "/x/index.html")) == "text/html")
    }
}
