"""
krepost/health.py
Простой health-check сервер на Unix-socket.

Отдаёт JSON со статусом каждого компонента.
Запрос: echo "" | socat - UNIX-CONNECT:/tmp/krepost_health.sock
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Optional

from loguru import logger


@dataclass
class ComponentStatus:
    name: str
    healthy: bool
    detail: str = ""
    last_check: float = field(default_factory=time.time)


class HealthRegistry:
    def __init__(self):
        self._checks: Dict[str, Callable[[], ComponentStatus]] = {}

    def register(self, name: str, check: Callable[[], ComponentStatus]) -> None:
        self._checks[name] = check

    def check_all(self) -> dict:
        results = {}
        all_healthy = True
        for name, check in self._checks.items():
            try:
                status = check()
            except Exception as e:
                status = ComponentStatus(name=name, healthy=False, detail=str(e))
            results[name] = {
                "healthy": status.healthy,
                "detail": status.detail,
            }
            if not status.healthy:
                all_healthy = False
        return {
            "status": "ok" if all_healthy else "degraded",
            "components": results,
            "timestamp": time.time(),
        }


class HealthServer:
    def __init__(self, registry: HealthRegistry,
                 socket_path: str = "/tmp/krepost_health.sock"):
        self.registry = registry
        self.socket_path = socket_path
        self._server: Optional[asyncio.AbstractServer] = None

    async def _handle(self, reader: asyncio.StreamReader,
                      writer: asyncio.StreamWriter) -> None:
        try:
            result = self.registry.check_all()
            writer.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))
            writer.write(b"\n")
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async def start(self) -> None:
        sock = Path(self.socket_path)
        sock.unlink(missing_ok=True)
        self._server = await asyncio.start_unix_server(
            self._handle, path=self.socket_path)
        logger.info(f"Health server listening on {self.socket_path}")

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            Path(self.socket_path).unlink(missing_ok=True)
