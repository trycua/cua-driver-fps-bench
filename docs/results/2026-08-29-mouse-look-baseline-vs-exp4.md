# Mouse-look benchmark: baseline vs. the kept patch (2026-08-29)

Same sandbox (`fps-c`, Fleet Ubuntu 24.04 VM, Xtigervnc `:1`, WebKitGTK window), same
game, same agent. The agent acts only through `cua-driver call`: one `click` to focus,
`move_cursor` to turn, `press_key w` to walk. cua-driver 0.22.2 built from source, two
episodes each, 15 fps screen capture of the desktop.

| | Baseline (cua-driver `main`) | Exp 4 kept patch |
|---|---|---|
| video | [`mouse-look-baseline-score0.0.mp4`](../../results/videos/mouse-look-baseline-score0.0.mp4) (3:23) | [`mouse-look-patched-score0.8.mp4`](../../results/videos/mouse-look-patched-score0.8.mp4) (1:43) |
| score | **0.00** (0/2) | **1.00** (2/2 in this recording; **0.8** over the 5-episode confirmation) |
| mouse_ratio | 0.018 | 0.319 |
| progress | 0.36 — stuck at the corner, `lock=false` | 0.95 |
| keys delivered | 16/16 | 42/42 |

Side-by-side page with both videos embedded (open locally, no network needed):
[`results/videos/mouse-look-compare.html`](../../results/videos/mouse-look-compare.html)
(rebuild with `scripts/build_video_page.py`).

## What you see

**Baseline.** Keys land, but the canvas click is a synthetic XSendEvent, so WebKit never
grants pointer lock (`lock=false` in the HUD). Every `move_cursor` moves only cua-driver's
overlay cursor; the camera stays at yaw −44° (needs −90°), the agent walks into the corner
and stops.

**Exp 4.** A real XTest button event counts as a user gesture; pointer lock engages
(`lock=true`) and the absolute XTest warps behind `move_cursor` produce `movementX`. Only
about a third of each delta survives the browser's recenter, but it is unbiased, so the
closed-loop agent converges, turns right and reaches the green goal.

## The change

`platform-linux/src/tools/impl_.rs`: `ClickTool` now treats WebKitGTK embedders the way
`press_key` already does — when the target is a WebKitGTK process the click takes the
foreground XTest rung instead of the background XSendEvent route. Every other target keeps
the no-focus-steal contract. Relative-motion variants (warp to `anchor+dx`) delivered full
`movementX` but with an asymmetric recenter bias that drifted yaw and collapsed the score to
0 (Exp 5–7, discarded).

Diff: [`results/videos/exp4-click-xtest.diff`](../../results/videos/exp4-click-xtest.diff) ·
branch `autoresearch/fps-c-20260829-6039fb8e` · found by a pi-autoresearch session in 5
experiments (log in `.auto/log.jsonl` on that branch).

## Update 2026-08-30: held-key game

The game no longer moves on taps (movement only while a key is held, 6 u/s), and the
Linux `press_key` gained `hold_ms`. Same sandbox, cua-driver built from this repo's
vendored source:

| | `main` (hold_ms, no Exp 4) | `main` + Exp 4 |
|---|---|---|
| video | [`held-keys-main-score0.0.mp4`](../../results/videos/held-keys-main-score0.0.mp4) | [`held-keys-exp4-score1.0.mp4`](../../results/videos/held-keys-exp4-score1.0.mp4) |
| score | **0.00** (0/2) | **1.00** (3/3) |
| walking | 3 held presses to the corner (progress 0.39) | 9 held presses per episode |
| mouse_ratio | 0.014 (57 moves, no turn) | 0.319 (18 moves) |
| episode time | max_actions | ~42 s |

Side by side (left `main`, right + Exp 4):
[`held-keys-side-by-side.mp4`](../../results/videos/held-keys-side-by-side.mp4);
tap-step era: [`mouse-look-side-by-side.mp4`](../../results/videos/mouse-look-side-by-side.mp4).

## Reproduce

```bash
# on the sandbox workspace (needs ffmpeg; see scripts/record_ab.sh)
bash scripts/record_ab.sh origin/main 2      # records baseline then current branch
```
