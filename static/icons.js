/* ============================================================================
   icons.js — self-host 한 lucide 아이콘 + 이름 매핑
   ----------------------------------------------------------------------------
   왜 이렇게 만들었나 (2026-08-31):

   1) **self-host.** CDN 을 걸면 cmux 가 오프라인이거나 CDN 이 흔들릴 때 화면에서
      아이콘만 사라진다. 이 대시보드는 "cmux 가 이상할 때" 보려고 만든 물건이라
      그 순간 외부 의존이 살아 있으리라 가정하면 안 된다. 그래서 lucide 패키지에서
      **쓰는 아이콘만** 뽑아 이 파일에 박았다(46개 · 8.5KB).

   2) **문자열 템플릿에 맞췄다.** 이 코드베이스의 렌더는 전부 `${...}` 조립이다.
      웹컴포넌트나 <use> 스프라이트를 쓰면 렌더 경로를 다 뜯어야 하므로,
      `ic('pin')` 이 **SVG 마크업 문자열**을 돌려주는 형태로 만든다.

   3) **색은 currentColor.** 아이콘이 부모 글자색을 따라가므로 라이트/다크 전환에
      아이콘 쪽 대응이 따로 필요 없다. 크기는 CSS(.lic{width:1em}) 가 정한다.

   ⚠️ 서버는 이제 아이콘을 **만들지 않는다.** 예전에는 nav.py `_GICON` 이 cmux 의
      SF Symbol 이름(gearshape.2.fill …)을 이모지로 바꿔 보냈고, 같은 표가
      index.html 과 edit.js 에도 복사돼 **총 3벌**이었다. 지금은 서버가 심볼 이름을
      그대로 주고 매핑은 여기(SYMBOL) 한 곳에서만 한다.

   lucide v1.11.0 — ISC License. https://lucide.dev
   ========================================================================== */

const LUCIDE =
{
  "anchor":[["path",{"d":"M12 6v16"}],["path",{"d":"m19 13 2-1a9 9 0 0 1-18 0l2 1"}],["path",{"d":"M9 11h6"}],["circle",{"cx":"12","cy":"4","r":"2"}]],
  "app-window":[["rect",{"x":"2","y":"4","width":"20","height":"16","rx":"2"}],["path",{"d":"M10 4v4"}],["path",{"d":"M2 8h20"}],["path",{"d":"M6 4v4"}]],
  "book":[["path",{"d":"M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H19a1 1 0 0 1 1 1v18a1 1 0 0 1-1 1H6.5a1 1 0 0 1 0-5H20"}]],
  "bot":[["path",{"d":"M12 8V4H8"}],["rect",{"width":"16","height":"12","x":"4","y":"8","rx":"2"}],["path",{"d":"M2 14h2"}],["path",{"d":"M20 14h2"}],["path",{"d":"M15 13v2"}],["path",{"d":"M9 13v2"}]],
  "briefcase":[["path",{"d":"M16 20V4a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"}],["rect",{"width":"20","height":"14","x":"2","y":"6","rx":"2"}]],
  "chart-column":[["path",{"d":"M3 3v16a2 2 0 0 0 2 2h16"}],["path",{"d":"M18 17V9"}],["path",{"d":"M13 17V5"}],["path",{"d":"M8 17v-3"}]],
  "circle":[["circle",{"cx":"12","cy":"12","r":"10"}]],
  "circle-check":[["circle",{"cx":"12","cy":"12","r":"10"}],["path",{"d":"m9 12 2 2 4-4"}]],
  "circle-dot":[["circle",{"cx":"12","cy":"12","r":"10"}],["circle",{"cx":"12","cy":"12","r":"1"}]],
  "circle-question-mark":[["circle",{"cx":"12","cy":"12","r":"10"}],["path",{"d":"M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"}],["path",{"d":"M12 17h.01"}]],
  "circle-x":[["circle",{"cx":"12","cy":"12","r":"10"}],["path",{"d":"m15 9-6 6"}],["path",{"d":"m9 9 6 6"}]],
  "crosshair":[["circle",{"cx":"12","cy":"12","r":"10"}],["line",{"x1":"22","x2":"18","y1":"12","y2":"12"}],["line",{"x1":"6","x2":"2","y1":"12","y2":"12"}],["line",{"x1":"12","x2":"12","y1":"6","y2":"2"}],["line",{"x1":"12","x2":"12","y1":"22","y2":"18"}]],
  "file-text":[["path",{"d":"M6 22a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h8a2.4 2.4 0 0 1 1.704.706l3.588 3.588A2.4 2.4 0 0 1 20 8v12a2 2 0 0 1-2 2z"}],["path",{"d":"M14 2v5a1 1 0 0 0 1 1h5"}],["path",{"d":"M10 9H8"}],["path",{"d":"M16 13H8"}],["path",{"d":"M16 17H8"}]],
  "flag":[["path",{"d":"M4 22V4a1 1 0 0 1 .4-.8A6 6 0 0 1 8 2c3 0 5 2 7.333 2q2 0 3.067-.8A1 1 0 0 1 20 4v10a1 1 0 0 1-.4.8A6 6 0 0 1 16 16c-3 0-5-2-8-2a6 6 0 0 0-4 1.528"}]],
  "folder":[["path",{"d":"M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"}]],
  "globe":[["circle",{"cx":"12","cy":"12","r":"10"}],["path",{"d":"M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20"}],["path",{"d":"M2 12h20"}]],
  "graduation-cap":[["path",{"d":"M21.42 10.922a1 1 0 0 0-.019-1.838L12.83 5.18a2 2 0 0 0-1.66 0L2.6 9.08a1 1 0 0 0 0 1.832l8.57 3.908a2 2 0 0 0 1.66 0z"}],["path",{"d":"M22 10v6"}],["path",{"d":"M6 12.5V16a6 3 0 0 0 12 0v-3.5"}]],
  "grip-vertical":[["circle",{"cx":"9","cy":"12","r":"1"}],["circle",{"cx":"9","cy":"5","r":"1"}],["circle",{"cx":"9","cy":"19","r":"1"}],["circle",{"cx":"15","cy":"12","r":"1"}],["circle",{"cx":"15","cy":"5","r":"1"}],["circle",{"cx":"15","cy":"19","r":"1"}]],
  "hammer":[["path",{"d":"m15 12-9.373 9.373a1 1 0 0 1-3.001-3L12 9"}],["path",{"d":"m18 15 4-4"}],["path",{"d":"m21.5 11.5-1.914-1.914A2 2 0 0 1 19 8.172v-.344a2 2 0 0 0-.586-1.414l-1.657-1.657A6 6 0 0 0 12.516 3H9l1.243 1.243A6 6 0 0 1 12 8.485V10l2 2h1.172a2 2 0 0 1 1.414.586L18.5 14.5"}]],
  "heart":[["path",{"d":"M2 9.5a5.5 5.5 0 0 1 9.591-3.676.56.56 0 0 0 .818 0A5.49 5.49 0 0 1 22 9.5c0 2.29-1.5 4-3 5.5l-5.492 5.313a2 2 0 0 1-3 .019L5 15c-1.5-1.5-3-3.2-3-5.5"}]],
  "house":[["path",{"d":"M15 21v-8a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1v8"}],["path",{"d":"M3 10a2 2 0 0 1 .709-1.528l7-6a2 2 0 0 1 2.582 0l7 6A2 2 0 0 1 21 10v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"}]],
  "inbox":[["polyline",{"points":"22 12 16 12 14 15 10 15 8 12 2 12"}],["path",{"d":"M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"}]],
  "layers":[["path",{"d":"M12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83z"}],["path",{"d":"M2 12a1 1 0 0 0 .58.91l8.6 3.91a2 2 0 0 0 1.65 0l8.58-3.9A1 1 0 0 0 22 12"}],["path",{"d":"M2 17a1 1 0 0 0 .58.91l8.6 3.91a2 2 0 0 0 1.65 0l8.58-3.9A1 1 0 0 0 22 17"}]],
  "library":[["path",{"d":"m16 6 4 14"}],["path",{"d":"M12 6v14"}],["path",{"d":"M8 8v12"}],["path",{"d":"M4 4v16"}]],
  "loader-circle":[["path",{"d":"M21 12a9 9 0 1 1-6.219-8.56"}]],
  "lock-keyhole":[["circle",{"cx":"12","cy":"16","r":"1"}],["rect",{"x":"3","y":"10","width":"18","height":"12","rx":"2"}],["path",{"d":"M7 10V7a5 5 0 0 1 10 0v3"}]],
  "mail":[["path",{"d":"m22 7-8.991 5.727a2 2 0 0 1-2.009 0L2 7"}],["rect",{"x":"2","y":"4","width":"20","height":"16","rx":"2"}]],
  "moon":[["path",{"d":"M20.985 12.486a9 9 0 1 1-9.473-9.472c.405-.022.617.46.402.803a6 6 0 0 0 8.268 8.268c.344-.215.825-.004.803.401"}]],
  "paintbrush":[["path",{"d":"m14.622 17.897-10.68-2.913"}],["path",{"d":"M18.376 2.622a1 1 0 1 1 3.002 3.002L17.36 9.643a.5.5 0 0 0 0 .707l.944.944a2.41 2.41 0 0 1 0 3.408l-.944.944a.5.5 0 0 1-.707 0L8.354 7.348a.5.5 0 0 1 0-.707l.944-.944a2.41 2.41 0 0 1 3.408 0l.944.944a.5.5 0 0 0 .707 0z"}],["path",{"d":"M9 8c-1.804 2.71-3.97 3.46-6.583 3.948a.507.507 0 0 0-.302.819l7.32 8.883a1 1 0 0 0 1.185.204C12.735 20.405 16 16.792 16 15"}]],
  "pencil":[["path",{"d":"M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z"}],["path",{"d":"m15 5 4 4"}]],
  "pin":[["path",{"d":"M12 17v5"}],["path",{"d":"M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z"}]],
  "plane":[["path",{"d":"M17.8 19.2 16 11l3.5-3.5C21 6 21.5 4 21 3c-1-.5-3 0-4.5 1.5L13 8 4.8 6.2c-.5-.1-.9.1-1.1.5l-.3.5c-.2.5-.1 1 .3 1.3L9 12l-2 3H4l-1 1 3 2 2 3 1-1v-3l3-2 3.5 5.3c.3.4.8.5 1.3.3l.5-.2c.4-.3.6-.7.5-1.2z"}]],
  "search":[["path",{"d":"m21 21-4.34-4.34"}],["circle",{"cx":"11","cy":"11","r":"8"}]],
  "settings":[["path",{"d":"M9.671 4.136a2.34 2.34 0 0 1 4.659 0 2.34 2.34 0 0 0 3.319 1.915 2.34 2.34 0 0 1 2.33 4.033 2.34 2.34 0 0 0 0 3.831 2.34 2.34 0 0 1-2.33 4.033 2.34 2.34 0 0 0-3.319 1.915 2.34 2.34 0 0 1-4.659 0 2.34 2.34 0 0 0-3.32-1.915 2.34 2.34 0 0 1-2.33-4.033 2.34 2.34 0 0 0 0-3.831A2.34 2.34 0 0 1 6.35 6.051a2.34 2.34 0 0 0 3.319-1.915"}],["circle",{"cx":"12","cy":"12","r":"3"}]],
  "shopping-cart":[["circle",{"cx":"8","cy":"21","r":"1"}],["circle",{"cx":"19","cy":"21","r":"1"}],["path",{"d":"M2.05 2.05h2l2.66 12.42a2 2 0 0 0 2 1.58h9.78a2 2 0 0 0 1.95-1.57l1.65-7.43H5.12"}]],
  "square-terminal":[["path",{"d":"m7 11 2-2-2-2"}],["path",{"d":"M11 13h4"}],["rect",{"width":"18","height":"18","x":"3","y":"3","rx":"2","ry":"2"}]],
  "star":[["path",{"d":"M11.525 2.295a.53.53 0 0 1 .95 0l2.31 4.679a2.123 2.123 0 0 0 1.595 1.16l5.166.756a.53.53 0 0 1 .294.904l-3.736 3.638a2.123 2.123 0 0 0-.611 1.878l.882 5.14a.53.53 0 0 1-.771.56l-4.618-2.428a2.122 2.122 0 0 0-1.973 0L6.396 21.01a.53.53 0 0 1-.77-.56l.881-5.139a2.122 2.122 0 0 0-.611-1.879L2.16 9.795a.53.53 0 0 1 .294-.906l5.165-.755a2.122 2.122 0 0 0 1.597-1.16z"}]],
  "sun":[["circle",{"cx":"12","cy":"12","r":"4"}],["path",{"d":"M12 2v2"}],["path",{"d":"M12 20v2"}],["path",{"d":"m4.93 4.93 1.41 1.41"}],["path",{"d":"m17.66 17.66 1.41 1.41"}],["path",{"d":"M2 12h2"}],["path",{"d":"M20 12h2"}],["path",{"d":"m6.34 17.66-1.41 1.41"}],["path",{"d":"m19.07 4.93-1.41 1.41"}]],
  "trending-up":[["path",{"d":"M16 7h6v6"}],["path",{"d":"m22 7-8.5 8.5-5-5L2 17"}]],
  "triangle-alert":[["path",{"d":"m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"}],["path",{"d":"M12 9v4"}],["path",{"d":"M12 17h.01"}]],
  "users":[["path",{"d":"M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"}],["path",{"d":"M16 3.128a4 4 0 0 1 0 7.744"}],["path",{"d":"M22 21v-2a4 4 0 0 0-3-3.87"}],["circle",{"cx":"9","cy":"7","r":"4"}]],
  "wrench":[["path",{"d":"M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.106-3.105c.32-.322.863-.22.983.218a6 6 0 0 1-8.259 7.057l-7.91 7.91a1 1 0 0 1-2.999-3l7.91-7.91a6 6 0 0 1 7.057-8.259c.438.12.54.662.219.984z"}]],
  "x":[["path",{"d":"M18 6 6 18"}],["path",{"d":"m6 6 12 12"}]],
  "zap":[["path",{"d":"M4 14a1 1 0 0 1-.78-1.63l9.9-10.2a.5.5 0 0 1 .86.46l-1.92 6.02A1 1 0 0 0 13 10h7a1 1 0 0 1 .78 1.63l-9.9 10.2a.5.5 0 0 1-.86-.46l1.92-6.02A1 1 0 0 0 11 14z"}]]
};

/* cmux 워크스페이스 그룹의 SF Symbol 이름 → lucide 이름.
   cmux 가 주는 값은 세 종류다: ①아는 심볼 이름 ②모르는 심볼 이름 ③사용자가 직접
   넣은 이모지. ③은 매핑할 방법이 없으므로 그대로 글자로 내보낸다. */
const SYMBOL = {
  'tray.full.fill':'inbox', 'tray.fill':'inbox',
  'paintbrush.fill':'paintbrush', 'house.fill':'house',
  'gearshape.2.fill':'settings', 'gearshape.fill':'settings',
  'book.closed.fill':'book', 'books.vertical.fill':'library',
  'chart.line.uptrend.xyaxis':'trending-up', 'chart.bar.fill':'chart-column',
  'graduationcap.fill':'graduation-cap', 'hammer.fill':'hammer',
  'wrench.and.screwdriver.fill':'wrench', 'briefcase.fill':'briefcase',
  'folder.fill':'folder', 'star.fill':'star', 'flag.fill':'flag',
  'bolt.fill':'zap', 'person.2.fill':'users', 'envelope.fill':'mail',
  'cart.fill':'shopping-cart', 'airplane':'plane', 'heart.fill':'heart',
  'doc.fill':'file-text',
};

/* 대화 상태 → 아이콘. 이름이 곧 뜻이 되게 골랐다:
     running    돌고 있다        → 도는 스피너
     permission 나 때문에 멈췄다 → 자물쇠
     waiting    프롬프트에서 대기 → 점 찍힌 원(입력 받을 준비)
     idle       오래 안 건드림   → 달
     unknown    근거가 없다      → 물음표 (추정으로 채우지 않는다) */
const STATUS = {
  running:'loader-circle', permission:'lock-keyhole',
  // 질문 대기(AskUserQuestion·훅의 yes/no)도 **같은 자물쇠**다 — 내가 할 행동이 같기 때문이다.
  // 구분은 아이콘이 아니라 라벨과 카드 상세에서 한다.
  question:'lock-keyhole',
  // 턴은 끝났는데 뒤에서 셸이 돌고 있는 상태. running 의 '도는 원'과 헷갈리면 안 되므로
  // 아예 다른 그림(번개)으로 둔다 — 급한 정도가 다르다는 걸 모양으로 먼저 알린다.
  background:'zap',
  waiting:'circle-dot', idle:'moon', unknown:'circle-question-mark',
};

const ATTR = s => Object.entries(s).map(([k,v]) => ` ${k}="${v}"`).join('');

/** ic(name, opts) → SVG 마크업 문자열.
 *  opts.cls   추가 클래스 (예: 'spin', 'claude')
 *  opts.title 접근성 이름 겸 툴팁 (없으면 aria-hidden)
 *  모르는 이름은 **빈 문자열**을 돌려준다 — 깨진 네모(⃞)를 그리는 것보다 낫다. */
function ic(name, opts){
  const shapes = LUCIDE[name];
  if(!shapes) return '';
  const o = opts || {};
  const body = shapes.map(([tag, a]) => `<${tag}${ATTR(a)}/>`).join('');
  const label = o.title ? ` role="img" aria-label="${String(o.title).replace(/"/g,'&quot;')}"`
                        : ' aria-hidden="true"';
  return `<svg class="lic${o.cls ? ' ' + o.cls : ''}" viewBox="0 0 24 24" fill="none"`
       + ` stroke="currentColor" stroke-width="2" stroke-linecap="round"`
       + ` stroke-linejoin="round"${label}>${body}</svg>`;
}

/** 상태 코드 → 아이콘 마크업. running 은 CSS 로 돌린다. */
function icStatus(state, title){
  const n = STATUS[state] || STATUS.unknown;
  return ic(n, {cls:'st-' + (STATUS[state] ? state : 'unknown') + (state === 'running' ? ' spin' : ''),
               title: title});
}

/** cmux 그룹 아이콘 — 심볼 이름이면 매핑하고, 이모지면 그대로 글자로 낸다. */
function icGroup(symbol){
  if(!symbol) return ic('folder');
  const mapped = SYMBOL[symbol];
  if(mapped) return ic(mapped);
  // 심볼 이름 꼴인데 표에 없다 → 폴더. 그 밖(이모지 등)은 사용자가 넣은 글자다.
  return /^[\w.]+$/.test(symbol) ? ic('folder')
       : `<span class="lic lic-emoji">${String(symbol).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}</span>`;
}

window.ICONS = {ic, icStatus, icGroup, LUCIDE, SYMBOL, STATUS};
window.ic = ic; window.icStatus = icStatus; window.icGroup = icGroup;

/** 앞말 받침에 따라 조사를 고른다 — `josa('모니터','이/가')` → `'가'`.
 *
 *  ⚠️ 이게 없으면 "모니터**이** 세션을 깨웠습니다" 가 된다(실제 신고 2026-09-05).
 *     문장에 끼워 넣는 값이 **자유 문자열**일 때는 조사를 하드코딩하면 안 된다 —
 *     wakeKind 만 해도 '모니터'(받침 없음)·'백그라운드 알림'(있음)·
 *     'artifact-watch-lifecycle'(영문) 이 실제로 섞여 온다.
 *
 *  한글이 아닌 값은 **읽는 소리**로 가른다:
 *    숫자 0·1·3·6·7·8 은 받침 있음(영/일/삼/육/칠/팔), 2·4·5·9 는 없음.
 *    영문은 a·i·o·u·y 로 끝나면 없음, 그 밖(자음과 묵음 e)은 있음 —
 *    'lifecycle'→"라이프사이클", 'Safari'→"사파리" 처럼 실제 발음과 맞는다.
 *  완벽한 한국어 처리는 아니지만, 눈에 띄게 틀린 것을 없애는 데는 충분하다.
 */
function josa(word, pair){
  const [withB, without] = String(pair).split('/');
  const s = String(word ?? '').trim();
  if(!s) return without;
  const ch = s[s.length - 1], code = ch.charCodeAt(0);
  if(code >= 0xac00 && code <= 0xd7a3) return (code - 0xac00) % 28 ? withB : without;
  const c = ch.toLowerCase();
  if(c >= '0' && c <= '9') return '013678'.includes(c) ? withB : without;
  if(c >= 'a' && c <= 'z') return 'aiouy'.includes(c) ? without : withB;
  return withB;                      // 기호 등 알 수 없는 끝은 받침 있는 쪽이 덜 어색하다
}

/** 값과 조사를 붙여 돌려준다 — `withJosa('모니터','이/가')` → `'모니터가'`. */
function withJosa(word, pair){ return `${word}${josa(word, pair)}`; }
