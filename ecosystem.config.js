// pm2 정의 — venv python으로 uvicorn 실행, tailscale(0.0.0.0) 노출
const path = require('path');
const DIR = __dirname;

module.exports = {
  apps: [{
    name: 'cmux-dashboard',
    cwd: DIR,
    script: path.join(DIR, '.venv/bin/python'),
    args: '-m uvicorn app:app --host 0.0.0.0 --port 7788',
    interpreter: 'none',
    autorestart: true,
    // 누수 방지용 **백스톱**이지 정상 피크를 조이는 장치가 아니다.
    // 실측(2026-08-28, M5 Max 128GB · Claude 탭 62개):
    //   정상상태 117~122MB · 스냅샷 로드 126MB · nav+state 동시 폴링 피크 **145MB**
    // 예전 값 150M 은 피크와 여유가 3% 뿐이라 **30초마다 재시작을 유발**했고,
    // pm2 가 SIGINT 를 보낼 때 진행 중이던 cmux 자식이 같이 죽어(rc=-2) 트리 조회가
    // 실패 → 화면이 "탭 0개 + 경고 벽"이 됐다. 한도가 안전장치가 아니라 장애 원인이었다.
    // → 피크의 3.5배로 잡는다. 파이썬은 해제한 메모리를 OS 에 바로 안 돌려줘 장기 가동 시
    //   RSS 가 자연히 우상향하므로(이 앱은 몇 주씩 뜬다) 그 여유까지 포함한 값이다.
    //   128GB 중 0.4% 라 시스템에는 무의미하고, 읽기 위주 대시보드가 512MB 를 쓰면
    //   그건 진짜 이상이므로 백스톱으로서는 여전히 유효하다.
    //   (시스템 차원의 메모리 감시는 memory-guard 데몬이 따로 한다)
    max_memory_restart: '512M',
    env: { PYTHONUNBUFFERED: '1' },
    out_file: path.join(DIR, 'data/pm2-out.log'),
    error_file: path.join(DIR, 'data/pm2-err.log'),
  }],
};
