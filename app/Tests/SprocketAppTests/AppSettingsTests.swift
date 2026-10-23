import Foundation
import Testing

@testable import SprocketApp

/// The zoom setting stays inside the range the viewer accepts.
struct AppSettingsTests {
    @Test func clampKeepsTheRange() {
        #expect(AppSettings.clamp(10) == 4)
        #expect(AppSettings.clamp(0) == 0.25)
        #expect(AppSettings.clamp(1.5) == 1.5)
        #expect(AppSettings.clamp(.nan) == AppSettings.defaultZoomSensitivity)
        #expect(AppSettings.clamp(.infinity) == AppSettings.defaultZoomSensitivity)
    }

    @Test func viewerJSONCarriesTheSetting() throws {
        let data = try #require(AppSettings.viewerJSON().data(using: .utf8))
        let object = try #require(try JSONSerialization.jsonObject(with: data) as? [String: Double])
        #expect(object[AppSettings.zoomSensitivityKey] == AppSettings.zoomSensitivity)
    }
}
