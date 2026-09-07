/* '지금 어디' — 살아있는 Claude 탭 목록 + 클릭 한 번으로 그 탭으로 이동.
   /now 독립 페이지와 대시보드의 '지금 어디' 탭이 같은 코드를 쓴다. */
window.NAV = (function(){
  /* 상태 아이콘 표는 icons.js(STATUS) 한 곳에만 둔다 — icStatus(state) 로 쓴다. */
  const HEAD = {running:'작업 중', permission:'권한 대기', question:'질문 대기',
                background:'뒤에서 진행', waiting:'입력 대기', idle:'유휴', unknown:'판정 불가'};
  const POLL_MS = 5000;
  /* 최근순보다 **앞에 두는** 상태. 둘 다 '지금 나와 관계된 것'이지만 급한 정도가 다르다:
       permission  내 승인을 기다리며 **멈춰 있다** — 내가 안 보면 아무것도 안 나아진다.
                   게다가 조용하다(출력도 진행도 없다). 그래서 맨 앞·맨 위.
       running     돌고 있다 — 보고 싶지만 내가 없어도 진행된다.
     나머지(대기·유휴·판정불가)는 같은 층으로 묶어 기존대로 최근순으로만 가른다.
     상태 순서를 전부 적용하면 15일 전 '대기' 가 20초 전 '유휴' 를 제치고 올라와
     최근순이 사실상 무력해진다(2026-08-30 결정, 유지).
     ⚠️ 이 표는 컬럼(창) 순서와 컬럼 안 카드 순서를 **동시에** 정한다. 그래서 권한 대기
        탭 하나가 생기면 그 창이 통째로 맨 왼쪽으로 오고, 그 안에서도 맨 위로 온다. */
  /* 최근순보다 **앞에 두는** 상태만 적는다 — 나머지(대기·유휴·판정불가)는 일부러 같은 층으로
     묶어 최근순에 맡긴다. 그래서 서버의 statusOrder 를 그대로 쓸 수는 없다(그건 전부를 가른다).
     ⚠️ 대신 **서버에 상태를 추가하면 여기도 봐야 한다.** background 를 넣고 이걸 안 고쳐서
        한동안 '뒤에서 진행'이 유휴와 같은 층에 있었다(2026-09-07). */
  const URGENT = {permission: 0, question: 0, running: 1, background: 2};
  /* 세션 UUID 를 명령줄에서 못 읽은 경우. 상태·활동의 근거가 그만큼 약하다는 뜻이라
     조용히 넘기지 않고 카드에 드러낸다(이 저장소 원칙: 근거를 함께 보여 준다). */
  const WEAK_SID = /추정|없음|버림/;
  const urgency = t => (t && t.status in URGENT) ? URGENT[t.status] : 9;
  let timer = null, els = null, lastData = null, query = '';
// 토글 상태는 브라우저에 기억시킨다 — 탭을 오갈 때마다 초기화되면 "검색했는데 결과가 줄었다"로 보인다
let showIdle = localStorage.getItem('navShowIdle') === '1';
// 보기 모드 — 'window'(창별 컬럼 보드) / 'status'(상태별 목록). 창 배치를 조망하고 싶다는
// 요구에 맞춰 기본은 창별이고, "방금 답한 게 뭐냐"만 볼 땐 상태별이 빠르므로 토글로 남긴다.
let viewMode = localStorage.getItem('navView') || 'window';
let lastHtml = null;   // 직전 렌더 결과 — 같으면 DOM 을 건드리지 않는다(아래 render 주석 참조)
// 클릭 시 cmux.app 을 macOS 최전면까지 끌어올릴지. cmux 내부 전환만 하면 브라우저를 보고
// 있을 때 화면이 그대로라 "이동했다"는 말만 뜬다.
let activateApp = (localStorage.getItem('navActivateApp') ?? '1') === '1';

/* ── 창·워크스페이스로 좁혀 보기 ────────────────────────────────────────
   헤더(창 이름 / 워크스페이스 이름)를 누르면 그것만 남는다. 여러 개를 눌러 더할 수 있다(OR).
   ⚠️ 필터는 **화면에 흔적이 없는 상태**다. 걸어 둔 걸 잊으면 "탭이 갑자기 사라졌다"가 되므로
      걸린 게 있으면 목록 맨 위에 칩으로 항상 드러내고, 몇 개가 숨었는지도 같이 적는다. */
const loadSet = k => { try { return new Set(JSON.parse(localStorage.getItem(k) || '[]')); }
                       catch { return new Set(); } };
let winFilter = loadSet('navWinFilter');
let wsFilter  = loadSet('navWsFilter');
let grpFilter = loadSet('navGrpFilter');
/* 그룹 키 — cmux 그룹은 id 가 정본이고, 없으면 이름으로 갈음한다.
   그룹이 아예 없는 탭은 '' 이 되고, 그룹으로 좁히는 중에는 (고를 헤더가 없으므로) 빠진다. */
const gkey = t => (t.group && (t.group.id || t.group.name)) || '';

/* 그룹 고정은 cmux 의 **네이티브 세션 JSON 이 디바운스로 늦게** 갱신된다. /api/nav 는 그 파일을
   읽으므로, 눌러 놓고 다음 폴링을 기다리면 몇 초 동안 옛 상태가 그대로 보인다 — "눌렀는데
   안 바뀐다"로 읽힌다. 서버가 라이브 RPC 로 확인해 준 값을 여기 덮어 두고, nav 가 같은 값을
   실어 오면 걷는다(추측으로 덮는 게 아니라 **확인된 값**만 덮는다). */
const pinOverride = new Map();

/* 그룹 정렬 모드.
     activity  기본 — '그 그룹에서 가장 최근에 움직인 멤버의 자리'(고정 그룹이 위)
     layout    cmux 사이드바에 실제로 놓인 순서 = 앵커 워크스페이스의 인덱스
   끌어서 옮기면 **cmux 실제 순서를 바꾸므로**, 그 결과가 보이도록 자동으로 layout 으로 넘어간다
   (활동순인 채로 두면 cmux 는 바뀌었는데 화면은 그대로라 "안 먹었다"로 읽힌다). */
let groupSort = localStorage.getItem('navGroupSort') || 'activity';
let dragGk = null, dragWin = null;

  const esc = s => (s??"").toString().replace(/[&<>"]/g,
      c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

  /* 이 요소 **안에서** 실제로 글자가 선택돼 있나.
     페이지 다른 곳의 선택까지 세면 엉뚱한 클릭이 막히고, 아예 안 세면 상세에서 끌어
     선택한 직후의 클릭에 카드가 접혀 복사할 수가 없다.
     ⚠️ index.html 에도 같은 함수가 있지만 여기서 그걸 부르면 안 된다 — `/now/full`
        (now.html)은 nav.js 만 싣기 때문에 그 함수가 없다. */
  function hasSelectionIn(el){
    const s = window.getSelection && window.getSelection();
    if(!s || s.isCollapsed || !el) return false;
    return el.contains(s.anchorNode) || el.contains(s.focusNode);
  }

  function ago(ts){
    if(!ts) return "—";
    const s = Date.now()/1000 - ts;
    if(s < 60) return `${Math.max(0,Math.floor(s))}초 전`;
    if(s < 3600) return `${Math.floor(s/60)}분 전`;
    if(s < 86400) return `${Math.floor(s/3600)}시간 전`;
    return `${Math.floor(s/86400)}일 전`;
  }

  function say(msg){
    if(typeof window.toast === 'function' && window.toast !== say) return window.toast(msg);
    const t = document.querySelector('#toast');
    if(!t){ return; }
    t.textContent = msg; t.classList.add('show');
    clearTimeout(t._h); t._h = setTimeout(()=>t.classList.remove('show'), 2200);
  }

  /* 클릭 → 그 탭으로 이동. surface.focus 하나로 창 전환 + 워크스페이스 전환 + 탭 선택이 모두 되고,
     서버가 이어서 cmux.app 을 최전면으로 올린다.

     ⚠️ "이동 실패: Failed to fetch" 가 간헐적으로 났던 이유 —
        서버 응답이 0.5초쯤 걸리는데 그 사이 **cmux 가 최전면이 되면서 이 페이지가 백그라운드로
        내려간다.** 백그라운드 전환 시점에 진행 중이던 fetch 가 끊기면 네트워크 오류로 떨어진다.
        → `keepalive: true` 로 페이지가 뒤로 가도 요청이 살아남게 하고, 그래도 실패하면 한 번
          자동 재시도한다(같은 탭으로 다시 focus 하는 것이라 멱등이라 안전).
        타임아웃도 명시해 무한 대기를 막는다. */
  const FOCUS_TIMEOUT_MS = 12000;

  async function postFocus(body){
    const ac = new AbortController();
    const t = setTimeout(() => ac.abort(), FOCUS_TIMEOUT_MS);
    try{
      const r = await fetch('/api/cmux/focus', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify(body), keepalive: true, signal: ac.signal,
      });
      const j = await r.json().catch(()=>({}));
      if(!r.ok) throw new Error(j.detail || ('HTTP ' + r.status));
      return j;
    } finally { clearTimeout(t); }
  }

  const humanErr = e =>
      e.name === 'AbortError'                    ? '응답이 없어 중단했습니다'
    : /Failed to fetch|NetworkError|Load failed/i.test(e.message) ? '대시보드 서버에 닿지 못했습니다'
    : e.message;

  async function focus(surfaceId, windowId, label){
    // 누른 그 순간부터 조용해진다(서버가 활성 탭 변경을 알려 주기 전까지의 공백을 메운다).
    hushed.set(surfaceId, Date.now());
    lastHtml = null; render();
    const body = {surface: surfaceId, window: windowId || null, activate: activateApp};
    let j;
    try{
      j = await postFocus(body);
    }catch(e){
      // 네트워크 계열 실패는 한 번 더 — 대개 백그라운드 전환 순간에 끊긴 것이라 재시도로 붙는다
      const retriable = e.name === 'AbortError'
        || /Failed to fetch|NetworkError|Load failed/i.test(e.message);
      if(!retriable){ say(`이동 실패: ${humanErr(e)}`); return; }
      await new Promise(r => setTimeout(r, 400));
      try{ j = await postFocus(body); }
      catch(e2){ say(`이동 실패: ${humanErr(e2)} — 다시 눌러보세요`); return; }
    }
    // 실패를 조용히 넘기지 않는다 — "이동했다"만 보고 사용자가 직접 창을 찾아 헤매게 된다.
    // verified 는 서버가 이동 직후 cmux 에 다시 물어 확인한 값이다.
    if(j.verified === false)
      say(`이동 안 됨 — 지금 활성: ${j.activeNow || '알 수 없음'} (다시 눌러보세요)`);
    else if(activateApp && j.activated === false)
      say(`탭은 이동함 · cmux 앱 전환 실패 (${j.activateVia || '원인 미상'})`);
    else
      say(`이동: ${label}`);
    setTimeout(()=>refresh(true), 700);      // 활성 탭 배지 갱신
  }

  /* 워크스페이스 고정/해제. 서버에는 **토글이 아니라 원하는 최종 상태**를 보낸다 —
     화면이 든 상태가 낡았으면 토글은 정확히 반대로 뒤집힌다. 서버가 실제 결과를 되돌려
     주므로 반영이 안 됐으면 그대로 드러낸다.
     고정은 그냥 표시가 아니다 — cmux 는 고정된 워크스페이스를 고정 블록 밖으로 못 나가게
     막으므로(편집 모드의 클램프), 여기서 풀고 다시 배치할 수 있어야 한다. */
  async function togglePin(wsid, pinned){
    if(!wsid) return;
    const want = !pinned;
    try{
      const r = await fetch('/api/cmux/pin', {method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({workspace: wsid, pinned: want})});
      const j = await r.json().catch(()=>({}));
      if(!r.ok) throw new Error(j.detail || ('HTTP ' + r.status));
      if(j.verified === false)
        say(`고정 상태가 반영되지 않았습니다 — 지금 ${j.pinned ? '고정됨' : '해제됨'}`);
      else say(want ? '고정했습니다' : '고정을 해제했습니다');
    }catch(e){ say(`고정 변경 실패: ${humanErr(e)}`); }
    lastHtml = null;
    refresh(true);
  }

  /* 헤더 검색창과 연결된 필터. 탭 제목뿐 아니라 위치·세션ID·cwd·마지막 프롬프트까지 훑는다
     — "그 얘기 하던 탭이 어디였지"로 찾는 경우가 많아서다. */
  function saveFilters(){
    localStorage.setItem('navWinFilter', JSON.stringify([...winFilter]));
    localStorage.setItem('navWsFilter',  JSON.stringify([...wsFilter]));
    localStorage.setItem('navGrpFilter', JSON.stringify([...grpFilter]));
  }
  function toggleFilter(set, key){
    if(!key) return;
    set.has(key) ? set.delete(key) : set.add(key);
    saveFilters(); lastHtml = null; render();
  }
  function clearFilters(){
    winFilter.clear(); wsFilter.clear(); grpFilter.clear();
    saveFilters(); lastHtml = null; render();
  }
  const passesFilter = t =>
       (!winFilter.size || winFilter.has(t.windowRef || ''))
    && (!wsFilter.size  || wsFilter.has(t.workspaceId || ''))
    && (!grpFilter.size || grpFilter.has(gkey(t)));

  /* cmux 를 다시 켜면 창·워크스페이스 id 가 바뀐다. 낡은 필터가 남으면 아무것도 안 걸려
     "탭이 하나도 없다"가 되고, 화면만 봐선 원인을 알 수 없다 → 지금 없는 값은 조용히 버린다.
     ⚠️ 판단은 **유휴까지 포함한 전체**로 한다. 유휴를 숨긴 탓에 잠깐 안 보이는 걸 사라졌다고
        오해하면, 유휴 토글을 켜는 순간 멀쩡한 필터가 풀려 버린다. */
  function pruneFilters(d){
    const wins = new Set(), wss = new Set(), grps = new Set();
    for(const t of (d.tabs||[])){
      wins.add(t.windowRef || ''); wss.add(t.workspaceId || ''); grps.add(gkey(t));
    }
    let dirty = false;
    for(const k of [...winFilter]) if(!wins.has(k)){ winFilter.delete(k); dirty = true; }
    for(const k of [...wsFilter])  if(!wss.has(k)){  wsFilter.delete(k);  dirty = true; }
    for(const k of [...grpFilter]) if(!grps.has(k)){ grpFilter.delete(k); dirty = true; }
    if(dirty) saveFilters();
  }

  function filterBar(d, hiddenN){
    const anyFilter = winFilter.size || wsFilter.size || grpFilter.size;
    // 배치순으로 보고 있는 것도 '보이지 않는 상태'다 — 걸려 있으면 바를 띄워 되돌릴 길을 남긴다.
    if(!anyFilter && groupSort !== 'layout') return '';
    const wl = new Map(), sl = new Map(), gl = new Map();
    for(const t of (d.tabs||[])){
      if(t.windowRef)   wl.set(t.windowRef, t.windowLabel || t.windowRef);
      if(t.workspaceId) sl.set(t.workspaceId, norm(t.workspaceTitle || '') || '(워크스페이스)');
      if(gkey(t))       gl.set(gkey(t), (t.group && t.group.name) || gkey(t));
    }
    const chip = (kind, key, label) =>
      `<button class="fchip" data-fkind="${kind}" data-fkey="${esc(key)}"
               title="${esc(label)} — 눌러서 해제">${kind === 'win' ? ic('app-window') : kind === 'grp' ? ic('folder') : ic('layers')}
         <span class="ft">${esc(label)}</span><span class="fx">×</span></button>`;
    return `<div class="navfilter">
      ${anyFilter ? '<span class="fl">좁혀 보는 중</span>' : ''}
      ${[...winFilter].map(k => chip('win', k, wl.get(k) || k)).join('')}
      ${[...grpFilter].map(k => chip('grp', k, gl.get(k) || k)).join('')}
      ${[...wsFilter].map(k => chip('ws', k, sl.get(k) || k)).join('')}
      ${hiddenN ? `<span class="fn">${hiddenN}개 숨김</span>` : ''}
      ${groupSort === 'layout' ? `<span class="fl">그룹 순서</span>
        <span class="fn">cmux 배치순 — 끌어서 바꾸면 cmux 에도 반영됩니다</span>
        <button class="gordclear">활동순으로</button>` : ''}
      ${anyFilter ? '<button class="fclear">모두 해제</button>' : ''}
      ${picker(d)}
    </div>`;
  }

  /* 상태별 보기에는 창 헤더가 없어서 **두 번째 창을 더할 자리가 없다**(창별 보기는 접힌
     컬럼 헤더가 그 역할을 한다). 그래서 이 모드에서만 필터 바가 창 고르개를 겸한다. */
  function picker(d){
    if(viewMode === 'window') return '';
    const wins = new Map(), grps = new Map();
    for(const t of (d.tabs||[])){
      if(t.windowRef) wins.set(t.windowRef, t.windowLabel || t.windowRef);
      if(gkey(t))     grps.set(gkey(t), (t.group && t.group.name) || gkey(t));
    }
    const row = (label, map, set, kind) => map.size < 2 ? '' :
      `<div class="fpick"><span class="fl">${label}</span>${[...map].map(([k,v])=>
        `<button class="fchip sm${set.has(k)?' on':''}" data-fkind="${kind}" data-fkey="${esc(k)}"
                 title="${esc(set.has(k) ? v+' 빼기' : v+' 더하기')}">${esc(v)}</button>`).join('')}</div>`;
    return row('창', wins, winFilter, 'win') + row('그룹', grps, grpFilter, 'grp');
  }

  /* 지금 이 그룹이 고정인가 — 확인된 덮개가 있으면 그것, 없으면 nav 가 준 값. */
  /* 한 창 안의 그룹 키를 화면에 놓인 순서대로. '뒤에 놓기'를 앞 기준으로 바꿀 때 쓴다. */
  function groupKeysIn(win){
    return [...win.querySelectorAll('.bgroup[data-gk]')].map(el => el.dataset.gk).filter(Boolean);
  }

  /* 그룹을 옮긴다 — **cmux 실제 순서**를 바꾼다(앵커 워크스페이스와 멤버가 통째로 따라간다).
     cmux 는 '앞에 놓기'만 실제로 동작하므로 아래로 내리는 건 "그 다음 그룹의 앞"으로 바꿔 보낸다.
     맨 뒤로 보내는 자리는 기준이 될 다음 그룹이 없어서 불가능하다 — 조용히 실패시키지 않고 말한다. */
  async function moveGroup(src, dst, before, win){
    let target = dst;
    if(!before){
      const keys = groupKeysIn(win);
      const i = keys.indexOf(dst);
      target = (i >= 0 && i + 1 < keys.length) ? keys[i + 1] : null;
      if(target === src) return;                    // 이미 그 자리다
      if(!target){
        say('맨 뒤로는 옮길 수 없습니다 — cmux 는 «앞에 놓기»만 지원합니다. 대신 다른 그룹을 위로 올리세요.');
        return;
      }
    }
    if(target === src) return;
    // 바꾼 결과가 보이도록 배치순으로 넘어간다(활동순이면 cmux 만 바뀌고 화면은 그대로다).
    groupSort = 'layout'; localStorage.setItem('navGroupSort', 'layout');
    try{
      const r = await fetch('/api/cmux/group/move', {method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({group: src, before: target})});
      const j = await r.json().catch(()=>({}));
      if(!r.ok) throw new Error(j.detail || r.status);
      // 서버가 앵커 인덱스로 직접 대조한 결과다 — 안 움직였으면 이유까지 말해 준다.
      if(!j.moved) say(j.pinned
        ? '고정된 그룹은 순서를 바꿀 수 없습니다 — 고정을 풀고 다시 해 보세요.'
        : '순서가 바뀌지 않았습니다.');
    }catch(e){
      say('그룹 이동 실패: ' + e.message);
    }
    await refresh(true);
  }

  function setGroupSort(mode){
    groupSort = mode; localStorage.setItem('navGroupSort', mode);
    lastHtml = null; render();
  }

  function gpinned(gk, g){
    return pinOverride.has(gk) ? pinOverride.get(gk) : !!(g && g.pinned);
  }

  async function toggleGroupPin(gk, want){
    pinOverride.set(gk, want); lastHtml = null; render();     // 먼저 화면에 반영(디스크가 늦으므로)
    try{
      const r = await fetch('/api/cmux/group/pin', {method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({group: gk, pinned: want})});
      const j = await r.json().catch(()=>({}));
      if(!r.ok) throw new Error(j.detail || r.status);
      if(typeof j.pinned === 'boolean') pinOverride.set(gk, j.pinned);
      // 서버가 스스로 확인한 결과가 어긋나면 그대로 알린다 — "됐다"만 띄우지 않는다.
      if(j.verified === false) say(`고정이 반영되지 않았습니다 (지금 ${j.pinned ? '고정' : '해제'})`);
    }catch(e){
      pinOverride.delete(gk);
      say('그룹 고정 실패: ' + e.message);
    }
    lastHtml = null; render();
  }

  /* nav 가 덮개와 같은 값을 실어 왔으면 덮개를 걷는다(계속 들고 있으면 cmux 에서 직접 바꾼
     고정이 화면에 영영 반영되지 않는다). */
  function reapPinOverride(d){
    if(!pinOverride.size) return;
    const live = new Map();
    for(const t of (d.tabs||[])) if(gkey(t)) live.set(gkey(t), !!(t.group && t.group.pinned));
    for(const [k, v] of [...pinOverride])
      if(live.has(k) && live.get(k) === v) pinOverride.delete(k);
  }

  /* 방금 끝난 것을 알리는 두 등급.
       justdone  뒤에서 돌던 게 막 끝났다(서버가 상태 전이를 보고 알려 준다) → 강하게
       fresh     그냥 답이 막 나왔다(대기 + 최근 2분) → 약하게
     ⚠️ 눌러서 그 탭으로 넘어간 뒤에는 뛰지 않는다. `here`(지금 보는 탭)는 CSS 가 걸러 내고,
        누른 직후의 몇 초는 아래 `hushed` 가 메운다 — 서버가 활성 탭 변경을 알려 주기까지
        한 번의 폴링이 걸리는데 그 사이에도 뛰면 "눌렀는데 반응이 없다"로 읽힌다. */
  const FRESH_SEC = 120;
  /* 눌러서 이동한 직후 **잠깐만** 조용히 시킨다.
     ⚠️ 예전엔 Set 에 넣기만 하고 **빼는 곳이 없었다.** 그래서 한 번 눌러 이동한 탭은
        페이지를 새로 열 때까지 영영 안 뛰었다 — "가끔 안 뛰는데 새로고침하면 뛴다" 는
        신고(2026-09-07)가 정확히 이것이었다. 새로고침이 유일한 해제 수단이었던 셈이다.
     이 침묵은 서버가 '활성 탭이 바뀌었다'를 알려 줄 때까지의 공백(폴링 한 번)만 메우면 된다.
     그 뒤로는 `.here`(지금 보는 탭)를 CSS 가 걸러 주므로 여기서 더 들고 있을 이유가 없다. */
  const HUSH_MS = 8000;
  const hushed = new Map();          // surfaceId → 조용히 시킨 시각
  function isHushed(sid){
    const at = hushed.get(sid);
    if(at === undefined) return false;
    if(Date.now() - at > HUSH_MS){ hushed.delete(sid); return false; }
    return true;
  }
  function bump(t){
    if(isHushed(t.surfaceId)) return '';
    /* **멈춰 있는 탭에만** 붙인다.
       - 막힘(permission·question)은 자기 강조가 이미 있고 더 급하다 → 겹치지 않게 비켜 준다.
       - 도는 중(running·background)이면 알릴 이유가 없다. 백그라운드가 끝난 직후라도 이미
         다시 돌고 있다면 사람이 벌써 이어서 시켰다는 뜻이다(실측에서 이 경우가 바로 나왔다). */
    if(t.status !== 'waiting') return '';
    if(t.bgJustDone) return ' justdone';
    if(t.lastActivity && (Date.now()/1000 - t.lastActivity) < FRESH_SEC) return ' fresh';
    return '';
  }

  function matches(t){
    if(!query) return true;
    // ⚠️ windowShowing("그 창이 지금 보여주는 워크스페이스")은 **이 탭과 무관한 정보**라
    //    검색 대상에서 뺀다 — 넣으면 같은 창의 무관한 탭이 전부 걸려 결과가 지저분해진다.
    const hay = [t.cleanTitle, t.title, t.workspaceTitle, t.windowLabel, t.tabRef,
                 t.surfaceRef, t.sessionId, t.cwd, t.lastPromptText, t.statusLabel,
                 t.workspaceDescription, t.group && t.group.name,
                 t.notif && t.notif.subtitle]
      .filter(Boolean).join(' ').toLowerCase();
    // 공백으로 나눈 모든 낱말을 포함해야 한다(AND) — 좁혀 들어가기 쉽게
    return query.split(/\s+/).filter(Boolean).every(w => hay.includes(w));
  }

  const when = ts => ts ? new Date(ts*1000).toLocaleString('ko-KR',
      {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}) : '—';

  /* 카드 툴팁 — "왜 이 시각인가"를 밝힌다.
     ⚠️ 파일 mtime 은 실행 중 세션에서 대화 없이도 갱신되므로 활동 시각으로 쓰지 않는다.
        참고용으로만 보여준다(값이 크게 어긋나 보이는 게 정상). */
  function tip(t){
    const L = [];
    L.push(`상태: ${HEAD[t.status]||t.status} (${t.statusSource||'-'})`);
    L.push(`활동 기준: ${t.activitySource || '-'} → ${when(t.lastActivity)}`);
    if(t.wokenBySystem)
      L.push(`⚠️ 이 활동은 사람이 시킨 게 아닙니다 — `
             + `${withJosa(t.wakeKind || '백그라운드 알림', '이/가')} 깨웠습니다`);
    if(t.lastHumanAt) L.push(`사람이 시킨 마지막 활동: ${when(t.lastHumanAt)}`);
    if(t.lastMessageAt) L.push(`마지막 대화: ${when(t.lastMessageAt)}`);
    if(t.notif) L.push(`cmux 알림: ${t.notif.subtitle} · ${when(t.notif.at)}`);
    if(t.fileMtime) L.push(`파일 갱신: ${when(t.fileMtime)} (활동 지표 아님)`);
    if(t.workspaceTitle) L.push(`워크스페이스: ${norm(t.workspaceTitle)}`);
    if(t.workspaceDescription) L.push(t.workspaceDescription);
    L.push(`세션 ${(t.sessionId||'').slice(0,8)} · pid ${t.pid}`);
    if(t.cwd) L.push(t.cwd);
    return L.join('\n');
  }

  function card(t){
    const here = t.isActiveTab ? '<span class="badge">지금 보는 탭</span>' : '';
    const hot = (Date.now()/1000 - (t.lastActivity||0)) < 120 ? ' hot' : '';
    // cmux 는 워크스페이스 이름을 탭 제목과 동기화하는 일이 잦다 → 같으면 한 번만 보여준다
    const norm = x => (x||'').replace(/^[◐◑◒◓◔◕✳✻✽✢·\s]+/,'').trim();
    const wsName = t.workspaceTitle || '';
    const wsDup = !wsName || norm(wsName) === norm(t.cleanTitle);
    // 그 창에서 안 보이는(가려진) 워크스페이스면, 대신 그 창이 지금 뭘 보여주는지 알려준다
    const hidden = !t.workspaceSelected
      ? `<span class="tag">가려짐${t.windowShowing ? ' · 창은 「'+esc(String(t.windowShowing).replace(/^[◐◑◒◓◔◕✳✻✽✢·\s]+/,'').slice(0,16))+'」 표시 중' : ''}</span>`
      : '';
    const loc = t.located
      ? `<b class="fwin" data-fkey="${esc(t.windowRef||'')}" title="이 창만 보기">${esc(t.windowLabel)}</b>` +
        (t.group && t.group.name
          ? `<span class="sep">›</span><span class="fgrp" data-fkey="${esc(gkey(t))}"
               title="이 그룹만 보기">${esc(t.group.name)}</span>` : '') +
        (wsDup ? '' : `<span class="sep">›</span><span class="fws" data-fkey="${esc(t.workspaceId||'')}" title="이 워크스페이스만 보기">${esc(wsName)}</span>`) +
        `<span class="sep">›</span><span class="tag">${esc(t.tabRef||'')}</span>` +
        hidden +
        (t.paneCount > 1 ? `<span class="tag">분할 ${t.paneCount}</span>` : '')
      : '<span class="tag">위치 불명 — 탭이 닫혔거나 cmux 밖</span>';
    const prompt = t.lastPromptText
      ? `<div class="prompt">${esc(t.lastPromptText)}</div>` : '';
    const btn = t.located
      ? `<button class="go">이 탭으로 →</button>` : '';
    return `<div class="nav-card ${t.status}${t.isActiveTab?' here':''}${bump(t)}"
      ${t.workspaceColor?`style="--wsc:${esc(t.workspaceColor)}"`:''}
      data-surface="${esc(t.surfaceId||'')}" data-window="${esc(t.windowId||'')}"
      data-label="${esc(t.cleanTitle)}"
      title="${esc(tip(t))}">
      <div class="ic">${icStatus(t.status, t.statusLabel)}</div>
      <div class="bd">
        <div class="ttl">${esc(t.cleanTitle)} ${here}</div>
        <div class="loc">${loc}</div>
        ${prompt}
      </div>
      <div class="rt">
        <span class="age${hot}">${ago(t.lastActivity)}</span>
        ${btn}
      </div>
    </div>`;
  }

  /* 창별 컬럼 보드 — '현재' 탭과 같은 board.css 를 쓴다(둘의 카드가 따로 놀지 않게). */
  function boardHtml(shown){
    const wins = new Map();
    for(const t of shown){
      const key = t.windowRef || '?';
      if(!wins.has(key)) wins.set(key, {ref: key, label: t.windowLabel || key,
                                        idx: t.windowIndex ?? 99, tabs: []});
      wins.get(key).tabs.push(t);
    }
    /* 컬럼(창) 순서 = **그 창에서 가장 최근에 움직인 탭** 기준 내림차순.
       한때 '활성 창 먼저, 나머지는 창 번호순'이었는데, 그러면 방금 뭔가 끝난 창이 뒤쪽
       컬럼에 숨는다(실측: 2분 전에 움직인 창 4가 네 번째 컬럼에 있었다). 가로 스크롤을
       해야 보이는 자리라 사실상 못 본다. 활성 창은 대개 가장 최근이라 자연히 앞에 오고,
       아니면 정말로 다른 창이 더 최근인 것이다. 같으면 창 번호로 가른다. */
    const actWin = (lastData && lastData.activeWindowRef) || null;
    for(const c of wins.values()){
      // 그 창의 '가장 급한 층'과, **그 층 안에서의** 최신 활동으로 순서를 정한다.
      // 전체 최신으로 재면, 돌고 있는 창이 옆 창의 방금 멈춘 탭에 밀린다(실측: 21초 전부터
      // 돌던 창 3 이 20초 전 대기 카드를 가진 창 2 뒤로 갔다).
      c.urgent = Math.min.apply(null, c.tabs.map(urgency));
      c.recent = c.tabs.filter(t => urgency(t) === c.urgent)
                       .reduce((m,t)=>Math.max(m, t.lastActivity||0), 0);
    }
    const picked = c => !winFilter.size || winFilter.has(c.ref);
    const cols = [...wins.values()].sort((a,b)=>
      (picked(b) - picked(a))                      // 고른 창 먼저 — 접힌 컬럼이 앞을 막지 않게
      || (a.urgent - b.urgent) || (b.recent - a.recent) || (a.idx - b.idx));
    return `<div class="board">` + cols.map(c=>{
      const cnt = {};
      c.tabs.forEach(t=>{ cnt[t.status]=(cnt[t.status]||0)+1; });
      const dots = ['permission','question','running','waiting','idle','unknown']
        .filter(k=>cnt[k]).map(k=>`<span class="wd">${icStatus(k, HEAD[k])}${cnt[k]}</span>`).join(' ');
      /* 컬럼 안은 **작업중·권한 대기 먼저, 그다음 최근 활동순** — 방금 움직인 것이 맨 위.
         이 화면의 물음은 "지금 어디서 뭐가 움직였나"이지 "cmux 사이드바가 어떻게 생겼나"가
         아니다. 실제 배치를 그대로 보려면 '현재' 탭과 편집 모드가 사이드바 순서로 그린다.
         활동 시각이 같으면(둘 다 안 움직인 탭들) 사이드바 순서로 갈라 자리가 안 흔들리게 한다. */
      const on = picked(c);
      const items = on ? c.tabs.slice().sort((a,b)=>
        (urgency(a) - urgency(b))
        || (b.lastActivity||0) - (a.lastActivity||0)
        || (a.workspaceIndex ?? 999) - (b.workspaceIndex ?? 999)
        || (a.surfaceIndex ?? 999) - (b.surfaceIndex ?? 999)) : [];
      return `<div class="bwin${c.ref===actWin?' here':''}${on?'':' folded'}">
        <div class="bwin-head${winFilter.has(c.ref)?' fon':''}" data-win="${esc(c.ref)}"
             title="${esc(winFilter.has(c.ref) ? '이 창 빼고 보기' : (winFilter.size ? '이 창도 함께 보기' : '이 창만 보기'))}">
          <span class="wt">${ic('app-window')} ${esc(c.label)}${c.ref===actWin?' <span class="btag on">활성</span>':''}</span>
          <span class="wdots">${dots}</span>
          <span class="wn">${c.tabs.length}</span>
        </div>
        ${on ? groupedBody(items) : ''}
      </div>`;
    }).join('') + `</div>`;
  }
  /* 컬럼 안을 cmux 워크스페이스 그룹으로 묶는다.

     `items` 가 이미 최근순이므로 **먼저 나온 순서 그대로 블록을 쌓으면** 블록 순서도
     최근순이 된다(그룹의 자리 = 그 그룹에서 가장 최근에 움직인 멤버의 자리).
     그룹 밖 워크스페이스도 자기 최근순 자리에 낱개 블록으로 남는다 — 예전에는 전부
     맨 아래로 몰아서, 방금 움직인 워크스페이스가 그룹에 안 속했다는 이유만으로
     한참 아래에 처박혔다. */
  function groupedBody(items){
    const blocks = [], byG = new Map(), byWs = new Map();
    for(const t of items){
      const g = t.group || null;
      if(g){
        const k = g.id || g.name;
        if(!byG.has(k)){ const b = {g, tabs: []}; byG.set(k, b); blocks.push(b); }
        byG.get(k).tabs.push(t);
      } else {
        // 그룹 밖은 워크스페이스 단위로 묶는다(한 워크스페이스의 탭 여럿이 흩어지지 않게)
        const k = t.workspaceId || t.tabRef;
        if(!byWs.has(k)){ const b = {g: null, tabs: []}; byWs.set(k, b); blocks.push(b); }
        byWs.get(k).tabs.push(t);
      }
    }
    const filtering = grpFilter.size > 0;
    /* 순서 규칙 — 손으로 정한 게 있으면 **그것만** 따른다.
       고정을 그 위에 또 얹으면 "내가 끌어 놓은 자리에 안 있는" 일이 생긴다. 손으로 정하는
       순간의 화면 순서를 통째로 스냅샷하므로, 그때 위에 있던 고정 그룹은 자연히 위에 남는다.
       ⚠️ Array.sort 는 안정 정렬이라, 어느 쪽이든 나머지 순서('그 그룹에서 가장 최근에 움직인
          멤버의 자리')는 흐트러지지 않는다. */
    const bkey = b => b.g ? (b.g.id || b.g.name) : '';
    if(groupSort === 'layout'){
      /* cmux 사이드바의 자리 = 그 그룹 앵커의 워크스페이스 인덱스(멤버 중 가장 앞선 값).
         다만 **급한 게 든 그룹은 그 위로 올린다.** 순수 배치순으로만 두면 작업 중인 탭이
         배치상 뒤쪽 그룹에 있을 때 한참 아래로 밀린다 — 실제로 그랬다(2026-09-07 신고:
         창 7 에서 대기·유휴뿐인 INBOX(배치 1)가 맨 위, 작업 중인 탭이 든 개인·생활(배치 23)이
         맨 아래). 이 화면의 존재 이유가 "지금 뭐가 급한지"라 그쪽이 먼저다.
         급한 게 없으면 순수 배치순이므로, 끌어서 맞춰 둔 순서는 그대로 보인다. */
      const urg = b => Math.min.apply(null, b.tabs.map(urgency));
      const at = b => Math.min.apply(null, b.tabs.map(t => t.workspaceIndex ?? 1e6));
      blocks.sort((a, b) => (urg(a) - urg(b)) || (at(a) - at(b)));
    } else {
      blocks.sort((a, b) =>
        (b.g && gpinned(bkey(b), b.g) ? 1 : 0) - (a.g && gpinned(bkey(a), a.g) ? 1 : 0));
    }
    return blocks.map(({g, tabs})=>{
      // 그룹 없는 묶음은 누를 헤더가 없다 → 그룹으로 좁히는 중이면 빠진다(passesFilter 와 같은 판단).
      if(!g) return filtering ? '' : `<div class="bungrouped">${wsBody(tabs)}</div>`;
      const gk = g.id || g.name;
      const on = !filtering || grpFilter.has(gk);
      /* ⚠️ 접기(collapsed)는 **caret 만** 맡는다. 헤더의 나머지는 창·워크스페이스와 같은 어법으로
         '이것만 보기'다. 예전엔 헤더 전체가 인라인 onclick 으로 접기였고, 그때 쓰던 id(ng0,ng1…)는
         render 마다 번호가 올라가 html 이 매번 달라졌다 — lastHtml 비교가 늘 어긋나 5초마다
         DOM 을 통째로 갈아치우고 있었다. id 를 없애고 closest('.bgroup') 로 바꿔 그것도 같이 고친다. */
      return `<div class="bgroup${on?'':' folded'}" data-gk="${esc(gk)}" style="border-left-color:${(g.color ? esc(g.color) : 'var(--group-fallback)')}">
        <div class="bgroup-head${grpFilter.has(gk)?' fon':''}" data-grp="${esc(gk)}" draggable="true"
             title="${esc(grpFilter.has(gk) ? '이 그룹 빼고 보기'
                          : (filtering ? '이 그룹도 함께 보기' : '이 그룹만 보기'))}">
          <span class="caret" title="접기 / 펼치기">▾</span><span class="gic">${icGroup(g.icon)}</span>
          <b>${esc(g.name)}</b>
          <button class="gpin${gpinned(gk, g)?' on':''}" data-gpin="${esc(gk)}"
                  data-pinned="${gpinned(gk, g)?'1':'0'}"
                  title="${gpinned(gk, g) ? 'cmux 그룹 고정 해제' : 'cmux 에서 이 그룹을 고정(맨 위로)'}"
            >${ic('pin',{cls:'pin'})}</button>
          <span style="margin-left:auto">${tabs.length}</span>
        </div>
        ${on ? `<div class="bgroup-body">${wsBody(tabs)}</div>` : ''}
      </div>`;
    }).join('');
  }
  /* 워크스페이스 안에 claude 탭이 여럿이면 그 위계를 드러낸다(window→group→workspace→tab).
     하나뿐이면 굳이 한 겹 더 두지 않는다 — 대부분이 그렇고, 중첩만 깊어진다. */
  function wsBody(tabs){
    const byWs = new Map();
    for(const t of tabs){
      const k = t.workspaceId || t.tabRef;
      if(!byWs.has(k)) byWs.set(k, []);
      byWs.get(k).push(t);
    }
    const filtering = wsFilter.size > 0;
    return [...byWs.values()].map(list=>{
      const wsid = list[0].workspaceId || '';
      // 평소엔 탭이 하나뿐이면 중첩을 만들지 않는다(대부분이 그렇다). 다만 **좁혀 보는 중에는**
      // 하나짜리도 블록으로 감싼다 — 헤더가 없으면 그 워크스페이스를 눌러서 더할 수가 없다.
      if(list.length < 2 && !filtering) return bcard(list[0]);
      const on = !filtering || wsFilter.has(wsid);
      const title = norm(list[0].workspaceTitle || '') || '(워크스페이스)';
      return `<div class="bws${on?'':' folded'}">
        <div class="bws-head${wsFilter.has(wsid)?' fon':''}" data-wsf="${esc(wsid)}"
             title="${esc(wsFilter.has(wsid) ? '이 워크스페이스 빼고 보기'
                          : (filtering ? '이 워크스페이스도 함께 보기' : '이 워크스페이스만 보기'))}"><span>${ic('layers')}</span><b>${esc(title)}</b>
          <span style="margin-left:auto">${list.length}</span></div>
        ${on ? `<div class="bws-body">${list.map(t=>bcard(t, true)).join('')}</div>` : ''}
      </div>`;
    }).join('');
  }

  /* 카드에 보일 이름.
     ⚠️ 탭 제목(cleanTitle)은 cmux 가 claude 대화를 보고 **자동 생성**하는 이름이라,
        사용자가 워크스페이스 이름을 손으로 바꿔도 대화를 더 하기 전까지 옛 이름 그대로다
        (실측: 워크스페이스 이름은 사람이 손으로 바꿔 둔 새 이름인데, 탭 제목은 "이미지 #2 #3"
        처럼 직전 대화에서 자동 생성된 옛 이름이었다).
        그래서 **사람이 붙인 워크스페이스 이름을 우선**하고, 자동 제목은 부제로 내린다.
        단, 워크스페이스 블록 안(한 워크스페이스에 탭이 여럿)에서는 블록 머리가 이미
        워크스페이스 이름이므로 카드는 탭 제목을 쓴다. */
  function cardNames(t, inWs){
    const wsName = norm(t.workspaceTitle || '');
    const tabName = t.cleanTitle || '';
    if(inWs || !wsName) return {main: tabName, sub: ''};
    return {main: wsName, sub: (norm(tabName) !== norm(wsName)) ? tabName : ''};
  }

  function bcard(t, inWs){
    const hot = (Date.now()/1000 - (t.lastActivity||0)) < 180 ? ' hot' : '';
    const nm = cardNames(t, inWs);
    const desc = (t.workspaceDescription||'').trim();
    return `<div class="bcard ${t.status}${t.isActiveTab?' here':''}${bump(t)}"
        data-surface="${esc(t.surfaceId||'')}" data-window="${esc(t.windowId||'')}"
        data-ws="${esc(t.workspaceId||'')}" data-pinned="${t.workspacePinned?'1':'0'}"
        data-label="${esc(t.cleanTitle)}" title="${esc(tip(t))}"
        ${t.workspaceColor?`style="--wsc:${esc(t.workspaceColor)}"`:''}
        ${desc?`data-desc="${esc(desc.slice(0,300))}"`:''}>
      <div class="brow">
        <span class="bic">${icStatus(t.status, t.statusLabel)}</span>
        <span class="btitle">${esc(nm.main)}</span>
        <span class="bage${hot}">${t.lastActivity?ago(t.lastActivity).replace(' 전',''):'—'}</span>
        ${t.located?`<button class="bgo" title="이 탭으로 이동">${ic('crosshair')}</button>`:''}
      </div>
      <div class="bmeta">
        ${t.tabRef?`<span class="btag">${esc(t.tabRef)}</span>`:''}
        ${t.isActiveTab?'<span class="btag on">지금 보는 탭</span>':''}
        ${nm.sub?`<span class="btag" title="cmux 가 대화를 보고 붙인 탭 이름">${esc(nm.sub.slice(0,18))}</span>`:''}
        ${t.workspaceSelected?`<span class="btag on" title="${esc('이 창을 앞으로 가져오면 바로 이 탭이 보입니다.\ncmux 는 창 하나가 워크스페이스 하나만 화면에 띄웁니다 — 배지가 없는 탭은\n같은 창에 있어도 사이드바에서 한 번 더 눌러야 나옵니다.')}">보임</span>`:''}
        ${t.workspacePinned?`<span class="btag">${ic('pin',{cls:'pin',title:'워크스페이스 고정'})}</span>`:''}
        ${t.wokenBySystem?`<span class="btag wake" title="${esc(
            '사람이 시킨 게 아니라 백그라운드 알림이 세션을 깨웠습니다'
            + (t.wakeKind?` (${t.wakeKind})`:'')
            + (t.lastHumanAt?`\n사람이 시킨 마지막 활동: ${ago(t.lastHumanAt)}`:''))}">${ic('bot')} 자동</span>`:''}
        ${t.paneCount>1?`<span class="btag">분할 ${t.paneCount}</span>`:''}
      </div>
      <div class="bdetail">
        ${t.lastPromptText?`<div class="bq">“${esc(t.lastPromptText)}”</div>`:''}
        <div class="bk">${esc(t.statusLabel)} · ${esc(t.statusSource||'')}</div>
        ${t.askText?`<div class="bk ask">${ic('circle-question-mark')}
           <span>${esc(t.askText)}</span></div>`:''}
        ${WEAK_SID.test(t.sessionIdSource||'')?`<div class="bk weak" title="${esc(
            '이 탭의 세션 UUID 를 명령줄에서 못 읽어 탭 제목으로 되짚었습니다.\n'
            + '제목이 안 맞거나 같은 제목이 둘이면 엉뚱한 세션을 볼 수 있고,\n'
            + '끝내 못 찾으면 대화 기록이 없어 상태·활동을 알림에만 의존합니다.\n\n'
            + '주로 포크(/fork)나 재개된 세션에서 생깁니다 — 그런 세션은 로그인 셸이 아니라\n'
            + 'claude 데몬 밑에서 돌고, 그 데몬이 물고 있는 CMUX_PANEL_ID 가 낡아 있습니다.'
          )}">${ic('triangle-alert')} 세션 판별 근거 약함 — ${esc(t.sessionIdSource||'')}</div>`:''}
        ${t.titleRenamedByUser?`<div class="bk rename" title="${esc(
            'cmux 는 패널 제목 앞에 상태 마커(◐ 작업중 / ✳ 대기)를 붙여 줍니다.\n'
            + '그런데 패널 이름을 직접 바꾸면 cmux 가 제목을 그 문자열로 덮어쓰고\n'
            + '더 이상 마커를 갱신하지 않습니다 — 이 대시보드가 읽던 「지금 상태」 신호가 사라집니다.\n\n'
            + '지금은 대화 기록(마지막 턴이 끝났는지)으로 대신 판정하고 있습니다.\n'
            + '원래 신호를 되살리려면 패널 이름을 지우고 cmux 가 짓게 두면 됩니다\n'
            + '(워크스페이스 이름은 바꿔도 무해합니다 — 마커는 패널 제목에 붙습니다).'
          )}">${ic('triangle-alert')} 패널 이름을 직접 바꿔 cmux 상태 마커가 없습니다 — 대화 기록으로 판정 중</div>`:''}
        ${t.wokenBySystem?`<div class="bk wake">${ic('bot')} 사람이 아니라 <b>${esc(t.wakeKind||'백그라운드 알림')}</b>${josa(t.wakeKind||'백그라운드 알림', '이/가')}
           세션을 깨웠습니다 · 사람이 시킨 마지막 활동 ${t.lastHumanAt?esc(ago(t.lastHumanAt)):'기록 범위 밖'}</div>`:''}
        ${t.cwd?`<div class="bk">${esc(t.cwd)}</div>`:''}
        ${(t.wsTabs&&t.wsTabs.length>1)?`<div class="bk" style="margin-top:5px">이 워크스페이스의 탭 ${t.wsTabs.length}개</div>`
          +t.wsTabs.map(x=>`<div class="bsurf">${x.type==='browser'?ic('globe'):ic('square-terminal')}
             <span class="bt">${esc(x.title||x.url||x.ref||'')}</span>
             <span class="btag">${esc(String(x.ref||'').replace('surface:','tab:'))}</span></div>`).join(''):''}
        <div class="bbtns"><button class="go">이 탭으로 →</button>${
          t.workspaceId ? `<button class="pin">${ic('pin',{cls:'pin'})} ${t.workspacePinned?'고정 해제':'고정'}</button>` : ''}${
          /* 워크스페이스에 탭이 하나뿐이면 블록 헤더(.bws-head)가 아예 안 생긴다(대부분이 그렇다).
             그 경우 여기가 그 워크스페이스로 좁힐 수 있는 유일한 자리다. */
          t.workspaceId ? `<button class="only">${ic('layers')} ${wsFilter.has(t.workspaceId)?'좁혀 보기 해제':'이 워크스페이스만'}</button>` : ''}</div>
      </div>
    </div>`;
  }

  const norm = x => (x||'').replace(/^[◐◑◒◓◔◕✳✻✽✢·\s]+/,'').trim();

  function render(){
    if(!lastData || !els) return;
    const d = lastData, order = d.statusOrder || ['running','permission','waiting','idle','unknown'];
    pruneFilters(d);
    reapPinOverride(d);
    // 검색·유휴까지만 적용한 것(base)과 좁혀 보기까지 적용한 것(shown)을 나눠 둔다 —
    // 둘의 차이가 곧 "좁혀 보기 때문에 숨은 개수"라, 그 숫자를 칩 옆에 적을 수 있다.
    const base = d.tabs.filter(t => (showIdle || t.status !== 'idle') && matches(t));
    const shown = base.filter(passesFilter);
    let html = '';
    for(const w of (d.warnings||[])) html += `<div class="nav-warn">⚠️ ${esc(w)}</div>`;
    html += filterBar(d, base.length - shown.length);
    if(!shown.length && !(viewMode === 'window' && base.length)){
      html += (winFilter.size || wsFilter.size) && base.length
        ? `<div class="empty">좁혀 보기에 걸린 탭이 없습니다 — 위 칩의 ×로 해제하세요.</div>`
        : query
        ? `<div class="empty">‘${esc(query)}’와 맞는 탭이 없습니다.${
            showIdle ? '' : ' (유휴 탭은 “유휴도 보기”를 켜야 보입니다)'}</div>`
        : `<div class="empty">표시할 Claude 탭이 없습니다.${
            d.total ? ` (유휴 ${d.counts.idle||0}개는 “유휴도 보기”로)` : ''}</div>`;
    }
    document.body.classList.toggle('nav-board', viewMode === 'window');
    // ⚠️ 보드에는 필터 **전**(base)을 넘긴다. 안 고른 창을 지워 버리면 두 번째로 고를 대상이
    //    화면에서 사라져 다중선택 자체가 불가능해진다 — 접어서 헤더만 남기는 건 boardHtml 몫이다.
    if(viewMode === 'window' && base.length){
      html += boardHtml(base);
    } else {
      for(const st of order){
        const g = shown.filter(t => t.status === st);
        if(!g.length) continue;
        html += `<div class="nav-grp"><div class="nav-grp-h">${icStatus(st, HEAD[st])} ${HEAD[st]||st}
          <span class="n">${g.length}개</span></div>${g.map(card).join('')}</div>`;
      }
    }
    /* ⚠️ 5초마다 innerHTML 을 통째로 갈아치우면 **가로 스크롤이 원점으로 튀고 펼쳐둔 카드가
       접힌다**(신고). 그래서 ①내용이 같으면 아예 손대지 않고 ②바꿔야 할 때만 스크롤 위치와
       펼침 상태를 살려 복원한다. 경과시간은 분 단위라 대부분의 폴링에서 html 이 동일해 스킵된다. */
    if(html === lastHtml) return;
    const prevBoard = els.main.querySelector('.board');
    const sx = prevBoard ? prevBoard.scrollLeft : 0;
    const opened = new Set([...els.main.querySelectorAll('.bcard.open[data-surface]')]
                             .map(e => e.dataset.surface));
    /* 뛰는 카드는 **진행 중이던 지점을 넘겨받는다.**
       ⚠️ innerHTML 을 갈아치우면 CSS 애니메이션은 0 부터 다시 시작한다. 그런데 갓 끝난 탭은
          경과시간이 "N초 전" 이라 **매 폴링마다 html 이 달라져** 5초마다 교체된다 —
          1.8초짜리 뜀이 매번 리셋돼 리듬이 끊기고, 타이밍에 따라 아예 안 뛴 것처럼 보인다.
          (위 주석의 "경과시간은 분 단위" 는 오래된 탭 얘기고, 여기서 문제되는 건 갓 끝난 탭이다) */
    /* ⚠️ WebKit(WKWebView·Safari)은 CSS 애니메이션의 currentTime 을 **늘 0 으로** 준다
          (playState 는 running 인데도 — 실측 2026-09-07). 그 값을 그대로 복원하면 매번
          0 으로 되돌리는 셈이라 아무 도움이 안 된다. 0 이하는 아예 담지 않아, 그런 엔진에서는
          이 복원이 **조용히 아무 일도 하지 않게** 둔다(Chromium 계열에서만 실제로 이어진다). */
    const beats = new Map();
    els.main.querySelectorAll('.bcard[data-surface],.nav-card[data-surface]').forEach(el=>{
      const a = el.getAnimations ? el.getAnimations()[0] : null;
      if(a && a.currentTime > 0) beats.set(el.dataset.surface, a.currentTime);
    });
    els.main.innerHTML = html;
    lastHtml = html;
    const nb = els.main.querySelector('.board');
    if(nb && sx) nb.scrollLeft = sx;
    if(opened.size) opened.forEach(sid=>{
      const el = els.main.querySelector(`.bcard[data-surface="${CSS.escape(sid)}"]`);
      if(el) el.classList.add('open');
    });
    if(beats.size) els.main.querySelectorAll('.bcard[data-surface],.nav-card[data-surface]')
      .forEach(el=>{
        const t = beats.get(el.dataset.surface);
        if(t == null) return;
        const a = el.getAnimations ? el.getAnimations()[0] : null;
        if(a) try{ a.currentTime = t; }catch(e){ /* 애니메이션이 바뀐 경우엔 그냥 처음부터 */ }
      });
    els.main.querySelectorAll('.nav-card[data-surface]').forEach(el=>{
      if(!el.dataset.surface) return;
      el.onclick = () => focus(el.dataset.surface, el.dataset.window, el.dataset.label);
    });
    /* 보드 카드: **어디를 눌러도** 펼쳐진다. 실제 이동은 펼친 뒤 안의 버튼이 한다
       (압축 뷰라 한 번의 클릭으로 창이 튀지 않게).

       ⚠️ 예전엔 제목 줄(.brow)에만 핸들러가 있어서 바로 아래 배지 줄(tab:212 · 보임 ·
          분할 2)을 누르면 아무 반응이 없었다. 같은 카드인데 누르는 자리에 따라 되고 안 되니
          고장으로 읽힌다(신고 2026-08-26). 예외는 둘뿐 —
          ① 펼쳐 놓은 상세 안(버튼·경로 텍스트): 여기서 접히면 버튼을 못 누른다.
             버튼마다 stopPropagation 을 다는 대신 상세 영역을 통째로 비켜 간다
             (실제로 `복사: --resume` 은 event 를 넘겨받고도 안 쓰고 있었다).
          ② 글자를 끌어 선택하는 중: 선택을 끝낸 클릭으로 카드가 접히면 복사할 수가 없다. */
    els.main.querySelectorAll('.bcard[data-surface]').forEach(el=>{
      el.onclick = e => {
        if(e.target.closest('.bdetail')) return;   // 상세 안은 조작·복사 영역
        if(hasSelectionIn(el)) return;             // 상세에서 글자를 끌어 선택한 직후
        el.classList.toggle('open');
      };
      const go = el.querySelector('.go');
      if(go) go.onclick = e => { e.stopPropagation();
        focus(el.dataset.surface, el.dataset.window, el.dataset.label); };
      // 접힌 상태에서 바로 누르는 이동 버튼 — 상세를 펼치지 않아도 갈 수 있게 제목 줄에 둔다.
      const bgo = el.querySelector('.bgo');
      if(bgo) bgo.onclick = e => { e.stopPropagation();
        focus(el.dataset.surface, el.dataset.window, el.dataset.label); };
      const pin = el.querySelector('.pin');
      if(pin) pin.onclick = e => { e.stopPropagation();
        togglePin(el.dataset.ws, el.dataset.pinned === '1'); };
      const only = el.querySelector('.only');
      if(only) only.onclick = e => { e.stopPropagation(); toggleFilter(wsFilter, el.dataset.ws); };
    });

    /* 좁혀 보기 — 창 헤더·워크스페이스 헤더·위치줄·칩, 어디를 눌러도 같은 토글로 들어온다.
       ⚠️ 전부 stopPropagation 이 필요하다. 이름들이 **클릭하면 그 탭으로 이동하는 카드 안**에
          있어서, 안 막으면 좁히려던 클릭이 창을 전환해 버린다. */
    els.main.querySelectorAll('.bwin-head[data-win]').forEach(el=>{
      el.onclick = e => { e.stopPropagation(); toggleFilter(winFilter, el.dataset.win); };
    });
    els.main.querySelectorAll('.bws-head[data-wsf]').forEach(el=>{
      el.onclick = e => { e.stopPropagation(); toggleFilter(wsFilter, el.dataset.wsf); };
    });
    els.main.querySelectorAll('.fwin[data-fkey]').forEach(el=>{
      el.onclick = e => { e.stopPropagation(); toggleFilter(winFilter, el.dataset.fkey); };
    });
    els.main.querySelectorAll('.fws[data-fkey]').forEach(el=>{
      el.onclick = e => { e.stopPropagation(); toggleFilter(wsFilter, el.dataset.fkey); };
    });
    els.main.querySelectorAll('.fgrp[data-fkey]').forEach(el=>{
      el.onclick = e => { e.stopPropagation(); toggleFilter(grpFilter, el.dataset.fkey); };
    });
    els.main.querySelectorAll('.bgroup-head[data-grp]').forEach(el=>{
      el.onclick = e => { e.stopPropagation(); toggleFilter(grpFilter, el.dataset.grp); };
      const gp = el.querySelector('.gpin');     // 고정은 이 버튼만 맡는다
      if(gp) gp.onclick = e => { e.stopPropagation();
        toggleGroupPin(gp.dataset.gpin, gp.dataset.pinned !== '1'); };
      const car = el.querySelector('.caret');   // 접기는 caret 만 맡는다
      if(car) car.onclick = e => { e.stopPropagation();
        el.closest('.bgroup').classList.toggle('collapsed'); };
    });
    const setOf = k => k === 'win' ? winFilter : k === 'grp' ? grpFilter : wsFilter;
    els.main.querySelectorAll('.fchip').forEach(el=>{
      el.onclick = () => toggleFilter(setOf(el.dataset.fkind), el.dataset.fkey);
    });
    const fclr = els.main.querySelector('.fclear');
    if(fclr) fclr.onclick = clearFilters;
    const gord = els.main.querySelector('.gordclear');
    if(gord) gord.onclick = () => setGroupSort('activity');

    /* 그룹 끌어 옮기기 — **같은 창 안에서만** 받는다. 그룹은 창에 속해 있어서 다른 창으로
       끌어도 cmux 에서는 아무 일도 일어나지 않는데, 화면만 옮겨지면 거짓말이 된다. */
    els.main.querySelectorAll('.bgroup[data-gk]').forEach(el=>{
      const head = el.querySelector('.bgroup-head');
      if(head){
        head.ondragstart = e => {
          dragGk = el.dataset.gk; dragWin = el.closest('.bwin');
          e.dataTransfer.effectAllowed = 'move';
          e.dataTransfer.setData('text/plain', el.dataset.gk);
        };
        head.ondragend = () => { dragGk = dragWin = null;
          els.main.querySelectorAll('.bgroup').forEach(x=>x.classList.remove('dropb','dropa')); };
      }
      el.ondragover = e => {
        if(!dragGk || dragGk === el.dataset.gk) return;
        if(el.closest('.bwin') !== dragWin) return;
        e.preventDefault(); e.dataTransfer.dropEffect = 'move';
        const r = el.getBoundingClientRect(), up = e.clientY < r.top + r.height / 2;
        el.classList.toggle('dropb', up); el.classList.toggle('dropa', !up);
      };
      el.ondragleave = () => el.classList.remove('dropb','dropa');
      el.ondrop = e => {
        e.preventDefault();
        const before = el.classList.contains('dropb');
        el.classList.remove('dropb','dropa');
        const win = dragWin;
        if(dragGk && dragGk !== el.dataset.gk) moveGroup(dragGk, el.dataset.gk, before, win);
        dragGk = dragWin = null;
      };
    });
    if(els.meta){
      const c = d.counts || {};
      // 권한 대기와 질문 대기는 **내가 할 행동이 같다**(가서 답한다) → 한 숫자로 합친다.
      // 숫자를 나누면 헤더만 복잡해지고, 정작 "지금 나를 기다리는 게 몇 개냐"는 흐려진다.
      const nPerm = (c.permission || 0) + (c.question || 0);
      /* 권한 대기는 **0 일 때와 아닐 때 무게가 달라야 한다.** 0 이면 그냥 숫자지만
         1 이상이면 그게 이 화면에서 제일 중요한 사실이다 — 나머지 숫자와 같은 굵기로
         적어 두면 눈이 그냥 지나간다. 순서도 우선순위대로 권한을 맨 앞에 둔다. */
      els.meta.innerHTML =
        `<span>Claude 탭 <b>${d.total}</b>개</span>` +
        `<span class="legend">` +
          `<b class="perm${nPerm ? ' hot' : ''}">${icStatus('permission')} 막힘 ${nPerm}</b>` +
          ` · ${icStatus('running')} 작업중 ${c.running||0}` +
          ` · ${icStatus('waiting')} 대기 ${c.waiting||0}` +
          ` · ${icStatus('idle')} 유휴 ${c.idle||0}</span>` +
        (query ? `<span>${ic('search')} ‘${esc(query)}’ <b>${shown.length}</b>개</span>` : '') +
        `<span>${ago(d.generatedAt)} 갱신</span>`;
    }
    /* 브라우저 탭 제목에 권한 대기 건수를 싣는다.
       이 대시보드는 대개 **백그라운드 탭**에 띄워 둔다. 그 상태에서 "나 때문에 멈춘 세션이
       있다"를 알려면 페이지를 봐야 하는데, 그러면 알림의 의미가 없다. 메일함 안 읽은 수처럼
       탭 제목에 실어 두면 **화면을 보지 않고도** 알 수 있다.
       ⚠️ 이건 '지금 어디'가 켜져 있는 동안만 갱신된다(다른 탭에선 NAV.stop() 이 돈다). */
    setBlockedCount((d.counts || {}).permission || 0);
  }

  function setBlockedCount(n){
    const base = document.title.replace(/^\(\d+\)\s*/, '');
    document.title = n > 0 ? `(${n}) ${base}` : base;
  }

  async function refresh(fresh){
    try{
      const r = await fetch('/api/nav' + (fresh ? '?fresh=true' : ''));
      if(!r.ok) throw new Error((await r.json().catch(()=>({}))).detail || r.status);
      lastData = await r.json();
      checkVersion(lastData.assetVersion);
      render();
    }catch(e){
      if(els && els.main && !lastData)
        els.main.innerHTML = `<div class="empty">불러오기 실패: ${esc(e.message)}</div>`;
    }
  }

  /* 열어 둔 페이지가 옛 코드로 도는 걸 사용자가 알 수 있게 한다.
     로드된 자산 URL 의 ?v= 와 서버가 알려 준 최신 버전을 비교한다. */
  function myAssetV(){
    const l = document.querySelector('link[href*="board.css"], script[src*="nav.js"]');
    const m = l && (l.href || l.src || '').match(/[?&]v=(\d+)/);
    return m ? m[1] : null;
  }
  function checkVersion(v){
    if(!v) return;
    const mine = myAssetV();
    if(!mine || String(v) === mine) return;
    let el = document.getElementById('newver');
    if(!el){
      el = document.createElement('div');
      el.id = 'newver';
      el.innerHTML = '<span>대시보드가 업데이트됐습니다</span>'
        + '<button onclick="location.reload()">새로고침</button>';
      document.body.appendChild(el);
    }
    el.classList.add('show');
  }

  function bind(sel){ return typeof sel === 'string' ? document.querySelector(sel) : sel; }

  /* sticky 헤더가 카드를 덮어 클릭을 가로채는 문제 해소 — 이 화면에서만 헤더 고정을 푼다.
     (대시보드의 다른 탭은 건드리지 않도록 stop() 에서 되돌린다.) */
  function headerUnstick(on){
    document.body.classList.toggle('nav-page', !!on);
  }

  /* opts: {main, meta, idleToggle} — 셀렉터 또는 엘리먼트 */
  function start(opts){
    els = {main: bind(opts.main), meta: opts.meta ? bind(opts.meta) : null};
    const tg = opts.idleToggle ? bind(opts.idleToggle) : null;
    if(tg){
      tg.checked = showIdle;
      tg.onchange = () => {
        showIdle = tg.checked; lastHtml = null;
        localStorage.setItem('navShowIdle', showIdle ? '1' : '0');
        render();
      };
    }
    const vw = opts.viewToggle ? bind(opts.viewToggle) : null;
    if(vw){
      vw.checked = (viewMode === 'window');
      vw.onchange = () => {
        viewMode = vw.checked ? 'window' : 'status'; lastHtml = null;
        localStorage.setItem('navView', viewMode);
        render();
      };
    }
    const ag = opts.activateToggle ? bind(opts.activateToggle) : null;
    if(ag){
      ag.checked = activateApp;
      ag.onchange = () => {
        activateApp = ag.checked;
        localStorage.setItem('navActivateApp', activateApp ? '1' : '0');
      };
    }
    stop();                    // 먼저 정리(stop 이 헤더 고정을 되돌리므로 순서 주의)
    if(opts.query !== undefined) query = (opts.query || '').trim().toLowerCase();
    headerUnstick(true);
    refresh(true);
    timer = setInterval(() => { if(!document.hidden) refresh(); }, POLL_MS);
  }
  function stop(){
    // 폴링을 멈추면 그 숫자는 낡는다 — 탭 제목에 남겨 두면 거짓말이 된다
    setBlockedCount(0);
    if(timer){ clearInterval(timer); timer = null; }
    headerUnstick(false);
  }

  function setQuery(q){
    const next = (q || '').trim().toLowerCase();
    if(next === query) return;
    query = next;
    lastHtml = null;
    render();
  }

  return {start, stop, refresh, render, focus, setQuery,
          get query(){ return query; }, get data(){ return lastData; }};
})();
