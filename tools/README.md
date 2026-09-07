# tools/

## `cmux-snapshot.py` — 레이아웃을 파일로 영구 저장하고 되살리는 CLI

웹 대시보드의 스냅샷은 **자동으로 계속 쌓이는 기록**이다. 이건 다르다 —
**지금 이 배치에 이름을 붙여 영구 보관하고, 원본을 닫아 리소스를 회수하고, 나중에 그대로 되살린다.**

```bash
python3 tools/cmux-snapshot.py save --label 릴리스작업 --note "v2 배포 전 상태"
python3 tools/cmux-snapshot.py list
python3 tools/cmux-snapshot.py show latest
python3 tools/cmux-snapshot.py restore latest --target new
python3 tools/cmux-snapshot.py close "group:인프라" --yes
```

**복원 엔진은 대시보드의 `restore.restore_layout_windows()`를 그대로 재사용한다.**
따로 구현하지 않은 이유는 하나다 — 두 벌이 되면 버그도 두 벌이 되고, 한쪽만 고치게 된다.
(대시보드 모듈은 전부 stdlib라 venv 없이 import된다.)

저장 위치는 `data/saved-layouts/`이고, JSON과 **사람이 읽는 `.md` 요약**을 함께 쓴다.
cmux가 꺼지든 맥이 재부팅되든 파일은 남는다.

### 범위(scope) 문법

```
all                    전부 (★현재 Claude Code 세션의 워크스페이스는 항상 제외한다)
window:<ref|uuid>      그 창의 워크스페이스 전부
group:<이름>            그 그룹의 멤버 전부
ws:<제목일부>           제목이 포함하는 워크스페이스
```

### ⚠️ 파괴 안전장치 — 사고에서 나온 것들

`close`는 워크스페이스를 **진짜로 닫는다.** 실행 중인 claude도 같이 죽는다.
`ws:` 필터를 넓게 잡아 사용자 원본 워크스페이스 두 개와 claude 두 개를 날린 사고가 있었고,
그 뒤에 붙은 장치들이다.

- 닫을 목록을 **창별로 묶어 먼저 보여주고**, 여러 창에 걸치면 경고한다
- **"실행 중 claude N개가 종료됩니다"를 명시**한다
- `--in window:N`으로 교집합을 걸 수 있다
- **현재 Claude Code 세션의 워크스페이스는 자동 제외**한다(자기 발밑을 파지 않도록)
- `--yes` 없이는 실행되지 않는다 (2단계)

`restore`에는 `--dry-run`이 있다. **먼저 돌려 보고 목록을 확인한 뒤에 실행하는 걸 권한다.**

> 이 스크립트는 원래 `~/.claude/skills/cmux-config/`의 스킬이었고, 저장소 안으로 옮기면서
> 대시보드 경로를 **이 파일의 부모 폴더**에서 먼저 찾도록 바꿨다.
> 다른 곳에 복사해 쓰려면 `CMUX_DASHBOARD_DIR`로 알려 주면 된다.
