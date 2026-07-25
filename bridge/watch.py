"""Outbound watcher — the INBOX half of the reMarkable <-> assistant bridge (engine side).

One cycle (:func:`run_once`):

    CloudTabletClient.list_folder("/Outbound")   # rmapi, off-network
      -> for each document: dedup on (uuid, content_hash)   (contract: bridge-state)
      -> new/changed: get() -> extract(render_handwriting=True)  (reuse Stage 3)
      -> shell `nightshift submit --type note-ingest --params <json>`  (contract: note-submission)
      -> record state ONLY after a successful submit (exit 0)

This SUPERSEDES the old review/execute dispatcher (``bridge/watcher.py``) as the product
entry point: notes leaving the tablet in ``/Outbound`` are handed to the nightshift-assistant,
which is the brain (interpret + deliver). There is no local agent here.

Design rails:

* Both external boundaries are **injected seams**: ``rmapi`` inside
  :class:`~bridge.cloud.CloudTabletClient`, and ``nightshift submit`` as the ``submit``
  exec seam passed to :func:`run_once`. Tests use fakes; nothing shells out or hits a
  network.
* State is recorded **only after** a submit exits 0, so a submit failure or a
  :class:`~bridge.cloud.CloudUnreachable` retries next cycle and never double-submits.
* A note with neither typed text nor rendered images is **not submitted** (contract
  ``note-submission``: nothing to act on).
* One bad document never crashes the cycle; the ``[watch] enabled`` kill-switch (default
  **OFF**) keeps the daemon loop inert until an operator flips it on.
"""

from __future__ import annotations

import json
import os
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .cloud import CloudTabletClient, CloudUnreachable, ExecFn, _default_run
from .extract import extract
from .state import State
from .tablet import parse_doc

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = PACKAGE_DIR / "config.toml"

_TRUTHY = {"1", "true", "yes", "on"}

NOTE_INGEST_TYPE = "note-ingest"


@dataclass
class WatchConfig:
    """Config for the Outbound watcher (config.toml ``[cloud]`` + ``[watch]``)."""

    enabled: bool
    folder: str
    rmapi_bin: str
    nightshift_bin: str
    staging_dir: Path
    poll_interval: int
    state_path: Path

    @classmethod
    def load(cls, path: Path = DEFAULT_CONFIG) -> "WatchConfig":
        data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
        cloud = data.get("cloud", {})
        watch = data.get("watch", {})
        paths = data.get("paths", {})

        enabled = bool(watch.get("enabled", False))
        env_enabled = os.environ.get("WATCH_ENABLED")
        if env_enabled is not None:
            enabled = env_enabled.strip().lower() in _TRUTHY

        rmapi_bin = os.environ.get("RMAPI_BIN") or cloud.get("rmapi_bin", "rmapi")
        nightshift_bin = os.environ.get("NIGHTSHIFT_BIN") or cloud.get("nightshift_bin", "nightshift")

        return cls(
            enabled=enabled,
            folder=cloud.get("folder", "/Outbound"),
            rmapi_bin=rmapi_bin,
            nightshift_bin=nightshift_bin,
            staging_dir=Path(
                cloud.get("staging_dir", "~/.local/share/remarkable-bridge/notes")
            ).expanduser(),
            poll_interval=int(cloud.get("poll_interval", 60)),
            state_path=Path(
                paths.get("state", "~/.local/state/remarkable-bridge/state.json")
            ).expanduser(),
        )


@dataclass
class WatchReport:
    """What one poll cycle did — handy for tests, logging, and the daemon loop."""

    submitted: list[str] = field(default_factory=list)  # note ids submitted this cycle
    skipped_seen: list[str] = field(default_factory=list)  # dedup skips
    skipped_empty: list[str] = field(default_factory=list)  # no text + no images
    errors: list[str] = field(default_factory=list)


def build_params(
    *, note_id: str, doc_name: str, source_folder: str, text: str, images_dir: str
) -> dict:
    """Build the frozen ``note-submission`` v1 ``--params`` object (contract exact)."""
    return {
        "note_id": note_id,
        "doc_name": doc_name,
        "source_folder": source_folder,
        "text": text,
        "images_dir": images_dir,
    }


def submit_argv(nightshift_bin: str, params: dict) -> list[str]:
    """Build the exact ``nightshift submit --type note-ingest --params <json>`` argv."""
    return [
        nightshift_bin,
        "submit",
        "--type",
        NOTE_INGEST_TYPE,
        "--params",
        json.dumps(params),
    ]


def run_once(
    *,
    client: CloudTabletClient,
    state: State,
    config: WatchConfig,
    submit: ExecFn | Callable,
) -> WatchReport:
    """Run exactly one poll cycle over the configured cloud folder.

    Records state (and calls :meth:`State.save`) only after a submit exits 0. A
    :class:`CloudUnreachable` while listing aborts the cycle gracefully (retry next time);
    a per-document failure is logged and never sinks the cycle.
    """
    report = WatchReport()

    try:
        entries = client.list_folder(config.folder)
    except CloudUnreachable as exc:
        report.errors.append(f"list {config.folder!r} unreachable (will retry): {exc}")
        return report

    for entry in entries:
        if not entry.is_document:
            continue
        try:
            raw = client.get(entry.path)
            doc = parse_doc(raw)

            if state.seen(doc):
                report.skipped_seen.append(doc.id)
                continue

            images_dir = config.staging_dir / doc.id
            extracted = extract(raw, render_handwriting=True, out_dir=images_dir)
            text = extracted.text or ""
            has_images = len(extracted.page_images) > 0

            # Contract note-submission: a note with neither text nor images is not submitted.
            if not text and not has_images:
                report.skipped_empty.append(doc.id)
                continue

            params = build_params(
                note_id=doc.id,
                doc_name=doc.visible_name,
                source_folder=config.folder,
                text=text,
                images_dir=str(images_dir) if has_images else "",
            )
            argv = submit_argv(config.nightshift_bin, params)
            res = submit(argv)

            if res.get("code") == 0:
                # Record ONLY after a successful submit (contract bridge-state): a submit
                # failure retries next cycle and never double-submits.
                state.record(doc, NOTE_INGEST_TYPE, "submitted")
                state.save()
                report.submitted.append(doc.id)
            else:
                report.errors.append(
                    f"{doc.id}: nightshift submit exited {res.get('code')} "
                    f"(will retry): {str(res.get('stderr', '')).strip()}"
                )
        except CloudUnreachable as exc:
            # Transient: log, no state change, retry next cycle. Never crash the cycle.
            report.errors.append(f"{entry.path!r} unreachable (will retry): {exc}")
        except Exception as exc:  # one bad doc must never crash the whole cycle
            report.errors.append(f"{entry.path!r}: {exc!r}")

    return report


def _nightshift_submit(config: WatchConfig) -> ExecFn:
    """Production submit seam: shell the real ``nightshift`` CLI."""

    def _submit(argv: list[str], *, cwd: str | None = None):
        return _default_run(argv, cwd=cwd)

    return _submit


def main(*, config: WatchConfig | None = None, submit: ExecFn | Callable | None = None) -> None:
    """Daemon entrypoint: poll the cloud folder forever, degrading gracefully.

    Kill-switch: with ``[watch] enabled`` OFF (the default) this returns immediately without
    listing the cloud or submitting anything — the unit can be installed dark and the loop
    stays inert until an operator flips the flag (or sets ``WATCH_ENABLED``).
    """
    config = config or WatchConfig.load()
    if not config.enabled:
        print("watch disabled ([watch] enabled=false) — loop inert", flush=True)
        return

    client = CloudTabletClient(rmapi_bin=config.rmapi_bin)
    submit = submit or _nightshift_submit(config)
    state = State.load(config.state_path)

    while True:
        report = run_once(client=client, state=state, config=config, submit=submit)
        for note_id in report.submitted:
            print(f"submitted note-ingest: {note_id}", flush=True)
        for err in report.errors:
            print(f"error: {err}", flush=True)
        time.sleep(config.poll_interval)


if __name__ == "__main__":
    main()
