import AppKit

// Redirected stdout is fully buffered, not line-buffered, so log output
// otherwise only appears once the process exits.
setvbuf(stdout, nil, _IONBF, 0)

// `swift run SprocketApp part.sprocket` passes the path to
// AppDelegate.startupDocuments. Left alone, AppKit also reads it as a file to
// open, and opens it a second time.
UserDefaults.standard.register(defaults: ["NSTreatUnknownArgumentsAsOpen": "NO"])

// The first document controller made becomes the shared one.
_ = SprocketDocumentController()

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.mainMenu = MainMenu.make()
app.run()
