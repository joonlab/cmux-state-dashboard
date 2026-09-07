import AppKit
import SwiftUI

/// 웹 대시보드 `static/tokens.css` 의 상태색을 그대로 옮긴 것.
/// 값을 바꿀 일이 생기면 **tokens.css 를 먼저 고치고** 여기를 맞춘다(저쪽이 정본).
enum Theme {
    // tokens.css --green/--amber/--claude/--dim/--red (다크 / 라이트 두 세트)
    private static let dark: [String: NSColor] = [
        "green":  .init(srgbRed: 0x5e/255, green: 0xd1/255, blue: 0x8a/255, alpha: 1),
        "lemon":  .init(srgbRed: 0xdf/255, green: 0xc9/255, blue: 0x5f/255, alpha: 1),
        "amber":  .init(srgbRed: 0xd9/255, green: 0xae/255, blue: 0x45/255, alpha: 1),
        "claude": .init(srgbRed: 0xd9/255, green: 0x85/255, blue: 0x5f/255, alpha: 1),
        "dim":    .init(srgbRed: 0x98/255, green: 0xa4/255, blue: 0x9e/255, alpha: 1),
        "red":    .init(srgbRed: 0xef/255, green: 0x61/255, blue: 0x57/255, alpha: 1),
    ]
    private static let light: [String: NSColor] = [
        "green":  .init(srgbRed: 0x1a/255, green: 0x7f/255, blue: 0x3c/255, alpha: 1),
        "lemon":  .init(srgbRed: 0x7a/255, green: 0x6a/255, blue: 0x12/255, alpha: 1),
        "amber":  .init(srgbRed: 0x8a/255, green: 0x61/255, blue: 0x00/255, alpha: 1),
        "claude": .init(srgbRed: 0xa8/255, green: 0x50/255, blue: 0x1f/255, alpha: 1),
        "dim":    .init(srgbRed: 0x55/255, green: 0x5c/255, blue: 0x5b/255, alpha: 1),
        "red":    .init(srgbRed: 0xc4/255, green: 0x32/255, blue: 0x2c/255, alpha: 1),
    ]

    /// 메뉴바·팝오버는 시스템 외관을 따라간다. 다크에서 라이트 팔레트를 쓰면 대비가 무너진다.
    static func token(_ name: String, dark isDark: Bool) -> NSColor {
        (isDark ? dark : light)[name] ?? .labelColor
    }

    /// ⚠️ 전역 "시스템 외관"으로 칠하면 안 된다. 메뉴바와 팝오버는 서로 다른 외관으로 그려질 수
    /// 있고(실측: 시스템은 다크인데 팝오버가 라이트로 렌더링됐다), 그러면 대비가 무너진다.
    /// 그리는 쪽이 자기 외관을 넘겨 준다.
    static func isDark(_ appearance: NSAppearance?) -> Bool {
        (appearance ?? NSAppearance.currentDrawing())
            .bestMatch(from: [.aqua, .darkAqua]) == .darkAqua
    }

    static func color(for status: TabStatus, dark: Bool) -> NSColor {
        switch status {
        case .permission, .question: return token("amber", dark: dark)  // 막힘 — 웹의 .lic.st-permission
        case .running:               return token("green", dark: dark)
        // amber(권한 대기)와 **같은 노랑이면 안 된다** — 급한 정도가 정반대다.
        // amber 는 주황 쪽 황금, 이쪽은 밝은 레몬(tokens.css --lemon 과 같은 값).
        case .background:            return token("lemon", dark: dark)
        case .waiting:               return token("claude", dark: dark)
        case .idle:                  return token("dim", dark: dark)
        case .unknown:               return token("red", dark: dark)
        }
    }

    /// 웹은 lucide 를 쓴다. 뜻이 같은 SF Symbol 로 옮긴 것 — 자물쇠/도는 원/점/달.
    static func symbol(for status: TabStatus) -> String {
        switch status {
        case .permission, .question: return "lock.fill"                 // lucide lock-keyhole
        case .running:               return "circle.dotted"             // lucide loader-circle
        case .background:            return "bolt.fill"                 // lucide zap
        case .waiting:               return "smallcircle.filled.circle" // lucide circle-dot
        case .idle:                  return "moon.fill"                 // lucide moon
        case .unknown:               return "questionmark.circle"
        }
    }

    static func swiftUI(_ c: NSColor) -> Color { Color(nsColor: c) }
}

extension NSColor {
    /// `#RRGGBB` (워크스페이스 색) → NSColor. 형식이 어긋나면 nil — 억지로 그리지 않는다.
    convenience init?(hex: String?) {
        guard var s = hex, s.hasPrefix("#") else { return nil }
        s.removeFirst()
        guard s.count == 6, let v = UInt32(s, radix: 16) else { return nil }
        self.init(srgbRed: CGFloat((v >> 16) & 0xff) / 255,
                  green: CGFloat((v >> 8) & 0xff) / 255,
                  blue: CGFloat(v & 0xff) / 255, alpha: 1)
    }
}
