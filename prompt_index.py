"""세션 검색 — 탭 정보 + 그 세션에서 사람이 한 말 **전부**(2026-10-10).

왜: 검색이 `lastPromptText`(마지막 프롬프트 하나, 그것도 180자에서 자름)만 봐서 «프롬프트에 있던 말인데 안 걸린다»는
    신고가 나왔다(웹·폰 모두). 그래서 세션마다 사람 발화를 자르지 않고 모아 두고, 검색은 서버가 판정한다.

색인: transcript 는 덧붙기만 하는 jsonl 이다 → 파일마다 «어디까지 읽었나(off)»를 들고, 늘어난 줄만 읽는다.
      파일이 줄었거나 inode 가 바뀌면 처음부터. 첫 읽기는 8MB 씩 끊어 읽는다(큰 세션에서 메모리 폭주 방지).
사람 발화 판정은 nav 의 것을 그대로 쓴다(_is_human_prompt · _queued_human · clean_prompt) — 카드와 같은 기준.
검색 규칙: 공백으로 나눈 낱말을 **모두** 포함(AND), 대소문자 무시, 한글은 NFC 로 맞춘다.
+ Claude 답(본 세션 assistant 의 글 블록)도 넣는다(10-15 신고: «D-U-N-S» 는 Claude 답에만 있어 안 걸렸다).
  도구 입력·결과·생각(thinking)·서브에이전트 기록은 넣지 않는다 — 폰 «대화» 화면에 보이는 말과 같은 범위.
한계: 이어진 세션(continued-in)은 «세션 id 파일 + 지금 쓰는 파일» 둘만 본다(사슬 중간 파일은 안 본다).
"""
import json
import os
import threading
import unicodedata

import nav

CHUNK = 8 * 1024 * 1024
PROMPT_MAX = 20_000            # 프롬프트 하나에 담는 상한(붙여넣은 긴 문서가 색인을 부풀리지 않게)
_CACHE = {}                    # path → {"off", "ino", "prompts": [(norm, raw)]}
_LOCK = threading.Lock()


def norm(s):
    return unicodedata.normalize("NFC", s or "").lower()


def _human_text(o):
    q = nav._queued_human(o)
    if q is not None:
        return q
    if nav._is_human_prompt(o):
        return nav._entry_text(o)
    return None


def _claude_text(o):
    if o.get("type") != "assistant" or o.get("isSidechain"):
        return None
    c = (o.get("message") or {}).get("content")
    if not isinstance(c, list):
        return None
    return "\n".join(b.get("text") or "" for b in c if isinstance(b, dict) and b.get("type") == "text").strip() or None


def prompts_of(path):
    """그 transcript 의 사람 발화 [(norm, raw)] — 늘어난 부분만 새로 읽는다."""
    try:
        st = os.stat(path)
    except OSError:
        return []
    with _LOCK:
        c = _CACHE.get(path)
        if not c or st.st_size < c["off"] or st.st_ino != c["ino"]:
            c = {"off": 0, "ino": st.st_ino, "prompts": []}
        if st.st_size > c["off"]:
            try:
                with open(path, "rb") as f:
                    f.seek(c["off"])
                    rest = b""
                    while True:
                        buf = f.read(CHUNK)
                        if not buf:
                            break
                        buf = rest + buf
                        cut = buf.rfind(b"\n")
                        if cut < 0:
                            rest = buf
                            continue
                        rest = buf[cut + 1:]
                        c["off"] += cut + 1
                        for ln in buf[:cut].splitlines():
                            if (b'"promptSource"' not in ln and b"queued_command" not in ln
                                    and not (b'"assistant"' in ln and b'"type":"text"' in ln)):
                                continue
                            try:
                                o = json.loads(ln)
                            except ValueError:
                                continue
                            t = _human_text(o)
                            raw = nav.clean_prompt(t, limit=None) if t else _claude_text(o)
                            if raw:
                                raw = unicodedata.normalize("NFC", raw[:PROMPT_MAX])
                                c["prompts"].append((raw.lower(), raw))
            except OSError:
                pass
        _CACHE[path] = c
        if len(_CACHE) > 600:                       # 닫힌 세션 파일이 쌓이지 않게(다음 검색에서 다시 읽힌다)
            for k in list(_CACHE)[:200]:
                _CACHE.pop(k, None)
        return c["prompts"]


def _files_of(t):
    files = []
    live = t.get("transcript")
    if live:
        files.append(live)
        sid = t.get("sessionId")
        if sid:
            own = os.path.join(os.path.dirname(live), f"{sid}.jsonl")
            if own != live and os.path.isfile(own):
                files.insert(0, own)
    return files


META_KEYS = ("cleanTitle", "title", "workspaceTitle", "windowLabel", "tabRef", "surfaceRef", "sessionId", "cwd",
             "lastPromptText", "statusLabel", "workspaceDescription", "askText")


def _meta(t):
    bits = [t.get(k) for k in META_KEYS] + [(t.get("group") or {}).get("name"),
                                            (t.get("notif") or {}).get("subtitle")]
    return norm(" ".join(str(b) for b in bits if b))


def _snippet(raw, low, word, width=70):
    i = low.find(word)
    if i < 0:
        return None
    a = max(0, i - width // 2)
    b = min(len(raw), i + len(word) + width)
    return ("…" if a > 0 else "") + raw[a:b] + ("…" if b < len(raw) else "")


def search(tabs, q):
    """{surfaceId: {"snippet": 프롬프트에서 걸린 부분 또는 None, "where": "meta"|"prompts"}} — 낱말 AND."""
    words = [w for w in norm(q).split() if w]
    if not words:
        return {}
    hits = {}
    for t in tabs:
        sid = t.get("surfaceId")
        if not sid:
            continue
        meta = _meta(t)
        missing = [w for w in words if w not in meta]
        if not missing:
            hits[sid] = {"snippet": None, "where": "meta"}
            continue
        prompts = [p for f in _files_of(t) for p in prompts_of(f)]
        if not prompts:
            continue
        corpus = "\n".join(p[0] for p in prompts)
        if all(w in corpus for w in missing):
            # 보여 줄 조각 — 메타에 없던 낱말이 처음 나오는 **가장 최근** 프롬프트에서
            snip = None
            for low, raw in reversed(prompts):
                if missing[0] in low:
                    snip = _snippet(raw, low, missing[0])
                    break
            hits[sid] = {"snippet": snip, "where": "prompts"}
    return hits
