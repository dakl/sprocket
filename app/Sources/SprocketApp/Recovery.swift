import Foundation

/// The recovery files each document's daemon writes while it has unsaved
/// edits (`--recovery`), named `<pid>-<token>.json`. A clean close or quit
/// deletes them, so one whose process is gone was left by a crash.
enum Recovery {
    /// Files in `directory` whose process is no longer alive.
    static func orphans(in directory: URL, isAlive: (Int32) -> Bool = EngineDaemon.isAlive) -> [URL] {
        let names = (try? FileManager.default.contentsOfDirectory(atPath: directory.path)) ?? []
        return names.sorted().filter { name in
            guard name.hasSuffix(".json"), let pid = Int32(name.prefix { $0 != "-" }) else { return false }
            return !isAlive(pid)
        }.map { directory.appendingPathComponent($0) }
    }

    /// The `path` the file says the edits belong to, if the file reads.
    static func documentPath(in file: URL) -> URL? {
        (try? Data(contentsOf: file))
            .flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
            .flatMap { $0["path"] as? String }
            .map { URL(fileURLWithPath: $0) }
    }

    /// Where restored edits should open: the named file they belong to, or nil
    /// for a new untitled document. An untitled document's file under
    /// `untitledDir` was only a holding place, and a file that is gone cannot
    /// be reopened.
    static func restoreTarget(for file: URL, untitledDir: URL) -> URL? {
        guard let path = documentPath(in: file), !isHoldingFile(path, untitledDir: untitledDir),
              FileManager.default.fileExists(atPath: path.path)
        else { return nil }
        return path
    }

    static func isHoldingFile(_ url: URL, untitledDir: URL) -> Bool {
        url.deletingLastPathComponent().standardizedFileURL == untitledDir.standardizedFileURL
    }
}
