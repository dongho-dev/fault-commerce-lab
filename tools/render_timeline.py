# ruff: noqa: E501
import argparse
import html
import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


def parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def load_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and isinstance(value.get("request_id"), str):
            records.append(value)
    return records


def build_requests(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[str(record["request_id"])].append(record)

    requests: list[dict[str, Any]] = []
    for request_id, items in grouped.items():
        parsed = [(item, parse_timestamp(item.get("timestamp"))) for item in items]
        timed = [(item, timestamp) for item, timestamp in parsed if timestamp is not None]
        if not timed:
            continue
        end_record = next(
            (item for item in reversed(items) if item.get("event") == "http_request_completed"),
            {},
        )
        end_time = max(timestamp for _, timestamp in timed)
        duration_ms = float(end_record.get("duration_ms", 0.0) or 0.0)
        start_time = min(timestamp for _, timestamp in timed)
        if duration_ms > 0:
            start_time = min(start_time, end_time - timedelta(milliseconds=duration_ms))

        stages: list[dict[str, Any]] = []
        product_id: object = ""
        for item, timestamp in timed:
            if not product_id and item.get("product_id") is not None:
                product_id = item["product_id"]
            stage = item.get("stage")
            if not isinstance(stage, str):
                continue
            stage_duration = float(item.get("duration_ms", 0.0) or 0.0)
            stage_start = timestamp - timedelta(milliseconds=stage_duration)
            stages.append(
                {
                    "name": stage,
                    "event": str(item.get("event", "")),
                    "start": stage_start.timestamp(),
                    "end": timestamp.timestamp(),
                }
            )
        requests.append(
            {
                "request_id": request_id,
                "product_id": product_id,
                "status": end_record.get("status_code", ""),
                "path": end_record.get("path", ""),
                "duration_ms": round(duration_ms, 3),
                "start": start_time.timestamp(),
                "end": end_time.timestamp(),
                "stages": stages,
            }
        )
    return sorted(requests, key=lambda item: item["start"])


def render_html(requests: list[dict[str, Any]]) -> str:
    safe_data = json.dumps(requests, ensure_ascii=False).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fault Commerce Request Timeline</title>
<style>
:root {{ color-scheme: dark; font-family: Inter, ui-sans-serif, system-ui, sans-serif; background:#0b1020; color:#e8edf7; }}
body {{ margin:0; padding:28px; }}
h1 {{ margin:0 0 6px; font-size:24px; }}
.subtitle {{ color:#93a4bd; margin-bottom:22px; }}
.filters {{ display:flex; gap:12px; margin-bottom:18px; flex-wrap:wrap; }}
select {{ background:#151d31; color:#e8edf7; border:1px solid #2b3854; padding:8px 10px; border-radius:8px; }}
.row {{ display:grid; grid-template-columns:minmax(240px, 1fr) 3fr; gap:14px; align-items:center; padding:10px 0; border-top:1px solid #1d2941; }}
.meta {{ min-width:0; }}
.request-id {{ font-family:ui-monospace, monospace; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
.detail {{ color:#93a4bd; font-size:12px; margin-top:4px; }}
.track {{ height:30px; background:#121a2b; border-radius:7px; position:relative; overflow:hidden; }}
.request {{ position:absolute; top:5px; height:20px; background:#315a9b; border-radius:5px; min-width:2px; opacity:.72; }}
.stage {{ position:absolute; top:7px; height:16px; background:#68d5c2; border-radius:3px; min-width:2px; box-shadow:0 0 0 1px rgba(255,255,255,.16); }}
.status-409 .request {{ background:#aa6e32; }} .status-500 .request {{ background:#a84255; }}
.empty {{ padding:30px; color:#93a4bd; border:1px dashed #2b3854; border-radius:10px; }}
</style>
</head>
<body>
<h1>Request timeline</h1>
<div class="subtitle">Observed request and processing-stage intervals from logs/app.jsonl</div>
<div class="filters">
  <label>Product <select id="product"><option value="">All</option></select></label>
  <label>Status <select id="status"><option value="">All</option></select></label>
</div>
<div id="timeline"></div>
<script>
const data = {safe_data};
const productSelect = document.getElementById('product');
const statusSelect = document.getElementById('status');
const timeline = document.getElementById('timeline');
const unique = (key) => [...new Set(data.map(item => String(item[key])).filter(Boolean))].sort();
for (const value of unique('product_id')) productSelect.add(new Option(value, value));
for (const value of unique('status')) statusSelect.add(new Option(value, value));
const escapeText = (value) => {{ const span=document.createElement('span'); span.textContent=String(value); return span.innerHTML; }};
function render() {{
  const rows = data.filter(item => (!productSelect.value || String(item.product_id)===productSelect.value) && (!statusSelect.value || String(item.status)===statusSelect.value));
  if (!rows.length) {{ timeline.innerHTML='<div class="empty">No matching observed requests.</div>'; return; }}
  const min = Math.min(...rows.map(item=>item.start)); const max=Math.max(...rows.map(item=>item.end)); const span=Math.max(max-min,.001);
  timeline.innerHTML = rows.map(item => {{
    const left=(item.start-min)/span*100; const width=Math.max((item.end-item.start)/span*100,.25);
    const stages=item.stages.map(stage=>{{ const sl=(stage.start-min)/span*100; const sw=Math.max((stage.end-stage.start)/span*100,.18); return `<div class="stage" style="left:${{sl}}%;width:${{sw}}%" title="${{escapeText(stage.name)}} · ${{escapeText(stage.event)}}"></div>`; }}).join('');
    return `<div class="row status-${{escapeText(item.status)}}"><div class="meta"><div class="request-id" title="${{escapeText(item.request_id)}}">${{escapeText(item.request_id)}}</div><div class="detail">product ${{escapeText(item.product_id||'—')}} · HTTP ${{escapeText(item.status||'—')}} · ${{escapeText(item.duration_ms)}} ms · ${{escapeText(item.path)}}</div></div><div class="track"><div class="request" style="left:${{left}}%;width:${{width}}%"></div>${{stages}}</div></div>`;
  }}).join('');
}}
productSelect.addEventListener('change', render); statusSelect.addEventListener('change', render); render();
</script>
</body>
</html>"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Render observed request logs as one HTML file.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    requests = build_requests(load_records(args.input))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_html(requests), encoding="utf-8")
    print(f"Rendered {len(requests)} requests to {html.escape(str(args.output))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
