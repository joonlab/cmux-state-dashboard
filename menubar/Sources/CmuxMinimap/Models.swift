import Foundation

/// `GET /api/nav` 응답 중 **메뉴바가 쓰는 것만** 디코딩한다.
/// `Decodable` 은 명시한 키만 읽으므로, 서버가 필드를 더 붙여도 여기는 안 깨진다.
struct NavResponse: Decodable {
    let generatedAt: Double
    let tabs: [NavTab]
    /// 읽다 만 탭 수. **조용히 버리지 않기 위해** 세어 둔다(0이 아니면 화면에 적는다).
    let droppedTabs: Int
    /// ⚠️ **0 인 상태는 키 자체가 없다**(`nav.py` 의 counts 조립부는 등장한 상태만 센다).
    ///    "키 없음 = 0" 을 못 읽으면 배지가 조용히 사라진다. 반드시 `?? 0` 으로 읽을 것.
    let counts: [String: Int]
    let total: Int
    /// 화면에 놓는 순서. **정본은 서버**(`STATUS_ORDER`)다 — 여기서 다시 적지 않는다.
    let statusOrder: [String]
    let statusLabel: [String: String]
    /// '막힘'(사람이 답해야만 풀리는 것) 집합. 서버가 `STATUS_ORDER` 0층에서 파생해 내려 준다.
    let blockedStatuses: [String]?

    /// 서버가 `blockedStatuses` 를 안 주는 구버전일 때만 쓰는 폴백.
    /// ⚠️ 이 값이 쓰이고 있다면 그건 서버가 낡았다는 뜻이지, 여기가 정본이라는 뜻이 아니다.
    static let legacyBlockedFallback = ["permission", "question"]

    /* ⚠️ 탭 배열은 **한 장씩** 디코딩한다. 기본 방식이면 탭 하나가 어긋나는 순간 응답 전체가
       실패해서 미니맵이 통째로 멎는다 — 실제로 그랬다(2026-09-03: `lastActivity` 가 null 인
       탭 하나 때문에 "The data couldn't be read because it is missing." 로 갱신이 멈췄다).
       76개 중 1개가 이상하다고 나머지 75개를 못 보는 건 이 도구의 목적에 정확히 반한다. */
    private struct Lenient: Decodable {
        let tab: NavTab?
        init(from decoder: Decoder) throws { tab = try? NavTab(from: decoder) }
    }

    private enum CodingKeys: String, CodingKey {
        case generatedAt, tabs, counts, total, statusOrder, statusLabel, blockedStatuses
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        generatedAt = (try? c.decode(Double.self, forKey: .generatedAt)) ?? 0
        counts = (try? c.decode([String: Int].self, forKey: .counts)) ?? [:]
        total = (try? c.decode(Int.self, forKey: .total)) ?? 0
        statusOrder = (try? c.decode([String].self, forKey: .statusOrder)) ?? []
        statusLabel = (try? c.decode([String: String].self, forKey: .statusLabel)) ?? [:]
        blockedStatuses = try? c.decode([String].self, forKey: .blockedStatuses)
        let raw = (try? c.decode([Lenient].self, forKey: .tabs)) ?? []
        tabs = raw.compactMap(\.tab)
        droppedTabs = raw.count - tabs.count
    }

    var blocked: [String] { blockedStatuses ?? Self.legacyBlockedFallback }
    /// 메뉴바 배지 숫자 = 막힘 상태들의 합.
    var blockedCount: Int { blocked.reduce(0) { $0 + (counts[$1] ?? 0) } }
    var runningCount: Int { counts["running"] ?? 0 }
    /// 턴은 끝났는데 뒤에서 셸이 돌고 있는 탭 수. 막힘과 달리 **내가 없어도 진행된다**.
    var backgroundCount: Int { counts["background"] ?? 0 }

    /// 서버가 준 순서로 줄 세운다. 목록 안에서는 최근 활동이 위로.
    func sorted(_ list: [NavTab]) -> [NavTab] {
        let rank = Dictionary(uniqueKeysWithValues: statusOrder.enumerated().map { ($1, $0) })
        return list.sorted {
            let a = rank[$0.status] ?? 99, b = rank[$1.status] ?? 99
            return a == b ? $0.activity > $1.activity : a < b
        }
    }
}

struct NavTab: Decodable, Identifiable {
    /* 필수는 **없으면 이 탭을 가리킬 수도, 분류할 수도 없는** 둘뿐이다.
       나머지를 필수로 두면 값 하나가 비었다고 멀쩡한 탭을 통째로 버리게 된다. */
    let surfaceId: String
    let status: String

    let windowId: String?
    let windowLabel: String?
    let statusLabel: String?
    let statusSource: String?
    let cleanTitle: String?
    let tabRef: String?
    let workspaceTitle: String?
    let workspaceColor: String?     // 워크스페이스 색이 없는 탭이 실제로 있다(실측 75개 중 17개)
    let askText: String?            // 질문 대기일 때만 온다
    /// 활동 시각을 못 구한 탭이 실제로 있다(세션 기록을 못 읽은 새 탭 등) → 없을 수 있다.
    let lastActivity: Double?
    let isActiveTab: Bool?
    /// cmux 워크스페이스 그룹(9개 체계). `icon` 은 **SF Symbol 이름이 그대로** 온다
    /// (hammer.fill · graduationcap.fill · gearshape.2.fill …) — 변환 없이 렌더할 수 있다.
    let group: NavGroup?

    var id: String { surfaceId }
    var kind: TabStatus { TabStatus(rawValue: status) ?? .unknown }
    var title: String { (cleanTitle?.isEmpty == false) ? cleanTitle! : "(제목 없음)" }
    var label: String { statusLabel ?? status }
    var place: String { [windowLabel, tabRef].compactMap { $0 }.joined(separator: " · ") }
    var activity: Double { lastActivity ?? 0 }
    var active: Bool { isActiveTab ?? false }

    /// 워크스페이스 제목을 **탭 제목과 다를 때만** 돌려준다.
    /// cmux 는 워크스페이스 제목 앞에 상태 마커(◐ ✳ …)를 붙이므로 그걸 떼고 비교한다 —
    /// 안 그러면 "◐ 어떤 작업…" 과 "어떤 작업…" 을 다른 것으로 보고 같은 말을 두 번 쓴다.
    var distinctWorkspaceTitle: String? {
        let ws = Self.stripMarker(workspaceTitle ?? "")
        guard !ws.isEmpty, ws != Self.stripMarker(title) else { return nil }
        return ws
    }

    private static func stripMarker(_ s: String) -> String {
        String(s.drop { !$0.isLetter && !$0.isNumber }).trimmingCharacters(in: .whitespaces)
    }
}

struct NavGroup: Decodable {
    let id: String?
    let name: String?
    let color: String?
    let icon: String?
}

/// 서버가 주는 상태 문자열의 표시용 매핑. **판정하지 않는다** — 판정은 `nav.decide_status` 가 정본이다.
enum TabStatus: String {
    case permission, question, running, background, waiting, idle, unknown
}

struct FocusResult: Decodable {
    let ok: Bool?
    /// 서버가 "정말 그 탭이 활성인가"를 되짚어 본 결과. `false` 면 이동이 안 된 것이다 — 삼키지 않는다.
    let verified: Bool?
    let activeNow: String?
}
