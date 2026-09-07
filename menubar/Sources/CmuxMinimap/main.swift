import AppKit

/// 메뉴바 전용 앱. Dock 아이콘도, 메뉴 막대도 없다(`.accessory` + Info.plist 의 LSUIElement).
@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    private var controller: StatusItemController?

    func applicationDidFinishLaunching(_ n: Notification) {
        controller = StatusItemController()
    }
}

/* 같은 앱이 이미 떠 있으면 그쪽을 물러나게 한다.
   저장소 안의 .app 과 /Applications 의 .app 은 **경로가 달라 macOS 가 별개로 띄운다** —
   개발 중에 둘 다 실행되면 메뉴바에 아이콘이 둘 생기고, 어느 쪽이 최신 빌드인지도 알 수 없다.
   방금 실행한 쪽이 최신이므로 먼저 있던 것을 정리한다. */
do {
    let me = ProcessInfo.processInfo.processIdentifier
    let bid = Bundle.main.bundleIdentifier ?? "com.joonlab.cmux-minimap"
    for other in NSRunningApplication.runningApplications(withBundleIdentifier: bid)
    where other.processIdentifier != me {
        other.terminate()
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
