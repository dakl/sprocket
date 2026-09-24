// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "SprocketApp",
    platforms: [.macOS(.v13)],
    targets: [
        .executableTarget(
            name: "SprocketApp",
            path: "Sources/SprocketApp"
        )
    ]
)
