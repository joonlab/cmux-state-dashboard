// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "CmuxMinimap",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(
            name: "CmuxMinimap",
            path: "Sources/CmuxMinimap"
        )
    ]
)
