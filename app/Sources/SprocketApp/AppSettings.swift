import Foundation

/// The settings in the Settings window, kept in UserDefaults. Every viewer
/// reads them when its page loads and is sent them again when one changes.
enum AppSettings {
    static let zoomSensitivityKey = "zoomSensitivity"
    /// The same range as viewer/wheel-zoom.js. The viewer clamps as well, so a
    /// value written by hand with `defaults write` cannot break zooming.
    static let zoomSensitivityRange: ClosedRange<Double> = 0.25...4
    static let defaultZoomSensitivity = 1.0

    static var zoomSensitivity: Double {
        get { clamp(UserDefaults.standard.object(forKey: zoomSensitivityKey) as? Double ?? defaultZoomSensitivity) }
        set { UserDefaults.standard.set(clamp(newValue), forKey: zoomSensitivityKey) }
    }

    static func clamp(_ value: Double) -> Double {
        guard value.isFinite else { return defaultZoomSensitivity }
        return min(zoomSensitivityRange.upperBound, max(zoomSensitivityRange.lowerBound, value))
    }

    /// What the viewer reads as window.__sprocketSettings, as JSON.
    static func viewerJSON() -> String {
        let data = try? JSONSerialization.data(withJSONObject: [zoomSensitivityKey: zoomSensitivity])
        return data.flatMap { String(data: $0, encoding: .utf8) } ?? "{}"
    }
}
