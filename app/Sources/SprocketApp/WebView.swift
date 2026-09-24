import SwiftUI
import WebKit

/// Static viewport only (M2 scope): loads viewer/index.html once and grants
/// it read access to the viewer directory, so it can fetch part.stl next to
/// it. No JS bridge back to Swift yet — that arrives with picking in M3+.
struct WebView: NSViewRepresentable {
    let url: URL
    let readAccessURL: URL

    func makeNSView(context: Context) -> WKWebView {
        let webView = WKWebView()
        webView.loadFileURL(url, allowingReadAccessTo: readAccessURL)
        return webView
    }

    func updateNSView(_ webView: WKWebView, context: Context) {
        if webView.url != url {
            webView.loadFileURL(url, allowingReadAccessTo: readAccessURL)
        }
    }
}
