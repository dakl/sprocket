import Testing

@testable import SprocketApp

/// The process id in a daemon socket's name, which decides whether a launch
/// may delete the socket as stale.
struct SocketNameTests {
    @Test func aPerDocumentSocketNamesItsProcess() {
        #expect(EngineDaemon.pid(fromSocketName: "sprocket-16400-3.sock") == 16400)
    }

    @Test func theSingleWindowAppsSocketStillParses() {
        #expect(EngineDaemon.pid(fromSocketName: "sprocket-90071.sock") == 90071)
    }

    @Test(arguments: [
        "sprocket-.sock",
        "sprocket-abc.sock",
        "sprocket-12-x.sock",
        "sprocket-12-.sock",
        "sprocket-1-2-3.sock",
        "sprocket-12.socket",
        "other-12.sock",
        "sprocket-12-3.sock.tmp",
    ])
    func anyOtherNameIsLeftAlone(name: String) {
        #expect(EngineDaemon.pid(fromSocketName: name) == nil)
    }
}
