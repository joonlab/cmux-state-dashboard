# cmux 실측 노트

> 두 달 동안 cmux를 자동화하며 **문서에 없어서 직접 부딪혀 알아낸 것들.**
> 전부 실측이고, 확인 못 한 건 그렇다고 적었다. 버전은 0.64.7 ~ 0.64.22 구간이다.
>
> cmux를 스크립트로 다루려는 사람에게는 이 파일이 이 저장소에서 가장 쓸모 있는 부분일 수 있다.

## 목차

- [소켓 제어](#소켓-제어)
- [세션 ↔ 탭 매핑](#세션--탭-매핑)
- [상태 마커와 알림 피드](#상태-마커와-알림-피드)
- [워크스페이스 · 그룹](#워크스페이스--그룹)
- [레이아웃과 복원](#레이아웃과-복원)
- [조용한 거짓말 — OK 를 믿으면 안 되는 곳](#조용한-거짓말--ok-를-믿으면-안-되는-곳)
- [성능](#성능)
- [파일 위치](#파일-위치)

---

## 소켓 제어

**`automation.socketControlMode`는 앱 시작 시에만 읽힌다.**
`cmux reload-config`로는 안 바뀐다. 파일엔 `password`라 써 있는데 라이브 `access_mode`는
여전히 `cmuxOnly`인 상태가 될 수 있고, 그러면 인증이 아니라 **신원검사에서** 거부된다.
설정 파일에 쓴 값과 프로세스가 지금 쓰는 값을 구분해야 한다.

**반면 `socketPassword`는 파일 감시로 핫리로드된다.** 그래서 비번만 고치는 건 앱 재시작 없이 먹는다.

**cmux는 `socketPassword`를 반복적으로 지운다.** 비번을 파일로만 넣어 두면 cmux가 자기 설정을
저장할 때 덮어쓴다. 앱 업데이트는 아예 `cmux.json`을 최소본으로 리셋하면서
`socketControlMode: "password"`는 남기고 **비번 필드만 지운다** → 비번 모드인데 비교할 비번이
없으니 **아무도 인증 못 하는 완전 잠금** 상태가 된다.
→ 이 저장소는 known-good 비번을 따로 스태시해 두고 **health 폴링마다 self-heal** 한다.

**기본 모드(`cmuxOnly`)는 cmux 자손·사용자 세션만 허용한다.** pm2나 launchd에서 띄운
프로세스는 거부된다. `env -i`도, `launchctl asuser`도 우회가 안 된다 — 모드 자체를 바꿔야 한다.

**소켓 경로가 0.64.17에서 이동했다.**
`~/Library/Application Support/cmux/cmux.sock` → **`~/.local/state/cmux/cmux.sock`**.
그런데 `last-socket-path` 파일은 **낡은 경로를 계속 가리킨다.** 그 값을 `CMUX_SOCKET_PATH`로
강제 지정하면 `Socket not found`가 나고, **경로를 지정 안 하면 cmux가 알아서 신 경로를 찾는다.**
→ "내 손으론 되는데 스크립트만 안 된다"의 흔한 정체가 이것이다. 존재검사 폴백을 두는 게 안전하다.

**`cmux rpc system.tree`는 caller의 창 하나만 준다.** 전체는
`cmux --id-format both tree --all --json`.

**`surface.resume.get`은 인자를 무시하고 caller 자신의 바인딩만 반환한다.**

**`surface.focus`의 파라미터 키는 `surface_id`다** (`surface`가 아니다).
이거 하나로 **창 전환 + 워크스페이스 전환 + 탭 선택**이 전부 된다 — 다른 창의 숨은
워크스페이스라도. 다만 **cmux 내부에서만** 바뀌므로, 다른 앱을 보고 있으면 화면은 그대로다.
macOS 최전면화는 `open -a`로 따로 해야 한다.

---

## 세션 ↔ 탭 매핑

**훅을 설치할 필요가 없다.** cmux가 claude를 띄울 때 이미 심어 둔 값 둘이면 된다.

```
claude 프로세스 ─ 환경변수 CMUX_PANEL_ID (= surface UUID) ──→ tree ──→ 창/워크스페이스/탭
                └ 명령줄  --session-id    (= Claude 세션 UUID) ──→ ~/.claude/projects/**/<sid>.jsonl
```

`ps -E` **한 번**으로 둘을 같이 읽어 트리와 조인하면 추측 없이 정확하다.

**⚠️ `--session-id`/`--resume`는 cmux가 기존 세션을 이어받을 때만 붙는다.**
새로 시작한 세션엔 없다. 그 인자 유무로 claude 프로세스를 거르면 **새 세션이 목록에서 통째로
빠진다.** 메인 판별은 **부모 프로세스**로 하는 게 맞다 — 탭 최상위 claude는 부모가 로그인 셸이고,
서브에이전트·플러그인이 띄운 건 부모가 `claude`나 `uv run`이다.

**⚠️ 데몬이 낡은 `CMUX_PANEL_ID`를 물고 있을 수 있다.**
Claude Code 2.1.246부터 포크·재개 세션을 데몬 밑에서 돌리는데, 그 데몬이 **자기를 처음 띄운
패널의 `CMUX_PANEL_ID`를 환경에 물고 있어** 나중에 다른 패널의 세션을 서빙해도 env가 옛 패널을
가리킨다. → 패널 제목으로 교차검증하되 규칙을 좁게(ps와 제목이 **둘 다 있고 서로 다를 때만**
제목을 믿는다 — 포크 형제는 제목을 공유하므로).

**`tty`는 기대하지 마라.** 트리의 `tty`는 실측 0/58로 전부 null이었다.

**cmux는 터미널마다 `CMUX_CLAUDE_WRAPPER_SHIM`(세션 추적 래퍼)을 심고 resume에서 그걸 우선
실행한다.** resume 명령을 직접 만든다면 같은 규칙을 존중해야 한다:

```sh
"$([ -x "${CMUX_CLAUDE_WRAPPER_SHIM:-}" ] && printf '%s' "$CMUX_CLAUDE_WRAPPER_SHIM" || printf claude)" --resume <sid>
```

**네이티브 resume 바인딩엔 `--dangerously-skip-permissions`가 원래 항상 붙어 있다.**
직접 만든 명령에서 이걸 빼면 복원된 claude가 매 도구 호출마다 멈춘다.

**⚠️ resume 바인딩의 `cd` 경로를 신뢰하지 마라.** cmux는 런치 폴더가 아니라
**저장 순간 터미널이 drift해 있던 폴더**를 `cd '<경로>' &&`로 박는다. 그 폴더가 rename되면
`cd`가 실패하고 `&&`가 뒤를 통째로 단락시킨다.
→ 진실 원천은 세션 `.jsonl`의 프로젝트 폴더 인코딩이고, 명령 조립은 `&&`(fatal)가 아니라
**`2>/dev/null;`(non-fatal)로** 해야 뒤가 살아난다.

---

## 상태 마커와 알림 피드

**탭 제목 마커 = 「지금 상태」** (cmux가 매 순간 갱신) ·
**알림 피드 = 「지나간 사건의 기록」.** 둘은 성질이 다르므로 알림은 **아직 유효할 때만** 써야 한다.
시효를 안 두면 16시간 전 알림 하나가 탭을 영구히 자물쇠로 굳힌다.

**⚠️ 패널 제목을 사람이 직접 바꾸면 cmux가 상태 마커를 다시 안 붙인다.**

| | 정상 탭 | 이름을 바꾼 탭 |
|---|---|---|
| `panels[].title` | `"◑ 작업 이름"` | `"작업 이름"` (마커 없음) |
| `customTitleSource` | — | **`"user"`** |

**워크스페이스 이름만 바꾼 건 무해하다** — 마커는 *패널* 제목에 붙는다.
실측 78개 중 6개가 이 상태였고 `customTitleSource == "user"`인 패널도 정확히 그 6개였다(1:1).

**알림 피드의 `subtitle='Waiting'`은 두 가지를 섞어 담고 있다.** 1,004건 재분류 결과:

| `body` | 건수 | 실제 |
|---|---|---|
| `"Claude is waiting for your input"` (고정 문구) | 252 | 그냥 프롬프트 대기 |
| 그 밖 — 질문 본문이 실린다 (**180/180 전부 `[선택지]` 포함**) | 180 | **AskUserQuestion, 사람을 기다리며 막힘** |
| `"Claude needs your permission"` | 217 | 도구 권한 대기 |

판별이 애매하지 않으므로 `body`로 가르면 된다. 그리고 질문 본문·선택지가 **알림에 이미 실려 있다** —
화면에 그대로 띄울 수 있다.

**세션 활동 시각에 `.jsonl` 파일 mtime을 쓰지 마라.**
**실행 중인 세션은 대화가 없어도 파일이 계속 touch된다.**

| | mtime − 마지막 대화 괴리 |
|---|---|
| 실행 중 45개 | 중앙값 **1494분**, 89%가 10분 초과 |
| 종료된 2055개 | 중앙값 **0.3분** |

→ 마지막 user/assistant 발화 시각을 쓴다. append-only 로그라 **캐시 키도 mtime이 아니라 size**가 맞다.

**사람이 시킨 활동과 시스템이 깨운 활동은 다르다.** transcript의 user 레코드에도
`promptSource=system`인 것(백그라운드 알림 등)이 섞인다 — 실측 54개 중 8개.
그리고 **프롬프트 미리보기는 `promptId`가 아니라 `promptSource` 기준으로 뽑아야 한다** —
`promptId`는 tool_result에도 붙어 미리보기가 통째로 빈다.

---

## 워크스페이스 · 그룹

**★그룹 멤버십은 `groupId`가 아니라 사이드바의 「연속 위치」로 정해진다.**
그래서 워크스페이스를 옮기면 **옆 그룹 앵커가 멤버를 흡수한다** — `--group`으로 배정해도 덮인다.
→ 위치를 바꾼 뒤에는 **소속을 다시 못 박는 reconciliation 패스**가 필요하다
(이 저장소는 위치 → 소속 → 위치 **2패스**로 돈다).

**그룹은 워크스페이스 목록 안에 「앵커 워크스페이스」로 존재하고 그 뒤에 멤버가 이어진다.**
즉 **그룹 순서 = 앵커의 위치**다. `workspace.group.create`가 자체 앵커를 만드므로,
그룹을 먼저 만들고 전 멤버를 `--group`으로 넣어야 한다(첫 멤버를 먼저 만들면 그 멤버가 그룹 밖에 남는다).

**`workspace.group.create`는 `cwd` 파라미터를 받는다.** 안 주면 cmux가 첫 자식의 cwd를
앵커로 추론한다. 그리고 **그룹의 `+` 버튼은 `app.workspaceInheritWorkingDirectory` 설정을
완전히 우회한다** — 소스에서 앵커 cwd를 명시 인자로 넘기며 상속 플래그를 무력화하기 때문이다.
**유일한 레버는 앵커의 cwd 자체다.**

**`workspace.group.create`는 `--from` 생략 시 「활성 사이드바 선택」을 멤버로 끌어간다.**
`--from`을 명시하면 원천 차단된다. (`--window`는 인덱스가 아니라 ref를 받는다.)

**`workspace-group list`는 호출자 창만 보여준다.** 그걸 전역으로 읽으면
"그룹이 붕괴했다"는 오진이 나온다. **스코프를 모르고 읽은 0은 「없음」이 아니다.**

**`workspace.group.pin`은 토글이 아니다.** 두 번 불러도 고정인 채고, 해제는 `unpin`이 따로 있다.
(워크스페이스 `pin`도 마찬가지 — 토글로 만들면 화면 상태가 낡았을 때 정확히 반대로 뒤집힌다.)

**`workspace.group.list` RPC는 라이브 `member_workspace_ids`를 준다.**
네이티브 세션 JSON은 디바운스되어 방금 한 변경이 한동안 안 보이므로, "옆 그룹이 흡수했나"를
판정할 땐 이쪽을 써야 한다.

**⚠️ cmux는 워크스페이스 그룹을 라이브 트리에 실어주지 않는다.**
네이티브 세션 파일의 `tabManager.workspaceGroups`에만 있다.

**워크스페이스에는 제목/설명 필드가 셋이다.**

| 필드 | 어디에 보이나 |
|---|---|
| `custom_title` | 사이드바 (사람이 보는 것) |
| `description` | 사이드바 (사람이 보는 것) |
| 터미널 탭 제목 (surface title) | **사이드바엔 안 보임** — 지나간 서브작업의 잔상일 수 있다 |

**`customColor`는 존재한다.** (필드명이 `color`가 아니라서 "색을 저장하지 않는다"고 오진하기 쉽다.)
실측: 그룹 9개는 전부 비어 있었지만 **워크스페이스는 78개 중 56개가 색을 갖고 있었다.**

**구 cmux(0.64.17 이전) 세션 JSON엔 `workspaceId` 필드가 아예 없다.**
UUID로 대조하는 코드는 그 시절 스냅샷에서 **전건 불일치**가 되므로, 빈 키 집합은
"대조 실패"가 아니라 **"대조 불가"로** 다뤄야 한다(제목 대조 폴백 등).

---

## 레이아웃과 복원

**네이티브 세션 JSON 하나에 충실 복원에 필요한 게 전부 있다.**

| 정보 | 위치 |
|---|---|
| 스플릿 배치·분할비율·방향 | `layout` 재귀 트리 (`split.dividerPosition` / `orientation`) |
| 탭 그룹·순서·선택탭 | `pane.panelIds` / `selectedPanelId` |
| claude resume 명령 | `terminal.resumeBinding.command` |
| 브라우저 URL | `browser.urlString` |
| 워크스페이스 이름·색·고정·cwd | `customTitle` / `customColor` / `isPinned` / `currentDirectory` |
| 창 배치 | `window.frame` |

**소켓 tree는 `dividerPosition`을 주지 않는다.** 레이아웃 원천은 항상 네이티브 JSON이다.

**`new-workspace --layout`이 스플릿 트리를 네이티브로 지원한다.**
`new-split`/`resize-pane` 조합 없이 워크스페이스 하나를 **단일 호출로 통째 재현**할 수 있다.

**`surface resume set --shell`은 바인딩만 하고 실행하지 않는다.**
inspection/manual 전용이라 이걸로는 claude가 안 뜬다. 자동 실행은 **레이아웃의 terminal `command`** 가 한다 — 그리고 그 command는 **`--focus false`로 만든 미실현 워크스페이스에서도
생성 시점에 전부 실행된다.**

**★`focus=false`로 만든 서피스는 `in_window=false`다.** 정의만 되고 **창에 부착(realize)되지 않아
화면에 안 그려진다.** API는 200 OK를 주고 구조도 실제로 생겼는데 사용자 화면엔 아무것도 없다.
`select-workspace` 한 번이면 전부 부착된다.
→ **"만들었다"와 "떠 있다"는 다른 사건이다.**

**⚠️ `close-window`는 pm2/launchd 컨텍스트에서 아예 안 먹는다** (cmux가 GUI 비연결 caller의
파괴적 창 조작을 거부한다). **숨은 창에 대해서는 GUI에서도 `OK`만 반환하고 아무 일도 안 한다.**
`close-workspace`도 **창의 마지막 워크스페이스는 못 닫는다.**
→ 창을 없애려면 그 창의 워크스페이스를 전부 `workspace close`로 닫으면 빈 창이 자동 종료된다.
(`group.delete`는 멤버 워크스페이스까지 닫으므로 비-GUI 컨텍스트에서 워크스페이스를 닫는
유일한 경로이기도 하다 — 그만큼 위험하다.)

**`move-workspace-to-window`는 워크스페이스만 옮기고 그룹 소속은 안 넘긴다.**
→ 복원 절차는 **①복원 → ②창 이동 → ③그룹 재구성 → ④색 적용** 순서여야 한다.

**`restore-session`은 스냅샷 전체를 새 창으로 연다.** 부분 손실의 해법이 아니다
(손으로 복구해 둔 것과 통째로 겹친다).

**네이티브 백업은 60개에서 회전한다.** 중요한 시점은 따로 고정 보관해야 밀려나지 않는다.

**★cmux가 창을 세션 JSON에 안 쓰는 일이 있다.**
소켓 tree는 창 4개를 주는데 네이티브 JSON엔 2개만 있는 상태를 실측했다. 그 창은 하루 종일
쓰였는데도 **한 줄도 기록되지 않았고**, 재부팅으로 통째 소실됐다.
cmux 자신의 `closed-item-history`에도 없었다 — **닫힌 게 아니라 존재를 몰랐던 것이다.**
→ 두 소스를 대조하고 **불일치를 반드시 화면에 드러내라.**

---

## 조용한 거짓말 — `OK`를 믿으면 안 되는 곳

이 절이 실전에서 제일 자주 문다. **전부 `OK`를 돌려주면서 아무 일도 안 한다.**

| 명령 | 실제 |
|---|---|
| `reorder-workspaces --index N` | **인덱스를 조용히 클램프한다.** 고정 워크스페이스는 고정 블록 밖으로 못 나가고 비고정은 그 안에 못 들어간다. 실측 `--index 8` → 5, `--index 0` → 6 |
| 그룹에 속한 워크스페이스를 다른 그룹 끝으로 | **그룹 블록 밖으로 못 나간다.** 소속을 먼저 바꿔야 간다 |
| `workspace.group.move --after_group_id` | **안 움직인다.** `before_group_id`만 실제로 동작한다 |
| **고정된** 그룹의 `group.move` | **안 움직인다** |
| `close-window` (숨은 창 / 비-GUI caller) | **아무 일도 안 한다** |
| `reorder-workspaces --order <현재 순서 그대로>` | **항등이 아니다.** 그룹 앵커를 `index 0 → 5`로 옮기는 계획이 나온다 |

**그리고 반영이 비동기다** — 직후에 조회하면 옛 값이 나온다.

→ 대응은 하나뿐이다. **응답을 믿지 말고 대조하라.**
이 저장소는 이동마다 계획 인덱스를 대조하고(`clamped`), 끝나면 배치를 통째로 다시 읽어
원하던 것과 맞추고(`mismatches`), 그룹 순서는 **앵커 워크스페이스 인덱스로 직접** 판정한다.
그리고 **어긋난 건 화면에 돌려준다** — 성공 문구만 띄우면 사용자가 그걸 믿고 넘어간다.

**⚠️ 문서에 없다고 없는 게 아니다.** `unpin`도 문서에 없다가 실제로 존재했고,
"그룹 순서 변경 RPC는 없다"는 결론도 틀렸다(앵커를 옮기는 방식으로 실재했다).
**추측으로 단정하지 말고 파 보는 게 맞다.**

---

## 성능

**브라우저 탭이 쌓이면 CPU가 죽는다 — 그리고 그건 앱 버그가 아니다.**
실측: 서피스 335개 중 브라우저 탭 **319개**(그중 296개가 워크스페이스 **하나**에)
→ CPU **100~105% 상시**, WindowServer 46~48%, **메모리는 free 93%.**

스택 샘플 2,094개를 분해하면 `CA::Transaction::commit()` → `NSView _layoutSubtreeWithOldSize:`가
68%, 재귀 **44단계**. 거기에 claude 스피너가 **초당 ~10회 탭 제목을 갱신**해 매번 레이아웃을
무효화한다. **"탭 수 × 제목 갱신"의 곱**이 렉의 실체다. 탭이 20개면 같은 스피너로도 체감이 없다.

정리 후: **CPU 100~105% → 2.6~11%**, 서피스 335 → 39.

**앱 버그와 데이터 볼륨은 스택 시그니처로 감별한다:**

| | 앱 버그 | 데이터 볼륨 |
|---|---|---|
| 스택 | SwiftUI `LazySubviewPlacements.placeSubviews` | AppKit `NSView _layoutSubtreeWithOldSize` 44단계 |
| CLI 응답 | 4초 무응답 (hang) | 정상 (0.23초) |
| 메모리 | 누수 (6.6 → 9.3GB) | 안정 |
| 해결 | 재시작·업데이트 | **탭 정리** (재시작해도 탭이 남아 재발한다) |

**부하 진단은 출력 없는 구간 측정으로 하라.** `top`을 2초 간격으로 터미널에 계속 출력하는
**그 행위 자체가** 터미널과 WindowServer를 밀어올린다(실측: 그걸 멈추니 CPU 91.64% idle).
`ps -r` 스냅샷 정렬값도 지속 부하가 아니다 — "98%"로 찍힌 프로세스가 구간 측정으로는 **0%였다**.

**cmux 창이 여러 Space에 흩어지면 Mission Control 썸네일이 안 그려질 수 있다.**
창 7개가 7개 Space에 1:1로 흩어진 상태에서 App Exposé가 현재 Space의 창 하나만 그렸다.
(cmux 버그도 대시보드 버그도 아니고, 트리거는 미확정.)

---

## 파일 위치

```
~/Library/Application Support/cmux/
    session-com.cmuxterm.app.json            네이티브 세션 (복원의 원천)
    session-com.cmuxterm.app-previous.json
    notification-feed-history-com.cmuxterm.app.json   Waiting/Permission/Completed 알림
    cmux.sock                                구 소켓 경로 (≤0.64.7)
    last-socket-path                         ⚠️ 낡은 경로를 계속 가리킨다
~/.local/state/cmux/cmux.sock                신 소켓 경로 (0.64.17~)
~/.config/cmux/cmux.json                     설정 (JSONC) · cmux.<TS>.bak 자동 백업이 옆에 쌓인다
~/.claude/projects/<encoded-cwd>/<sid>.jsonl Claude Code 세션 전사
```

---

<sub>이 노트는 [cmux 관제실](../README.md)을 만들며 쌓인 것이다.
각 항목이 어떤 사고에서 나왔는지는 [개발사(史)](HISTORY.md)에 있다.</sub>
