# FPS movement benchmark for cua-driver + Fleet autoresearch — design

Date: 2026-08-29

## Goal

Measure how reliably **cua-driver** delivers keyboard input to a first-person
3D game running on a Linux desktop, and hill-climb that score automatically by
patching cua-driver's source, rebuilding, and re-running the benchmark on many
Fleet sandboxes in parallel.

## Components

| Piece | Path | Role |
| --- | --- | --- |
| Task | `tasks/fps_lshape/` | cua-bench task: three.js L-shaped platform, evaluate = reached goal |
| Agent | `fps_bench/agent.py` | `CuaDriverAgent(BaseAgent)`: closed-loop policy that acts only through `cua-driver call …` inside the environment |
| Runner | `fps_bench/runner.py` | Runs N episodes of task+agent against a session (local docker or Fleet) and writes a score JSON |
| Fleet | `fps_bench/fleet.py` | Pool (gVisor runtime) + claim/release helpers, long-running exec in the sandbox |
| Autoresearch | `fps_bench/autoresearch.py` | Parallel experiment loop: propose diff → claim → apply+build → bench → record → release |
| Image | `image/` | `FROM cua-ubuntu-24.04:docker-latest` + bench_ui + Rust + pre-cloned cua-driver source, boot-time prebuild |

## The game (`tasks/fps_lshape/gui/index.html`)

* L-shaped platform: short leg 3×8 (start at its far end, facing the corner),
  long leg 3×16 along +X from the corner, glowing goal at its far end.
* Controls: mouse look via three.js `PointerLockControls` (vendored r147; a
  no-lock fallback feeds `movementX/Y` into the same controls object), `W/A/S/D`
  move only while held (6 u/s velocity; a press-and-release tap does not move,
  so the driver must hold keys — `press_key` with `hold_ms`), `Space` jump. Stepping off the platform resets to the
  start and counts a fall.
* State is exposed at `window.__state` (`x, z, yaw, reached, falls, keydowns,
  mousemoves, mouse_dx, locked, keys{}`) and `window.__progress()`;
  `window.__press(key)` / `window.__look(deg)` drive the
  same code path for the oracle.
* three.js is vendored (`gui/three.min.js`) and inlined by `main.py` because the
  desktop has no internet.

`evaluate()` returns `[reached (0/1), progress (0..1)]`. `reached` is the
benchmark metric; progress is diagnostic.

## The agent

The benchmark isolates the **input path**: perception is privileged (the agent
reads `window.__state` through `session.execute_javascript`), action is not — every
key goes through `cua-driver call press_key '{"key": …}'` executed *inside* the
environment via `session.run_command`. The policy is deterministic: face the
corner, walk the short leg, turn to +X, walk the long leg, re-plan from the live
state after every press (so dropped or duplicated keys are recovered from). An
episode ends on goal, on a step budget, or on a wall-clock budget.

Per-episode record: reached, progress, presses sent, keydowns observed by the
page, falls, wall time. Score = mean(reached) over episodes; secondary =
keydowns/presses (delivery ratio).

## Environments

* **Local**: `cb run task tasks/fps_lshape --agent-import-path fps_bench.agent:CuaDriverAgent`
  (docker, image `public.ecr.aws/k5j5w0x5/cua-ubuntu-24.04:docker-latest`, which is
  what cua-sandbox uses for local docker on trycua/cua main). Or
  `fps_bench/runner.py --local`, which uses cua-bench as a library.
* **Fleet**: Fleet's pool-admission policy only admits images from a fixed set of
  repositories, so the custom image is pushed as a tag of
  `296062593712.dkr.ecr.us-west-2.amazonaws.com/cua-gymdriver-dev` and the pool
  template uses `runtime = gvisor` (plain container, not a KubeVirt containerDisk).
  The cua-sandbox `Pool.apply` helper can't set the runtime, so `fleet.py` builds
  the template/pool requests with `fleet_sdk` directly and then uses
  `Sandbox.create(pool=…)` for claims. The runner connects cua-bench's
  `RemoteDesktopSession(api_url=<fleet service url>)` (client-only mode) to the
  sandbox's computer-server on port 8000.

## Image

Built for `linux/amd64` on the Mac with buildx. Compiling Rust under amd64
emulation is impractical, so the image only pre-fetches crates; a supervisord
program (`cua-driver-prebuild`) compiles `cua-driver` natively at container boot
and installs it to `/usr/local/bin/cua-driver`. Warm pool members are therefore
already built when claimed, and a patched rebuild is incremental.

## Autoresearch loop

```
best = baseline (empty diff)
workers (N parallel):
  loop:
    diff = propose(best.diff, history)            # Claude, sees cua-driver source excerpts + past results
    sb   = claim(pool)                            # Sandbox.create(pool=…, name=exp-…)
    wait prebuild.done; git apply diff; cargo build --release; install
    result = bench(sb, episodes)                  # runner against the sandbox
    record(results/experiments.jsonl)
    if result.score > best.score: best = (diff, score)   # hill-climb, ties keep best
    release(sb)                                   # sb.close() releases the claim
```

Diffs are cumulative on top of the current best (the proposer receives the best
diff and produces a full replacement diff against the pinned `CUA_REF`). Build or
apply failures are recorded as score −1 and never become best. Every run also
records the raw per-episode data so regressions can be inspected later.

## Primary loop: pi + pi-cua + pi-autoresearch (added 2026-08-29)

Per the user's direction the agent loop is pi with two extensions rather than the
custom Claude loop above (which stays as an alternative in `fps_bench/autoresearch.py`):

* **pi-cua** pins a pi session to a Fleet Linux sandbox; every tool call (bash,
  edit, …) runs there in a workspace cloned from this repo's origin at the local
  commit plus an uncommitted overlay. That is why the repo is pushed to
  `github.com/r33drichards/cua-driver-fps-bench` and why `cua-driver/` is
  **vendored** (a plain copy of `libs/cua-driver/rust` at `cua-driver/UPSTREAM_REF`)
  instead of pre-cloned in an image: pi-autoresearch's keep/revert is git-based
  and the sandbox sees exactly the repo.
* **pi-autoresearch** owns the hill-climb: `.auto/prompt.md` (objective, metrics,
  files in scope), `.auto/measure.sh` (bootstrap guest → `cargo build --release`
  → `bench/run_in_sandbox.py` → `METRIC score=…`), `.auto/log.jsonl`.
* Parallelism = one pi session per sandbox (`scripts/pi_autoresearch.sh <sandbox>`),
  each an independent hill-climb on its own branch; results are merged by hand
  (or with `scripts/pi_collect.sh`, to be written once a session has run).
* Headless driving: `scripts/pi_sandbox.py` wraps the pi-cua backend CLI
  (`create` is an async operation; `bind` writes the session→sandbox mapping the
  extension reads at `session_start`), then `pi --session-id … -p "<prompt>"`.

## Fleet findings (2026-08-29, from the fleet-debug subagent)

* **gVisor container pools are admitted but unusable today**: the pool operator
  builds the pod with `resources: {}` (BestEffort), and the host recycles
  BestEffort runsc sandboxes ~80 s after start → CrashLoopBackOff. Fix belongs in
  `cloud/osgym/pool-operator/pod_backend.py` (map `cpuCores`/`memory` to
  requests/limits). Until then `image/Dockerfile` (the pre-cloned image, pushed to
  `cua-gymdriver-dev`) cannot be scheduled through Fleet.
* **KubeVirt VM pools were unschedulable during the session** ("0/28 nodes
  available: Insufficient memory/cpu"; autoscaler did not add nodes in 25 min),
  which is also why pi-cua's `create fps-a` timed out. The VM path
  (`fleet.ensure_vm_pool` + `ensure_bootstrapped`) is the one to use once
  capacity returns.
* Diagnosis recipe: `scripts/fleet_probe.py` (gateway k8s proxy: pods, pod logs,
  VMIs, osgymsandboxes) and `scripts/fleet_status.py <pool>`.

## Non-goals (YAGNI)

* Gamepad / touch input — mouse (PointerLockControls) + keyboard are the only
  paths under test. (Mouse-look was added on 2026-08-29 at the user's request;
  the earlier discrete-turn keys are gone.)
* Training data / traces — the benchmark is a scalar score plus JSON.
* Windows / macOS pools.
