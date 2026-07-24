"""Tablet transport + the `tablet-document` contract reader.

Two layers live here:

* ``TabletClient`` — the injected SSH transport seam (ADR-0003). It exposes raw
  file access + a create-only upload path. **It has no delete or overwrite method
  at all** — the safety rail is structural, not a convention (ADR-0003,
  ``response-publish``). ``SubprocessTabletClient`` is the production impl (shells
  out to ``ssh``/``scp`` via a ``~/.ssh/config`` host alias); ``FakeTabletClient``
  (in tests) serves fixtures and records uploads.

* The ``tablet-document`` contract reader — pure functions that turn the raw
  ``.metadata`` + ``.content`` (+ page ``.rm`` bytes) into a normalized
  :class:`TabletDoc`. This is the reference implementation of that frozen contract.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable
from uuid import uuid4

# reMarkable on-device notebook root (ADR-0003).
XOCHITL = "/home/root/.local/share/remarkable/xochitl"


# --------------------------------------------------------------------------- #
# Raw transport records
# --------------------------------------------------------------------------- #
@dataclass
class RawDoc:
    """Raw bytes for one document UUID, as read off the tablet (no parsing)."""

    doc_id: str
    metadata: bytes
    content: bytes
    pages: dict[str, bytes] = field(default_factory=dict)  # page_id -> .rm bytes


@dataclass
class TabletDoc:
    """Normalized record consumed by the rest of the pipeline (contract: tablet-document)."""

    id: str
    visible_name: str
    parent: str
    type: str  # "DocumentType" | "CollectionType"
    last_modified: int
    deleted: bool
    tags: list[str]
    content_hash: str


# --------------------------------------------------------------------------- #
# The injected transport seam — NO delete / overwrite methods exist.
# --------------------------------------------------------------------------- #
@runtime_checkable
class TabletClient(Protocol):
    """SSH scan/pull/upload seam. Create-only: no delete, no overwrite (ADR-0003)."""

    def scan_raw(self) -> list[RawDoc]:
        """Enumerate every document UUID and return its raw metadata/content/pages."""
        ...

    def ensure_folder(self, path: str) -> str:
        """Ensure a collection at ``path`` (e.g. ``/Claude/Responses``) exists; return its uuid."""
        ...

    def upload(self, *, doc_id: str, visible_name: str, parent: str, pdf_bytes: bytes) -> None:
        """Create a new PDF document under ``parent``. Create-only — never overwrites."""
        ...


# --------------------------------------------------------------------------- #
# Contract reader (tablet-document) — pure, transport-independent, unit-tested.
# --------------------------------------------------------------------------- #
def _tag_names(raw_tags: object) -> list[str]:
    """Normalize one tag array into lowercased names, tolerating documented variants.

    Handles: list of ``{"name": ...}`` dicts, bare ``["review"]`` string lists, and
    ignores anything else. Unknown fields inside a dict are ignored (forward-compatible).
    """
    names: list[str] = []
    if not isinstance(raw_tags, list):
        return names
    for entry in raw_tags:
        if isinstance(entry, str):
            names.append(entry.strip().lower())
        elif isinstance(entry, dict):
            name = entry.get("name")
            if isinstance(name, str):
                names.append(name.strip().lower())
    return names


def read_tags(content: dict) -> list[str]:
    """Union of document-level + page-level tag names, lowercased (contract: tablet-document).

    Tolerates the v6 shape (``tags`` + ``cPages.pages[].tags``) and older/alternate
    shapes (bare string lists, top-level ``pageTags``). Unknown fields are ignored.
    """
    names: list[str] = []
    # Document-level tags.
    names += _tag_names(content.get("tags"))
    # Page-level tags, v6 shape: cPages.pages[].tags
    cpages = content.get("cPages")
    if isinstance(cpages, dict):
        pages = cpages.get("pages")
        if isinstance(pages, list):
            for page in pages:
                if isinstance(page, dict):
                    names += _tag_names(page.get("tags"))
    # Legacy/alternate: a flat top-level pageTags array.
    names += _tag_names(content.get("pageTags"))
    # De-dup preserving order.
    seen: set[str] = set()
    ordered: list[str] = []
    for n in names:
        if n and n not in seen:
            seen.add(n)
            ordered.append(n)
    return ordered


def compute_content_hash(raw: RawDoc) -> str:
    """sha256 over the .content bytes + every page .rm bytes (dedup key).

    Re-tagging or editing a page changes .content or a .rm blob, so the hash changes
    (drives dedup — contract bridge-state).
    """
    h = hashlib.sha256()
    h.update(raw.content)
    for page_id in sorted(raw.pages):
        h.update(page_id.encode("utf-8"))
        h.update(raw.pages[page_id])
    return h.hexdigest()


def parse_doc(raw: RawDoc) -> TabletDoc:
    """Turn one :class:`RawDoc` into a normalized :class:`TabletDoc`."""
    meta = json.loads(raw.metadata.decode("utf-8")) if raw.metadata else {}
    content = json.loads(raw.content.decode("utf-8")) if raw.content else {}

    parent = str(meta.get("parent", "") or "")
    deleted = bool(meta.get("deleted", False)) or parent == "trash"

    return TabletDoc(
        id=raw.doc_id,
        visible_name=str(meta.get("visibleName", "") or ""),
        parent=parent,
        type=str(meta.get("type", "DocumentType") or "DocumentType"),
        last_modified=int(meta.get("lastModified", 0) or 0),
        deleted=deleted,
        tags=read_tags(content),
        content_hash=compute_content_hash(raw),
    )


def scan(client: TabletClient) -> list[TabletDoc]:
    """Scan the tablet and return normalized :class:`TabletDoc` records."""
    return [parse_doc(raw) for raw in client.scan_raw()]


def is_routable(doc: TabletDoc, route_tags: set[str]) -> bool:
    """A doc is routable if not deleted, is a document, and carries a configured route tag."""
    if doc.deleted or doc.type != "DocumentType":
        return False
    return any(tag in route_tags for tag in doc.tags)


def route_for(doc: TabletDoc, route_tags: set[str]) -> str | None:
    """Return the first configured route tag present on the doc, else None."""
    for tag in doc.tags:
        if tag in route_tags:
            return tag
    return None


# --------------------------------------------------------------------------- #
# Production transport — subprocess ssh/scp (create-only).
# --------------------------------------------------------------------------- #
class SubprocessTabletClient:
    """Real ``TabletClient`` that shells out to ``ssh``/``scp`` via a host alias.

    No delete/overwrite method is defined — writes are create-only (ADR-0003).
    """

    def __init__(self, host: str = "remarkable", xochitl: str = XOCHITL) -> None:
        self.host = host
        self.xochitl = xochitl

    def _ssh(self, cmd: str) -> bytes:
        return subprocess.run(
            ["ssh", self.host, cmd], check=True, capture_output=True
        ).stdout

    def _read(self, remote_rel: str) -> bytes:
        return self._ssh(f"cat {self.xochitl}/{remote_rel}")

    def scan_raw(self) -> list[RawDoc]:
        listing = self._ssh(
            f"ls -1 {self.xochitl} 2>/dev/null || true"
        ).decode("utf-8", "replace")
        ids = sorted(
            {
                line[: -len(".metadata")]
                for line in listing.splitlines()
                if line.endswith(".metadata")
            }
        )
        docs: list[RawDoc] = []
        for doc_id in ids:
            try:
                metadata = self._read(f"{doc_id}.metadata")
            except subprocess.CalledProcessError:
                continue
            try:
                content = self._read(f"{doc_id}.content")
            except subprocess.CalledProcessError:
                content = b"{}"
            pages: dict[str, bytes] = {}
            page_listing = self._ssh(
                f"ls -1 {self.xochitl}/{doc_id} 2>/dev/null || true"
            ).decode("utf-8", "replace")
            for line in page_listing.splitlines():
                if line.endswith(".rm"):
                    page_id = line[: -len(".rm")]
                    pages[page_id] = self._read(f"{doc_id}/{line}")
            docs.append(RawDoc(doc_id=doc_id, metadata=metadata, content=content, pages=pages))
        return docs

    def _put(self, data: bytes, remote_rel: str) -> None:
        """scp bytes to ``xochitl/<remote_rel>``. Create-only by construction — we only
        ever write freshly-minted uuids, never an existing document's path."""
        with tempfile.NamedTemporaryFile() as tf:
            tf.write(data)
            tf.flush()
            subprocess.run(
                ["scp", "-q", tf.name, f"{self.host}:{self.xochitl}/{remote_rel}"],
                check=True,
                capture_output=True,
            )

    def _create_collection(self, coll_id: str, name: str, parent: str) -> None:
        now = str(int(time.time() * 1000))
        meta = {
            "visibleName": name,
            "type": "CollectionType",
            "parent": parent,
            "deleted": False,
            "lastModified": now,
            "metadatamodified": True,
            "modified": True,
            "pinned": False,
            "synced": False,
            "version": 0,
        }
        self._put(json.dumps(meta).encode("utf-8"), f"{coll_id}.metadata")
        self._put(b"{}", f"{coll_id}.content")

    def ensure_folder(self, path: str) -> str:
        """Resolve or create the collection tree for ``path``; return the leaf uuid.

        Create-only: existing collections are reused; missing ones are minted with new
        uuids. Nothing is ever deleted or overwritten.
        """
        segments = [s for s in path.strip("/").split("/") if s]
        docs = scan(self)
        parent = ""
        for seg in segments:
            match = next(
                (
                    d
                    for d in docs
                    if d.type == "CollectionType"
                    and d.visible_name == seg
                    and d.parent == parent
                    and not d.deleted
                ),
                None,
            )
            if match is not None:
                parent = match.id
            else:
                new_id = str(uuid4())
                self._create_collection(new_id, seg, parent)
                parent = new_id
        return parent

    def upload(self, *, doc_id: str, visible_name: str, parent: str, pdf_bytes: bytes) -> None:
        """Create a new PDF document under ``parent`` (create-only, never overwrites).

        ``doc_id`` is a freshly-minted uuid supplied by the caller, so the write cannot
        collide with — let alone overwrite — an existing tablet document.
        """
        now = str(int(time.time() * 1000))
        meta = {
            "visibleName": visible_name,
            "type": "DocumentType",
            "parent": parent,
            "deleted": False,
            "lastModified": now,
            "metadatamodified": True,
            "modified": True,
            "pinned": False,
            "synced": False,
            "version": 0,
        }
        content = {
            "fileType": "pdf",
            "formatVersion": 1,
            "pageCount": 0,
            "coverPageNumber": 0,
            "documentMetadata": {},
            "extraMetadata": {},
        }
        self._put(pdf_bytes, f"{doc_id}.pdf")
        self._put(json.dumps(content).encode("utf-8"), f"{doc_id}.content")
        self._put(json.dumps(meta).encode("utf-8"), f"{doc_id}.metadata")
