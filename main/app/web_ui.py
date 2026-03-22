from __future__ import annotations

import logging
from pathlib import Path

from aiohttp import web

from app.config_utils import RUNTIME_SETTINGS_SCHEMA, build_runtime_config
from app.settings import STATIC_DIR, TEMPLATES_DIR, WEB_UI_HOST, WEB_UI_PORT


log = logging.getLogger("aivtuber")
HTML_PATH = TEMPLATES_DIR / "web_ui.html"
WEB_UI_HTML = HTML_PATH.read_text(encoding="utf-8")


async def ui_index(request: web.Request) -> web.Response:
    return web.Response(
        text=WEB_UI_HTML,
        content_type="text/html",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


async def ui_state_api(request: web.Request) -> web.Response:
    app = request.app["aivtuber"]
    state = app.ui_state()
    state["runtime_settings_schema"] = request.app["runtime_settings_schema"]
    return web.json_response(state)


async def ui_send_api(request: web.Request) -> web.Response:
    app = request.app["aivtuber"]
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    mode = str(data.get("mode", "user")).strip().lower()
    text = str(data.get("text", "")).strip()
    if mode not in {"user", "cmd"}:
        return web.json_response({"error": "mode must be user or cmd"}, status=400)
    if not text:
        return web.json_response({"error": "text is required"}, status=400)

    try:
        app.submit_local_text(mode, text)
    except RuntimeError as exc:
        return web.json_response({"error": str(exc)}, status=409)
    return web.json_response({"ok": True, "message": "已发送"})


async def ui_persona_api(request: web.Request) -> web.Response:
    app = request.app["aivtuber"]
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    persona = str(data.get("persona", "")).strip().lower()
    if not persona:
        return web.json_response({"error": "persona is required"}, status=400)

    try:
        result = app.request_persona_switch(persona)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)

    if result == "noop":
        return web.json_response({"ok": True, "message": "当前已经是这个人格", "status": result})
    return web.json_response({"ok": True, "message": "人格已切换", "status": result})


async def ui_runtime_settings_api(request: web.Request) -> web.Response:
    app = request.app["aivtuber"]
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    raw_runtime = data.get("settings")
    if not isinstance(raw_runtime, dict):
        return web.json_response({"error": "settings is required"}, status=400)

    try:
        runtime = build_runtime_config(raw_runtime)
        app.update_runtime_settings(runtime.to_dict())
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=400)

    return web.json_response({"ok": True, "message": "运行配置已保存并应用"})


async def ui_exit_api(request: web.Request) -> web.Response:
    request.app["shutdown_event"].set()
    return web.json_response({"ok": True, "message": "正在退出"})


async def start_web_ui(app, shutdown_event) -> web.AppRunner:
    web_app = web.Application()
    web_app["aivtuber"] = app
    web_app["shutdown_event"] = shutdown_event
    web_app.router.add_get("/", ui_index)
    web_app.router.add_get("/api/state", ui_state_api)
    web_app.router.add_post("/api/send", ui_send_api)
    web_app.router.add_post("/api/persona", ui_persona_api)
    web_app.router.add_post("/api/runtime-settings", ui_runtime_settings_api)
    web_app.router.add_post("/api/exit", ui_exit_api)
    web_app.router.add_static(
        "/static/",
        STATIC_DIR,
        show_index=False,
        append_version=True,
    )
    web_app["runtime_settings_schema"] = RUNTIME_SETTINGS_SCHEMA

    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, host=WEB_UI_HOST, port=WEB_UI_PORT)
    await site.start()
    log.info("Web UI ready at http://%s:%s", WEB_UI_HOST, WEB_UI_PORT)
    return runner
