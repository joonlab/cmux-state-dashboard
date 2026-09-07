/* 편집 모드 — 칸반 보드를 끌어서 **실제 cmux 배치**를 바꾼다.
   ('현재' 탭과 '지금 어디' 탭에서만 쓴다. 히스토리·복구는 지나간 스냅샷이라
    끌어봐야 바꿀 대상이 없다 — 그 탭에서는 편집 버튼을 감춘다.)

   설계에서 먼저 정한 것이 **되돌리기**다. cmux 재배치는 눈에 잘 띄지 않게 어긋날 수
   있어서(아래 두 함정), 되돌릴 수단 없이 적용부터 만들면 사용자가 "뭐가 바뀐 건지"를
   손으로 복구해야 한다. 그래서:
     · 편집에 들어서는 순간의 배치를 통째로 스냅샷해 둔다  → [원래대로]
     · 적용할 때마다 직전 배치를 스택에 쌓는다             → [되돌리기]
   되돌리기는 적용과 **같은 엔드포인트로 같은 모양의 payload** 를 보낸다. 복원 전용
   경로를 따로 만들면 그 경로만 덜 검증된 채 남는다.

   ── cmux 쪽 함정 두 가지(둘 다 실측) ──────────────────────────────────
   ① 그룹 멤버십은 groupId 가 아니라 사이드바의 **연속 위치**로 정해진다. 순서를 옮기면
      옆 그룹 앵커가 멤버를 흡수한다(2026-07-22 사고). → 서버가 순서를 바꾼 **직후**의
      멤버십을 다시 읽어 원하는 소속으로 못 박는다.
   ② `reorder-workspace --index N` 은 요청을 **조용히 클램프한다**. 고정(📌)된
      워크스페이스는 고정 블록 밖으로 못 나가고, 고정 아닌 것은 그 안으로 못 들어온다.
      그런데도 출력은 `OK`. → 적용 후 배치를 다시 읽어 대조하고, 어긋난 카드를 화면에
      표시한다. 여기서는 미리 막지 않는다(cmux 규칙을 우리가 복제하면 언젠가 어긋난다).

   창(컬럼) 순서는 cmux 에 반영하지 않는다 — 창 순서를 바꾸는 명령이 cmux 에 없다.
   대시보드 보기 순서로만 기억한다(localStorage). UI 에 그렇게 적어 둔다.
*/
window.BOARDEDIT = (function(){
  const LS_WINORDER = 'editWinOrder';
  const MAXMOVE_HINT = 40;         // 서버 상한과 같은 값(넘으면 서버가 막는다)

  let on = false;                  // 편집 모드인가
  let MODEL = null;                // 지금 화면이 보여주는 배치(작업본)
  let BASE = null;                 // 편집 진입 시점 payload — [원래대로]
  let HIST = [];                   // 적용 직전 payload 스택 — [되돌리기]
  let busy = false;
  let host = null;                 // 보드를 그릴 컨테이너
  let onExit = null;               // 편집을 나갈 때 호출(폴링 재개용)

  const esc = s => (s??'').toString().replace(/[&<>"]/g,
      c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  const say = m => (typeof window.toast === 'function') ? window.toast(m) : null;

  /* ── 모델 ────────────────────────────────────────────────────────────── */

  /* 서버의 배치(창→워크스페이스 순서 + 그룹 멤버십)를 화면 구조로 접는다.
     사이드바 순서를 훑으며 **연속된 같은 그룹**을 한 블록으로 묶는다 — cmux 가
     멤버십을 판정하는 방식과 같게 접어야, 화면에서 옮긴 결과가 cmux 에서도 같아진다. */
  function buildModel(arr){
    const ws = {};
    const wins = arr.windows.map(w=>{
      const gmap = {}; (w.groups||[]).forEach(g=>gmap[g.id]=g);
      const blocks = []; let cur = null;
      (w.workspaces||[]).forEach(x=>{
        ws[x.id] = Object.assign({}, x, {windowId: w.id});
        const g = x.groupId ? gmap[x.groupId] : null;
        if(g){
          if(cur && cur.gid === g.id) cur.members.push(x.id);
          else { cur = {type:'group', gid:g.id, members:[x.id]}; blocks.push(cur); }
        } else {
          if(cur && cur.type === 'plain') cur.members.push(x.id);
          else { cur = {type:'plain', gid:null, members:[x.id]}; blocks.push(cur); }
        }
      });
      return {id:w.id, ref:w.ref, title:w.title, index:w.index,
              groups:gmap, blocks, groupWarning:w.groupWarning,
              selectedWorkspaceId:w.selectedWorkspaceId};
    });
    // 컬럼 순서는 대시보드 보기 설정 — 기억해 둔 순서를 먼저, 모르는 창은 창 번호순으로 뒤에
    const saved = readWinOrder();
    wins.sort((a,b)=>{
      const ia = saved.indexOf(a.id), ib = saved.indexOf(b.id);
      if(ia !== -1 && ib !== -1) return ia - ib;
      if(ia !== -1) return -1;
      if(ib !== -1) return 1;
      return (a.index??99) - (b.index??99);
    });
    return {wins, ws, activeWindowRef: arr.activeWindowRef, capturedAt: arr.capturedAt};
  }

  function readWinOrder(){
    try{ return JSON.parse(localStorage.getItem(LS_WINORDER) || '[]'); }
    catch(e){ return []; }
  }
  function saveWinOrder(){
    try{ localStorage.setItem(LS_WINORDER, JSON.stringify(MODEL.wins.map(w=>w.id))); }
    catch(e){}
  }

  /* 모델 → 서버가 받는 payload.
     그룹은 **멤버가 없어도 전부** 보낸다. 안 보내면 서버가 "그 그룹은 클라이언트가
     몰랐다"와 "그 그룹을 비웠다"를 구분할 수 없다. */
  function payload(model){
    const windows = model.wins.map(w=>({
      id: w.id, order: w.blocks.reduce((a,b)=>a.concat(b.members), [])
    }));
    const groups = [];
    model.wins.forEach(w=>{
      const seen = {};
      w.blocks.forEach(b=>{ if(b.type==='group') seen[b.gid] = (seen[b.gid]||[]).concat(b.members); });
      Object.keys(w.groups).forEach(gid=>groups.push({id:gid, members: seen[gid] || []}));
    });
    return {windows, groups};
  }

  /* `cur` 를 `want` 로 만드는 **최소 이동 횟수**. 서버 `_plan_moves` 와 같은 계산이다.
     "몇 장이 자리가 바뀌었나"가 아니라 "cmux 를 몇 번 건드리나"를 세야 한다 —
     한 장을 앞으로 끌면 뒤의 세 장이 밀리는데, 그걸 '4개 변경'으로 보여 주면 사용자가
     자기가 한 것보다 큰 일이 벌어진다고 읽는다. */
  function minMoves(cur, want){
    const sim1 = cur.slice(), g = [];
    want.forEach((id, idx)=>{
      if(sim1[idx] === id) return;
      const j = sim1.indexOf(id);
      if(j < 0) return;
      sim1.splice(j,1); sim1.splice(idx,0,id); g.push(id);
    });
    // LCS 를 그대로 두는 쪽이 대개 더 적다
    const n = cur.length, m = want.length;
    const dp = Array.from({length:n+1}, ()=>new Int32Array(m+1));
    for(let i=n-1;i>=0;i--) for(let j=m-1;j>=0;j--)
      dp[i][j] = cur[i]===want[j] ? dp[i+1][j+1]+1 : Math.max(dp[i+1][j], dp[i][j+1]);
    const keep = new Set();
    let i=0, j=0;
    while(i<n && j<m){
      if(cur[i]===want[j]){ keep.add(cur[i]); i++; j++; }
      else if(dp[i+1][j] >= dp[i][j+1]) i++;
      else j++;
    }
    const sim2 = cur.slice(); let c2 = 0;
    want.forEach((id, idx)=>{
      if(keep.has(id)) return;
      const pos = sim2.indexOf(id);
      if(pos < 0) return;
      let target = idx === 0 ? 0 : sim2.indexOf(want[idx-1]) + 1;
      if(pos < target) target--;
      if(pos === target) return;
      sim2.splice(pos,1); sim2.splice(target,0,id); c2++;
    });
    const ok2 = sim2.length===want.length && sim2.every((x,k)=>x===want[k]);
    return ok2 ? Math.min(g.length, c2) : g.length;
  }

  /* 바뀐 것 세기 — "적용" 이 무엇을 건드릴지 사용자가 누르기 전에 알 수 있게. */
  function diffCount(){
    if(!BASE || !MODEL) return {moved:0, regrouped:0, crossWindow:0};
    const baseWin = {}, baseGid = {};
    BASE.windows.forEach(w=>w.order.forEach(id=>baseWin[id] = w.id));
    BASE.groups.forEach(g=>g.members.forEach(id=>baseGid[id] = g.id));
    const now = payload(MODEL);
    let moved = 0, regrouped = 0, crossWindow = 0;
    const baseOrder = {};
    BASE.windows.forEach(w=>baseOrder[w.id] = w.order);
    now.windows.forEach(w=>{
      const before = (baseOrder[w.id] || []).filter(id=>w.order.indexOf(id) !== -1);
      const after  = w.order.filter(id=>before.indexOf(id) !== -1);
      moved += minMoves(before, after);
      w.order.forEach(id=>{ if(baseWin[id] && baseWin[id] !== w.id) crossWindow++; });
    });
    const nowGid = {};
    now.groups.forEach(g=>g.members.forEach(id=>nowGid[id] = g.id));
    now.windows.forEach(w=>w.order.forEach(id=>{
      if((baseGid[id]||null) !== (nowGid[id]||null)) regrouped++;
    }));
    return {moved, regrouped, crossWindow};
  }

  /* 편집 진입 시점 대비 자리가 바뀐 카드 — 화면에서 눈에 띄게 한다 */
  function movedSet(){
    const out = new Set();
    if(!BASE) return out;
    const base = {};
    BASE.windows.forEach(w=>w.order.forEach((id,i)=>base[id]={win:w.id, idx:i}));
    const now = payload(MODEL);
    now.windows.forEach(w=>w.order.forEach((id,i)=>{
      const a = base[id];
      if(a && (a.win !== w.id || a.idx !== i)) out.add(id);
    }));
    return out;
  }

  /* ── 렌더 ────────────────────────────────────────────────────────────── */

  let CLASH = new Set();    // 마지막 적용에서 cmux 가 다르게 놓은 워크스페이스

  function render(){
    if(!host || !MODEL) return;
    const moved = movedSet();
    const d = diffCount();
    const nMove = d.moved + d.crossWindow;
    const dirty = nMove > 0 || d.regrouped > 0;

    const bar = `<div id="editbar">
      <span class="etitle">${ic('pencil')} 편집 모드</span>
      <span class="ecount">바뀐 자리 <b>${nMove}</b>${
        d.crossWindow?` <span class="muted">(창 간 ${d.crossWindow})</span>`:''} ·
        소속 변경 <b>${d.regrouped}</b></span>
      <span class="spacer"></span>
      <button class="primary" id="eApply" ${(!dirty||busy)?'disabled':''}>적용</button>
      <button id="eUndo" ${(!HIST.length||busy)?'disabled':''}>되돌리기${
        HIST.length?` (${HIST.length})`:''}</button>
      <button id="eReset" ${(!dirty||busy)?'disabled':''}>원래대로</button>
      <button id="eReload" ${busy?'disabled':''}>새로 읽기</button>
      <button id="eExit" ${busy?'disabled':''}>편집 끝내기</button>
      ${nMove > MAXMOVE_HINT ? `<span class="warn">${ic('triangle-alert')} 한 번에 ${nMove}장이 움직입니다 —
        서버 상한(${MAXMOVE_HINT})을 넘으면 거부됩니다. 나눠서 적용하세요.</span>` : ''}
      <span class="warn">폴링을 멈춰 두었습니다 · 창(컬럼) 순서는 대시보드 보기 순서로만
        저장됩니다(cmux 에 창 순서를 바꾸는 명령이 없습니다) · ${ic('pin',{cls:'pin'})} 고정은 cmux 가 고정 블록
        밖으로 못 나가게 막습니다</span>
    </div>`;

    const cols = MODEL.wins.map(w=>{
      const n = w.blocks.reduce((a,b)=>a+b.members.length, 0);
      const body = w.blocks.map((b, bi)=>{
        const g = b.type==='group' ? w.groups[b.gid] : null;
        const cards = b.members.map(id=>card(MODEL.ws[id], b, moved)).join('');
        if(!g) return `<div class="eblock plain" data-kind="block" data-win="${esc(w.id)}"
            data-bi="${bi}"><div class="eblock-body" data-drop="cards"
            data-win="${esc(w.id)}" data-bi="${bi}">${cards}</div></div>`;
        return `<div class="eblock" data-kind="block" data-win="${esc(w.id)}" data-bi="${bi}"
            style="border-left-color:${(g.color ? esc(g.color) : 'var(--group-fallback)')}">
          <div class="eblock-head" data-handle="block">
            <span class="grip">${ic('grip-vertical')}</span><span class="gic">${icGroup(g.icon)}</span>
            <b>${esc(g.name||'(그룹)')}</b>${g.pinned?`<span>${ic('pin',{cls:'pin',title:'고정'})}</span>`:''}
            <span class="n">${b.members.length}</span>
          </div>
          <div class="eblock-body" data-drop="cards" data-win="${esc(w.id)}"
               data-bi="${bi}">${cards}</div>
        </div>`;
      }).join('');
      return `<div class="ecol" data-kind="col" data-win="${esc(w.id)}">
        <div class="ecol-head" data-handle="col">
          <span class="grip">${ic('grip-vertical')}</span><span class="wt">${ic('app-window')} ${esc(w.title)}${
            w.ref===MODEL.activeWindowRef?' <span class="btag on">활성</span>':''}</span>
          <span class="wn">WS ${n}</span>
        </div>
        ${w.groupWarning ? `<div class="warn" style="font-size:11px;color:var(--amber);margin-bottom:6px">
            ${ic('triangle-alert')} 그룹을 읽지 못했습니다: ${esc(w.groupWarning)}</div>` : ''}
        <div class="ecol-body" data-drop="blocks" data-win="${esc(w.id)}">${body}</div>
      </div>`;
    }).join('');

    host.innerHTML = bar
      + `<div id="editresultslot"></div><div class="eboard" id="eboard">${cols}</div>`;
    if(LASTRESULT) paintResult(LASTRESULT);
    wire();
  }


  function card(x, block, moved){
    if(!x) return '';
    const isAnchor = x.isAnchor && block.type==='group';
    const cls = ['ecard'];
    if(isAnchor) cls.push('anchor');
    if(x.pinned) cls.push('pinned');
    if(moved.has(x.id)) cls.push('moved');
    if(CLASH.has(x.id)) cls.push('clash');
    if(x.selected) cls.push('selectedws');
    const t = String(x.title||'(제목 없음)').replace(/^[◐◑◒◓◔◕✳✻✽✢·\s]+/,'').trim() || '(제목 없음)';
    return `<div class="${cls.join(' ')}" data-kind="card" data-id="${esc(x.id)}"
        ${isAnchor?'data-anchor="1"':''} title="${esc(
          (isAnchor?'그룹 앵커 — 끌 수 없습니다(앵커가 빠지면 그룹이 흩어집니다)\n':'')
          + (x.pinned?'고정 — cmux 가 고정 블록 밖으로 못 나가게 막습니다\n':'')
          + t + '\n' + (x.ref||''))}">
      <span class="grip">${isAnchor?ic('anchor'):ic('grip-vertical')}</span>
      <span class="et">${esc(t)}</span>
      <button class="epin${x.pinned?' on':''}" data-id="${esc(x.id)}"
        data-pinned="${x.pinned?'1':'0'}"
        title="${x.pinned?'고정 해제 — 고정을 풀어야 이 카드를 고정 블록 밖으로 옮길 수 있습니다'
                        :'고정 — cmux 가 이 카드를 고정 블록 안에 묶어 둡니다'}">${ic('pin',{cls:'pin'})}</button>
      ${x.surfaces>1?`<span class="eb">${ic('layers')}${x.surfaces}</span>`:''}
      <span class="eb">${esc(String(x.ref||'').replace('workspace:',''))}</span>
    </div>`;
  }

  /* ── 끌기 ────────────────────────────────────────────────────────────── */

  let drag = null;   // {kind, el, ghost, from:{...}}

  function wire(){
    host.querySelectorAll('#eApply,#eUndo,#eReset,#eReload,#eExit').forEach(b=>{
      b.onclick = () => ({eApply:apply, eUndo:undo, eReset:reset,
                          eReload:reload, eExit:exit})[b.id]();
    });
    host.querySelectorAll('.ecard:not([data-anchor])').forEach(el=>{
      el.addEventListener('pointerdown', e=>start(e, 'card', el));
    });
    /* 고정 토글. ⚠️ pointerdown 을 여기서 끊지 않으면 버튼을 누르는 순간 카드 드래그가
       시작된다(드래그는 pointerdown 으로 무장한다). 고정은 편집 모드에서 특히 필요하다 —
       cmux 가 고정된 워크스페이스를 고정 블록 밖으로 못 나가게 막기 때문에(클램프),
       옮기려면 먼저 여기서 풀어야 한다. */
    host.querySelectorAll('.epin').forEach(b=>{
      b.addEventListener('pointerdown', e=>{ e.stopPropagation(); });
      b.addEventListener('click', e=>{
        e.stopPropagation();
        togglePin(b.dataset.id, b.dataset.pinned === '1');
      });
    });
    host.querySelectorAll('.eblock-head[data-handle="block"]').forEach(h=>{
      h.addEventListener('pointerdown', e=>start(e, 'block', h.closest('.eblock')));
    });
    host.querySelectorAll('.ecol-head[data-handle="col"]').forEach(h=>{
      h.addEventListener('pointerdown', e=>start(e, 'col', h.closest('.ecol')));
    });
  }

  function start(e, kind, el){
    if(e.button !== 0 || drag || busy) return;
    e.preventDefault();
    const sx = e.clientX, sy = e.clientY;
    let armed = false;
    const move = ev=>{
      if(!armed){
        if(Math.abs(ev.clientX-sx) + Math.abs(ev.clientY-sy) < 5) return;
        armed = true;
        begin(kind, el, ev);
      }
      if(drag) over(ev);
    };
    const up = ev=>{
      document.removeEventListener('pointermove', move);
      document.removeEventListener('pointerup', up);
      document.removeEventListener('pointercancel', up);
      if(drag) finish(ev);
    };
    document.addEventListener('pointermove', move);
    document.addEventListener('pointerup', up);
    document.addEventListener('pointercancel', up);
  }

  function begin(kind, el, ev){
    const r = el.getBoundingClientRect();
    const ghost = el.cloneNode(true);
    ghost.id = 'eghost';
    ghost.style.width = r.width + 'px';
    ghost.style.left = r.left + 'px';
    ghost.style.top = r.top + 'px';
    document.body.appendChild(ghost);
    document.body.classList.add('grabbing');
    el.classList.add('dragging');
    drag = {kind, el, ghost, dx: ev.clientX - r.left, dy: ev.clientY - r.top, target: null};
  }

  function over(ev){
    drag.ghost.style.left = (ev.clientX - drag.dx) + 'px';
    drag.ghost.style.top  = (ev.clientY - drag.dy) + 'px';
    autoScroll(ev);
    clearIndicator();
    drag.target = findTarget(ev);
    if(drag.target) showIndicator(drag.target);
  }

  /* 보드가 가로로 길고 컬럼이 세로로 길다 — 가장자리에 가면 따라 스크롤해 준다.
     이게 없으면 화면 밖 창으로는 아예 끌어다 놓을 수가 없다. */
  function autoScroll(ev){
    const b = document.getElementById('eboard');
    if(b){
      const r = b.getBoundingClientRect(), edge = 70;
      if(ev.clientX < r.left + edge)  b.scrollLeft -= Math.max(6, (r.left + edge - ev.clientX)/3);
      if(ev.clientX > r.right - edge) b.scrollLeft += Math.max(6, (ev.clientX - r.right + edge)/3);
    }
    const edge = 80;
    if(ev.clientY < edge) window.scrollBy(0, -Math.max(6, (edge - ev.clientY)/3));
    if(ev.clientY > window.innerHeight - edge)
      window.scrollBy(0, Math.max(6, (ev.clientY - window.innerHeight + edge)/3));
  }

  /* 커서 위치에서 "어디에 끼울지"를 고른다. 카드는 카드 목록 사이, 블록은 블록 사이,
     컬럼은 컬럼 사이. 세로 목록은 중점보다 위/아래로, 가로(컬럼)는 좌/우로 가른다. */
  function findTarget(ev){
    const kind = drag.kind;
    if(kind === 'col'){
      const cols = [...host.querySelectorAll('.ecol')];
      let idx = cols.length;
      for(let i=0;i<cols.length;i++){
        const r = cols[i].getBoundingClientRect();
        if(ev.clientX < r.left + r.width/2){ idx = i; break; }
      }
      return {kind:'col', idx};
    }
    const sel = kind === 'card' ? '[data-drop="cards"]' : '[data-drop="blocks"]';
    const zones = [...host.querySelectorAll(sel)];
    let zone = null, best = Infinity;
    for(const z of zones){
      const r = z.getBoundingClientRect();
      const inX = ev.clientX >= r.left - 12 && ev.clientX <= r.right + 12;
      if(!inX) continue;
      // 세로로는 가장 가까운 영역(빈 영역도 잡히도록 거리로 고른다)
      const dy = ev.clientY < r.top ? r.top - ev.clientY
               : ev.clientY > r.bottom ? ev.clientY - r.bottom : 0;
      if(dy < best){ best = dy; zone = z; }
    }
    if(!zone || best > 90) return null;
    const items = [...zone.children].filter(c=>!c.classList.contains('edrop'));
    let idx = items.length;
    for(let i=0;i<items.length;i++){
      const r = items[i].getBoundingClientRect();
      if(ev.clientY < r.top + r.height/2){ idx = i; break; }
    }
    return {kind, zone, idx, win: zone.dataset.win,
            bi: zone.dataset.bi !== undefined ? +zone.dataset.bi : null};
  }

  function clearIndicator(){
    host.querySelectorAll('.edrop').forEach(e=>e.remove());
  }
  function showIndicator(t){
    const ind = document.createElement('div');
    ind.className = 'edrop' + (t.kind==='col' ? ' col' : '');
    if(t.kind === 'col'){
      const b = document.getElementById('eboard');
      const cols = [...b.querySelectorAll('.ecol')];
      b.insertBefore(ind, cols[t.idx] || null);
    } else {
      const items = [...t.zone.children].filter(c=>!c.classList.contains('edrop'));
      t.zone.insertBefore(ind, items[t.idx] || null);
    }
  }

  function finish(){
    const t = drag.target;
    clearIndicator();
    drag.ghost.remove();
    drag.el.classList.remove('dragging');
    document.body.classList.remove('grabbing');
    const kind = drag.kind, el = drag.el;
    drag = null;
    if(!t){ render(); return; }
    if(kind === 'col')       moveCol(el.dataset.win, t.idx);
    else if(kind === 'block') moveBlock(el.dataset.win, +el.dataset.bi, t.win, t.idx);
    else                      moveCard(el.dataset.id, t.win, t.bi, t.idx);
    normalize();
    render();
  }

  const winOf = id => MODEL.wins.find(w=>w.id === id);

  function moveCol(winId, idx){
    const i = MODEL.wins.findIndex(w=>w.id === winId);
    if(i < 0) return;
    const [w] = MODEL.wins.splice(i, 1);
    MODEL.wins.splice(i < idx ? idx-1 : idx, 0, w);
    saveWinOrder();
  }

  function moveBlock(fromWin, bi, toWin, idx){
    const a = winOf(fromWin), b = winOf(toWin);
    if(!a || !b) return;
    const blk = a.blocks[bi];
    if(!blk) return;
    /* ⚠️ cmux 그룹은 **창에 매여 있다** — `workspace.group.list` 가 창 단위이고 그룹 id 도
       그 창 것이다. 그룹째로 다른 창에 떨어뜨리면 그 그룹 id 를 남의 창 워크스페이스에
       붙이는 셈이라, 어떻게 되는지 확인되지 않은 길로 들어간다. 확인 안 된 길로 사용자
       배치를 밀어넣지 않는다 — 대신 무엇을 하면 되는지 알려 준다. */
    if(a !== b && blk.type === 'group'){
      say('그룹은 창을 건너 옮길 수 없습니다 (cmux 그룹은 창에 매여 있습니다) — 카드를 하나씩 옮기세요');
      return;
    }
    a.blocks.splice(bi, 1);
    if(a === b && bi < idx) idx--;
    b.blocks.splice(idx, 0, blk);
    if(a !== b) blk.members.forEach(id=>{ if(MODEL.ws[id]) MODEL.ws[id].windowId = b.id; });
  }

  function moveCard(id, toWin, toBi, idx){
    // 원래 자리에서 뺀다
    let from = null;
    for(const w of MODEL.wins) for(const b of w.blocks){
      const i = b.members.indexOf(id);
      if(i >= 0){ from = {w, b, i}; break; }
    }
    if(!from) return;
    const to = winOf(toWin);
    if(!to) return;
    const tb = to.blocks[toBi];
    if(!tb) return;
    if(tb.type === 'group' && idx === 0){
      // 앵커는 언제나 그룹의 머리다 — 그 앞으로는 못 끼운다
      const anchorFirst = MODEL.ws[tb.members[0]] && MODEL.ws[tb.members[0]].isAnchor;
      if(anchorFirst) idx = 1;
    }
    from.b.members.splice(from.i, 1);
    if(from.b === tb && from.i < idx) idx--;
    tb.members.splice(idx, 0, id);
    if(MODEL.ws[id]) MODEL.ws[id].windowId = to.id;
  }

  /* 끌기 결과로 생긴 자투리를 정리한다.
     ⚠️ 같은 그룹이 두 블록으로 갈라지면 안 된다 — cmux 는 **연속된 자리**만 한 그룹으로
        보므로, 떨어진 두 덩이는 저장할 수 없는 상태다. 이어 붙여 하나로 만든다. */
  function normalize(){
    MODEL.wins.forEach(w=>{
      w.blocks = w.blocks.filter(b=>b.members.length);
      const seen = {};
      const out = [];
      w.blocks.forEach(b=>{
        if(b.type === 'group' && seen[b.gid]){
          seen[b.gid].members = seen[b.gid].members.concat(b.members);
          return;
        }
        if(b.type === 'group') seen[b.gid] = b;
        // 이웃한 plain 끼리는 합친다(경계가 늘어나면 드롭 지점만 헷갈린다)
        const last = out[out.length-1];
        if(b.type === 'plain' && last && last.type === 'plain'){
          last.members = last.members.concat(b.members);
          return;
        }
        out.push(b);
      });
      w.blocks = out;
      // 그룹 블록 안에서 앵커는 항상 맨 앞
      w.blocks.forEach(b=>{
        if(b.type !== 'group') return;
        const ai = b.members.findIndex(id=>MODEL.ws[id] && MODEL.ws[id].isAnchor);
        if(ai > 0){ const [a] = b.members.splice(ai,1); b.members.unshift(a); }
      });
      // 빈 창에도 놓을 자리가 있어야 하므로 빈 plain 블록 하나는 남긴다
      if(!w.blocks.length) w.blocks = [{type:'plain', gid:null, members:[]}];
    });
  }

  /* ── 서버 반영 ───────────────────────────────────────────────────────── */

  let LASTRESULT = null;

  async function post(path, body){
    const r = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'},
                                body: JSON.stringify(body)});
    const j = await r.json().catch(()=>({}));
    if(!r.ok) throw new Error(j.detail || ('HTTP ' + r.status));
    return j;
  }

  function markClashes(j){
    CLASH = new Set();
    (j.clamped||[]).forEach(c=>CLASH.add(c.workspace));
    (j.mismatches||[]).forEach(m=>{
      (m.wanted||[]).forEach((id,i)=>{ if((m.actual||[])[i] !== id) CLASH.add(id); });
      (m.missing||[]).concat(m.extra||[]).forEach(id=>CLASH.add(id));
    });
  }

  /* 적용과 되돌리기가 **같은 경로**를 쓴다. 복원 전용 경로를 따로 두면 그쪽만 덜 검증된
     채 남는데, 하필 그게 무언가 잘못됐을 때 쓰는 길이다.

     ⚠️ 되돌릴 지점은 `payload(MODEL)`(= 방금 끈 결과)가 **아니라** `BASE`(= 적용 직전에
        cmux 가 실제로 갖고 있던 배치)다. MODEL 을 쌓으면 되돌리기가 방금 적용한 것과
        똑같은 배치를 다시 보내 아무 일도 안 일어난다(실측). */
  async function send(desired, label, keepHistory){
    busy = true; render();
    const before = BASE;
    try{
      const j = await post('/api/cmux/arrange', Object.assign({dryRun:false}, desired));
      if(keepHistory && before){
        HIST.push(before);
        if(HIST.length > 20) HIST.shift();
      }
      adopt(j.arrangement);
      markClashes(j);
      LASTRESULT = Object.assign({}, j, {label});
      say(`${label} 완료`);
      return true;
    }catch(e){
      LASTRESULT = {label, error: e.message};
      say(`${label} 실패: ${e.message}`);
      await reload(true);
      return false;
    }finally{
      busy = false; render();
    }
  }

  /* 서버가 돌려준 '적용 후 실제 배치'를 화면의 정본으로 삼는다.
     화면 모델을 그대로 두면 cmux 가 클램프한 결과가 화면에 안 보여, 사용자가 원하는
     대로 된 줄 안다. 실제로 된 것을 보여 주고, 어긋난 카드에 표시를 남긴다. */
  function adopt(arr){
    if(!arr) return;
    MODEL = buildModel(arr);
    normalize();
    BASE = payload(MODEL);
  }

  async function apply(){
    const d = diffCount();
    if(!d.moved && !d.regrouped && !d.crossWindow){ say('바뀐 게 없습니다'); return; }
    await send(payload(MODEL), '적용', true);
  }

  async function undo(){
    const prev = HIST.pop();
    if(!prev) return;
    const ok = await send(prev, '되돌리기', false);
    if(!ok) HIST.push(prev);                  // 실패했으면 되돌릴 지점을 잃지 않는다
  }

  /* 아직 cmux 에 안 보낸 편집을 편집 진입 시점으로 되감는다(cmux 는 안 건드린다). */
  function reset(){
    if(!BASE) return;
    applyPayloadToModel(BASE);
    CLASH = new Set();
    render();
    say('편집 시작 시점으로 되돌렸습니다 (cmux 는 아직 안 건드렸습니다)');
  }

  function applyPayloadToModel(p){
    const gByWs = {};
    p.groups.forEach(g=>g.members.forEach(id=>gByWs[id]=g.id));
    const byWin = {};
    MODEL.wins.forEach(w=>byWin[w.id]=w);
    p.windows.forEach(pw=>{
      const w = byWin[pw.id];
      if(!w) return;
      const blocks = []; let cur = null;
      pw.order.forEach(id=>{
        const gid = gByWs[id] || null;
        if(MODEL.ws[id]) MODEL.ws[id].windowId = w.id;
        if(gid){
          if(cur && cur.gid === gid) cur.members.push(id);
          else { cur = {type:'group', gid, members:[id]}; blocks.push(cur); }
        } else {
          if(cur && cur.type === 'plain') cur.members.push(id);
          else { cur = {type:'plain', gid:null, members:[id]}; blocks.push(cur); }
        }
      });
      w.blocks = blocks;
    });
    normalize();
  }

  /* 고정/해제. 서버에는 원하는 최종 상태를 보내고(토글은 화면이 낡았을 때 반대로 뒤집힌다),
     끝나면 배치를 다시 읽는다 — 고정 상태가 바뀌면 cmux 가 허용하는 위치도 함께 바뀐다. */
  async function togglePin(wsid, pinned){
    if(!wsid || busy) return;
    const want = !pinned;
    busy = true; render();
    try{
      const j = await post('/api/cmux/pin', {workspace: wsid, pinned: want});
      if(j.verified === false)
        say(`고정 상태가 반영되지 않았습니다 — 지금 ${j.pinned ? '고정됨' : '해제됨'}`);
      else say(want ? '고정했습니다' : '고정을 해제했습니다');
      /* ⚠️ **요청한 값이 아니라 서버가 확인해 준 실제 값**을 쓴다. 요청값으로 갱신하면
         반영이 안 됐을 때도 화면만 고정으로 바뀌어, 이 화면이 내내 경고해 온 바로 그
         상태(화면과 cmux 가 다른데 성공처럼 보임)를 스스로 만든다.
         배치를 통째로 다시 읽지 않는 이유는 아직 안 보낸 편집이 날아가기 때문 —
         고정 상태 한 칸만 갈아 끼우고 순서·소속은 사용자가 만든 대로 남긴다. */
      if(MODEL && MODEL.ws[wsid])
        MODEL.ws[wsid].pinned = (typeof j.pinned === 'boolean') ? j.pinned : want;
    }catch(e){
      // 실패했으면 화면을 건드리지 않는다 — 여전히 cmux 의 상태가 정본이다
      say(`고정 변경 실패: ${e.message}`);
    }
    busy = false;
    render();
  }

  async function reload(quiet){
    busy = true;
    try{
      const r = await fetch('/api/cmux/arrangement', {cache:'no-store'});
      const arr = await r.json();
      if(!r.ok) throw new Error(arr.detail || r.status);
      adopt(arr);
      if(!quiet) say('cmux 배치를 다시 읽었습니다');
    }catch(e){
      say(`배치를 읽지 못했습니다: ${e.message}`);
    }finally{
      busy = false;
      render();
    }
  }

  /* ── 결과 표시 ───────────────────────────────────────────────────────── */

  function wsName(id){
    const x = MODEL && MODEL.ws[id];
    return x ? String(x.title||id).replace(/^[◐◑◒◓◔◕✳✻✽✢·\s]+/,'').trim() : id.slice(0,8);
  }

  function paintResult(j){
    const slot = document.getElementById('editresultslot');
    if(!slot) return;
    const L = [];
    if(j.error){
      L.push(`<h4>${ic('circle-x')} ${esc(j.label)} 실패</h4><div>${esc(j.error)}</div>
        <div class="muted">cmux 의 실제 배치를 다시 읽어 화면에 반영했습니다.</div>`);
      slot.innerHTML = `<div id="editresult" class="bad"><span class="x">✕</span>${L.join('')}</div>`;
    } else {
      const bad = (j.clamped||[]).length + (j.mismatches||[]).length + (j.warnings||[]).length;
      L.push(`<h4>${bad?ic('triangle-alert'):ic('circle-check')} ${esc(j.label)} — ${esc((j.steps||[]).length)}개 조작${
        bad?`, <b>${bad}곳이 요청과 다릅니다</b>`:''}</h4>`);
      if((j.warnings||[]).length){
        L.push('<ul>');
        j.warnings.slice(0,8).forEach(w=>L.push(`<li>${esc(w)}</li>`));
        if(j.warnings.length>8) L.push(`<li class="muted">외 ${j.warnings.length-8}건</li>`);
        L.push('</ul>');
      }
      if((j.clamped||[]).length){
        L.push(`<div>cmux 가 위치를 조정했습니다 <span class="muted">(고정 워크스페이스
          경계 때문입니다 — cmux 규칙상 그 자리에는 놓을 수 없습니다)</span></div><ul>`);
        j.clamped.slice(0,8).forEach(c=>L.push(
          `<li>${esc(wsName(c.workspace))} — ${c.wanted}번 자리 요청 → ${c.planned}번에 놓임</li>`));
        if(j.clamped.length>8) L.push(`<li class="muted">외 ${j.clamped.length-8}건</li>`);
        L.push('</ul>');
      }
      (j.mismatches||[]).forEach(m=>{
        if(m.kind === 'order'){
          L.push(`<div>${ic('app-window')} ${esc(m.title||m.id.slice(0,8))} — ${esc(m.detail)}</div>`);
        } else if(m.kind === 'group'){
          L.push(`<div>${ic('folder')} ${esc(m.title||m.id.slice(0,8))} — ${esc(m.detail)}</div><ul>`);
          (m.missing||[]).slice(0,6).forEach(id=>L.push(`<li>빠짐: ${esc(wsName(id))}</li>`));
          (m.extra||[]).slice(0,6).forEach(id=>L.push(`<li>끼어듦: ${esc(wsName(id))}</li>`));
          L.push('</ul>');
        } else {
          L.push(`<div>${esc(m.detail)}</div>`);
        }
      });
      if(!bad) L.push(`<div class="muted">cmux 의 실제 배치와 화면이 일치합니다(적용 후 다시 읽어 대조).</div>`);
      slot.innerHTML = `<div id="editresult" class="${bad?'bad':'ok'}">
        <span class="x">✕</span>${L.join('')}</div>`;
    }
    const x = slot.querySelector('.x');
    if(x) x.onclick = ()=>{ LASTRESULT = null; slot.innerHTML = ''; };
  }

  /* ── 진입/종료 ───────────────────────────────────────────────────────── */

  async function enter(opts){
    opts = opts || {};
    host = typeof opts.host === 'string' ? document.querySelector(opts.host) : opts.host;
    onExit = opts.onExit || null;
    if(!host) return;
    on = true;
    document.body.classList.add('editing');
    HIST = []; CLASH = new Set(); LASTRESULT = null;
    host.innerHTML = '<div class="empty">cmux 배치를 읽는 중…</div>';
    await reload(true);
  }

  /* silent=true 면 onExit 을 부르지 않는다 — 탭을 옮기느라 호출부가 이미 다시 그릴
     참이라, 여기서 또 그리면 화면이 두 번 갈리고 호출이 서로를 부른다. */
  function exit(silent){
    on = false;
    document.body.classList.remove('editing', 'grabbing');
    clearIndicator();
    const g = document.getElementById('eghost'); if(g) g.remove();
    MODEL = BASE = null; HIST = []; LASTRESULT = null;
    if(onExit && !silent) onExit();
    onExit = null;
  }

  return {enter, exit, render, reload,
          get active(){ return on; },
          get busy(){ return busy; }};
})();
