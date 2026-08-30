# Project brief: a first-person movement benchmark for cua-driver

## Goal

Measure how reliably [cua-driver](https://github.com/trycua/cua/tree/main/libs/cua-driver) delivers mouse and keyboard input to a first-person 3D game on a Linux desktop, then raise that score automatically by patching cua-driver and re-running the benchmark on many CUA Fleet sandboxes in parallel.

## Deliverables

### 1. Install cua-bench

`uv sync` installs cua-bench, cua-sandbox, and the Anthropic SDK. Fleet credentials come from the Keychain entry `cua-sandbox-fleet-api` or `CUA_CLIENT_ID` / `CUA_CLIENT_SECRET`.

### 2. A three.js first-person game as a cua-bench task

`tasks/fps_lshape/` renders an L-shaped platform. The player starts at the end of the short leg; a glowing goal marks the end of the long leg. Falling off resets the player and counts a fall.

Controls use prebuilt three.js libraries, vendored because the desktop is offline: the mouse turns the camera through `PointerLockControls`, `WASD` moves only while held, and `Space` jumps.

`evaluate()` returns `[reached, progress]`; the score is `reached`: did the player make the goal?

### 3. A minimal agent that acts only through cua-driver

`fps_bench/agent.py` reads the game state directly but sends every action through `cua-driver call …` inside the environment: `move_cursor` to turn, `press_key` with a `hold_ms` to walk (movement requires held keys). It re-plans after each action, so it recovers from dropped input. Each episode records whether the goal was reached, keydowns seen per press sent (`delivery_ratio`), and mouse pixels seen per pixel sent (`mouse_ratio`).

### 4. A Fleet image with the cua-driver source pre-cloned

`image/Dockerfile` adds bench_ui, Rust, and a sparse clone of `trycua/cua` at a pinned ref to `cua-ubuntu-24.04`. Because compiling Rust under amd64 emulation is impractical, supervisord compiles cua-driver natively at container boot. The image is pushed as a tag of `cua-gymdriver-dev`, the repository Fleet's pool admission allows.

### 5. Autoresearch on Fleet

[pi](https://github.com/earendil-works/pi) runs the loop with two extensions. [pi-cua](https://github.com/injaneity/pi-cua) pins a pi session to one Fleet sandbox and runs every tool call there on a copy of this repo. [pi-autoresearch](https://github.com/davebcn87/pi-autoresearch) drives try → measure → keep/revert: it edits the vendored source in `cua-driver/`, runs `.auto/measure.sh` (rebuild, benchmark, print `METRIC score=…`), and keeps a change only when the score rises.

Each experiment claims one sandbox, runs, and releases the claim. `scripts/pi_autoresearch.sh <sandbox>` runs one hill-climb per sandbox; launch several for parallel experiments. Results land in `.auto/log.jsonl`.

`fps_bench/autoresearch.py` remains as an alternative Claude-driven loop with the same claim → build → benchmark → release cycle.

## Out of scope

Training data or traces; Windows or macOS pools.
