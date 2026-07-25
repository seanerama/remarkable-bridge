"""reMarkable **cloud** transport — the read side of the `tablet-document` contract
over the reMarkable cloud API (works off-network, unlike the LAN-SSH `TabletClient`).

Two things live here:

* ``CloudTabletClient`` — shells the ``rmapi`` CLI (ddvk fork; configured on the host at
  ``~/.config/rmapi/rmapi.conf``) to ``ls <folder>`` and ``get <doc>``. The ``rmapi`` exec
  is an **injected seam** (``run(argv, cwd=...) -> {code, stdout, stderr}``) so tests never
  shell ``rmapi`` or touch the cloud. It exposes **only reads** — ``list_folder`` and
  ``get``; there is deliberately **no delete/overwrite/put method at all** (safety rail,
  ADR-0003 posture, mirroring ``TabletClient``).

* ``CloudUnreachable`` — the cloud/rmapi analogue of ``TabletUnreachable``: a *transport*
  failure (rmapi missing, auth/network error, nonzero exit, or a download that yielded no
  document). The watcher treats it as transient — log, change no state, retry next cycle.

``rmapi get <path>`` downloads the document as a single ``<visibleName>.rmdoc`` file into the
current working directory. A ``.rmdoc`` is a plain ZIP (verified read-only on 2026-07-24)
whose entries are ``<uuid>.metadata``, ``<uuid>.content``, an optional ``<uuid>.pdf``, and —
for a notebook — page strokes at ``<uuid>/<page>.rm``. :meth:`CloudTabletClient.get` unpacks
that into a :class:`~bridge.tablet.RawDoc`, so the existing ``extract()`` and ``parse_doc()``
work unchanged.
"""

from __future__ import annotations

import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol, runtime_checkable

from .tablet import RawDoc

DEFAULT_RMAPI_BIN = "rmapi"

# The injected exec seam: run an argv, return the captured result. ``cwd`` lets ``get``
# point ``rmapi``'s download at a scratch dir. Kept as a plain dict so a fake in tests is a
# one-liner and no import is needed to build one.
ExecResult = dict  # {"code": int, "stdout": str, "stderr": str}


@runtime_checkable
class ExecFn(Protocol):
    def __call__(self, argv: list[str], *, cwd: str | None = None) -> ExecResult: ...


class CloudUnreachable(Exception):
    """rmapi/cloud could not be reached (binary missing, auth/network failure, nonzero
    exit, or an empty download). Mirrors :class:`~bridge.tablet.TabletUnreachable`: the
    watcher logs it, changes no state, and retries next cycle — never crashes, never
    double-submits."""


@dataclass(frozen=True)
class CloudEntry:
    """One entry from ``rmapi ls <folder>``."""

    name: str
    type: str  # "document" | "collection"
    path: str  # full cloud path, e.g. "/Outbound/My Note"

    @property
    def is_document(self) -> bool:
        return self.type == "document"


def _default_run(argv: list[str], *, cwd: str | None = None) -> ExecResult:
    """Production exec seam: shell the argv, capture output, decode leniently."""
    proc = subprocess.run(argv, capture_output=True, cwd=cwd)
    return {
        "code": proc.returncode,
        "stdout": proc.stdout.decode("utf-8", "replace"),
        "stderr": proc.stderr.decode("utf-8", "replace"),
    }


def _join(folder: str, name: str) -> str:
    return f"{folder.rstrip('/')}/{name}"


def parse_ls(stdout: str, folder: str) -> list[CloudEntry]:
    """Parse ``rmapi ls`` default output into :class:`CloudEntry` records.

    The default format is one entry per line, ``[d]<TAB><name>`` (collection) or
    ``[f]<TAB><name>`` (document). Blank/other lines are ignored (forward-compatible).
    """
    entries: list[CloudEntry] = []
    for line in stdout.splitlines():
        line = line.rstrip("\n")
        if not line.strip():
            continue
        marker, tab, name = line.partition("\t")
        if not tab:
            continue
        marker = marker.strip()
        name = name.strip()
        if not name:
            continue
        if marker == "[f]":
            kind = "document"
        elif marker == "[d]":
            kind = "collection"
        else:
            continue
        entries.append(CloudEntry(name=name, type=kind, path=_join(folder, name)))
    return entries


def _raw_from_zip(zip_path: Path) -> RawDoc:
    """Unpack a ``.rmdoc``/``.zip`` download into a :class:`RawDoc`."""
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        # The top-level (no-slash) *.metadata entry names the document uuid.
        meta_name = next(
            (n for n in names if n.endswith(".metadata") and "/" not in n), None
        )
        if meta_name is None:
            raise CloudUnreachable(f"{zip_path.name}: archive has no .metadata entry")
        doc_id = meta_name[: -len(".metadata")]
        metadata = z.read(meta_name)
        content_name = f"{doc_id}.content"
        content = z.read(content_name) if content_name in names else b"{}"
        pages: dict[str, bytes] = {}
        prefix = f"{doc_id}/"
        for n in names:
            if n.startswith(prefix) and n.endswith(".rm"):
                page_id = Path(n).name[: -len(".rm")]
                pages[page_id] = z.read(n)
    return RawDoc(doc_id=doc_id, metadata=metadata, content=content, pages=pages)


def _raw_from_dir(root: Path) -> RawDoc:
    """Fallback: build a :class:`RawDoc` from an already-extracted document directory
    (``<uuid>.metadata`` + ``<uuid>.content`` + ``<uuid>/<page>.rm``)."""
    meta = next((p for p in sorted(root.glob("*.metadata"))), None)
    if meta is None:
        raise CloudUnreachable(f"{root}: no .metadata after download")
    doc_id = meta.name[: -len(".metadata")]
    content_path = root / f"{doc_id}.content"
    pages: dict[str, bytes] = {}
    page_dir = root / doc_id
    if page_dir.is_dir():
        for rm in sorted(page_dir.glob("*.rm")):
            pages[rm.name[: -len(".rm")]] = rm.read_bytes()
    return RawDoc(
        doc_id=doc_id,
        metadata=meta.read_bytes(),
        content=content_path.read_bytes() if content_path.exists() else b"{}",
        pages=pages,
    )


def raw_from_download(download_dir: Path) -> RawDoc:
    """Turn whatever ``rmapi get`` dropped in ``download_dir`` into a :class:`RawDoc`.

    Handles both the observed shape (a single ``*.rmdoc``/``*.zip`` archive) and a
    defensively-supported already-extracted directory. Raises :class:`CloudUnreachable`
    when the download produced nothing usable.
    """
    archives = sorted(download_dir.glob("*.rmdoc")) + sorted(download_dir.glob("*.zip"))
    if archives:
        return _raw_from_zip(archives[0])
    return _raw_from_dir(download_dir)


class CloudTabletClient:
    """Read-only reMarkable cloud client (rmapi transport). No delete/overwrite exists."""

    def __init__(self, *, run: ExecFn | Callable | None = None, rmapi_bin: str = DEFAULT_RMAPI_BIN) -> None:
        self._run = run or _default_run
        self.rmapi_bin = rmapi_bin

    def _rmapi(self, args: list[str], *, cwd: str | None = None) -> ExecResult:
        """Invoke ``rmapi <args>`` through the injected seam; a missing binary is
        unreachability, not a crash."""
        try:
            return self._run([self.rmapi_bin, *args], cwd=cwd)
        except FileNotFoundError as exc:
            raise CloudUnreachable(f"rmapi unavailable: {exc}") from exc

    def list_folder(self, folder: str) -> list[CloudEntry]:
        """List a cloud folder. Nonzero exit / rmapi failure => :class:`CloudUnreachable`."""
        res = self._rmapi(["ls", folder])
        if res["code"] != 0:
            raise CloudUnreachable(
                f"rmapi ls {folder!r} failed (exit {res['code']}): {res['stderr'].strip()}"
            )
        return parse_ls(res["stdout"], folder)

    def get(self, doc_path: str) -> RawDoc:
        """Download one document and unpack it into a :class:`RawDoc`.

        ``rmapi get`` writes ``<visibleName>.rmdoc`` into a scratch dir we control; we then
        unpack that archive. Nonzero exit / empty download => :class:`CloudUnreachable`.
        """
        with tempfile.TemporaryDirectory(prefix="rmapi-get-") as td:
            res = self._rmapi(["get", doc_path], cwd=td)
            if res["code"] != 0:
                raise CloudUnreachable(
                    f"rmapi get {doc_path!r} failed (exit {res['code']}): {res['stderr'].strip()}"
                )
            return raw_from_download(Path(td))
