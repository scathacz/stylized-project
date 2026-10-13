# -*- coding: utf-8 -*-
"""Isolated 落笺 server for Playwright 速记 tests."""
from __future__ import annotations

import os
import socket
import tempfile
import threading
import time
from pathlib import Path


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def start_note_server(data_dir: Path | None = None) -> tuple[str, Path]:
    data = Path(data_dir or tempfile.mkdtemp(prefix="luojian-note-"))
    data.mkdir(parents=True, exist_ok=True)
    os.environ["LUOJIAN_DATA"] = str(data)
    from desk import archive, db

    db.DATA = data
    db.DB_PATH = data / "desk.db"
    archive.ARCHIVE = data / "archive"
    db.init_db()

    import uvicorn
    import app as appmod

    port = _free_port()
    config = uvicorn.Config(appmod.app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 8
    while time.time() < deadline:
        if getattr(server, "started", False):
            break
        try:
            with socket.create_connection(("127.0.0.1", port), 0.2):
                break
        except OSError:
            time.sleep(0.05)
    else:
        raise RuntimeError("速记测试服务没有起来")
    return f"http://127.0.0.1:{port}/note", data
