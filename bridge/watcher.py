"""Watcher — orchestrates one review-route poll cycle and the daemon loop.

One cycle (walking skeleton):

    tablet.scan() → detect a `review`-tagged, un-seen doc (contract tablet-document)
    → extract typed text → agents.run(review job) → publish Markdown→PDF→upload
    → state.record() atomically AFTER a successful upload (contract bridge-state).

Every routed doc yields a published report, even on failure/timeout/needs-clarification
(the pipeline never silently drops a job). State is committed only after upload succeeds,
so a failed upload retries next cycle rather than being lost.
"""

from __future__ import annotations

import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import tablet
from .agents import AgentJob, AgentRunner, ClaudeAgentRunner
from .clock import Clock, SystemClock
from .extract import extract
from .publish import PdfRenderer, WeasyPrintRenderer, publish
from .state import State
from .tablet import TabletClient

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = PACKAGE_DIR / "config.toml"


@dataclass
class Config:
    route_tags: set[str]
    ssh_host: str
    xochitl: str
    responses_folder: str
    state_path: Path
    out_dir: Path
    logs_dir: Path
    workspace_root: Path
    prompts_dir: Path
    model: str | None
    agent_timeout: int
    poll_interval: int

    @classmethod
    def load(cls, path: Path = DEFAULT_CONFIG) -> "Config":
        data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
        routes = data.get("routes", {})
        tablet_cfg = data.get("tablet", {})
        agent_cfg = data.get("agent", {})
        paths = data.get("paths", {})
        watcher_cfg = data.get("watcher", {})

        def _p(value: str) -> Path:
            return Path(value).expanduser()

        return cls(
            route_tags={t.lower() for t in routes.get("tags", ["review"])},
            ssh_host=tablet_cfg.get("ssh_host", "remarkable"),
            xochitl=tablet_cfg.get("xochitl", tablet.XOCHITL),
            responses_folder=tablet_cfg.get("responses_folder", "/Claude/Responses"),
            state_path=_p(paths.get("state", "~/.local/state/remarkable-bridge/state.json")),
            out_dir=_p(paths.get("out", "out")),
            logs_dir=_p(paths.get("logs", "logs")),
            workspace_root=_p(paths.get("workspace", "workspace")),
            prompts_dir=PACKAGE_DIR / "prompts",
            model=agent_cfg.get("model") or None,
            agent_timeout=int(agent_cfg.get("timeout", 600)),
            poll_interval=int(watcher_cfg.get("poll_interval", 60)),
        )


@dataclass
class CycleReport:
    """What one poll cycle did — handy for tests, logging, and the daemon loop."""

    processed: list[str] = field(default_factory=list)  # visible_names published
    skipped_seen: list[str] = field(default_factory=list)  # doc ids skipped by dedup
    errors: list[str] = field(default_factory=list)


def run_once(
    *,
    client: TabletClient,
    runner: AgentRunner,
    clock: Clock,
    state: State,
    config: Config,
    renderer: PdfRenderer | None = None,
) -> CycleReport:
    """Run exactly one review-route poll cycle. Returns a summary of what happened."""
    report = CycleReport()
    raw_by_id = {raw.doc_id: raw for raw in client.scan_raw()}

    for raw in raw_by_id.values():
        doc = tablet.parse_doc(raw)
        route = tablet.route_for(doc, config.route_tags)
        if route is None or doc.deleted or doc.type != "DocumentType":
            continue
        # Stage 1 handles only the review route; other routes are later stages.
        if route != "review":
            continue
        if state.seen(doc):
            report.skipped_seen.append(doc.id)
            continue

        try:
            extracted = extract(raw)
            workspace = config.workspace_root / doc.id
            workspace.mkdir(parents=True, exist_ok=True)
            job = AgentJob(
                route="review",
                doc=doc,
                text=extracted.text,
                workspace=workspace,
                page_images=extracted.page_images,
            )
            result = runner.run(job)
            published = publish(
                result,
                doc,
                clock.today(),
                client,
                renderer=renderer,
                out_dir=config.out_dir,
            )
            if published.uploaded:
                # Record ONLY after a successful upload (contract bridge-state).
                state.record(doc, route, result.status)
                state.save()
                report.processed.append(published.visible_name)
            else:
                report.errors.append(f"{doc.id}: upload did not complete")
        except Exception as exc:  # never let one bad doc crash the cycle
            report.errors.append(f"{doc.id}: {exc!r}")

    return report


def build_production_deps(config: Config) -> tuple[TabletClient, AgentRunner, Clock, PdfRenderer]:
    """Wire the real seams for the deployed daemon."""
    client = tablet.SubprocessTabletClient(host=config.ssh_host, xochitl=config.xochitl)
    runner = ClaudeAgentRunner(
        prompts_dir=config.prompts_dir,
        logs_dir=config.logs_dir,
        model=config.model,
        timeout=config.agent_timeout,
    )
    return client, runner, SystemClock(), WeasyPrintRenderer()


def main() -> None:
    """Daemon entrypoint: poll the tablet forever, degrading gracefully on errors."""
    config = Config.load()
    client, runner, clock, renderer = build_production_deps(config)
    state = State.load(config.state_path)

    while True:
        try:
            report = run_once(
                client=client,
                runner=runner,
                clock=clock,
                state=state,
                config=config,
                renderer=renderer,
            )
            for name in report.processed:
                print(f"published: {name}", flush=True)
            for err in report.errors:
                print(f"error: {err}", flush=True)
        except tablet.TabletUnreachable as exc:
            # Transient transport failure: log, change no state, retry next cycle
            # (ADR-0005, acceptance #4). Never crash, never reprocess.
            print(f"tablet unreachable (will retry): {exc}", flush=True)
        except Exception as exc:  # any other cycle error — log, retry next cycle
            print(f"cycle failed (will retry): {exc!r}", flush=True)
        time.sleep(config.poll_interval)


if __name__ == "__main__":
    main()
