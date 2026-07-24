"""Transport-layer tests for SubprocessTabletClient: batched scan, incremental change
detection + marker, and SSH-unreachable handling (Stage 2).

These exercise the real orchestration (scan_raw / scan_raw_changed / _batch_cat / page
pulls / marker persistence) with a purely in-memory fake ``_ssh`` — no device, no network.
The reachability tests drive the real ``_ssh`` with a monkeypatched ``subprocess.run`` to
prove transport failures map to ``TabletUnreachable`` while a merely-nonzero remote command
does not.
"""

from __future__ import annotations

import json
import re
import subprocess

import pytest

from bridge import tablet
from bridge.tablet import SubprocessTabletClient, TabletUnreachable

XO = "/xo"


class FakeDevice(SubprocessTabletClient):
    """SubprocessTabletClient whose ``_ssh`` interprets our commands against an in-memory FS.

    ``fs`` maps a relative path (``"u1.metadata"``, ``"u1/pid.rm"``) to bytes; ``mtimes``
    maps ``.metadata``/``.content`` rels to epoch seconds (drives change detection).
    """

    def __init__(self, fs, mtimes, **kw):
        self.fs = fs
        self.mtimes = mtimes
        self.ssh_calls: list[str] = []
        super().__init__(host="fake", xochitl=XO, **kw)

    def _ssh(self, cmd: str) -> bytes:  # type: ignore[override]
        self.ssh_calls.append(cmd)
        if "-exec stat" in cmd:  # incremental change detection
            lines = [
                f"{epoch} {XO}/{rel}"
                for rel, epoch in self.mtimes.items()
                if rel.endswith((".metadata", ".content"))
            ]
            return ("\n".join(lines) + "\n").encode()
        if "printf" in cmd:  # batched cat
            rels = re.findall(r"printf '\\036%s\\036' (\S+);", cmd)
            out = b""
            for rel in rels:
                out += b"\x1e" + rel.encode() + b"\x1e" + self.fs.get(rel, b"")
            return out
        if "-name '*.metadata'" in cmd:  # full listing
            paths = sorted(f"{XO}/{rel}" for rel in self.fs if rel.endswith(".metadata"))
            return ("\n".join(paths) + "\n").encode()
        if cmd.startswith("cat "):  # single page read
            full = cmd[len("cat "):].strip().strip("'")
            rel = full[len(XO) + 1:]
            if rel in self.fs:
                return self.fs[rel]
            raise subprocess.CalledProcessError(1, cmd)
        raise AssertionError(f"unexpected command: {cmd}")


def _fv2_content(page_id: str) -> bytes:
    return json.dumps(
        {"formatVersion": 2, "tags": [], "pageTags": [],
         "cPages": {"pages": [{"id": page_id, "template": "Blank"}]}}
    ).encode()


def _fv1_content(page_ids: list[str]) -> bytes:
    return json.dumps(
        {"formatVersion": 1, "tags": [], "pageTags": [],
         "pages": page_ids, "redirectionPageMap": list(range(len(page_ids)))}
    ).encode()


def _meta(name: str) -> bytes:
    return json.dumps({"visibleName": name, "type": "DocumentType", "parent": ""}).encode()


def _device(**kw) -> FakeDevice:
    fs = {
        "u1.metadata": _meta("Doc One"),
        "u1.content": _fv2_content("pageA"),
        "u1/pageA.rm": b"strokes-A",
        "u2.metadata": _meta("Doc Two"),
        "u2.content": _fv1_content(["pageX", "pageY"]),  # fv1; no .rm on device
    }
    mtimes = {
        "u1.metadata": 100, "u1.content": 100,
        "u2.metadata": 300, "u2.content": 300,
    }
    return FakeDevice(fs, mtimes, **kw)


# ---- full scan (used by ensure_folder) ---------------------------------------
def test_scan_raw_reads_all_docs_batched():
    dev = _device()
    raws = {r.doc_id: r for r in dev.scan_raw()}
    assert set(raws) == {"u1", "u2"}
    assert raws["u1"].pages == {"pageA": b"strokes-A"}  # fv2 page pulled
    assert raws["u2"].pages == {}  # fv1 page ids present but no .rm bytes -> skipped, no crash
    # exactly one batched cat call for all metadata+content (not one-per-file)
    assert sum("printf" in c for c in dev.ssh_calls) == 1


def test_scan_raw_parses_both_format_versions():
    docs = {d.id: d for d in tablet.scan(_device())}
    assert docs["u1"].visible_name == "Doc One"
    assert docs["u2"].visible_name == "Doc Two"
    assert docs["u1"].content_hash != docs["u2"].content_hash


# ---- incremental change detection + marker -----------------------------------
def test_scan_raw_changed_first_run_pulls_all_then_advances_marker():
    dev = _device()
    first = {r.doc_id for r in dev.scan_raw_changed()}
    assert first == {"u1", "u2"}  # marker started at 0
    assert dev._last_marker == 300  # advanced to max mtime seen


def test_scan_raw_changed_second_run_pulls_nothing():
    dev = _device()
    dev.scan_raw_changed()  # marker -> 300
    second = dev.scan_raw_changed()
    assert second == []  # reachable, nothing new (NOT an error)


def test_scan_raw_changed_pulls_only_the_touched_doc():
    dev = _device()
    dev.scan_raw_changed()  # marker -> 300
    dev.mtimes["u1.content"] = 400  # edit u1
    changed = dev.scan_raw_changed()
    assert [r.doc_id for r in changed] == ["u1"]
    assert dev._last_marker == 400


def test_marker_persists_across_client_instances(tmp_path):
    marker = tmp_path / "marker"
    dev1 = _device(marker_path=marker)
    dev1.scan_raw_changed()
    assert marker.read_text() == "300"
    # A fresh client loads the marker and pulls nothing until something moves.
    dev2 = _device(marker_path=marker)
    assert dev2._last_marker == 300
    assert dev2.scan_raw_changed() == []


# ---- SSH reachability: transport failure -> TabletUnreachable ----------------
def _patch_run(monkeypatch, *, result=None, exc=None):
    def fake_run(argv, **kw):
        if exc is not None:
            raise exc
        return result
    monkeypatch.setattr(tablet.subprocess, "run", fake_run)


def test_ssh_connection_refused_maps_to_unreachable(monkeypatch):
    _patch_run(monkeypatch, result=subprocess.CompletedProcess(
        [], returncode=255, stdout=b"", stderr=b"ssh: connect ... Connection refused"))
    with pytest.raises(TabletUnreachable):
        SubprocessTabletClient(host="nope")._ssh("echo hi")


def test_ssh_timeout_maps_to_unreachable(monkeypatch):
    _patch_run(monkeypatch, exc=subprocess.TimeoutExpired(cmd="ssh", timeout=1))
    with pytest.raises(TabletUnreachable):
        SubprocessTabletClient(host="nope")._ssh("echo hi")


def test_ssh_binary_missing_maps_to_unreachable(monkeypatch):
    _patch_run(monkeypatch, exc=FileNotFoundError("ssh"))
    with pytest.raises(TabletUnreachable):
        SubprocessTabletClient(host="nope")._ssh("echo hi")


def test_remote_nonzero_is_not_unreachable(monkeypatch):
    # A remote command that merely exits nonzero (e.g. cat of a missing file) is a normal
    # CalledProcessError the readers handle locally — NOT a transport failure.
    _patch_run(monkeypatch, result=subprocess.CompletedProcess(
        [], returncode=1, stdout=b"", stderr=b"cat: no such file"))
    with pytest.raises(subprocess.CalledProcessError):
        SubprocessTabletClient(host="nope")._ssh("cat /nope")


def test_ssh_success_returns_stdout(monkeypatch):
    _patch_run(monkeypatch, result=subprocess.CompletedProcess(
        [], returncode=0, stdout=b"hello", stderr=b""))
    assert SubprocessTabletClient(host="ok")._ssh("echo hello") == b"hello"
