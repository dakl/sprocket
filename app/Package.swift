// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "SprocketApp",
    platforms: [.macOS(.v13)],
    targets: [
        .executableTarget(
            name: "SprocketApp",
            path: "Sources/SprocketApp"
        ),
        // `swift test`. Swift Testing, not XCTest: the Command Line Tools ship
        // without XCTest, and a full Xcode is not assumed.
        .testTarget(
            name: "SprocketAppTests",
            dependencies: ["SprocketApp"],
            path: "Tests/SprocketAppTests"
        ),
    ]
)
