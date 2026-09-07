/* ============================================================================
   theme.js — 라이트/다크 전환
   ----------------------------------------------------------------------------
   상태는 셋이다. 둘이 아니라 셋인 게 핵심이다:
     'system'  저장값 없음 → OS 설정을 따라간다(tokens.css 의 @media 가 처리)
     'light' / 'dark'  사용자가 이 사이트에서 명시적으로 고름 → <html data-theme> 로 못박는다

   왜 'system' 을 남기나: 사람이 "이 사이트는 밝게"라고 고른 것과 "OS 를 따라가라"는
   서로 다른 뜻이다. 토글을 껐다 켜면 무조건 dark/light 둘 중 하나로 굳어 버리는 UI 는
   OS 를 야간에 자동으로 어둡게 두는 설정을 무력화한다. 그래서 순환은 셋을 돈다.

   ⚠️ 깜빡임(FOUC) 방지: 이 스크립트는 <head> 에서, **CSS 보다 먼저 동기 실행**돼야
      한다. body 가 그려진 뒤 data-theme 를 붙이면 다크 화면이 한 프레임 번쩍인다.
      그래서 apply() 만 즉시 실행하고 버튼 배선은 DOM 이 준비된 뒤로 미룬다.
   ========================================================================== */
(function(){
  const KEY = 'cmux-dash-theme';
  const ORDER = ['system', 'light', 'dark'];
  const LABEL = {system:'시스템 설정을 따릅니다', light:'밝게', dark:'어둡게'};

  function get(){
    try{ const v = localStorage.getItem(KEY); return ORDER.includes(v) ? v : 'system'; }
    catch(e){ return 'system'; }   // 사생활 보호 모드 등에서 localStorage 가 던진다
  }
  function apply(mode){
    const r = document.documentElement;
    if(mode === 'system') r.removeAttribute('data-theme');
    else r.setAttribute('data-theme', mode);
  }
  function set(mode){
    try{ mode === 'system' ? localStorage.removeItem(KEY) : localStorage.setItem(KEY, mode); }
    catch(e){}
    apply(mode);
    render();
  }
  /** 지금 실제로 보이는 테마(시스템 추종이면 OS 설정을 물어본다). 아이콘을 고르는 데 쓴다. */
  function effective(){
    const m = get();
    if(m !== 'system') return m;
    return matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
  }
  function render(){
    const b = document.getElementById('themeBtn');
    if(!b || !window.ic) return;
    const m = get(), eff = effective();
    b.innerHTML = ic(eff === 'light' ? 'sun' : 'moon')
                + (m === 'system' ? '<span class="tmode">자동</span>' : '');
    b.title = `테마: ${LABEL[m]}` + (m === 'system' ? ` (지금 ${LABEL[eff]})` : '')
            + `\n눌러서 ${LABEL[ORDER[(ORDER.indexOf(m)+1) % 3]]}`;
    b.setAttribute('aria-label', b.title.split('\n')[0]);
  }

  apply(get());   // ← CSS 적용 전에 끝나야 한다

  function wire(){
    render();
    const b = document.getElementById('themeBtn');
    if(b) b.onclick = () => set(ORDER[(ORDER.indexOf(get()) + 1) % 3]);
    // 시스템 추종 중일 때 OS 가 바뀌면 아이콘도 따라 바뀌어야 한다
    matchMedia('(prefers-color-scheme: light)').addEventListener('change', render);
  }
  if(document.readyState === 'loading') document.addEventListener('DOMContentLoaded', wire);
  else wire();

  window.THEME = {get, set, effective};
})();
