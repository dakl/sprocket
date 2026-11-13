// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "SprocketApp",
    platforms: [.macOS(.v13)],
    dependencies: [
        // Auto-update from the appcast on dakl/sprocket-releases (see docs/PACKAGING.md).
        .package(url: "https://github.com/sparkle-project/Sparkle", from: "2.6.0")
    ],
    targets: [
        .executableTarget(
            name: "SprocketApp",
            dependencies: [.product(name: "Sparkle", package: "Sparkle")],
            path: "Sources/SprocketApp",
            // A bundle has Sparkle in Contents/Frameworks (package.sh copies it), and
            // `swift run` has it beside the binary in .build.
            linkerSettings: [
                .unsafeFlags([
                    "-Xlinker", "-rpath", "-Xlinker", "@executable_path/../Frameworks", "-Xlinker",
                    "-rpath", "-Xlinker", "@executable_path",
                ])
            ]
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
