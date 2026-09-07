"""공용 경로/설정 상수."""
import os

HOME = os.path.expanduser("~")

# cmux 바이너리 (pm2 비대화 셸에서도 확실하도록 절대경로 폴백)
CMUX_BIN = (
    os.environ.get("CMUX_BIN")
    or "/Applications/cmux.app/Contents/Resources/bin/cmux"
)

# cmux 앱 번들 — 포커스 이동 시 macOS 최전면으로 끌어올리는 데 쓴다(`open -a`)
CMUX_APP = os.environ.get("CMUX_APP", "/Applications/cmux.app")
CMUX_BUNDLE_ID = "com.cmuxterm.app"

# cmux 상태 파일들
HOOK_JSON = os.path.join(HOME, ".cmuxterm", "claude-hook-sessions.json")
NATIVE_JSON = os.path.join(
    HOME, "Library", "Application Support", "cmux", "session-com.cmuxterm.app.json"
)
NATIVE_PREV_JSON = os.path.join(
    HOME, "Library", "Application Support", "cmux",
    "session-com.cmuxterm.app-previous.json",
)

# Claude Code 세션 저장소
CLAUDE_PROJECTS = os.path.join(HOME, ".claude", "projects")

# cmux 설정(JSONC) — 소켓 비밀번호(socketPassword)를 여기서 읽는다(단일 소스).
CMUX_CONFIG_JSON = os.path.join(HOME, ".config", "cmux", "cmux.json")
CMUX_CONFIG_DIR = os.path.dirname(CMUX_CONFIG_JSON)   # 자동백업 cmux.<TS>.bak 위치

# cmux 소켓 경로(고정) 및 last-socket-path 파일
CMUX_APPSUPPORT = os.path.join(HOME, "Library", "Application Support", "cmux")
CMUX_SOCKET_FILE = os.path.join(CMUX_APPSUPPORT, "cmux.sock")   # 구 경로(≤0.64.7)
CMUX_LAST_SOCKET = os.path.join(CMUX_APPSUPPORT, "last-socket-path")
# 신 경로(0.64.17~): 소켓이 XDG state 디렉토리로 이동. last-socket-path 는
# 낡은 구 경로를 계속 가리키므로(cmux가 갱신 안 함) 이 경로를 존재검사로 폴백한다.
CMUX_SOCKET_XDG = os.path.join(HOME, ".local", "state", "cmux", "cmux.sock")

# 앱 설정
PORT = int(os.environ.get("CMUX_DASH_PORT", "7788"))
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("CMUX_DASH_DATA", os.path.join(PROJECT_DIR, "data"))
DB_PATH = os.path.join(DATA_DIR, "snapshots.db")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
# 회전(60개 제한)되지 않는 고정 네이티브 백업 — 리셋 직전 등 중요한 레이아웃을 영구 보관.
# load_layout_windows 폴백이 backups + pinned 를 함께 스캔한다.
PINNED_DIR = os.path.join(DATA_DIR, "pinned")
RECOVERY_JSON = os.path.join(DATA_DIR, "latest-recovery.json")
RECOVERY_TXT = os.path.join(DATA_DIR, "latest-recovery.txt")
# 소켓 비밀번호 known-good 스태시 — cmux 업데이트가 cmux.json 을 리셋해 socketPassword 를
# 날려도, 대시보드가 여기 보관해 둔 마지막 정상 비번으로 자가복구(self-heal)한다.
CMUX_PW_STASH = os.path.join(DATA_DIR, "cmux-socketpw.json")

# 스냅샷/보관 정책
POLL_INTERVAL = int(os.environ.get("CMUX_DASH_POLL", "30"))   # 초
EVENT_DEBOUNCE = 3          # 이벤트 발생 후 캡처까지 디바운스(초)
RETENTION_DAYS = 30         # (구) 평면 컷 보관일 — 계층적 보존으로 대체됨, 호환용으로 남김
# 계층적 보존: 오래될수록 성기게 남겨 '옛 시점 되돌리기'를 영구히 가능하게 한다.
#   최근 RETAIN_FULL_DAYS 일 = 전부 / 그 다음 RETAIN_HOURLY_DAYS 일까지 = 시간당 1개
#   그 이전 = RETAIN_COARSE_BUCKET_SEC 마다 1개 / 마일스톤은 항상 영구.
# (2026-08-05 이전 정책은 30일 평면 컷이라 그 이전이 마일스톤만 남았다 = 되돌리기 불가)
RETAIN_FULL_DAYS = 7
RETAIN_HOURLY_DAYS = 30
RETAIN_COARSE_BUCKET_SEC = 4 * 3600     # 4시간 = 하루 6개
PRUNE_INTERVAL_SEC = 3600               # prune 은 비싸므로 1시간에 한 번만
MAX_NATIVE_BACKUPS = 60     # cmux 네이티브 세션 백업 보관 개수
