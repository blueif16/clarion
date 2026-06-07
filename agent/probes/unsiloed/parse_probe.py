"""Unsiloed `/parse` probe — submit a file (or URL) and dump EXACTLY what the
parser detects, with a focus on how well it reads charts natively.

Charts come back as `Picture` segments whose `markdown`/`html` carry a
VLM-generated *description* (not pixels); data tables come back as `Table`
segments (Markdown pipe-table + HTML). This probe submits each fixture with a
chart-tuned config, polls the async job, then prints a per-segment breakdown and
— when a `ground_truth.json` answer key is present next to the fixtures — shows
the true data beside Unsiloed's reading so you can eyeball accuracy. The full raw
JSON for every run is saved under `out/` for deeper inspection.

This is a throwaway PROBE, not wired into the kernel. It deliberately lives
outside `clarion/` and imports zero project code — it only needs the
`UNSILOED_API_KEY` in `agent/.env`.

Setup:
  # one-time: put your key in agent/.env  → UNSILOED_API_KEY="unsiloed_..."
  agent/.venv/bin/python agent/probes/unsiloed/make_charts.py   # render fixtures

Run:
  PY=agent/.venv/bin/python
  $PY agent/probes/unsiloed/parse_probe.py --all                # every fixture
  $PY agent/probes/unsiloed/parse_probe.py fixtures/pie_share.png
  $PY agent/probes/unsiloed/parse_probe.py https://example.com/report.pdf

Tuning (env):
  UNSILOED_LAYOUT=advanced_layout_detection   # default: smart_layout_detection
  UNSILOED_POLL_TIMEOUT=300                    # seconds before giving up on a job
"""

from __future__ import annotations

import json
import mimetypes
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
OUT = HERE / "out"
# agent/.env lives two levels up from agent/probes/unsiloed/.
_AGENT_ROOT = HERE.parent.parent
load_dotenv(_AGENT_ROOT / ".env")

API_KEY = os.environ.get("UNSILOED_API_KEY", "").strip()
BASE_URL = os.environ.get("UNSILOED_BASE_URL", "https://prod.visionapi.unsiloed.ai").rstrip("/")
LAYOUT = os.environ.get("UNSILOED_LAYOUT", "smart_layout_detection")
POLL_TIMEOUT = int(os.environ.get("UNSILOED_POLL_TIMEOUT", "300"))
POLL_EVERY = 4  # seconds

# Extensions Unsiloed accepts (parse endpoint): PDF, raster images, Office files.
_MIME = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".bmp": "image/bmp",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".doc": "application/msword",
    ".xls": "application/vnd.ms-excel",
    ".ppt": "application/vnd.ms-powerpoint",
}

# Tell the Picture VLM to actually read the chart, not just say "a bar chart".
_CHART_PROMPT = (
    "If this image is a chart or graph, describe it precisely: chart type, title, "
    "the axis labels with units, every series/legend entry, and the numeric value "
    "for each bar/slice/point (read them off the axis or labels). State the overall "
    "trend. If a value is ambiguous or unreadable, say so explicitly — do not guess."
)

# Chart-tuned parse config (multipart fields are all strings; JSON fields are
# JSON-encoded strings). High-res + validate Picture/Table + VLM descriptions.
def _config() -> dict[str, str]:
    return {
        "use_high_resolution": "true",
        "layout_analysis": LAYOUT,
        "ocr_strategy": "auto_detection",
        "segment_filter": "all",
        "validate_segments": json.dumps(["Table", "Picture", "Formula"]),
        "segment_analysis": json.dumps(
            {
                "Table": {"html": "VLM", "markdown": "VLM", "model_id": "us_table_v2"},
                "Picture": {"html": "VLM", "markdown": "VLM", "vlm": _CHART_PROMPT},
                "Formula": {"markdown": "VLM"},
            }
        ),
    }


def _headers() -> dict[str, str]:
    return {"api-key": API_KEY, "accept": "application/json"}


def submit(source: str) -> dict:
    """POST a file (multipart) or URL to /parse; return the create-job response."""
    data = _config()
    if source.startswith(("http://", "https://")):
        data["url"] = source
        resp = requests.post(f"{BASE_URL}/parse", headers=_headers(), data=data, timeout=60)
    else:
        path = Path(source)
        if not path.is_absolute():
            path = (Path.cwd() / path) if (Path.cwd() / path).exists() else (HERE / path)
        if not path.exists():
            raise FileNotFoundError(f"no such file: {source}")
        mime = _MIME.get(path.suffix.lower()) or mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        with path.open("rb") as fh:
            resp = requests.post(
                f"{BASE_URL}/parse",
                headers=_headers(),
                data=data,
                files={"file": (path.name, fh, mime)},
                timeout=120,
            )
    if resp.status_code != 200:
        raise RuntimeError(f"submit failed [{resp.status_code}]: {resp.text[:500]}")
    return resp.json()


def poll(job_id: str) -> dict:
    """GET /parse/{job_id} until terminal; return the final result payload."""
    url = f"{BASE_URL}/parse/{job_id}"
    deadline = time.monotonic() + POLL_TIMEOUT
    while True:
        resp = requests.get(url, headers=_headers(), timeout=60)
        if resp.status_code != 200:
            raise RuntimeError(f"poll failed [{resp.status_code}]: {resp.text[:300]}")
        body = resp.json()
        status = str(body.get("status", "")).lower()
        if status in ("succeeded", "completed", "success"):
            return body
        if status in ("failed", "error"):
            raise RuntimeError(f"job failed: {body.get('message') or body.get('error') or body}")
        if time.monotonic() > deadline:
            raise TimeoutError(f"job {job_id} still {status!r} after {POLL_TIMEOUT}s")
        time.sleep(POLL_EVERY)


def _iter_segments(result: dict):
    for chunk in result.get("chunks", []) or []:
        for seg in chunk.get("segments", []) or []:
            yield seg


def _truncate(text: str, n: int = 1400) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[:n] + f" …(+{len(text) - n} chars)"


def report(name: str, created: dict, result: dict, truth: dict | None) -> None:
    segs = list(_iter_segments(result))
    counts: dict[str, int] = {}
    for s in segs:
        counts[s.get("segment_type", "?")] = counts.get(s.get("segment_type", "?"), 0) + 1

    print(f"\n{'═' * 78}\n▶ {name}")
    print(f"  status={result.get('status')}  chunks={result.get('total_chunks', len(result.get('chunks', [])))}  "
          f"segments={len(segs)}  credit_used={created.get('credit_used')}  quota_remaining={created.get('quota_remaining')}")
    started, finished = result.get("started_at"), result.get("finished_at")
    if started and finished:
        print(f"  created_at={result.get('created_at')}  finished_at={finished}")
    print(f"  segment types: {counts or '(none)'}")

    if truth:
        print(f"  ┌ GROUND TRUTH  [{truth.get('chart_type', '?')}] {truth.get('title', '')}")
        print(f"  │ data: {_truncate(json.dumps(truth.get('data', {}), ensure_ascii=False), 600)}")
        if truth.get("expect"):
            print(f"  └ expect: {truth['expect']}")

    # The interesting bits: what the VLM said about charts (Picture) and tables.
    for s in segs:
        st = s.get("segment_type", "?")
        if st not in ("Picture", "Table", "Formula"):
            continue
        conf = s.get("confidence")
        page = s.get("page_number")
        print(f"\n  ── {st}  (page {page}, conf {conf})")
        body = s.get("markdown") or s.get("content") or s.get("html") or ""
        print("     " + _truncate(body).replace("\n", "\n     "))
        if s.get("image"):
            print(f"     [cropped image] {s['image'][:120]}…")

    OUT.mkdir(exist_ok=True)
    stem = name.replace("/", "_").replace(":", "_")
    out_path = OUT / f"{stem}.json"
    out_path.write_text(json.dumps({"created": created, "result": result}, indent=2, ensure_ascii=False))
    print(f"\n  → full JSON saved: {out_path.relative_to(_AGENT_ROOT.parent)}")


def _ground_truth() -> dict:
    gt = FIXTURES / "ground_truth.json"
    if gt.exists():
        try:
            return json.loads(gt.read_text())
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _targets(args: list[str]) -> list[str]:
    if args == ["--all"]:
        if not FIXTURES.exists():
            sys.exit(f"no fixtures dir ({FIXTURES}); run make_charts.py first.")
        files = sorted(
            str(p) for p in FIXTURES.iterdir()
            if p.suffix.lower() in _MIME and p.is_file()
        )
        if not files:
            sys.exit(f"no parseable fixtures in {FIXTURES}; run make_charts.py first.")
        return files
    return args


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    if not API_KEY:
        sys.exit("UNSILOED_API_KEY not set — add it to agent/.env (see .env.example).")

    truth = _ground_truth()
    targets = _targets(args)
    print(f"== Unsiloed /parse probe ==  base={BASE_URL}  layout={LAYOUT}  targets={len(targets)}")

    failures = 0
    for src in targets:
        name = Path(src).name if not src.startswith("http") else src
        try:
            created = submit(src)
            job_id = created.get("job_id")
            if not job_id:
                raise RuntimeError(f"no job_id in create response: {created}")
            print(f"\n[{name}] job {job_id} — polling (≤{POLL_TIMEOUT}s)…", flush=True)
            result = poll(job_id)
            report(name, created, result, truth.get(name))
        except Exception as exc:  # noqa: BLE001 - one bad file shouldn't stop the sweep
            failures += 1
            print(f"\n[{name}] ✗ {type(exc).__name__}: {exc}")
    print(f"\n{'═' * 78}\nDONE — {len(targets) - failures}/{len(targets)} parsed OK.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
