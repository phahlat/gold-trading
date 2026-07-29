from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


EXECUTED_MARKER = "✅ Trade executed |"
REJECTED_MARKER = "❌ Trade execution rejected |"
REQUEST_MARKER = "🧾 Trade execution request |"
NOT_CONFIRMED_MARKER = "⛔ Signal not confirmed"
LIVE_STATUS_MARKER = "📊 Live run status |"
BACKTEST_STATUS_MARKER = "📊 Backtest run status |"

KV_PATTERN = re.compile(r"(\w+)=([^=]*?)(?=\s+\w+=|$)")
CANCEL_REASON_PATTERN = re.compile(r"Signal not confirmed \(([^\)]+)\)")


@dataclass
class Event:
    event_type: str
    strategy: str
    reason: str
    data: dict[str, str]
    file: str
    line_no: int
    raw: str


class TradeLogAnalyzer:
    def __init__(self) -> None:
        self.events: list[Event] = []
        self.run_statuses: list[dict[str, str]] = []
        self.files_analyzed: list[str] = []
        self.lines_scanned: int = 0

    def analyze_file(self, path: Path) -> None:
        self.files_analyzed.append(str(path))
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line_no, line in enumerate(handle, start=1):
                self.lines_scanned += 1
                self._process_line(path, line_no, line.rstrip("\n"))

    def _process_line(self, path: Path, line_no: int, line: str) -> None:
        if EXECUTED_MARKER in line:
            data = _extract_kv(line)
            strategy = _extract_strategy(data)
            self.events.append(Event("executed", strategy, "", data, str(path), line_no, line))
            return

        if REJECTED_MARKER in line:
            data = _extract_kv(line)
            strategy = _extract_strategy(data)
            reason = data.get("reason", "unknown")
            self.events.append(Event("rejected", strategy, reason, data, str(path), line_no, line))
            return

        if REQUEST_MARKER in line:
            data = _extract_kv(line)
            strategy = _extract_strategy(data)
            self.events.append(Event("requested", strategy, "", data, str(path), line_no, line))
            return

        if NOT_CONFIRMED_MARKER in line:
            data = _extract_kv(line)
            strategy = _extract_strategy(data)
            reason_match = CANCEL_REASON_PATTERN.search(line)
            reason = reason_match.group(1).strip() if reason_match else "not_confirmed"
            self.events.append(Event("canceled", strategy, reason, data, str(path), line_no, line))
            return

        if LIVE_STATUS_MARKER in line or BACKTEST_STATUS_MARKER in line:
            status_data = _extract_kv(line)
            mode = "live" if LIVE_STATUS_MARKER in line else "backtest"
            status_data["mode"] = mode
            status_data["file"] = str(path)
            status_data["line"] = str(line_no)
            self.run_statuses.append(status_data)

    def summary(self) -> dict[str, Any]:
        by_strategy: dict[str, dict[str, Any]] = {}
        event_counters: dict[str, Counter[str]] = defaultdict(Counter)
        rejected_reasons: dict[str, Counter[str]] = defaultdict(Counter)
        canceled_reasons: dict[str, Counter[str]] = defaultdict(Counter)

        for event in self.events:
            event_counters[event.strategy][event.event_type] += 1
            if event.event_type == "rejected":
                rejected_reasons[event.strategy][event.reason] += 1
            if event.event_type == "canceled":
                canceled_reasons[event.strategy][event.reason] += 1

        for strategy in sorted(event_counters.keys()):
            counters = event_counters[strategy]
            by_strategy[strategy] = {
                "requested": counters.get("requested", 0),
                "executed": counters.get("executed", 0),
                "rejected": counters.get("rejected", 0),
                "canceled": counters.get("canceled", 0),
                "rejected_reasons": dict(rejected_reasons.get(strategy, Counter())),
                "canceled_reasons": dict(canceled_reasons.get(strategy, Counter())),
            }

        canceled_runs = [s for s in self.run_statuses if s.get("status") == "canceled_by_user"]
        completed_runs = [s for s in self.run_statuses if s.get("status") == "completed"]

        executed_events = [
            {
                "strategy": e.strategy,
                "ticket": e.data.get("ticket", ""),
                "symbol": e.data.get("symbol", ""),
                "direction": e.data.get("direction", ""),
                "level": e.data.get("level", "1"),
                "volume": e.data.get("volume", ""),
                "entry": e.data.get("entry", ""),
                "sl": e.data.get("sl", ""),
                "tp": e.data.get("tp", ""),
                "file": e.file,
                "line": e.line_no,
            }
            for e in self.events
            if e.event_type == "executed"
        ]

        rejected_events = [
            {
                "strategy": e.strategy,
                "reason": e.reason,
                "retcode": e.data.get("retcode", ""),
                "direction": e.data.get("direction", ""),
                "symbol": e.data.get("symbol", ""),
                "level": e.data.get("level", ""),
                "file": e.file,
                "line": e.line_no,
            }
            for e in self.events
            if e.event_type == "rejected"
        ]

        canceled_events = [
            {
                "strategy": e.strategy,
                "reason": e.reason,
                "direction": e.data.get("direction", ""),
                "symbol": e.data.get("symbol", ""),
                "key": e.data.get("key", ""),
                "file": e.file,
                "line": e.line_no,
            }
            for e in self.events
            if e.event_type == "canceled"
        ]

        return {
            "meta": {
                "files_analyzed": self.files_analyzed,
                "file_count": len(self.files_analyzed),
                "lines_scanned": self.lines_scanned,
                "events_total": len(self.events),
            },
            "by_strategy": by_strategy,
            "runs": {
                "canceled_by_user": canceled_runs,
                "completed": completed_runs,
                "all_statuses": self.run_statuses,
            },
            "events": {
                "executed": executed_events,
                "rejected": rejected_events,
                "canceled": canceled_events,
            },
        }


def _extract_kv(line: str) -> dict[str, str]:
    if "|" not in line:
        return {}
    payload = line.split("|", 1)[1].strip()
    data: dict[str, str] = {}
    for match in KV_PATTERN.finditer(payload):
        key = match.group(1).strip()
        value = match.group(2).strip()
        data[key] = value
    return data


def _extract_strategy(data: dict[str, str]) -> str:
    strategy = data.get("strategy", "unknown").strip().lower()
    return strategy if strategy else "unknown"


def _expand_patterns(patterns: list[str]) -> list[Path]:
    paths: list[Path] = []
    seen: set[str] = set()
    for pattern in patterns:
        for path in sorted(Path().glob(pattern)):
            if not path.is_file():
                continue
            key = str(path.resolve())
            if key in seen:
                continue
            seen.add(key)
            paths.append(path)
    return paths


def _print_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "(none)"

    widths = {col: len(col) for col in columns}
    normalized: list[dict[str, str]] = []

    for row in rows:
        normalized_row: dict[str, str] = {}
        for col in columns:
            value = row.get(col, "")
            text = str(value)
            normalized_row[col] = text
            widths[col] = max(widths[col], len(text))
        normalized.append(normalized_row)

    header = " | ".join(col.ljust(widths[col]) for col in columns)
    separator = "-+-".join("-" * widths[col] for col in columns)
    lines = [header, separator]
    for row in normalized:
        lines.append(" | ".join(row[col].ljust(widths[col]) for col in columns))
    return "\n".join(lines)


def _print_summary(result: dict[str, Any], max_details: int) -> None:
    meta = result["meta"]
    print("=== Log Analysis Summary ===")
    print(f"Files analyzed: {meta['file_count']}")
    print(f"Lines scanned: {meta['lines_scanned']}")
    print(f"Events parsed: {meta['events_total']}")
    print()

    strategy_rows = []
    for strategy, data in sorted(result["by_strategy"].items()):
        strategy_rows.append(
            {
                "strategy": strategy,
                "requested": data["requested"],
                "executed": data["executed"],
                "rejected": data["rejected"],
                "canceled": data["canceled"],
            }
        )
    print("=== Per-Strategy Totals ===")
    print(_print_table(strategy_rows, ["strategy", "requested", "executed", "rejected", "canceled"]))
    print()

    print("=== Rejected Reasons By Strategy ===")
    rejected_rows: list[dict[str, Any]] = []
    for strategy, data in sorted(result["by_strategy"].items()):
        reasons = data.get("rejected_reasons", {})
        if not reasons:
            continue
        for reason, count in sorted(reasons.items(), key=lambda item: (-item[1], item[0])):
            rejected_rows.append({"strategy": strategy, "reason": reason, "count": count})
    print(_print_table(rejected_rows, ["strategy", "reason", "count"]))
    print()

    print("=== Canceled/Blocked Reasons By Strategy ===")
    canceled_rows: list[dict[str, Any]] = []
    for strategy, data in sorted(result["by_strategy"].items()):
        reasons = data.get("canceled_reasons", {})
        if not reasons:
            continue
        for reason, count in sorted(reasons.items(), key=lambda item: (-item[1], item[0])):
            canceled_rows.append({"strategy": strategy, "reason": reason, "count": count})
    print(_print_table(canceled_rows, ["strategy", "reason", "count"]))
    print()

    canceled_runs = result["runs"]["canceled_by_user"]
    completed_runs = result["runs"]["completed"]
    print("=== Run Status ===")
    print(f"Completed runs: {len(completed_runs)}")
    print(f"Canceled runs: {len(canceled_runs)}")
    if canceled_runs:
        run_rows = [
            {"mode": r.get("mode", ""), "status": r.get("status", ""), "file": Path(r.get("file", "")).name, "line": r.get("line", "")}
            for r in canceled_runs[:max_details]
        ]
        print(_print_table(run_rows, ["mode", "status", "file", "line"]))
    print()

    executed = result["events"]["executed"]
    rejected = result["events"]["rejected"]
    canceled = result["events"]["canceled"]

    print("=== Sample Executed Trades ===")
    print(_print_table(executed[:max_details], ["strategy", "ticket", "symbol", "direction", "level", "volume", "entry", "sl", "tp", "file", "line"]))
    print()

    print("=== Sample Rejected Trades ===")
    print(_print_table(rejected[:max_details], ["strategy", "reason", "retcode", "symbol", "direction", "level", "file", "line"]))
    print()

    print("=== Sample Canceled/Blocked Signals ===")
    print(_print_table(canceled[:max_details], ["strategy", "reason", "symbol", "direction", "key", "file", "line"]))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Analyze trading bot logs and summarize executed/rejected/canceled strategy events."
    )
    parser.add_argument(
        "--patterns",
        nargs="+",
        default=["logs/gold-bot*.log", "logs/ema-bot*.log", "logs/ema-bot.log*"],
        help="Glob patterns for log files.",
    )
    parser.add_argument(
        "--json-out",
        help="Optional path to write full JSON summary.",
    )
    parser.add_argument(
        "--max-details",
        type=int,
        default=20,
        help="Max detailed rows to print for samples.",
    )
    args = parser.parse_args()

    files = _expand_patterns(args.patterns)
    if not files:
        print("No log files matched the provided patterns.")
        return 1

    analyzer = TradeLogAnalyzer()
    for file_path in files:
        analyzer.analyze_file(file_path)

    result = analyzer.summary()
    _print_summary(result, max_details=max(1, args.max_details))

    if args.json_out:
        json_path = Path(args.json_out)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print()
        print(f"JSON summary written to: {json_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
