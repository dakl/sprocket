import Foundation
import Testing

@testable import SprocketApp

/// Finding the recovery files a crash left behind, and where each one reopens.
struct RecoveryTests {
    let root: URL
    let recovery: URL
    let untitled: URL

    init() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent("recovery-tests-\(UUID().uuidString)")
        recovery = root.appendingPathComponent("Recovery")
        untitled = root.appendingPathComponent("Untitled")
        try FileManager.default.createDirectory(at: recovery, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: untitled, withIntermediateDirectories: true)
    }

    @discardableResult
    func write(_ name: String, path: String?) throws -> URL {
        let file = recovery.appendingPathComponent(name)
        let body: [String: Any] = ["path": path ?? NSNull(), "snapshot": ["features": []]]
        try JSONSerialization.data(withJSONObject: body).write(to: file)
        return file
    }

    @Test func onlyFilesOfDeadProcessesAreOrphans() throws {
        try write("100-a.json", path: nil)
        try write("200-b.json", path: nil)
        let orphans = Recovery.orphans(in: recovery, isAlive: { $0 == 200 })
        #expect(orphans.map(\.lastPathComponent) == ["100-a.json"])
    }

    @Test func filesThatAreNotRecoveryFilesAreIgnored() throws {
        try write("100-a.json.tmp", path: nil)
        try write("notapid-a.json", path: nil)
        try write("100-a.txt", path: nil)
        #expect(Recovery.orphans(in: recovery, isAlive: { _ in false }).isEmpty)
    }

    @Test func aMissingRecoveryFolderHasNoOrphans() {
        #expect(Recovery.orphans(in: root.appendingPathComponent("nope"), isAlive: { _ in false }).isEmpty)
    }

    @Test func editsOfANamedFileReopenThatFile() throws {
        let part = root.appendingPathComponent("part.sprocket")
        try Data("{}".utf8).write(to: part)
        let file = try write("100-a.json", path: part.path)
        #expect(Recovery.restoreTarget(for: file, untitledDir: untitled)?.path == part.path)
    }

    @Test func editsOfAnUntitledDocumentOpenANewOne() throws {
        let holding = untitled.appendingPathComponent("X.sprocket")
        try Data("{}".utf8).write(to: holding)
        let file = try write("100-a.json", path: holding.path)
        #expect(Recovery.restoreTarget(for: file, untitledDir: untitled) == nil)
        #expect(Recovery.isHoldingFile(holding, untitledDir: untitled))
    }

    @Test func editsOfAFileThatIsGoneOpenANewDocument() throws {
        let file = try write("100-a.json", path: root.appendingPathComponent("deleted.sprocket").path)
        #expect(Recovery.restoreTarget(for: file, untitledDir: untitled) == nil)
    }

    @Test func anUnreadableFileHasNoPath() throws {
        let file = recovery.appendingPathComponent("100-a.json")
        try Data("not json".utf8).write(to: file)
        #expect(Recovery.documentPath(in: file) == nil)
        #expect(Recovery.restoreTarget(for: file, untitledDir: untitled) == nil)
    }
}
