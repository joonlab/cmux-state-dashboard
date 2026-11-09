import Foundation
import SwiftUI

/// 대시보드 서버(기본 :7788)에 붙는 유일한 통로.
///
/// **여기서 상태를 판정하지 않는다.** 서버의 `/api/nav` 가 정본이고 이 클래스는 그것을 옮겨 담을 뿐이다.
/// 캐시 경로가 6ms 라 5초 폴링으로 충분하다(웹 대시보드와 같은 주기).
@MainActor
final class NavClient: ObservableObject {
    @Published private(set) var snapshot: NavResponse?
    @Published private(set) var lastError: String?
    @Published private(set) var fetchedAt: Date?
    /// 마지막 이동 시도의 결과 문구. 서버가 `verified:false` 를 주면 그걸 그대로 보여준다.
    @Published var lastFocusNote: String?
    /// 그 문구가 붙은 시각 — 실패 문구는 20초 뒤 갱신이 성공하면 걷는다(11-10: 서버가 잠깐 재시작한 순간의
    /// «서버 없음»이 다음에 탭을 누를 때까지 몇십 분씩 남아, 지금도 고장 난 것처럼 보였다).
    private var focusNoteAt: Date?

    /// 루프백 고정. 호스트명(`joonlab-m5-max`)으로 붙으면 macOS 의 로컬 네트워크 권한 대상이 되지만
    /// 127.0.0.1 은 그 대상이 아니다 — 권한 다이얼로그 없이 조용히 동작한다.
    ///
    /// 포트만 `CMUXMM_PORT` 로 연다(호스트는 열지 않는다 — 루프백을 벗어나는 순간 권한 대상이
    /// 되고, 이 앱이 남의 기계를 들여다볼 이유도 없다). 폴링 주기는 `CMUXMM_POLL` (초).
    static let port = ProcessInfo.processInfo.environment["CMUXMM_PORT"].flatMap(Int.init) ?? 7788
    static var dashboardURL: URL { URL(string: "http://127.0.0.1:\(port)")! }
    private let base = NavClient.dashboardURL
    private let pollInterval: Duration = .seconds(
        ProcessInfo.processInfo.environment["CMUXMM_POLL"].flatMap(Int.init) ?? 5)
    private var task: Task<Void, Never>?

    func start() {
        guard task == nil else { return }
        task = Task { [weak self] in
            while !Task.isCancelled {
                await self?.refresh()
                try? await Task.sleep(for: self?.pollInterval ?? .seconds(5))
            }
        }
    }

    func stop() { task?.cancel(); task = nil }

    func refresh() async {
        do {
            var req = URLRequest(url: base.appending(path: "api/nav"))
            req.timeoutInterval = 8
            let (data, resp) = try await URLSession.shared.data(for: req)
            guard let http = resp as? HTTPURLResponse, http.statusCode == 200 else {
                throw NavError.badStatus((resp as? HTTPURLResponse)?.statusCode ?? -1)
            }
            snapshot = try JSONDecoder().decode(NavResponse.self, from: data)
            fetchedAt = Date()
            lastError = nil
            if let at = focusNoteAt, Date().timeIntervalSince(at) > 20 { lastFocusNote = nil; focusNoteAt = nil }
        } catch {
            // 스냅샷은 지우지 않는다 — 서버가 잠깐 죽어도 마지막으로 안 것은 남겨두고,
            // 대신 '언제 것인지'를 화면에 드러낸다(조용히 낡은 화면을 보여주지 않기 위해).
            lastError = Self.describe(error)
        }
    }

    /// 그 탭으로 점프. **웹과 같은 3필드 계약**(`surface` + `window` + `activate`)을 쓴다.
    /// `window` 를 빼면 다중 창에서 cmux 내부만 맞고 화면은 엉뚱한 창이 뜬다.
    func focus(_ tab: NavTab) async {
        // 서버가 막 재시작하는 몇 초(연결 거부)면 1.5초 간격으로 두 번 더 해 본다
        for attempt in 0..<3 {
            if attempt > 0 { try? await Task.sleep(for: .milliseconds(1500)) }
            if await focusOnce(tab) { return }
        }
        lastFocusNote = "이동 실패: 서버 없음 (:\(NavClient.port)) — 3번 시도"
        focusNoteAt = Date()
    }

    /// 한 번 시도. 다시 해 볼 만한 실패(연결 거부)면 false.
    private func focusOnce(_ tab: NavTab) async -> Bool {
        do {
            var req = URLRequest(url: base.appending(path: "api/cmux/focus"))
            req.httpMethod = "POST"
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.timeoutInterval = 10
            req.httpBody = try JSONSerialization.data(withJSONObject: [
                "surface": tab.surfaceId, "window": tab.windowId, "activate": true,
            ])
            let (data, resp) = try await URLSession.shared.data(for: req)
            guard let http = resp as? HTTPURLResponse, http.statusCode == 200 else {
                throw NavError.badStatus((resp as? HTTPURLResponse)?.statusCode ?? -1)
            }
            let r = try JSONDecoder().decode(FocusResult.self, from: data)
            // 서버가 스스로 검증한 결과를 삼키지 않는다 — "이동했다"고만 말하고 실제로는
            // 안 옮겨간 상태를 만들지 않기 위해 서버가 넣어 둔 값이다.
            lastFocusNote = (r.verified == false)
                ? "이동 안 됨 — 지금 활성: \(r.activeNow ?? "?")"
                : nil
            focusNoteAt = lastFocusNote == nil ? nil : Date()
            await refresh()
            return true
        } catch {
            let ns = error as NSError
            if ns.domain == NSURLErrorDomain, ns.code == NSURLErrorCannotConnectToHost { return false }
            lastFocusNote = "이동 실패: \(Self.describe(error))"
            focusNoteAt = Date()
            return true
        }
    }

    private static func describe(_ e: Error) -> String {
        if let n = e as? NavError { return n.text }
        let ns = e as NSError
        if ns.domain == NSURLErrorDomain, ns.code == NSURLErrorCannotConnectToHost {
            return "서버 없음 (:\(NavClient.port))"
        }
        return ns.localizedDescription
    }

    enum NavError: Error {
        case badStatus(Int)
        var text: String { if case .badStatus(let c) = self { return "HTTP \(c)" }; return "오류" }
    }
}
