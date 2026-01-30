from __future__ import annotations

from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles


def mount_webui(app: FastAPI) -> None:
    base = Path(__file__).resolve().parent.parent  # .../ai_tm
    static_dir = base / "webui" / "static"

    if static_dir.exists():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="webui")
    else:
        @app.get("/", response_class=HTMLResponse)
        def _root():
            return """<!doctype html>
<html>
  <head><meta charset="utf-8"><title>box_ai_tm</title></head>
  <body style="font-family: sans-serif; padding: 20px;">
    <h2>box_ai_tm server is running ✅</h2>
    <ul>
      <li><a href="/api/health">/api/health</a></li>
      <li><a href="/api/workspace">/api/workspace</a></li>
    </ul>
  </body>
</html>"""
