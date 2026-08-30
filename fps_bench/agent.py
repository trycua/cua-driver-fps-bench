"""Minimal cua-driver agent for the L-platform task (mouse-look + WASD game).

Perception is privileged (the live game state is read through
``session.execute_javascript``); every *action* goes through ``cua-driver call``
executed inside the environment, so the score isolates cua-driver's input path:

* turning  -> ``move_cursor`` (relative mouse motion drives the three.js
  PointerLockControls camera; 0.002 rad per pixel)
* walking  -> ``press_key`` "w" with ``hold_ms`` (the game moves only while a
  key is held, SPEED=6 u/s; short bursts sized from the remaining distance)
* the first action is a ``click`` on the game canvas so the page has focus and
  can request pointer lock.

The policy is deterministic and closed-loop: after each action it re-reads the
state and re-plans, so dropped or duplicated inputs are recovered from.
"""

from __future__ import annotations

import json
import math
import shlex
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from cua_bench.agents.base import AgentResult, BaseAgent, FailureMode

# Geometry / conventions mirrored from tasks/fps_lshape/gui/index.html
START = (1.5, 7.0)
GOAL = (15.0, -1.5)
RAD_PER_PX = 0.002          # PointerLockControls sensitivity
SPEED = 6.0                 # game walk speed, units/s while a key is held
HOLD_MIN_MS, HOLD_MAX_MS = 120, 500
YAW_TOL = math.radians(6)   # accept heading within ±6°
DRIVER_BIN = "cua-driver"
DRIVER_SESSION = "fps-bench"


class GameSession(Protocol):
    async def execute_javascript(self, pid: int | str, javascript: str) -> Any: ...
    async def run_command(self, command: str, *, check: bool = True) -> dict[str, Any]: ...


@dataclass
class EpisodeRecord:
    reached: bool = False
    progress: float = 0.0
    presses: int = 0            # press_key calls
    hold_ms_total: int = 0      # sum of hold_ms over press_key calls
    keydowns: int = 0           # keydowns the page saw
    mouse_moves: int = 0        # move_cursor calls
    mousemoves_seen: int = 0    # mousemove events the page saw
    mouse_px_sent: float = 0.0
    mouse_px_seen: float = 0.0
    falls: int = 0
    seconds: float = 0.0
    failure: str = ""
    driver_errors: int = 0
    action_log: list[dict[str, Any]] = field(default_factory=list)

    @property
    def delivery_ratio(self) -> float:
        return self.keydowns / self.presses if self.presses else 0.0

    @property
    def mean_hold_ms(self) -> float:
        return self.hold_ms_total / self.presses if self.presses else 0.0

    @property
    def mouse_ratio(self) -> float:
        return abs(self.mouse_px_seen) / abs(self.mouse_px_sent) if self.mouse_px_sent else 0.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["delivery_ratio"] = self.delivery_ratio
        d["mouse_ratio"] = self.mouse_ratio
        d["mean_hold_ms"] = self.mean_hold_ms
        return d


def wrap(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


def forward_of(yaw: float) -> tuple[float, float]:
    """World-space forward for a PointerLockControls yaw (yaw 0 => -Z)."""
    return (-math.sin(yaw), -math.cos(yaw))


def plan(state: dict[str, Any]) -> tuple[str, float] | None:
    """Next action for the state: ("turn", dyaw_rad) | ("walk", remaining_units) | None when done.

    Route: face -Z (yaw 0) and walk to the long leg's z band, then face +X
    (yaw -pi/2) and walk to the goal.
    """
    if state.get("reached"):
        return None
    x, z, yaw = float(state["x"]), float(state["z"]), float(state["yaw"])
    on_long_leg = z <= -1.0
    target_yaw = -math.pi / 2 if on_long_leg else 0.0
    dyaw = wrap(target_yaw - yaw)
    if abs(dyaw) > YAW_TOL:
        return ("turn", dyaw)
    if not on_long_leg:
        return ("walk", max(0.0, z - (-1.5)))   # to the middle of the long leg's z band
    return ("walk", max(0.0, GOAL[0] - x))     # may be a tiny nudge inside the goal radius


def hold_ms_for(remaining: float) -> int:
    """Key hold for one walk burst: at most 3 units per burst, clamped to 120..500 ms."""
    return max(HOLD_MIN_MS, min(HOLD_MAX_MS, int(1000 * min(remaining, 3.0) / SPEED)))


def driver_call(tool: str, args: dict[str, Any], session_id: str = DRIVER_SESSION) -> str:
    payload = dict(args)
    if session_id:
        payload["session"] = session_id
    return f"{DRIVER_BIN} call {tool} {shlex.quote(json.dumps(payload))}"


class CuaDriverAgent(BaseAgent):
    """Drives the L-platform game with cua-driver ``move_cursor`` + ``press_key`` inside the env."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.max_actions = int(kwargs.get("max_actions", kwargs.get("max_presses", 90)))
        self.time_budget = float(kwargs.get("time_budget", 300.0))
        self.settle_ms = int(kwargs.get("settle_ms", 150))
        self.window_pid: int | None = kwargs.get("window_pid")
        self.window_title = kwargs.get("window_title", "L-Platform")
        self.last_record: EpisodeRecord | None = None
        self._cursor: tuple[int, int] | None = None
        self._screen: tuple[int, int] = (1024, 768)

    @staticmethod
    def name() -> str:
        return "cua-driver-fps"

    # --- perception (privileged) ---------------------------------------------
    async def _state(self, session: GameSession, pid: int) -> dict[str, Any]:
        raw = await session.execute_javascript(pid, "JSON.stringify(window.__state)")
        return json.loads(raw) if isinstance(raw, str) else dict(raw)

    # --- driver helpers ---------------------------------------------------------
    async def _call(self, session: GameSession, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        res = await session.run_command(driver_call(tool, args), check=False)
        out = res.get("stdout") or ""
        info = {"tool": tool, "args": args, "rc": res.get("return_code", res.get("returncode")),
                "out": out[-300:], "err": (res.get("stderr") or "")[-300:]}
        try:
            info["json"] = json.loads(out)
        except (json.JSONDecodeError, ValueError):
            info["json"] = None
        return info

    async def _resolve_window(self, session: GameSession) -> dict[str, Any]:
        info = await self._call(session, "list_windows", {})
        data = info["json"] or {}
        windows = data.get("windows") or data.get("structuredContent", {}).get("windows") or []
        for w in windows:
            if self.window_title.lower() in str(w.get("title", "")).lower():
                frame = w.get("frame") or w.get("bounds") or {}
                return {"pid": w.get("pid"), "window_id": w.get("window_id") or w.get("id"), "frame": frame}
        return {}

    async def _screen_size(self, session: GameSession) -> None:
        info = await self._call(session, "get_screen_size", {})
        j = info["json"] or {}
        w, h = j.get("width"), j.get("height")
        if isinstance(w, (int, float)) and isinstance(h, (int, float)):
            self._screen = (int(w), int(h))

    def _target_args(self, target: dict[str, Any]) -> dict[str, Any]:
        args: dict[str, Any] = {}
        if target.get("pid") is not None:
            args["pid"] = target["pid"]
        if target.get("window_id") is not None:
            args["window_id"] = target["window_id"]
        return args

    async def _focus(self, session: GameSession, target: dict[str, Any]) -> dict[str, Any]:
        """Click the game canvas center: focuses the page and lets it request pointer lock."""
        frame = target.get("frame") or {}
        cx = int(frame.get("x", 0) + frame.get("width", self._screen[0]) / 2) if frame else self._screen[0] // 2
        cy = int(frame.get("y", 0) + frame.get("height", self._screen[1]) / 2) if frame else self._screen[1] // 2
        self._cursor = (cx, cy)
        return await self._call(session, "click", {"x": cx, "y": cy, **self._target_args(target)})

    async def _turn(self, session: GameSession, dyaw: float, target: dict[str, Any]) -> tuple[dict[str, Any], float]:
        """Relative mouse motion: PointerLockControls yaw decreases with +movementX."""
        dx = -dyaw / RAD_PER_PX
        dx = max(-400.0, min(400.0, dx))  # bounded so the pointer stays on screen
        cx, cy = self._cursor or (self._screen[0] // 2, self._screen[1] // 2)
        nx = int(max(2, min(self._screen[0] - 2, cx + dx)))
        moved = nx - cx
        if moved == 0:  # at the screen edge: recenter first, then move
            self._cursor = (self._screen[0] // 2, cy)
            await self._call(session, "move_cursor", {"x": self._cursor[0], "y": cy, **self._target_args(target)})
            nx = int(self._cursor[0] + dx)
            moved = nx - self._cursor[0]
        info = await self._call(session, "move_cursor", {"x": nx, "y": cy, **self._target_args(target)})
        self._cursor = (nx, cy)
        return info, float(moved)

    async def _walk(self, session: GameSession, remaining: float, target: dict[str, Any]) -> dict[str, Any]:
        hold = hold_ms_for(remaining)
        info = await self._call(session, "press_key", {"key": "w", "hold_ms": hold, **self._target_args(target)})
        info["hold_ms"] = hold
        return info

    # --- episode ----------------------------------------------------------------
    async def run_episode(self, session: GameSession, pid: int) -> EpisodeRecord:
        rec = EpisodeRecord()
        t0 = time.monotonic()
        await self._call(session, "start_session", {})
        try:
            await self._screen_size(session)
            target = await self._resolve_window(session)
            rec.action_log.append({"target": target, "screen": self._screen})
            rec.action_log.append(await self._focus(session, target))
            await session.run_command(f"sleep {self.settle_ms / 1000:.3f}", check=False)
            state = await self._state(session, pid)
            actions = 0
            while actions < self.max_actions and time.monotonic() - t0 < self.time_budget:
                step = plan(state)
                if step is None:
                    break
                kind, arg = step
                before_kd, before_mm, before_px = int(state.get("keydowns", 0)), int(state.get("mousemoves", 0)), float(state.get("mouse_dx", 0))
                if kind == "turn":
                    info, px = await self._turn(session, arg, target)
                    rec.mouse_moves += 1
                    rec.mouse_px_sent += px
                else:
                    info = await self._walk(session, arg, target)
                    rec.presses += 1
                    rec.hold_ms_total += int(info["hold_ms"])
                actions += 1
                if info["rc"] not in (0, None):
                    rec.driver_errors += 1
                await session.run_command(f"sleep {self.settle_ms / 1000:.3f}", check=False)
                state = await self._state(session, pid)
                info["delivered_keydowns"] = int(state.get("keydowns", 0)) - before_kd
                info["delivered_mousemoves"] = int(state.get("mousemoves", 0)) - before_mm
                info["delivered_px"] = float(state.get("mouse_dx", 0)) - before_px
                info["state"] = {k: state.get(k) for k in ("x", "z", "yaw", "locked")}
                rec.action_log.append(info)
            rec.reached = bool(state.get("reached"))
            rec.keydowns = int(state.get("keydowns", 0))
            rec.mousemoves_seen = int(state.get("mousemoves", 0))
            rec.mouse_px_seen = float(state.get("mouse_dx", 0))
            rec.falls = int(state.get("falls", 0))
            prog = await session.execute_javascript(pid, "window.__progress()")
            rec.progress = float(prog or 0.0)
            if not rec.reached:
                rec.failure = "max_actions" if actions >= self.max_actions else "timeout"
        except Exception as e:  # keep the record, surface the failure
            rec.failure = f"error: {e!r}"[:300]
        finally:
            rec.seconds = time.monotonic() - t0
            await self._call(session, "end_session", {})
        self.last_record = rec
        return rec

    async def perform_task(
        self, task_description: str, session: Any, logging_dir: Path | None = None
    ) -> AgentResult:
        pid = self.window_pid
        if pid is None:
            import importlib

            pid = getattr(importlib.import_module("main"), "pid", None)  # tasks/fps_lshape/main.py keeps it
        if pid is None:
            raise RuntimeError("CuaDriverAgent needs the game window pid (window_pid kwarg)")
        rec = await self.run_episode(session, int(pid))
        if logging_dir:
            Path(logging_dir).mkdir(parents=True, exist_ok=True)
            (Path(logging_dir) / "episode.json").write_text(json.dumps(rec.to_dict(), indent=2))
        mode = FailureMode.NONE if rec.reached else (
            FailureMode.MAX_STEPS_EXCEEDED if rec.failure == "max_actions" else FailureMode.UNKNOWN
        )
        return AgentResult(total_input_tokens=0, total_output_tokens=0, failure_mode=mode)


def summarize(records: list[EpisodeRecord]) -> dict[str, Any]:
    n = len(records)
    if n == 0:
        return {"episodes": 0, "score": 0.0}
    return {
        "episodes": n,
        "score": sum(r.reached for r in records) / n,
        "mean_progress": sum(r.progress for r in records) / n,
        "delivery_ratio": sum(r.delivery_ratio for r in records) / n,
        "mouse_ratio": sum(r.mouse_ratio for r in records) / n,
        "mean_presses": sum(r.presses for r in records) / n,
        "mean_hold_ms": sum(r.mean_hold_ms for r in records) / n,
        "mean_mouse_moves": sum(r.mouse_moves for r in records) / n,
        "mean_seconds": sum(r.seconds for r in records) / n,
        "falls": sum(r.falls for r in records),
        "driver_errors": sum(r.driver_errors for r in records),
        "failures": [r.failure for r in records if r.failure],
    }
