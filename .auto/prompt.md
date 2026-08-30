# Autoresearch: cua-driver key delivery in a first-person game

## Objective
Make `cua-driver call press_key` reliably deliver key presses to a pywebview
(WebKitGTK) window on the X11 XFCE desktop of this Linux sandbox. The benchmark
(`bench/run_in_sandbox.py`) launches a three.js first-person game on an L-shaped
platform (`tasks/fps_lshape/gui/index.html`), and an agent (`fps_bench/agent.py`)
that reads the game state but can only act through cua-driver: `move_cursor`
(relative mouse motion turns the camera via three.js PointerLockControls, 0.002
rad/px) and `press_key` "w" with `hold_ms` (movement requires held keys: the game
moves 6 u/s only while a key is down, so a tap without a hold goes nowhere). The first
action is a `click` on the canvas (focus + pointer-lock request). The episode
succeeds when the goal is reached.

## Metrics
- **Primary**: `score` (fraction of episodes that reach the goal, 0..1, higher is better)
- **Secondary**: `delivery_ratio` (keydowns seen by the page / press_key calls) and
  `mouse_ratio` (mouse pixels the page saw / pixels sent via move_cursor) — the
  most sensitive signals; raise these first. Also `mean_progress`, `mean_presses`,
  `mean_mouse_moves`, `falls`, `driver_errors`, `bench_seconds`

## How to Run
`./.auto/measure.sh` — bootstraps guest deps (idempotent), rebuilds cua-driver
(`cua-driver/rust`, release), runs the benchmark, prints `METRIC name=value` lines.
The first run also compiles from scratch (several minutes); later runs are incremental.
Set `EPISODES=1` for a fast signal while iterating, `EPISODES=5` to confirm a keep.

Useful diagnostics inside the sandbox (DISPLAY=:1):
- `cua-driver/rust/target/release/cua-driver call list_windows '{}'` — is the
  "L-Platform" window visible to the driver, and with what pid/window_id?
- `... call press_key '{"key":"w","pid":<pid>}'` vs `'{"key":"w","delivery_mode":"foreground"}'`
- `xdotool key w` — control: does the page count a keydown from XTest at all?
- `.auto/runs/*.json` — per-press log (`key_log[].delivered`) for the last run.

## Files in Scope
- `cua-driver/rust/crates/platform-linux/src/**` — Linux backend: X11 input
  injection (XSendEvent / XTest / XInput2), window targeting, AT-SPI, focus handling.
- `cua-driver/rust/crates/cua-driver-core/src/**` — driver core / tool dispatch,
  only where the Linux press_key path needs it.
- `cua-driver/rust/crates/cua-driver/src/**` — CLI/tool plumbing for press_key
  (e.g. resolving a window target when none is given). Note: movement requires
  held keys, so `press_key` must honour `hold_ms` (`{"key":"w","pid":..,"hold_ms":<0..5000>}`,
  key down for that long, then up); the agent walks with 120..500 ms holds.

## Off Limits
- `bench/`, `fps_bench/`, `tasks/` — the benchmark and the agent are the fixed yardstick.
- `.auto/measure.sh` metric names.
- macOS / Windows platform crates (must still compile but do not change behavior).

## Constraints
- `cargo build --release -p cua-driver` must succeed; keep `cargo check` clean.
- No new system dependencies beyond what `bench/bootstrap_guest.sh` installs.
- Prefer the smallest change that raises `delivery_ratio`; keep the no-focus-steal
  ("background") contract as the default and only escalate to a foreground/XTest
  path when the background path provably cannot land.

## Known-good idea from a sibling loop (2026-08-29, Chromium variant, gVisor pool)
`results/pi-sessions/peer-best-xtest-when-focused.diff` reached score 1.0 (from 0.0)
on a Chromium build of this game: in `platform-linux/src/input/mod.rs::send_key`,
if `x11_focus_is_within(display, xid)` already holds, route through `send_key_xtest`
(no activation, still "background"); and drop the blanket
`unavailable_gtk_keyboard_background` refusal in `PressKeyTool`. Keys already land on
this WebKitGTK build (delivery_ratio 1.0 on main), so the open problem here is
`move_cursor` → real pointer motion (`mouse_ratio`); apply the same "when the target
owns focus, use XTest" idea to pointer motion.

## gVisor (Fleet container) baseline, 2026-08-30 — image cua-driver-bench-20260830b
Same game, cua-driver main (0.22.2) built at boot, `serve` daemon up, 0 driver errors:
score 0.0, 0/90 keys. Probe on the sandbox: `press_key {pid}` (default background)
is refused with `background_unavailable` ("target has no focus-free input backend")
BEFORE the WebKitGTK auto-escalation runs; `delivery_mode: foreground` delivers;
`move_cursor` moves only the overlay. The Exp 2 gating patch
(`results/pi-sessions/fps-a-640fe99c-exp2-3.patch`) and `peer-best-xtest-when-focused`
target exactly this refusal. WebKitWebProcess/WebKitNetworkProcess ARE visible under
/proc there, so detection is not the problem; the refusal ordering is.

## What's Been Tried
- 2026-08-29 baseline (stock cua-driver 0.4.2 on a Fleet Ubuntu 24.04 VM, XFCE on
  Xtigervnc :1, pywebview/WebKitGTK window): score 0.00, delivery_ratio 0.00 over
  180 press_key calls; move_cursor moved only the overlay cursor (page saw no
  mousemove). Control experiment on the same window: `xdotool key w` (XTest, focused
  window) → keydown delivered; `xdotool key --window <id> w` (XSendEvent) → NOT
  delivered; `xdotool mousemove` (XTest) → mousemove delivered. Conclusion: WebKitGTK
  ignores synthetic XSendEvent key events; XTest-style injection into the focused
  window works. press_key without pid/window fails with "No windows found for pid 0".
- Harness gaps fixed on the remote tip (commits 52c521f / 815b8b0 / e795f84):
  `measure.sh` now starts `cua-driver serve --dangerously-bypass-approvals` (the
  `call` subcommand does NOT auto-spawn the daemon — without this every press_key
  errors "daemon is not running"), exports XAUTHORITY, and builds on a 5G tmpfs
  with CARGO_BUILD_JOBS=4 (the VM root disk is ~10 GB and the release build OOMs
  the small Fleet VM if run naively). `bootstrap_guest.sh` grants the non-root
  `cua` user X access (the XFCE desktop runs as root, so `cua` gets "Authorization
  required / cannot open display :1" without an xauth copy + `xhost`).
- Exp 1 (committed by a parallel agent from this session's patch): press_key
  removes the `unavailable_webkit_keyboard_background` refusal and adds
  `deliver_fg = delivery.is_foreground() || (!foreground && is_webkitgtk_embedder(pid))`
  so WebKitGTK targets auto-escalate to the foreground XTest rung. **BUT this is
  currently dead code**: the very next check, `unavailable_gtk_keyboard_background`,
  fires for pywebview (WebKitGTK links libgtk → `is_gtk_process()` is true) and
  returns a `background_unavailable` refusal BEFORE `deliver_fg` ever runs. So
  press_key still errors out every call → delivery_ratio stays 0. Confirmed by
  reading the handler; a live measurement is pending sandbox availability.
- Exp 2 (this iteration): gate `unavailable_gtk_keyboard_background` on
  `!is_webkit_target` (hoisted `is_webkitgtk_embedder(pid)` once). Now WebKitGTK
  embedders skip the GTK refusal and fall through to the auto-escalation, so the
  foreground XTest key actually fires. Plain (non-WebKit) GTK apps keep the
  refusal. Minimal, unblocks the already-committed intent. Live measurement pending.
- Exp 3 (this iteration): move_cursor default `scope=window` only moves the
  synthetic overlay, so the page's PointerLockControls sees no mousemove
  (mouse_ratio=0). Added: when the target pid is a WebKitGTK embedder, also inject
  a REAL absolute XTest motion via `send_move_xtest_desktop(xi, yi)`. The agent
  drives yaw with absolute screen coords whose per-step delta equals the intended
  movementX, and after the focus click the real pointer sits at the agent's initial
  cursor — so an absolute XTest move to (xi, yi) yields movementX = dx. Non-WebKit
  targets keep the "don't move the user's pointer" contract. Live measurement
  pending; risk: under Chromium/WebKitGTK pointer-lock, absolute XTest warps may
  not produce the expected movementX delta (may need relative XTest motion instead).

### Infrastructure blocker (2026-08-29)
- The Fleet warm pool `cua-pi-linux-rw` started returning **403 Forbidden** for
  `osgymsandboxwarmpools` (credentials/auth revoked), and VMs kept going offline
  mid cargo-build (OOM/reclaim). A live VM (fps-b-2) was recovered via direct
  Tailscale SSH; measurements are being retried there with CARGO_BUILD_JOBS=1.
- The pi-cua controller's `backend.py` pinned `uv run --python 3.11` while
  `cua-sandbox==0.3.4` now requires `>=3.12,<3.14` — patched locally to `3.12`
  so `cua_sandbox ensure` can re-provision.
- `bootstrap_guest.sh` does NOT pip-install `cua-bench` (only `cua-bench-ui` +
  `pywebview`); `fps_bench/agent.py` imports `cua_bench.agents.base`. Provisioning
  must `pip install cua-bench` after bootstrap (done manually per VM).
