<div align="center">

# cmux Control Room

**When you run dozens of Claude Code sessions at once, this finds the one that is waiting on you.**

See every window, workspace, group and tab in [cmux](https://cmux.com) — and every Claude Code
session inside them — **on one screen. Rearrange them from there. Bring them back when they're gone.**

[한국어](README.md) · [Development history](docs/HISTORY.md) · [cmux field notes](docs/CMUX-NOTES.md)

<br>

![85 live Claude tabs across 5 windows, laid out as columns](docs/assets/board-all.png)

<sub>5 windows · 8 groups · 96 workspaces · 85 live Claude tabs.
All synthetic — <code>python run_demo.py</code> gives you this exact screen right now.</sub>

</div>

---

## Why this exists

It started on 2026-07-02 with a report, not a feature request:

> **"You know those cmux workspaces and tabs I had open? The Claude Code sessions that were
> running in them all died. Can I get them back?"**

Yes — because cmux had already written `claude --resume <session-id>` to disk for every tab.
Eight minutes later came the actual spec, and the first version shipped that night.

Then the tool changed identity **three times**.

| | What it was | What it became | The line that caused it |
|---|---|---|---|
| **Jul 6** | A table of resume commands to copy-paste | **Something that restores the layout itself** | *"not just restoring Claude sessions — I want my window / workspace / tab / browser layout preserved so I can selectively bring back exactly what I want"* |
| **Aug 22** | A recovery tool you open after an accident | **A control tool you open having lost nothing** | *"with this many interfaces, when I'm running several things at once I can't tell which Claude response I'm supposed to look at"* |
| **Aug 26** | A read-only dashboard | **A control surface that actually moves cmux** | *"drag the board to change the real cmux arrangement. Design undo first."* |

After the third one it was renamed from "State & Recovery Dashboard" to **"Control Room"** —
on the user's own reasoning: *"it has become a cmux management tool itself, so the name needs to change."*

---

## The real problem it solves — "blocked"

With five windows and eighty tabs, the question isn't *what is running*.
It's **where is the thing that makes zero progress until I look at it.**

```
permission   Claude is stopped, waiting for my approval. Nothing moves until I look.
             And it is quiet — no output, no progress. If I don't hunt for it I never know.  ← top
question     It asked me something (AskUserQuestion) and is standing there. Same action from me.
running      It's working. I'd like to see it, but it proceeds without me.
background   The turn is done and a shell is still going. When it finishes, someone has to poke it.
waiting      Turn finished, sitting at the prompt. The work is already done.
idle         Quiet.
```

The first two are **"blocked."** The header count, the `(4)` in the browser tab title, and the
menu-bar badge are all that same sum. They aren't split into separate numbers for one reason —
**splitting them blurs "how many things are waiting on me right now."**

The definition lives in exactly **one dictionary** in `nav.py`; sort order and the definition of
"blocked" are both derived from it and shipped to clients. If the web UI and the menu-bar app each
wrote their own status list they would **drift silently** — and in fact the web UI's status legend
was running with `question` missing from it.

### Blocked cards catch your eye without being hunted for

<div align="center">

![Blocked cards bounce three times with a colour pulse](docs/assets/blocked-bump.gif)

</div>

Motion is **the only channel that reaches you without being looked for**, so it is used here and
nowhere else — this has to be the only thing moving on screen for it to mean anything.

> Four commits went into this one animation. Through the third, "0% stationary" was *measured* and
> declared fixed. But the decay was too steep and the last hop was 1.2px. A keyframe can say
> "in motion" while a human eye reads it as stopped.
> **The test had to be "you can see it move," not "it is moving."** ([the whole story](docs/HISTORY.md))

---

## The screens

### 🎯 Where am I — which window and tab each live Claude session is in

<div align="center">

![Column board per window. blocked 6, running 8, waiting 9, idle 60](docs/assets/board-blocked.png)

</div>

One column per window, with the cmux group → workspace → tab hierarchy inside it.
**The active window comes first**, the rest in window order — sorting columns by activity makes the
window you were looking for run away every time something moves. Recency ordering applies
**inside** a column only.

- **The 3px bar on the left** = the workspace colour you painted in cmux — *which piece of work is this*
- **The faint background tint** = status — *is it running, is it waiting for me*
- Idle cards get no tint. Most cards are idle, and colouring those too would mean
  **"being coloured" stops being a signal.**

Expanding a card shows the last human prompt, the evidence behind the status call, cwd, and the
jump button. Prompt extraction keys off **`promptSource`**, not `promptId` — the latter is attached
to tool results too, which empties the preview entirely.

<div align="center">

![Expanded card with last prompt, status evidence and actions](docs/assets/card-expanded.png)

</div>

### Click a card, land on that tab

<div align="center">

![Clicking a card makes that workspace active and moves the "current tab" badge](docs/assets/click-to-jump.gif)

</div>

A single `surface.focus` does **window switch + workspace switch + tab select** — even for a hidden
workspace in another window. On top of that, `open -a` raises **cmux.app to the front of macOS**.
Without that step the tab has moved but the screen hasn't, so *"it just says it moved and I still
have to go find the window myself."*

The result is **self-verified**. If it doesn't match, you get `didn't move — currently active:
surface:NNN` instead of a success message. Showing only success is how people end up trusting a
screen that is lying.

### ✏️ Edit mode — drag to change the real cmux arrangement

<div align="center">

![Edit mode: drag handles, pin toggles, apply / undo / reset](docs/assets/edit-mode.png)

</div>

Drag workspace order, group membership and cross-window moves, hit **apply**, and cmux really
becomes that. Polling stops while editing (reads and writes fight over the same screen).

Undo uses **the same endpoint and the same payload shape** as apply — a dedicated restore path
would be the one that stays under-tested.

cmux lies quietly in three ways here, and all three are handled by **not trusting the response**:

| What cmux does | What the control room does |
|---|---|
| **Silently clamps** the requested index (`--index 8` → 5, output still `OK`) | Compares the planned index on every move, re-reads the whole arrangement at the end, and **returns the mismatches to the screen** |
| Group membership is decided by **contiguous sidebar position**, not by groupId | Position → membership → position, **two passes**. Otherwise the neighbouring group's anchor absorbs your members |
| `after_group_id` — and pinned groups — **return OK without moving** | Compares anchor-workspace indices directly to decide `moved` |

### Current · History · Recovery · Session health

<table>
<tr>
<td width="50%"><a href="docs/assets/tab-current.png"><img src="docs/assets/tab-current.png" alt="Current tab — live tree"></a><br><b>Current</b> — live tree joining structure (<code>/api/state</code>) with status (<code>/api/nav</code>), with a copy button for each <code>claude --resume</code>.</td>
<td width="50%"><a href="docs/assets/tab-recovery.png"><img src="docs/assets/tab-recovery.png" alt="Recovery tab — minimaps and nested checkboxes"></a><br><b>Recovery</b> — pick a snapshot and restore <b>item by item</b>. This is the one tab that was never compacted: <b>seeing the split layout and choosing from it is the point of this screen.</b></td>
</tr>
<tr>
<td><a href="docs/assets/tab-history.png"><img src="docs/assets/tab-history.png" alt="History tab"></a><br><b>History</b> — snapshots from 30s polling plus change detection. Open one, diff it against now.</td>
<td><a href="docs/assets/tab-health.png"><img src="docs/assets/tab-health.png" alt="Session health tab"></a><br><b>Session health</b> — sessions that have been quiet a long time, with their memory. <b>There is deliberately no kill button</b> — you read the list and do it in a terminal.</td>
</tr>
</table>

Light/dark is **three states, not two** (`system → light → dark`).
"This site should be light" and "follow the OS" are different statements and weren't merged.

<div align="center">

![Light theme](docs/assets/board-light.png)

</div>

---

## 🖥 Menu-bar minimap (native Swift)

<div align="center">

<img src="docs/assets/menubar.png" width="480" alt="Menu bar badge and popover">

</div>

The web dashboard is **a place you have to go and look at**, and blocked is **the state you never
learn about unless you hunt for it**. That mismatch is what produced the menu-bar app. If the
bouncing card was an attempt to catch your eye without being hunted for, the badge is an attempt to
**not need the dashboard open at all**.

- **Badge = blocked N.** If nothing is blocked, the running count in green; if only background work
  remains, a gold bolt; otherwise `zzz`.
- **Popover** = status summary + blocked / running / background list (**grouped by cmux group**) +
  click to jump to that tab.
- **It does not decide status.** It only carries what `/api/nav` gave it — including the order and
  the definition of "blocked."

```bash
cd menubar && bash scripts/bundle.sh release && open ./CmuxMinimap.app
# different port:  CMUXMM_PORT=7801 open ./CmuxMinimap.app --args
```

> The app draws only its own status item and talks only to `127.0.0.1` — no TCC, no local-network
> permission, ad-hoc signing is enough. The `.app` bundle is needed for `LSUIElement`, not for
> permissions. See [menubar/README.md](menubar/README.md), which records the measured incident where
> **a full menu bar makes macOS silently push your icon to the far left where the app menu covers it.**

---

## Try it in 30 seconds — demo mode

**Works without cmux, and without macOS.** Synthetic state is served in exactly the shape of the
real API, so what renders is the actual `nav.js` and `board.css`. Every screenshot in this README
was taken from it.

```bash
git clone https://github.com/joonlab/cmux-state-dashboard.git
cd cmux-state-dashboard
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python run_demo.py          # → http://localhost:7788
```

In the demo, **dragging actually moves things, pin toggles work, and clicking a card really makes
that tab active** — a read-only demo can't show you what this tool is. `POST /api/demo/reset`
puts it back.

To change what it shows, edit [`demo_fixture.py`](demo_fixture.py) and nothing else — windows,
groups, workspaces and tabs are a list of tuples in there. Times are stored as `ago` seconds and
converted per request, so **"8 seconds ago" is alive whenever you open it.**

---

## Pointing it at real cmux

```bash
pm2 start ecosystem.config.js      # → http://localhost:7788
pm2 save                           # survives reboot
```

| Setting | Default | |
|---|---|---|
| `CMUX_DASH_PORT` | `7788` | port |
| `CMUX_DASH_POLL` | `30` | snapshot poll interval (seconds) |
| `CMUX_DASH_DATA` | `./data` | DB and backups |
| `CMUX_DASH_DEMO` | — | `1` for demo mode |

Every tab has its own URL — `/now` `/current` `/history` `/recovery` `/health`, plus `/now/full`
(standalone fullscreen, for a phone or a second monitor). Refresh, bookmark and back all return you
to that tab.

### It works 100% without the socket — the root of the design

This was settled on day one, after twenty minutes of failing to reach the cmux socket from pm2.
**Turning on socket control requires restarting cmux — which is precisely the accident this tool exists to prevent.**

```
read / recover  ← files are enough
   ~/Library/Application Support/cmux/session-*.json   windows, workspaces, splits, resume commands
   ~/.claude/projects/**/*.jsonl                        session labels, last utterance
   ps -E                                                CMUX_PANEL_ID + --session-id
   notification-feed-history-*.json                     Waiting / Permission / Completed

control         ← only when the socket is available (buttons disable otherwise)
   tree --all --json · surface.focus · workspace.group.* · new-workspace --layout
```

**No hook installation is needed** to map sessions to tabs. cmux sets `CMUX_PANEL_ID`
(= surface UUID) in the environment and passes `--session-id` on the command line, so a single
`ps -E` reads both and joins them against the tree — **exactly, with no guessing.**

There is an independent backup too: every snapshot writes `data/latest-recovery.{json,txt}`.
**Even with the dashboard down**, `cat data/latest-recovery.txt` gets you back.

---

## Design principles — all of them came from being burned

This is the most reusable part of the repository. Each has its full story in
[the development history](docs/HISTORY.md).

**⚖️ When two sources disagree, pick one as canonical — but never hide the disagreement.**
The socket tree said 4 windows, the native JSON said 2. Choosing native as canonical was correct.
What was wrong is that nothing warned about the gap — and so **a whole window disappeared and nobody knew.**

**🕳 A wrong value makes the user act on it. When you don't know, leave it empty.**
Guessing session-to-tab assignment by title attached one session to two identically-named
workspaces, and the user clicked the wrong tab based on that phantom.

**🖱 A selector click can pass where a human finger is blocked.**
A sticky header (89px) covered cards (110px) and intercepted clicks — but `cmux browser click`
targets the element directly and is immune to occlusion. Automated tests verify *does this element
exist*; a human click verifies *does this point reach this element*. **Those are different claims.**
→ Click verification moved to `elementFromPoint` reachability.

**⏱ Never use file mtime as session activity time.**
A running session's `.jsonl` keeps getting touched with no conversation (median gap **1494 min**
across 45 running sessions, versus **0.3 min** across 2055 finished ones). A session from nine hours
ago showed up at the top as "16 seconds ago." For an append-only log, **size — not mtime — is the
right cache key.**

**🔕 An empty result from a safety guard means no guard at all.**
The double-launch guard parsed `ps` output as text, blew up on a non-UTF-8 byte, and an `except`
swallowed it — so it concluded **"0 running"** and relaunched everything. Parse external command
output as bytes with `errors="replace"`.

**📉 "I can't see it" is not necessarily a UI bug.**
"Old snapshots aren't visible" turned out to be **a flat 30-day cut deleting one day's worth every
day.** Adding pagination alone would have produced "you can see everything, and the old ones are
already gone." → Tiered retention (7 days full / hourly to 30 / 4-hourly beyond / milestones forever)
plus zlib: **21,284 rows at 2.51 GB → 7,003 rows at 0.25 GB.**

**🧯 `max_memory_restart` is a leak backstop, not a throttle on normal peaks.**
150MB limit against a measured 145MB peak — 3% headroom. The app restarted every 30 seconds, and
pm2's SIGINT killed the in-flight cmux child with it, so the screen became "0 tabs and a wall of
warnings." **The limit wasn't the safety net; it was the outage.**

**🎨 Separate the brand layer from the meaning layer.**
Five colours on this screen already carry meaning. Dropping a signature colour on top guarantees a
collision. → Brand lives in text, borders and fills; meaning lives in a 3px bar and small icons.
**They take different forms, so they don't eat each other.** (Same reason: *lines have to be rare for
a line to be a signal.*)

**♿️ Under "reduce motion," slow it down — don't turn it off.**
The spinner was disabled with `animation:none` under `prefers-reduced-motion` and consequently
**had never once spun.** That setting is about large transitions and parallax, not about deleting a
13px progress indicator.

---

## Layout

```
app.py              FastAPI · routes · demo guards
nav.py              "where am I" collection + status decision  ← STATUS_ORDER lives here, once
cmux_client.py      cmux CLI/socket wrapper (incl. password self-heal)
snapshotter.py      normalise · hash · snapshot · independent backup
layout.py           native session JSON → split tree parser
restore.py          faithful layout restore (shared by the dashboard and the CLI skill)
claude_index.py     label / cwd / activity extraction from .jsonl
db.py               SQLite (tiered retention · zlib)
demo.py             demo mode — synthetic state in the real API's shape
demo_fixture.py     its data. Edit this alone to change the demo
static/             single-page UI (tokens.css is the only source of colour)
menubar/            Swift menu-bar minimap
```

## Requirements

- macOS · [cmux](https://cmux.com) · Python 3.11+
- Control features (focus, restore, rearrange) need cmux socket control enabled
  (`automation.socketControlMode` in `~/.config/cmux/cmux.json`).
  **Viewing and recovery work without the socket.**
- Demo mode needs neither cmux nor macOS.

## Further reading

- **[Development history](docs/HISTORY.md)** — 68 commits from 2026-07-02. The trigger, three
  identity shifts, and the debugging stories. (This repository starts from a single initial commit —
  a few of the original commits carried client and personal names and were not published.)
- **[cmux field notes](docs/CMUX-NOTES.md)** — undocumented cmux behaviour, learned by hitting it.
- **[Menu-bar minimap](menubar/README.md)** — the Swift app and the menu-bar saturation incident.

## License

[MIT](LICENSE)

<sub>This tool was built with [Claude Code](https://claude.com/claude-code), to manage Claude Code
sessions. Commit messages and code comments carry measured numbers and the traps that were stepped
in, because <b>that is the information you most wish you had when you dig at the same spot again.</b></sub>
