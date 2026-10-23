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

    /// Sprocket > Check for Updates…
    @objc func checkForUpdates(_ sender: Any?) {
        controller.checkForUpdates(sender)
    }
}

extension Updater: NSMenuItemValidation {
    func validateMenuItem(_ menuItem: NSMenuItem) -> Bool {
        menuItem.action == #selector(checkForUpdates(_:))
            ? isEnabled && controller.updater.canCheckForUpdates
            : true
    }
}
