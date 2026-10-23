import AppKit
import SwiftUI

/// Sprocket > Settings… (Cmd+,). One window for the app, made on first use.
final class SettingsWindowController: NSWindowController {
    static let shared = SettingsWindowController()

    private init() {
        let window = NSWindow(contentViewController: NSHostingController(rootView: SettingsView()))
        window.title = "Settings"
        window.styleMask = [.titled, .closable]
        window.isReleasedWhenClosed = false
        super.init(window: window)
        window.center()
    }

    required init?(coder: NSCoder) { fatalError("not used") }
}

private struct SettingsView: View {
    // @AppStorage writes UserDefaults, and every ViewerWebView watches it.
    // The slider moves in log2 steps, so 0.5x and 2x sit the same distance
    // from 1x.
    @AppStorage(AppSettings.zoomSensitivityKey) private var zoomSensitivity = AppSettings.defaultZoomSensitivity

    private var exponent: Binding<Double> {
        Binding(
            get: { log2(AppSettings.clamp(zoomSensitivity)) },
            set: { zoomSensitivity = AppSettings.clamp(pow(2, ($0 * 4).rounded() / 4)) }
        )
    }

    var body: some View {
        Form {
            LabeledContent("Zoom speed") {
                VStack(alignment: .leading, spacing: 4) {
                    Slider(
                        value: exponent,
                        in: log2(AppSettings.zoomSensitivityRange.lowerBound)...log2(AppSettings.zoomSensitivityRange.upperBound)
                    ) {
                        EmptyView()
                    } minimumValueLabel: {
                        Text("Slow")
                    } maximumValueLabel: {
                        Text("Fast")
                    }
                    HStack {
                        Text(String(format: "%.2g\u{00D7}", AppSettings.clamp(zoomSensitivity)))
                            .monospacedDigit()
                            .foregroundStyle(.secondary)
                        Spacer()
                        Button("Reset") { zoomSensitivity = AppSettings.defaultZoomSensitivity }
                            .disabled(zoomSensitivity == AppSettings.defaultZoomSensitivity)
                    }
                    Text("How far the scroll wheel or a two-finger swipe zooms the view.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                .frame(width: 280)
            }
        }
        .padding(20)
        .fixedSize()
    }
}
