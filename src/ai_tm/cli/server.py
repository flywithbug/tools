#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
DEFAULT_DATA_DIR = BASE_DIR / "data"
DEFAULT_STORE = {
    "locales": ["en", "zh"],
    "entries": {},
}


def _read_json_body(handler: SimpleHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0"))
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return {}


def _write_json(handler: SimpleHTTPRequestHandler, status: int, payload: Any) -> None:
    data = json.dumps(payload, ensure_ascii=True).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def _safe_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def _load_store(path: Path) -> dict[str, Any]:
    if not path.exists():
        return json.loads(json.dumps(DEFAULT_STORE))
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return json.loads(json.dumps(DEFAULT_STORE))


class I18nHandler(SimpleHTTPRequestHandler):
    store_path: Path

    def translate_path(self, path: str) -> str:  # Serve files from WEB_DIR
        rel = path.lstrip("/")
        if not rel:
            rel = "index.html"
        return str(WEB_DIR / rel)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            return self._handle_api_get(parsed)
        return super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            return self._handle_api_post(parsed)
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_PUT(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            return self._handle_api_put(parsed)
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_DELETE(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            return self._handle_api_delete(parsed)
        self.send_error(HTTPStatus.NOT_FOUND)

    def _handle_api_get(self, parsed) -> None:
        store = _load_store(self.store_path)
        if parsed.path == "/api/ping":
            return _write_json(self, HTTPStatus.OK, {"ok": True})
        if parsed.path == "/api/locales":
            return _write_json(self, HTTPStatus.OK, {"locales": store["locales"]})
        if parsed.path == "/api/entries":
            query = parse_qs(parsed.query)
            search = (query.get("search") or [""])[0].strip().lower()
            entries = store["entries"]
            if search:
                entries = {k: v for k, v in entries.items() if search in k.lower()}
            return _write_json(self, HTTPStatus.OK, {"entries": entries})
        if parsed.path == "/api/export":
            return _write_json(self, HTTPStatus.OK, store)
        self.send_error(HTTPStatus.NOT_FOUND)

    def _handle_api_post(self, parsed) -> None:
        store = _load_store(self.store_path)
        body = _read_json_body(self)
        if parsed.path == "/api/locales":
            locales = body.get("locales") or []
            store["locales"] = [str(x).strip() for x in locales if str(x).strip()]
            _safe_write_json(self.store_path, store)
            return _write_json(self, HTTPStatus.OK, {"locales": store["locales"]})
        if parsed.path == "/api/entries":
            key = str(body.get("key") or "").strip()
            values = body.get("values") or {}
            if not key:
                return _write_json(self, HTTPStatus.BAD_REQUEST, {"error": "key_required"})
            store["entries"].setdefault(key, {})
            for locale, text in values.items():
                store["entries"][key][str(locale)] = str(text)
            for locale in store["entries"][key].keys():
                if locale not in store["locales"]:
                    store["locales"].append(locale)
            _safe_write_json(self.store_path, store)
            return _write_json(self, HTTPStatus.OK, {"entries": store["entries"][key]})
        if parsed.path == "/api/import":
            locales = body.get("locales") or []
            entries = body.get("entries") or {}
            store = {
                "locales": [str(x).strip() for x in locales if str(x).strip()],
                "entries": {str(k): v for k, v in entries.items()},
            }
            _safe_write_json(self.store_path, store)
            return _write_json(self, HTTPStatus.OK, {"ok": True})
        self.send_error(HTTPStatus.NOT_FOUND)

    def _handle_api_put(self, parsed) -> None:
        store = _load_store(self.store_path)
        body = _read_json_body(self)
        if parsed.path.startswith("/api/entries/"):
            key = parsed.path.split("/api/entries/", 1)[1]
            if not key:
                return _write_json(self, HTTPStatus.BAD_REQUEST, {"error": "key_required"})
            values = body.get("values") or {}
            store["entries"].setdefault(key, {})
            for locale, text in values.items():
                store["entries"][key][str(locale)] = str(text)
            for locale in store["entries"][key].keys():
                if locale not in store["locales"]:
                    store["locales"].append(locale)
            _safe_write_json(self.store_path, store)
            return _write_json(self, HTTPStatus.OK, {"entries": store["entries"][key]})
        self.send_error(HTTPStatus.NOT_FOUND)

    def _handle_api_delete(self, parsed) -> None:
        store = _load_store(self.store_path)
        if parsed.path.startswith("/api/entries/"):
            key = parsed.path.split("/api/entries/", 1)[1]
            if key in store["entries"]:
                del store["entries"][key]
                _safe_write_json(self.store_path, store)
            return _write_json(self, HTTPStatus.OK, {"ok": True})
        self.send_error(HTTPStatus.NOT_FOUND)


def main() -> None:
    parser = argparse.ArgumentParser(description="Local i18n manager")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    args = parser.parse_args()

    data_dir = Path(os.path.expanduser(args.data_dir)).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    store_path = data_dir / "i18n_store.json"

    handler_class = I18nHandler
    handler_class.store_path = store_path

    os.chdir(WEB_DIR)
    server = ThreadingHTTPServer((args.host, args.port), handler_class)
    print(f"Local i18n manager running at http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
