import AppKit
import SwiftUI
import Combine

/// 메뉴바 아이템 하나와 그 팝오버를 관리한다.
@MainActor
final class StatusItemController {
    private static let autosave = "CmuxMinimap"
    private let item = NSStatusItem.statusItem()
    private let client = NavClient()
    private let popover = NSPopover()
    private var bag = Set<AnyCancellable>()

    init() {
        popover.behavior = .transient
        popover.contentViewController = NSHostingController(
            rootView: PopoverView(client: client, onJump: { [weak self] in self?.close() }))

        // 자리와 위치를 **명시적으로** 요구한다.
        // 메뉴바가 포화 상태면 macOS 는 새 status item 을 조용히 안 그린다(윈도우조차 안 만든다).
        // autosaveName 이 있어야 "NSStatusItem Preferred Position <name>" 으로 자리를 요구할 수
        // 있고, 사용자가 옮긴 위치도 재실행 후 유지된다.
        //
        // ⚠️ 선호 위치를 **요구하지 않으면 포화 시 조용히 사라진다.** 실측(이 맥):
        //    노치 오른쪽 가용 구간은 848pt 에서 시작하는데 첫 아이템이 882pt 라 여유가 34pt 뿐이고,
        //    선호 위치가 없는 새 항목은 x=31(애플 메뉴 옆)로 밀려 앱 메뉴에 완전히 가려졌다.
        //    "앱은 떠 있고 status item 도 있는데 화면에만 없는" 상태라 눈으로는 원인을 못 찾는다.
        //    0 = 가장 우측을 요구한다는 뜻(값이 낮을수록 우측).
        // 사용자가 ⌘드래그로 옮기면 그 값이 여기 저장되므로, **이미 값이 있으면 건드리지 않는다.**
        let posKey = "NSStatusItem Preferred Position \(Self.autosave)"
        if UserDefaults.standard.object(forKey: posKey) == nil {
            UserDefaults.standard.set(0.0, forKey: posKey)
        }
        item.autosaveName = Self.autosave
        item.isVisible = true

        if let b = item.button {
            b.target = self
            b.action = #selector(toggle)
            b.imagePosition = .imageLeading
        }

        // 스냅샷이 바뀔 때마다 배지를 다시 그린다.
        client.$snapshot.sink { [weak self] snap in self?.render(snap) }.store(in: &bag)
        client.$lastError.sink { [weak self] _ in self?.render(self?.client.snapshot) }.store(in: &bag)
        render(nil)
        client.start()
        if ProcessInfo.processInfo.environment["CMUXMM_DEBUG"] != nil { debugDump() }
    }

    /// 자리를 실제로 얻었는지 확인용 — status item 이 '있는데 안 보이는' 상태를 구별한다.
    private func debugDump() {
        Task { @MainActor in
            for i in 0..<4 {
                try? await Task.sleep(for: .seconds(1))
                let b = item.button
                let w = b?.window
                var s = "[dump \(i)] visible=\(item.isVisible) length=\(item.length)"
                s += " autosave=\(item.autosaveName ?? "nil")"
                s += " button=\(b != nil) image=\(b?.image != nil)"
                s += " title=\(b?.attributedTitle.string ?? "-")"
                s += " window=\(w != nil) frame=\(w.map { String(describing: $0.frame) } ?? "nil")"
                s += " winVisible=\(w?.isVisible ?? false)"
                s += " screen=\(w?.screen.map { String(describing: $0.frame) } ?? "nil")\n"
                FileHandle.standardError.write(Data(s.utf8))
            }
        }
    }

    // ── 배지 ──────────────────────────────────────────────────────────
    // 숫자는 **막힘(permission+question)** 이다 — 내가 안 보면 0 이 진행되는 것들.
    // 막힘이 없을 때만 작업중 수를 초록으로 보여준다(둘을 같은 색으로 쓰면 뜻이 섞인다).
    private func render(_ snap: NavResponse?) {
        guard let b = item.button else { return }
        let dark = Theme.isDark(b.effectiveAppearance)  // 메뉴바 버튼 자신의 외관

        guard let s = snap else {
            b.image = badge("exclamationmark.triangle", count: 0, color: Theme.token("red", dark: dark))
            b.attributedTitle = NSAttributedString(string: "")
            b.toolTip = "cmux 관제실 — \(client.lastError ?? "불러오는 중")"
            return
        }

        let blocked = s.blockedCount, running = s.runningCount, bg = s.backgroundCount
        let (sym, color, count): (String, NSColor, Int)
        if blocked > 0 {
            sym = Theme.symbol(for: .permission); color = Theme.token("amber", dark: dark)
            count = blocked
        } else if running > 0 {
            // 살아 있는 것을 한 숫자로 보여 주되(작업중 + 뒤에서 진행), 색은 급한 쪽을 따른다.
            sym = Theme.symbol(for: .running); color = Theme.token("green", dark: dark)
            count = running + bg
        } else if bg > 0 {
            // 전경은 다 끝나고 뒤에서만 도는 상태 — 유휴(zzz)와 헷갈리면 안 되므로 색까지 바꾼다.
            sym = Theme.symbol(for: .background); color = Theme.token("lemon", dark: dark)
            count = bg
        } else {
            /* ⚠️ 유휴만 팝오버(moon)와 **다른 심볼**을 쓴다.
               메뉴바의 moon.fill 은 macOS **집중 모드** 아이콘과 사실상 같은 그림이라, 집중 모드가
               켜져 있으면 달이 둘 나란히 서서 "앱이 두 개 떴다"로 읽힌다(신고 2026-09-03 —
               실제로는 status item 이 하나뿐이었다). 팝오버 카드 안에서는 시스템 아이콘과 겹칠
               일이 없으므로 거기서는 웹과 같은 moon 을 그대로 쓴다.
               메뉴바에 놓는 그림은 **뜻이 맞는지보다 시스템 아이콘과 헷갈리지 않는지가 먼저**다. */
            sym = "zzz"; color = Theme.token("dim", dark: dark); count = 0
        }
        b.image = badge(sym, count: count, color: color)
        b.attributedTitle = NSAttributedString(string: "")
        let stale = client.lastError.map { " (갱신 실패: \($0))" } ?? ""
        b.toolTip = "막힘 \(blocked) · 작업중 \(running) · 뒤에서 \(bg) · 탭 \(s.total)\(stale)"
    }

    /// 아이콘과 숫자를 **한 장으로 합성**한다.
    ///
    /// 왜 굳이: 이 맥의 메뉴바는 노치 오른쪽 가용 구간이 848pt 에서 시작하는데 첫 아이템이
    /// 882pt 라 **여유가 34pt 뿐**이다. 아이콘(이미지)과 숫자(attributedTitle)를 따로 두면
    /// NSStatusItem 폭이 49pt 가 되고, 자리를 못 얻은 항목을 macOS 는 조용히 화면 **왼쪽 끝**
    /// (x=31, 애플 메뉴 옆)으로 밀어낸다 — 앱 메뉴에 가려 "사라진 것처럼" 보인다.
    /// 한 장으로 합치면 20~28pt 라 그 틈에 들어간다.
    private func badge(_ symbolName: String, count: Int, color: NSColor) -> NSImage? {
        let symCfg = NSImage.SymbolConfiguration(pointSize: 12, weight: .medium)
            .applying(NSImage.SymbolConfiguration(paletteColors: [color]))
        guard let sym = NSImage(systemSymbolName: symbolName, accessibilityDescription: "cmux 상태")?
            .withSymbolConfiguration(symCfg) else { return nil }

        let text: NSAttributedString? = count > 0 ? NSAttributedString(
            string: "\(count)",
            attributes: [.font: NSFont.monospacedDigitSystemFont(ofSize: 10, weight: .bold),
                         .foregroundColor: color]) : nil
        let gap: CGFloat = text == nil ? 0 : 1.5
        let textW = text?.size().width ?? 0
        let h: CGFloat = 18
        let w = sym.size.width + gap + ceil(textW)

        let img = NSImage(size: NSSize(width: w, height: h), flipped: false) { _ in
            sym.draw(at: NSPoint(x: 0, y: (h - sym.size.height) / 2),
                     from: .zero, operation: .sourceOver, fraction: 1)
            if let t = text {
                t.draw(at: NSPoint(x: sym.size.width + gap, y: (h - t.size().height) / 2))
            }
            return true
        }
        img.isTemplate = false        // 상태색이 뜻을 나른다 — 템플릿으로 두면 단색이 된다
        return img
    }

    // ── 팝오버 ────────────────────────────────────────────────────────
    @objc private func toggle() {
        if popover.isShown { close(); return }
        guard let b = item.button else { return }
        // LSUIElement 앱이라 활성화를 해 줘야 팝오버 안의 클릭이 바로 먹는다.
        NSApp.activate()
        popover.show(relativeTo: b.bounds, of: b, preferredEdge: .minY)
        Task { await client.refresh() }   // 열 때는 5초를 기다리지 않는다
    }

    private func close() { popover.performClose(nil) }
}

private extension NSStatusItem {
    static func statusItem() -> NSStatusItem {
        NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    }
}
