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
import shlex
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable
from uuid import uuid4

# reMarkable on-device notebook root (ADR-0003).
XOCHITL = "/home/root/.local/share/remarkable/xochitl"


class TabletUnreachable(Exception):
    """The tablet could not be reached over SSH (connect refused / timeout / no route).

    Distinct from "reachable, nothing new". The watcher treats this as *transient*:
    log it, change no state, retry next cycle (never crash, never reprocess) —
    ADR-0005, spec acceptance test #4. It signals a *transport* failure, NOT a remote
    command that merely exited nonzero (e.g. ``cat`` of a missing page), which stays a
    plain :class:`subprocess.CalledProcessError` the readers handle locally.
    """


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


def page_ids(content: dict) -> list[str]:
    """Enumerate a document's page ids, tolerating BOTH on-device format versions.

    Verified on-device (2026-07-24, ``docs/device-schema.md``):

    * ``formatVersion: 2`` → pages are **objects** under ``cPages.pages[]``, each with an
      ``id`` (and ``template``/``idx``/...).
    * ``formatVersion: 1`` → ``pages`` is a **flat top-level array of id strings** (plus a
      sibling ``redirectionPageMap``); there is no ``cPages``.

    Either shape yields the same ``[page_id, ...]``; unknown shapes yield ``[]`` rather
    than raising, so a malformed/novel ``.content`` never crashes a scan.
    """
    def _ids_from(seq: object) -> list[str]:
        out: list[str] = []
        if not isinstance(seq, list):
            return out
        for entry in seq:
            if isinstance(entry, str):
                out.append(entry)
            elif isinstance(entry, dict) and isinstance(entry.get("id"), str):
                out.append(entry["id"])
        return out

    cpages = content.get("cPages")
    if isinstance(cpages, dict) and isinstance(cpages.get("pages"), list):
        return _ids_from(cpages["pages"])  # formatVersion 2
    return _ids_from(content.get("pages"))  # formatVersion 1 (flat string array)


def _parse_cat_stream(out: bytes) -> dict[str, bytes]:
    """Parse the ``\\x1e``-delimited batched-``cat`` stream into ``{rel_path: bytes}``.

    The stream is ``\\x1e<rel>\\x1e<file-bytes>`` repeated per file (see
    :meth:`SubprocessTabletClient._batch_cat`). Only text ``.metadata``/``.content`` JSON
    flows through here, so the record separator can never collide with file content.
    """
    result: dict[str, bytes] = {}
    parts = out.split(b"\x1e")
    it = iter(parts[1:])  # parts[0] is the empty lead-in before the first separator
    for rel_b, body in zip(it, it):
        result[rel_b.decode("utf-8", "replace")] = body
    return result


def _parse_stat_lines(out: bytes, marker: int) -> tuple[set[str], int]:
    """Parse ``<epoch> <path>`` lines into ``(changed_doc_ids, max_epoch_seen)``.

    A doc id is "changed" when its ``.metadata`` or ``.content`` mtime is strictly newer
    than ``marker``. ``max_epoch_seen`` becomes the next marker (see change detection in
    :meth:`SubprocessTabletClient.scan_raw_changed`).
    """
    changed: set[str] = set()
    max_epoch = marker
    for line in out.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line:
            continue
        epoch_s, _, path = line.partition(" ")
        try:
            epoch = int(epoch_s)
        except ValueError:
            continue
        base = path.rsplit("/", 1)[-1]
        if base.endswith(".metadata"):
            doc_id = base[: -len(".metadata")]
        elif base.endswith(".content"):
            doc_id = base[: -len(".content")]
        else:
            continue
        if epoch > marker:
            changed.add(doc_id)
        if epoch > max_epoch:
            max_epoch = epoch
    return changed, max_epoch


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
    """Scan the tablet and return normalized :class:`TabletDoc` records.

    Reachability: if the client cannot reach the tablet it raises
    :class:`TabletUnreachable`, which propagates here unchanged — callers distinguish
    "unreachable" (retry next cycle, no state change) from "reachable, nothing new"
    (an empty list). "Nothing new" is never an error.
    """
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

    def __init__(
        self,
        host: str = "remarkable",
        xochitl: str = XOCHITL,
        *,
        marker_path: Path | str | None = None,
        connect_timeout: int = 15,
        timeout: int = 120,
    ) -> None:
        self.host = host
        self.xochitl = xochitl
        # Change-detection marker: the max .metadata/.content mtime (epoch seconds) seen
        # on the last scan. Persisted locally on the *watcher host* (never written to the
        # tablet — the device stays strictly read-only bar create-only uploads, ADR-0003).
        self._marker_path = Path(marker_path).expanduser() if marker_path else None
        self._last_marker = self._load_marker()
        self.connect_timeout = connect_timeout
        self.timeout = timeout

    # ---- SSH transport ------------------------------------------------------ #
    def _ssh(self, cmd: str) -> bytes:
        """Run one remote command; raise :class:`TabletUnreachable` on TRANSPORT failure.

        A transport failure (connect refused/timeout/no route — ssh's own exit 255, a
        client timeout, or a missing ``ssh`` binary) is unreachability. A remote command
        that merely exits nonzero (e.g. ``cat`` of a missing file) is NOT — it surfaces
        as :class:`subprocess.CalledProcessError` for the caller to handle locally.
        """
        argv = [
            "ssh",
            "-o", "BatchMode=yes",
            "-o", f"ConnectTimeout={self.connect_timeout}",
            self.host,
            cmd,
        ]
        try:
            proc = subprocess.run(argv, capture_output=True, timeout=self.timeout)
        except FileNotFoundError as exc:  # ssh not installed on this host
            raise TabletUnreachable(f"ssh unavailable: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise TabletUnreachable(f"ssh to {self.host!r} timed out after {self.timeout}s") from exc
        if proc.returncode == 255:  # ssh's own "could not connect" exit code
            stderr = proc.stderr.decode("utf-8", "replace").strip()
            raise TabletUnreachable(f"ssh to {self.host!r} failed: {stderr}")
        if proc.returncode != 0:
            raise subprocess.CalledProcessError(proc.returncode, argv, proc.stdout, proc.stderr)
        return proc.stdout

    def _read(self, remote_rel: str) -> bytes:
        return self._ssh(f"cat {shlex.quote(f'{self.xochitl}/{remote_rel}')}")

    def _batch_cat(self, rels: list[str]) -> dict[str, bytes]:
        """Read many files in ONE ssh round-trip (contract/ADR-0003: batched cat).

        Emits ``\\x1e<rel>\\x1e<bytes>`` per file over a single connection; missing files
        yield empty bytes (``|| true``) rather than aborting the batch. Used for the small
        text ``.metadata``/``.content`` JSON only — binary page ``.rm`` bytes are read
        individually (they could contain the separator).
        """
        if not rels:
            return {}
        parts = []
        for rel in rels:
            remote = shlex.quote(f"{self.xochitl}/{rel}")
            parts.append(f"printf '\\036%s\\036' {shlex.quote(rel)}; cat {remote} 2>/dev/null || true")
        return _parse_cat_stream(self._ssh("; ".join(parts)))

    def _read_pages(self, doc_id: str, content: bytes) -> dict[str, bytes]:
        """Pull a doc's page ``.rm`` bytes, driven by the parsed content (both formats)."""
        try:
            parsed = json.loads(content) if content else {}
        except (ValueError, UnicodeDecodeError):
            parsed = {}
        pages: dict[str, bytes] = {}
        for pid in page_ids(parsed):
            try:
                pages[pid] = self._read(f"{doc_id}/{pid}.rm")
            except subprocess.CalledProcessError:
                continue  # not every page has strokes (e.g. blank PDF pages)
        return pages

    def _read_docs(self, ids: list[str]) -> list[RawDoc]:
        """Materialize :class:`RawDoc`s for ``ids``: one batched cat + per-doc page pulls."""
        if not ids:
            return []
        rels = [f"{i}.metadata" for i in ids] + [f"{i}.content" for i in ids]
        blobs = self._batch_cat(rels)
        docs: list[RawDoc] = []
        for doc_id in ids:
            metadata = blobs.get(f"{doc_id}.metadata") or b""
            if not metadata:
                continue  # no metadata → not a real document dir
            content = blobs.get(f"{doc_id}.content") or b"{}"
            docs.append(
                RawDoc(
                    doc_id=doc_id,
                    metadata=metadata,
                    content=content,
                    pages=self._read_pages(doc_id, content),
                )
            )
        return docs

    def scan_raw(self) -> list[RawDoc]:
        """Full scan of every document (unchanged contract; used by ``ensure_folder``).

        Enumerates all ``.metadata`` via one ``find``, then a single batched ``cat`` for
        every doc's metadata+content, then per-doc page bytes. Kept exhaustive on purpose
        so ``ensure_folder`` always sees the whole collection tree; the poll loop should
        prefer :meth:`scan_raw_changed`.
        """
        listing = self._ssh(
            f"find {shlex.quote(self.xochitl)} -maxdepth 1 -name '*.metadata'"
        ).decode("utf-8", "replace")
        ids = sorted(
            {
                line.rsplit("/", 1)[-1][: -len(".metadata")]
                for line in listing.splitlines()
                if line.strip().endswith(".metadata")
            }
        )
        return self._read_docs(ids)

    def scan_raw_changed(self) -> list[RawDoc]:
        """Incremental scan: return only docs changed since the last marker; advance it.

        One ``find ... -exec stat`` lists ``(mtime, path)`` for every ``.metadata``/
        ``.content`` in a single round-trip; docs whose mtime exceeds the stored marker are
        pulled (metadata+content batched, then their pages). The marker advances to the max
        mtime seen and is persisted, so the next cycle pulls nothing unless something moved.
        First run (marker 0) pulls everything, exactly like :meth:`scan_raw`.

        Tradeoff (spec §4): a metadata-only edit still triggers a page pull for that one
        doc, because we recompute its ``content_hash`` before deciding — the downstream
        ``(uuid, content_hash)`` dedup (contract ``bridge-state``) makes that a no-op, and
        unchanged docs are never touched at all. It never pulls the *whole tree* of pages.

        Reachability: an unreachable tablet raises :class:`TabletUnreachable` from ``_ssh``
        *before* the marker is advanced, so a failed cycle leaves state untouched.
        """
        out = self._ssh(
            f"find {shlex.quote(self.xochitl)} -maxdepth 1 "
            r"\( -name '*.metadata' -o -name '*.content' \) "
            r"-exec stat -c '%Y %n' {} \;"
        )
        changed, max_epoch = _parse_stat_lines(out, self._last_marker)
        docs = self._read_docs(sorted(changed))
        # Advance the marker only after a successful pull (no partial-progress on failure).
        self._last_marker = max_epoch
        self._save_marker(max_epoch)
        return docs

    # ---- marker persistence (watcher host, never the tablet) ---------------- #
    def _load_marker(self) -> int:
        if self._marker_path and self._marker_path.exists():
            try:
                return int(self._marker_path.read_text(encoding="utf-8").strip() or "0")
            except (ValueError, OSError):
                return 0
        return 0

    def _save_marker(self, epoch: int) -> None:
        if not self._marker_path:
            return
        self._marker_path.parent.mkdir(parents=True, exist_ok=True)
        self._marker_path.write_text(str(int(epoch)), encoding="utf-8")

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
