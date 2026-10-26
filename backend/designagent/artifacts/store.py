"""Artifact registry.

An artifact is anything the frontend's right-hand pane can display or offer for
download: a Markdown summary, a .docx, a Mol* view spec, a table. Files live
under DATA_DIR/artifacts and metadata in a JSON index beside them, so artifacts
outlive the process and a reconnecting browser can still fetch them.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

ArtifactKind = Literal["markdown", "docx", "molstar", "table", "json"]

_MIME = {
    "markdown": "text/markdown; charset=utf-8",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "molstar": "application/json",
    "table": "application/json",
    "json": "application/json",
}


class ArtifactStore:
    def __init__(self, root: Path):
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._index_path = self._root / "index.json"
        self._lock = threading.RLock()
        self._index: dict[str, dict] = self._load()

    def _load(self) -> dict[str, dict]:
        if not self._index_path.exists():
            return {}
        try:
            return json.loads(self._index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def _flush(self) -> None:
        tmp = self._index_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._index, indent=2), encoding="utf-8", newline="\n")
        tmp.replace(self._index_path)

    # --- writes -------------------------------------------------------
    def add(
        self,
        kind: ArtifactKind,
        title: str,
        *,
        content: str | bytes | dict | None = None,
        suffix: str = "",
        session_id: str = "",
        meta: dict[str, Any] | None = None,
        artifact_id: str | None = None,
    ) -> dict:
        """Persist an artifact and return its reference (id/kind/title/url)."""
        aid = artifact_id or f"{kind}-{uuid.uuid4().hex[:12]}"
        suffix = suffix or _default_suffix(kind)
        path = self._root / f"{aid}{suffix}"

        if isinstance(content, (dict, list)):
            path.write_text(
                json.dumps(content, indent=2, default=str), encoding="utf-8", newline="\n"
            )
        elif isinstance(content, bytes):
            path.write_bytes(content)
        elif isinstance(content, str):
            path.write_text(content, encoding="utf-8", newline="\n")

        record = {
            "id": aid,
            "kind": kind,
            "title": title,
            "url": f"/api/artifacts/{aid}",
            "path": str(path),
            "mime": _MIME.get(kind, "application/octet-stream"),
            "session_id": session_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "meta": meta or {},
        }
        with self._lock:
            self._index[aid] = record
            self._flush()
        return self.ref(aid)

    # --- reads --------------------------------------------------------
    def ref(self, artifact_id: str) -> dict:
        """The slim form that goes into agent state and over the wire."""
        rec = self._index[artifact_id]
        return {
            "id": rec["id"],
            "kind": rec["kind"],
            "title": rec["title"],
            "url": rec["url"],
            "created_at": rec["created_at"],
        }

    def get(self, artifact_id: str) -> dict | None:
        with self._lock:
            return self._index.get(artifact_id)

    def read_bytes(self, artifact_id: str) -> bytes | None:
        rec = self.get(artifact_id)
        if not rec:
            return None
        path = Path(rec["path"])
        return path.read_bytes() if path.exists() else None

    def read_json(self, artifact_id: str) -> Any | None:
        raw = self.read_bytes(artifact_id)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    def list_for_session(self, session_id: str) -> list[dict]:
        with self._lock:
            recs = [r for r in self._index.values() if r.get("session_id") == session_id]
        recs.sort(key=lambda r: r.get("created_at", ""))
        return [self.ref(r["id"]) for r in recs]


def _default_suffix(kind: str) -> str:
    return {
        "markdown": ".md",
        "docx": ".docx",
        "molstar": ".json",
        "table": ".json",
        "json": ".json",
    }.get(kind, ".bin")
