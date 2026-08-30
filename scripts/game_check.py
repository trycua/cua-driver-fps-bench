"""Headless smoke test of the L-platform game (no desktop needed).

Run: .venv/bin/python scripts/game_check.py
Checks: a held key moves the player (a tap barely does), real mouse movement turns the camera,
the oracle route reaches the goal, walking off the edge resets.
"""

import asyncio
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tasks" / "fps_lshape"))
from main import ORACLE_STEPS, game_html  # noqa: E402

from playwright.async_api import async_playwright  # noqa: E402


async def state(pg):
    return await pg.evaluate("({x:__state.x,z:__state.z,yaw:__state.yaw,keydowns:__state.keydowns,mousemoves:__state.mousemoves,reached:__state.reached,falls:__state.falls,fell:__state.fell})")


async def main() -> int:
    async with async_playwright() as p:
        b = await p.chromium.launch(args=["--use-gl=swiftshader"])
        pg = await b.new_page(viewport={"width": 800, "height": 600})
        errs: list[str] = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        await pg.set_content(game_html())
        await pg.wait_for_timeout(600)
        print("page errors:", errs)
        assert not errs, errs

        # Real keyboard: an instant tap must barely move (no per-keydown nudge).
        await pg.keyboard.press("w")
        await pg.wait_for_timeout(100)
        s0 = await state(pg)
        print("after 1 W tap:", s0)
        assert s0["keydowns"] == 1 and 7.0 - s0["z"] < 0.3, s0
        # Holding W for 500 ms => ~SPEED*0.5 = 3 units toward -Z.
        await pg.keyboard.down("w")
        await pg.wait_for_timeout(500)
        await pg.keyboard.up("w")
        await pg.wait_for_timeout(100)
        s = await state(pg)
        print("after 500ms W hold:", s)
        assert s["keydowns"] >= 2 and 1.5 <= s0["z"] - s["z"] <= 4.5, s

        # Real mouse movement (no pointer lock in this context => fallback path): 200px right => ~-0.4 rad yaw.
        await pg.mouse.move(400, 300)
        await pg.mouse.move(600, 300, steps=4)
        await pg.wait_for_timeout(100)
        s2 = await state(pg)
        print("after mouse move:", s2)
        assert s2["mousemoves"] >= 2 and abs(abs(s2["yaw"]) - abs(s["yaw"])) > 0.2, s2

        # Oracle route via hooks.
        await pg.evaluate("window.__reset()")
        for kind, arg in ORACLE_STEPS:
            await pg.evaluate(f"window.__press({arg!r})" if kind == "key" else f"window.__look({float(arg)})")
        await pg.wait_for_timeout(100)
        s3 = await state(pg)
        print("oracle:", s3)
        assert s3["reached"] is True and s3["falls"] == 0, s3

        # Fall: walk straight past the corner.
        await pg.evaluate("window.__reset()")
        for _ in range(12):
            await pg.evaluate("window.__press('w')")
        await pg.wait_for_timeout(100)
        s4 = await state(pg)
        print("fall test:", s4)
        assert s4["fell"] is True and s4["falls"] == 1 and math.isclose(s4["z"], 7.0, abs_tol=1e-6), s4

        out = Path(__file__).resolve().parents[1] / "results" / "game_check.png"
        out.parent.mkdir(exist_ok=True)
        await pg.screenshot(path=str(out))
        await b.close()
        print("OK, screenshot:", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
