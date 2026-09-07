import SwiftUI
import AppKit

struct PopoverView: View {
    @ObservedObject var client: NavClient
    var onJump: () -> Void          // 이동 직후 팝오버를 닫는다(cmux 가 최전면으로 오므로)

    /// SwiftUI 가 이 뷰를 **실제로 어떤 외관으로 그리는지**를 그대로 따른다.
    @Environment(\.colorScheme) private var scheme
    private var isDark: Bool { scheme == .dark }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            Divider().padding(.vertical, 6)
            if let s = client.snapshot {
                summary(s)
                Divider().padding(.vertical, 6)
                list(s)
            } else {
                Text(client.lastError.map { "붙지 못했다 — \($0)" } ?? "불러오는 중…")
                    .font(.system(size: 12)).foregroundStyle(.secondary)
                    .padding(.vertical, 8)
            }
            footer
        }
        .padding(12)
        .frame(width: 340)
        // 팝오버 기본 재질은 뒤 창을 비친다(vibrancy). 밝은 페이지 위에 뜨면 다크 팝오버인데도
        // 바닥이 밝아져 상태색이 흐려진다(실측: 흰 웹페이지 위에서 '작업 중' 초록이 거의 안 읽혔다).
        // 상태색이 뜻을 나르는 화면이라 대비가 먼저다 — 재질감을 조금 남기고(0.92) 바닥을 채운다.
        .background(Color(nsColor: .windowBackgroundColor).opacity(0.92))
    }

    // ── 헤더: 언제 것인지를 항상 드러낸다 ────────────────────────────────
    // 서버가 죽어도 마지막 스냅샷은 남겨 두므로, '언제 받은 것인지'가 없으면
    // 낡은 화면을 지금 상태로 착각하게 된다.
    private var header: some View {
        HStack(spacing: 6) {
            Text("cmux 관제실").font(.system(size: 13, weight: .semibold))
            Spacer()
            TimelineView(.periodic(from: .now, by: 1)) { ctx in
                Text(age(client.fetchedAt, now: ctx.date))
                    .font(.system(size: 11)).foregroundStyle(.secondary)
                    .monospacedDigit()
            }
            Button { Task { await client.refresh() } } label: {
                Image(systemName: "arrow.clockwise").font(.system(size: 10))
            }
            .buttonStyle(.plain).help("지금 새로고침")
        }
    }

    private func age(_ t: Date?, now: Date) -> String {
        guard let t else { return "—" }
        let s = Int(now.timeIntervalSince(t))
        return s < 2 ? "방금" : (s < 60 ? "\(s)초 전" : "\(s / 60)분 전")
    }

    // ── 요약: 서버가 준 statusOrder 순서 그대로 ─────────────────────────
    private func summary(_ s: NavResponse) -> some View {
        HStack(spacing: 10) {
            ForEach(s.statusOrder, id: \.self) { key in
                let n = s.counts[key] ?? 0          // ⚠️ 0 인 상태는 키가 없다
                if n > 0, let kind = TabStatus(rawValue: key) {
                    HStack(spacing: 4) {
                        Circle()
                            .fill(Theme.swiftUI(Theme.color(for: kind, dark: isDark)))
                            .frame(width: 6, height: 6)
                        Text("\(s.statusLabel[key] ?? key) \(n)")
                            .font(.system(size: 11)).monospacedDigit()
                            .foregroundStyle(.secondary)
                    }
                }
            }
            Spacer()
        }
    }

    // ── 목록: 막힘 + 작업중만. 대기·유휴는 위 요약 숫자로 충분하다 ──────
    // cmux 그룹(제품·앱 개발 / 교육·컨설팅 / 인프라·자동화·운영 …)으로 묶는다.
    // "지금 어느 영역이 막혔나"가 제목 줄을 읽기 전에 먼저 보인다.
    private func list(_ s: NavResponse) -> some View {
        let shown = s.sorted(s.tabs.filter {
            s.blocked.contains($0.status) || $0.status == "running" || $0.status == "background" })
        let sections = Self.sections(shown)
        return Group {
            if shown.isEmpty {
                Text("막힌 것도, 도는 것도 없다")
                    .font(.system(size: 12)).foregroundStyle(.secondary)
                    .padding(.vertical, 10)
            } else {
                ScrollView {
                    VStack(alignment: .leading, spacing: 10) {
                        ForEach(sections) { sec in
                            VStack(alignment: .leading, spacing: 3) {
                                GroupHeader(section: sec)
                                ForEach(sec.tabs) { tab in
                                    TabRow(tab: tab, isDark: isDark) {
                                        Task { await client.focus(tab); onJump() }
                                    }
                                }
                            }
                        }
                    }
                }
                .frame(maxHeight: 340)
            }
        }
    }

    /// 정렬된 목록을 **그룹이 처음 나온 순서대로** 묶는다.
    /// 목록은 이미 서버의 `statusOrder` 로 정렬돼 있으므로, 결과적으로
    /// **가장 급한 탭을 가진 그룹이 위**에 온다 — 그룹 순서를 따로 정하지 않아도 된다.
    static func sections(_ tabs: [NavTab]) -> [GroupSection] {
        var order: [String] = []
        var bucket: [String: [NavTab]] = [:]
        var meta: [String: NavGroup] = [:]
        for t in tabs {
            let key = t.group?.id ?? t.group?.name ?? "—"
            if bucket[key] == nil {
                order.append(key)
                if let g = t.group { meta[key] = g }
            }
            bucket[key, default: []].append(t)
        }
        return order.map {
            GroupSection(id: $0, name: meta[$0]?.name ?? "그룹 없음",
                         icon: meta[$0]?.icon, tabs: bucket[$0] ?? [])
        }
    }

    private var footer: some View {
        VStack(alignment: .leading, spacing: 4) {
            if let note = client.lastFocusNote {
                Text(note).font(.system(size: 11))
                    .foregroundStyle(Theme.swiftUI(Theme.token("amber", dark: isDark)))
            }
            if let s = client.snapshot, s.droppedTabs > 0 {
                // 조용히 사라지면 "왜 저 탭이 안 보이지"를 영영 모른다.
                Text("탭 \(s.droppedTabs)개는 형식이 어긋나 읽지 못했습니다")
                    .font(.system(size: 11))
                    .foregroundStyle(Theme.swiftUI(Theme.token("amber", dark: isDark)))
            }
            if let err = client.lastError, client.snapshot != nil {
                Text("갱신 실패 — \(err)").font(.system(size: 11))
                    .foregroundStyle(Theme.swiftUI(Theme.token("red", dark: isDark)))
            }
            Divider().padding(.vertical, 4)
            HStack {
                Button("대시보드 열기") {
                    NSWorkspace.shared.open(NavClient.dashboardURL)
                }
                .buttonStyle(.plain).font(.system(size: 11))
                Spacer()
                Button("종료") { NSApp.terminate(nil) }
                    .buttonStyle(.plain).font(.system(size: 11)).foregroundStyle(.secondary)
                    // 눌러서 끈 뒤 "다시 어떻게 켜지?" 가 되는 걸 막는다 — 켜는 법을 여기 적어 둔다.
                    .help("메뉴바에서 사라집니다. 다시 켜려면 Spotlight(⌘Space)에서 «CmuxMinimap»")
            }
        }
    }
}

struct GroupSection: Identifiable {
    let id: String
    let name: String
    let icon: String?
    let tabs: [NavTab]
}

private struct GroupHeader: View {
    let section: GroupSection

    /// cmux 가 주는 아이콘 이름은 SF Symbol 그대로다. 다만 **실재하는지 확인하고** 쓴다 —
    /// 없는 이름을 넘기면 SwiftUI 는 조용히 빈 자리를 그려서 헤더가 어긋나 보인다.
    private var symbol: String {
        if let i = section.icon,
           NSImage(systemSymbolName: i, accessibilityDescription: nil) != nil { return i }
        return "folder"
    }

    var body: some View {
        HStack(spacing: 5) {
            Image(systemName: symbol).font(.system(size: 11))  // 10pt 에선 hammer/chart 형태가 안 갈린다
            Text(section.name).font(.system(size: 11, weight: .medium))
            Spacer()
        }
        .foregroundStyle(.secondary)
        .padding(.horizontal, 6)
    }
}

private struct TabRow: View {
    let tab: NavTab
    let isDark: Bool
    let action: () -> Void
    @State private var hover = false

    var body: some View {
        Button(action: action) {
            HStack(alignment: .top, spacing: 7) {
                StatusIcon(kind: tab.kind, isDark: isDark)
                    .frame(width: 14)
                    .padding(.top, 1)
                VStack(alignment: .leading, spacing: 2) {
                    Text(tab.title)
                        .font(.system(size: 12)).lineLimit(1)
                    // 워크스페이스는 **탭 제목과 다를 때만** 한 줄 내준다.
                    // 색점이 그 워크스페이스의 색이라, 같은 프로젝트끼리 눈으로 묶인다.
                    if let ws = tab.distinctWorkspaceTitle {
                        HStack(spacing: 4) {
                            if let c = NSColor(hex: tab.workspaceColor) {
                                Circle().fill(Color(nsColor: c)).frame(width: 5, height: 5)
                            }
                            Text(ws).font(.system(size: 10)).lineLimit(1)
                                .foregroundStyle(.secondary)
                        }
                    }
                    HStack(spacing: 5) {
                        Text(tab.place)
                            .font(.system(size: 10)).foregroundStyle(.secondary)
                        Text(tab.label)
                            .font(.system(size: 10))
                            .foregroundStyle(Theme.swiftUI(Theme.color(for: tab.kind, dark: isDark)))
                    }
                    // 질문 대기일 때만 온다 — 무엇을 묻고 있는지 알면 가기 전에 답이 정해진다.
                    if let ask = tab.askText, !ask.isEmpty {
                        Text(ask.replacingOccurrences(of: "\n", with: " "))
                            .font(.system(size: 10)).lineLimit(2)
                            .foregroundStyle(.secondary)
                    }
                }
                Spacer(minLength: 0)
            }
            .padding(.vertical, 5).padding(.horizontal, 6)
            .background(RoundedRectangle(cornerRadius: 5)
                .fill(hover ? Color.primary.opacity(0.08) : .clear))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .onHover { hover = $0 }
        .help("클릭하면 그 탭으로 이동한다")
    }
}

/// 작업중 아이콘은 **돈다**. 다만 '동작 줄이기'가 켜져 있어도 **멈추지 않는다**.
///
/// 이 맥은 그 접근성 설정이 켜져 있다. 거기서 애니메이션을 끄면 "한 번도 안 움직이는" 화면이
/// 되고, 그건 정지 상태와 구별되지 않는다(웹 대시보드 스피너가 실제로 그랬다).
/// 그래서 끄는 대신 **느리게** 한다.
private struct StatusIcon: View {
    let kind: TabStatus
    let isDark: Bool
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var spin = false

    var body: some View {
        let img = Image(systemName: Theme.symbol(for: kind))
            .font(.system(size: 11))
            .foregroundStyle(Theme.swiftUI(Theme.color(for: kind, dark: isDark)))
        if kind == .running {
            img.rotationEffect(.degrees(spin ? 360 : 0))
                .animation(.linear(duration: reduceMotion ? 4.5 : 2)
                    .repeatForever(autoreverses: false), value: spin)
                .onAppear { spin = true }
        } else {
            img
        }
    }
}
