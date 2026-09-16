"""Minimal local JSONL audit without messages, arguments, output, or credentials."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class LocalAuditEvent:
    timestamp: str
    conversation_id: str
    workspace: str
    action: str
    result: str
    duration_ms: int
    request_id: str


class LocalAuditLog:
    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().resolve(strict=False)
        self._lock = asyncio.Lock()

    async def record(
        self,
        *,
        conversation_id: str,
        workspace: Path,
        action: str,
        result: str,
        duration_ms: int,
        request_id: str,
    ) -> None:
        event = LocalAuditEvent(
            timestamp=datetime.now(UTC).isoformat(),
            conversation_id=conversation_id,
            workspace=str(workspace),
            action=action,
            result=result,
            duration_ms=duration_ms,
            request_id=request_id,
        )
        encoded = json.dumps(asdict(event), ensure_ascii=False, separators=(",", ":")) + "\n"
        async with self._lock:
            await asyncio.to_thread(self._append, encoded)

    def _append(self, encoded: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="") as handle:
            handle.write(encoded)
