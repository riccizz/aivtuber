# -*- coding: utf-8 -*-
"""AIVtuber entrypoint.

Keep this file focused on startup/orchestration only.
Runtime logic lives in `aivtuber_app.py`, and the local browser UI lives in
`web_ui.py`.
"""

from __future__ import annotations

import aiohttp
import asyncio
import argparse
import contextlib
import http.cookies
import logging
import os
import shutil
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Optional

MAIN_DIR = Path(__file__).resolve().parent
ROOT_DIR = MAIN_DIR.parent
THIRD_PARTY_DIR = ROOT_DIR / "third_party"

for path in (str(MAIN_DIR), str(THIRD_PARTY_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

from app.core import AIVtuberApp
from app.settings import CONFIG_EXAMPLE_PATH, CONFIG_PATH, WEB_UI_HOST, WEB_UI_PORT
from app.config_utils import AppConfig
from app.web_ui import start_web_ui


session: Optional[aiohttp.ClientSession] = None
SESSDATA = ""
log = logging.getLogger("aivtuber")


def init_session() -> None:
    cookies = http.cookies.SimpleCookie()
    cookies["SESSDATA"] = SESSDATA
    cookies["SESSDATA"]["domain"] = "bilibili.com"

    global session
    session = aiohttp.ClientSession()
    session.cookie_jar.update_cookies(cookies)


def open_web_ui(url: str) -> None:
    candidates: list[list[str]] = []

    if os.environ.get("WSL_DISTRO_NAME"):
        candidates.append(["cmd.exe", "/c", "start", "", url])

    candidates.append(["xdg-open", url])

    for cmd in candidates:
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            log.info("Tried opening browser via: %s", cmd[0])
            return
        except Exception:
            continue

    try:
        if webbrowser.open(url, new=2):
            log.info("Opened browser via webbrowser module")
            return
    except Exception:
        pass

    log.warning("Could not auto-open browser. Open this URL manually: %s", url)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the AIVtuber app.")
    parser.add_argument(
        "--local",
        action="store_true",
        help="Enable local-only mode for this run, without changing config.json.",
    )
    return parser.parse_args()


def ensure_local_config() -> None:
    if CONFIG_PATH.exists():
        return
    if not CONFIG_EXAMPLE_PATH.exists():
        raise FileNotFoundError(f"Missing config files: {CONFIG_PATH} and {CONFIG_EXAMPLE_PATH}")
    shutil.copyfile(CONFIG_EXAMPLE_PATH, CONFIG_PATH)
    log.warning("config.json was missing. Created it from config.example.json.")


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)

    init_session()
    args = parse_args()
    ensure_local_config()
    config = AppConfig.from_json(CONFIG_PATH)
    if args.local:
        config.local_only = True
    app = AIVtuberApp(config)

    shutdown_event = asyncio.Event()
    ui_runner = await start_web_ui(app, shutdown_event)
    open_web_ui(f"http://{WEB_UI_HOST}:{WEB_UI_PORT}")
    threading.Thread(target=app.initialize_runtime, daemon=True).start()
    local_only = config.local_only

    try:
        if local_only:
            await shutdown_event.wait()
        else:
            client_task = asyncio.create_task(app.run_single_client())
            shutdown_task = asyncio.create_task(shutdown_event.wait())
            done, pending = await asyncio.wait(
                {client_task, shutdown_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.gather(*pending)
            if client_task in done:
                await client_task
    finally:
        app.shutdown()
        await ui_runner.cleanup()
        if session is not None:
            await session.close()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
