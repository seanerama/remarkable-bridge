"""Unit tests for the rmapi cloud transport (bridge/cloud.py).

No rmapi, no cloud, no network: the exec seam is a fake that serves checked-in fixtures
(a captured ``rmapi ls`` listing and real ``.rmdoc`` downloads built from the read-only
device fixtures under ``tests/fixtures/real/``).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from bridge.cloud import (
    CloudEntry,
    CloudTabletClient,
    CloudUnreachable,
    parse_ls,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "cloud"
NOTEBOOK_UUID = "8352580e-521a-4084-ba0c-cd62bbd915f5"  # 2 real handwriting pages
ERASED_UUID = "b2258fba-865f-45cd-b94a-8d6abe83ef10"  # 1 erased (no-stroke) page


def _ls_text() -> str:
    return (FIXTURES / "ls-outbound.txt").read_text(encoding="utf-8")


def test_parse_ls_splits_documents_and_collections():
    entries = parse_ls(_ls_text(), "/Outbound")
    assert entries == [
        CloudEntry("Learn the basics", "document", "/Outbound/Learn the basics"),
        CloudEntry("Archive", "collection", "/Outbound/Archive"),
        CloudEntry("Notebook", "document", "/Outbound/Notebook"),
    ]
    docs = [e for e in entries if e.is_document]
    assert [e.name for e in docs] == ["Learn the basics", "Notebook"]


def test_list_folder_uses_injected_seam_no_shelling():
    calls: list[list[str]] = []

    def fake_run(argv, *, cwd=None):
        calls.append(argv)
        return {"code": 0, "stdout": _ls_text(), "stderr": ""}

    client = CloudTabletClient(run=fake_run, rmapi_bin="rmapi")
    entries = client.list_folder("/Outbound")

    assert calls == [["rmapi", "ls", "/Outbound"]]
    assert len(entries) == 3


def test_list_folder_nonzero_exit_raises_cloud_unreachable():
    def fake_run(argv, *, cwd=None):
        return {"code": 1, "stdout": "", "stderr": "refresh token expired"}

    client = CloudTabletClient(run=fake_run)
    with pytest.raises(CloudUnreachable):
        client.list_folder("/Outbound")


def test_missing_rmapi_binary_is_cloud_unreachable():
    def fake_run(argv, *, cwd=None):
        raise FileNotFoundError("rmapi")

    client = CloudTabletClient(run=fake_run)
    with pytest.raises(CloudUnreachable):
        client.list_folder("/Outbound")


def test_get_unpacks_rmdoc_into_rawdoc():
    """get() runs `rmapi get`, whose seam drops the fixture .rmdoc into cwd; the client
    unpacks it into a RawDoc with the right metadata/content/pages."""

    def fake_run(argv, *, cwd=None):
        # Emulate `rmapi get <path>` writing <visibleName>.rmdoc into the download dir.
        assert argv[:2] == ["rmapi", "get"]
        shutil.copy(FIXTURES / f"{NOTEBOOK_UUID}.rmdoc", Path(cwd) / "download.rmdoc")
        return {"code": 0, "stdout": "downloading...\nOK", "stderr": ""}

    client = CloudTabletClient(run=fake_run)
    raw = client.get("/Outbound/Learn the basics")

    assert raw.doc_id == NOTEBOOK_UUID
    assert b"visibleName" in raw.metadata
    assert raw.content and raw.content != b"{}"
    assert set(raw.pages) == {
        "869a62cc-6246-4d72-b37d-1498cbf9c06c",
        "c7418c51-56bf-4a7a-b79a-6312c8fc66bd",
    }
    assert all(isinstance(b, bytes) and b for b in raw.pages.values())


def test_get_nonzero_exit_raises_cloud_unreachable():
    def fake_run(argv, *, cwd=None):
        return {"code": 1, "stdout": "", "stderr": "not found"}

    client = CloudTabletClient(run=fake_run)
    with pytest.raises(CloudUnreachable):
        client.get("/Outbound/Missing")


def test_get_empty_download_raises_cloud_unreachable():
    def fake_run(argv, *, cwd=None):
        return {"code": 0, "stdout": "OK", "stderr": ""}  # nothing written

    client = CloudTabletClient(run=fake_run)
    with pytest.raises(CloudUnreachable):
        client.get("/Outbound/Ghost")


def test_client_has_no_delete_or_write_method():
    """Structural safety rail (ADR-0003): the cloud client exposes reads only."""
    for forbidden in ("delete", "rm", "remove", "put", "upload", "overwrite", "mkdir"):
        assert not hasattr(CloudTabletClient, forbidden)
