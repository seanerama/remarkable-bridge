"""Local JSON dedup state (contract: bridge-state).

Makes the pipeline idempotent across poll cycles and restarts. Dedup key is
``(uuid, last_hash)`` — a doc is reprocessed only when its ``content_hash`` changes,
never merely because it was seen. Writes are atomic (temp + fsync + os.replace) so a
crash mid-write cannot corrupt the file. A doc is recorded only *after* a successful
publish (enforced by the caller), so a failed run retries next cycle rather than being lost.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .tablet import TabletDoc

DEFAULT_STATE_PATH = Path.home() / ".local" / "state" / "remarkable-bridge" / "state.json"
SCHEMA_VERSION = 1


class State:
    """In-memory view of the dedup state, backed by an atomically-written JSON file."""

    def __init__(self, path: Path, documents: dict[str, dict] | None = None) -> None:
        self.path = path
        self.documents: dict[str, dict] = documents or {}

    # ---- persistence -------------------------------------------------------- #
    @classmethod
    def load(cls, path: Path = DEFAULT_STATE_PATH) -> "State":
        path = Path(path)
        if not path.exists():
            return cls(path=path, documents={})
        data = json.loads(path.read_text(encoding="utf-8"))
        version = data.get("version", 1)
        if version > SCHEMA_VERSION:
            raise ValueError(
                f"state.json version {version} is newer than supported {SCHEMA_VERSION}"
            )
        documents = data.get("documents") or {}
        return cls(path=path, documents=documents)

    def save(self) -> None:
        """Atomic write: temp file in the same dir → fsync → os.replace."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": SCHEMA_VERSION, "documents": self.documents}
        blob = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")

        fd, tmp_name = tempfile.mkstemp(
            dir=str(self.path.parent), prefix=self.path.name + ".", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(blob)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_name, self.path)
        except BaseException:
            # Best-effort cleanup of the temp file; never leave it behind on failure.
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass
            raise

    # ---- dedup logic -------------------------------------------------------- #
    def seen(self, doc: TabletDoc) -> bool:
        """True if this doc was already processed at its current ``content_hash``."""
        entry = self.documents.get(doc.id)
        return bool(entry) and entry.get("last_hash") == doc.content_hash

    def record(self, doc: TabletDoc, route: str, result_status: str) -> None:
        """Record a successfully-published run. Caller must call :meth:`save` to commit."""
        self.documents[doc.id] = {
            "last_hash": doc.content_hash,
            "last_mtime": doc.last_modified,
            "route": route,
            "status": result_status,
            "processed_at": datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
        }
