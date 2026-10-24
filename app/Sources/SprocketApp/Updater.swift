import AppKit
import Sparkle

/// Finds and installs new releases from the appcast on dakl/sprocket-releases.
/// Only a release bundle (`package.sh --bundle`) carries `SUFeedURL` and
/// `SUPublicEDKey`. A source build has neither, so it never starts the updater
/// and never replaces itself with a release.
final class Updater: NSObject {
    static let shared = Updater()

    private let controller = SPUStandardUpdaterController(
        startingUpdater: false, updaterDelegate: nil, userDriverDelegate: nil)

    var isEnabled: Bool {
        Bundle.main.object(forInfoDictionaryKey: "SUFeedURL") != nil
            && Bundle.main.object(forInfoDictionaryKey: "SUPublicEDKey") != nil
    }

    /// Sparkle checks once a day after this, and asks the first time whether
    /// to check on its own.
    func start() {
        guard isEnabled else { return }
        controller.startUpdater()
    }

    /// Sprocket > Check for Updates…, and Check Now in Settings.
    @objc func checkForUpdates(_ sender: Any?) {
        controller.checkForUpdates(sender)
    }

    /// Sparkle keeps both switches in UserDefaults, so they hold when the
    /// updater has not started.
    var updater: SPUUpdater { controller.updater }
}

/// What the Settings window shows about updates. Sparkle reports when it can
/// check again and when it last checked through KVO, so this republishes both.
final class UpdateSettings: ObservableObject {
    @Published private(set) var canCheck = false
    @Published private(set) var lastCheck: Date?
    private var observations: [NSKeyValueObservation] = []

    var isEnabled: Bool { Updater.shared.isEnabled }

    init() {
        let updater = Updater.shared.updater
        observations = [
            updater.observe(\.canCheckForUpdates, options: [.initial, .new]) { [weak self] updater, _ in
                DispatchQueue.main.async { self?.canCheck = updater.canCheckForUpdates }
            },
            updater.observe(\.lastUpdateCheckDate, options: [.initial, .new]) { [weak self] updater, _ in
                DispatchQueue.main.async { self?.lastCheck = updater.lastUpdateCheckDate }
            },
        ]
    }

    var checksAutomatically: Bool {
        get { Updater.shared.updater.automaticallyChecksForUpdates }
        set {
            objectWillChange.send()
            Updater.shared.updater.automaticallyChecksForUpdates = newValue
        }
    }

    var downloadsAutomatically: Bool {
        get { Updater.shared.updater.automaticallyDownloadsUpdates }
        set {
            objectWillChange.send()
            Updater.shared.updater.automaticallyDownloadsUpdates = newValue
        }
    }

    func checkNow() {
        Updater.shared.checkForUpdates(nil)
    }
}

extension Updater: NSMenuItemValidation {
    func validateMenuItem(_ menuItem: NSMenuItem) -> Bool {
        menuItem.action == #selector(checkForUpdates(_:))
            ? isEnabled && controller.updater.canCheckForUpdates
            : true
    }
}
