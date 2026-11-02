#!/usr/bin/env python3
"""Offline Codex token export and multi-computer aggregation. Python 3.9+."""

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import sys
from datetime import date, datetime, timedelta, timezone

SCHEMA = "codex-token-usage/v1"
SHARED = "__SHARED_UNATTRIBUTED__"
FIELDS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens",
          "output_tokens", "reasoning_output_tokens", "total_tokens")
CSV_FIELDS = ["machine", "date", *FIELDS, "event_count"]


def digest(value):
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("missing timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must have a timezone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")


def utc_offset(value):
    if not re.fullmatch(r"[+-]\d{2}:\d{2}", value):
        raise argparse.ArgumentTypeError("expected an offset such as +08:00")
    hours, minutes = map(int, value[1:].split(":"))
    if hours > 23 or minutes > 59:
        raise argparse.ArgumentTypeError("invalid UTC offset")
    delta = timedelta(hours=hours, minutes=minutes)
    return timezone(delta if value[0] == "+" else -delta)


def counts(value):
    if not isinstance(value, dict):
        return None
    if not any(key in value for key in ("input_tokens", "output_tokens", "total_tokens")):
        return None
    result = {}
    for key in FIELDS:
        number = value.get(key, 0)
        if type(number) is not int or number < 0:
            raise ValueError("token counters must be nonnegative integers")
        result[key] = number
    if "total_tokens" not in value:
        result["total_tokens"] = result["input_tokens"] + result["output_tokens"]
    if result["total_tokens"] != result["input_tokens"] + result["output_tokens"]:
        raise ValueError("total_tokens differs from input_tokens + output_tokens")
    if result["cached_input_tokens"] > result["input_tokens"]:
        raise ValueError("cached input exceeds input")
    if result["reasoning_output_tokens"] > result["output_tokens"]:
        raise ValueError("reasoning output exceeds output")
    return result


class Diagnostics:
    def __init__(self):
        self.totals = {}
        self.examples = []

    def add(self, kind, location=""):
        self.totals[kind] = self.totals.get(kind, 0) + 1
        if len(self.examples) < 50:
            self.examples.append({"kind": kind, "location": location})

    def as_dict(self):
        return {"counts": self.totals, "examples": self.examples}


def scan_file(path, diagnostics):
    """Read a fixed-size snapshot; an actively written trailing line is ignored."""
    previous = None
    session_id = None
    seen_snapshots = set()
    with path.open("rb") as handle:
        remaining = os.fstat(handle.fileno()).st_size
        line_number = 0
        while remaining > 0:
            raw = handle.readline(remaining)
            remaining -= len(raw)
            line_number += 1
            location = str(path) + ":" + str(line_number)
            if not raw.endswith(b"\n"):
                diagnostics.add("incomplete_trailing_line", location)
                break
            # Avoid decoding conversation bodies and tool output.
            if b'"session_meta"' not in raw and b'"token_count"' not in raw:
                continue
            try:
                item = json.loads(raw.decode("utf-8-sig"))
                if not isinstance(item, dict):
                    raise ValueError("invalid log entry")
                payload = item.get("payload")
                if not isinstance(payload, dict):
                    continue
                if item.get("type") == "session_meta":
                    candidate = payload.get("id") or payload.get("session_id")
                    if isinstance(candidate, str) and candidate:
                        session_id = candidate
                    continue
                if item.get("type") != "event_msg" or payload.get("type") != "token_count":
                    continue
                info = payload.get("info")
                if not isinstance(info, dict):
                    continue  # Rate-limit-only events contain no token usage.
                total = counts(info.get("total_token_usage"))
                if total is None:
                    diagnostics.add("missing_cumulative_counter_skipped", location)
                    continue
                if session_id is None:
                    diagnostics.add("missing_session_id_skipped", location)
                    continue
                when = timestamp(item.get("timestamp"))
                signature = (when, tuple(total[key] for key in FIELDS))
                if signature in seen_snapshots:
                    continue
                seen_snapshots.add(signature)
                if previous == total:
                    continue
                # Compaction/status snapshots can carry placeholder last counters.
                # They are irrelevant when the cumulative usage did not change.
                last = counts(info.get("last_token_usage"))
                if previous is None:
                    delta = last if last is not None else total
                    if last is not None and total != last:
                        diagnostics.add("initial_history_gap_only_last_counted", location)
                    elif last is None:
                        diagnostics.add("initial_cumulative_assigned_to_first_timestamp", location)
                elif any(total[key] < previous[key] for key in FIELDS):
                    # Resets/compaction: never interpret the new total as extra usage.
                    if last is None:
                        previous = total
                        diagnostics.add("counter_reset_without_last_skipped", location)
                        continue
                    delta = last
                    diagnostics.add("counter_reset_used_last", location)
                else:
                    difference = {key: total[key] - previous[key] for key in FIELDS}
                    delta = difference
                    if last is not None and difference != last:
                        # Unobserved requests cannot be assigned to this event's date.
                        delta = last
                        diagnostics.add("history_gap_only_last_counted", location)
                previous = total
                if delta["total_tokens"] == 0:
                    continue
                # Validate differences too: partially reset subset counters may be invalid.
                delta = counts(delta)
                identity = {"session_id": session_id, "timestamp_utc": when,
                            "cumulative": total, "last": last}
                yield {"event_id": digest(identity), "session_id": session_id,
                       "timestamp_utc": when, **delta}
            except (ValueError, TypeError, UnicodeError) as exc:
                diagnostics.add("invalid_token_or_metadata_record", location + " (" + str(exc) + ")")


def in_range(record, args):
    day = datetime.fromisoformat(record["timestamp_utc"]).astimezone(args.utc_offset).date()
    return day, (args.start is None or day >= args.start) and (args.end is None or day <= args.end)


def unique_records(records, diagnostics):
    result = {}
    for record in records:
        key = record["event_id"]
        if key in result:
            if result[key] != record:
                raise ValueError("conflicting counts for the same event: " + key)
            diagnostics.add("duplicate_event_removed")
        else:
            result[key] = record
    return sorted(result.values(), key=lambda item: (item["timestamp_utc"], item["event_id"]))


def write_csv(path, fieldnames, rows):
    # BOM makes Chinese machine names and labels readable in Windows Excel.
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def summarize(records, args):
    daily = {}
    machines = {}
    for record in records:
        day, selected = in_range(record, args)
        if not selected:
            continue
        machine = record["machine"]
        key = (machine, day.isoformat())
        if key not in daily:
            daily[key] = {"machine": machine, "date": day.isoformat(),
                          **dict.fromkeys(FIELDS, 0), "event_count": 0}
        row = daily[key]
        row["event_count"] += 1
        for field in FIELDS:
            row[field] += record[field]
    daily_rows = sorted(daily.values(), key=lambda row: (row["date"], row["machine"]))
    day_totals = {}
    for row in daily_rows:
        day_totals[row["date"]] = day_totals.get(row["date"], 0) + row["total_tokens"]
        machine = row["machine"]
        if machine not in machines:
            machines[machine] = {"machine": machine, **dict.fromkeys(FIELDS, 0), "event_count": 0}
        for field in (*FIELDS, "event_count"):
            machines[machine][field] += row[field]
    grand_total = sum(row["total_tokens"] for row in machines.values())
    for row in machines.values():
        row["share_percent"] = f'{100 * row["total_tokens"] / grand_total:.6f}' if grand_total else "0.000000"
    for row in daily_rows:
        total = day_totals[row["date"]]
        row["day_share_percent"] = f'{100 * row["total_tokens"] / total:.6f}' if total else "0.000000"
    machine_rows = sorted(machines.values(), key=lambda row: (-row["total_tokens"], row["machine"]))
    return {"total_tokens": grand_total, "machines": machine_rows,
            "utc_offset": str(args.utc_offset), "from": str(args.start) if args.start else None,
            "to": str(args.end) if args.end else None,
            "scope": "Observed local log tokens; not subscription quota or billed cost."}, daily_rows


def report(records, out, args, prefix=""):
    summary, daily_rows = summarize(records, args)
    write_csv(out / (prefix + "daily.csv"), [*CSV_FIELDS, "day_share_percent"], daily_rows)
    write_csv(out / (prefix + "machines.csv"),
              ["machine", *FIELDS, "event_count", "share_percent"], summary["machines"])
    return summary


def collect_local(codex_home, machine, sessions_dirs=None):
    machine = machine.strip()
    if not machine or machine == SHARED:
        raise ValueError("choose a nonempty, nonreserved machine name")
    roots = sessions_dirs or [codex_home / "sessions", codex_home / "archived_sessions"]
    paths = set()
    for root in roots:
        if root.is_dir():
            paths.update(path.resolve() for path in root.rglob("*.jsonl") if path.is_file())
        elif sessions_dirs:
            raise ValueError("session directory does not exist: " + str(root))
    if not paths:
        raise ValueError("no session JSONL files found; check --codex-home or --sessions-dir")
    diagnostics = Diagnostics()

    def source():
        for path in sorted(paths):
            try:
                yield from scan_file(path, diagnostics)
            except OSError as exc:
                # Fail closed rather than silently reporting a complete-looking subtotal.
                raise ValueError("cannot read session file: " + str(path) + " (" + str(exc) + ")") from exc

    records = unique_records(source(), diagnostics)
    for record in records:
        record["machine"] = machine
    return records, diagnostics, len(paths)


def export(args):
    machine = args.machine.strip()
    records, diagnostics, file_count = collect_local(args.codex_home, machine, args.sessions_dir)
    records = [record for record in records if in_range(record, args)[1]]
    args.out.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w.-]", "_", machine).strip(". ")[:60] or "computer"
    stem = safe + "-" + digest(machine)[:8]
    target = args.out / (stem + ".usage.jsonl")
    header = {"type": "header", "schema": SCHEMA, "machine": machine,
              "exported_at_utc": datetime.now(timezone.utc).isoformat(),
              "utc_offset": str(args.utc_offset), "from": str(args.start) if args.start else None,
              "to": str(args.end) if args.end else None, "files_scanned": file_count,
              "diagnostics": diagnostics.as_dict()}
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(header, ensure_ascii=False) + "\n")
        for record in records:
            handle.write(json.dumps({"type": "usage", **record}, ensure_ascii=False) + "\n")
    summary = report(records, args.out, args, stem + ".")
    summary.update({"machine": machine, "files_scanned": file_count,
                    "diagnostics": diagnostics.as_dict()})
    write_json(args.out / (stem + ".summary.json"), summary)
    print("Export:", target.resolve())
    print("Tokens:", f'{summary["total_tokens"]:,}', "| Events:", len(records), "| Files:", file_count)
    print("Diagnostics:", json.dumps(diagnostics.totals, ensure_ascii=False))
    print("Copy the .usage.jsonl file to the shared collection folder, then run merge.")


def load_exports(paths, diagnostics):
    def streams():
        for path in paths:
            with path.open("r", encoding="utf-8-sig") as handle:
                yield str(path), handle
    return load_export_streams(streams(), diagnostics)


def load_export_streams(streams, diagnostics):
    events = {}
    exported_machines = set()
    for path, handle in streams:
        header = json.loads(handle.readline())
        if not isinstance(header, dict) or header.get("type") != "header" or header.get("schema") != SCHEMA:
            raise ValueError("not a supported export: " + str(path))
        machine = header.get("machine")
        if not isinstance(machine, str) or not machine.strip() or machine == SHARED:
            raise ValueError("invalid machine in export: " + str(path))
        exported_machines.add(machine)
        for kind, count in header.get("diagnostics", {}).get("counts", {}).items():
            if count:
                diagnostics.add("source_warning:" + kind, str(path))
        for number, line in enumerate(handle, 2):
            if not line.strip():
                continue
            record = json.loads(line)
            if not isinstance(record, dict) or record.get("type") != "usage" or record.get("machine") != machine:
                raise ValueError("invalid usage record: " + str(path) + ":" + str(number))
            key = record.get("event_id")
            if not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{64}", key):
                raise ValueError("invalid event ID in " + str(path))
            counters = counts(record)
            if counters is None or not isinstance(record.get("session_id"), str):
                raise ValueError("missing usage fields in " + str(path))
            clean = {"event_id": key, "session_id": record["session_id"],
                     "timestamp_utc": timestamp(record.get("timestamp_utc")), **counters}
            if key in events:
                if events[key][0] != clean:
                    raise ValueError("conflicting exports for event " + key + "; re-export the source logs")
                events[key][1].add(machine)
                diagnostics.add("duplicate_event_removed")
            else:
                events[key] = (clean, {machine})
    merged = []
    for record, machines in events.values():
        if len(machines) > 1:
            diagnostics.add("shared_event_unattributed", record["event_id"])
        merged.append({**record, "machine": next(iter(machines)) if len(machines) == 1 else SHARED})
    return merged, sorted(exported_machines)


def merge(args):
    paths = set()
    for item in args.inputs:
        if item.is_dir():
            paths.update(path.resolve() for path in item.rglob("*.usage.jsonl") if path.is_file())
        elif item.is_file():
            paths.add(item.resolve())
        else:
            raise ValueError("input does not exist: " + str(item))
    if not paths:
        raise ValueError("no .usage.jsonl exports found")
    diagnostics = Diagnostics()
    records, machines = load_exports(sorted(paths), diagnostics)
    args.out.mkdir(parents=True, exist_ok=True)
    summary = report(records, args.out, args)
    summary.update({"exports_read": len(paths), "exported_machines": machines,
                    "diagnostics": diagnostics.as_dict()})
    write_json(args.out / "summary.json", summary)
    print("Report:", args.out.resolve())
    print("Unique tokens:", f'{summary["total_tokens"]:,}')
    for row in summary["machines"]:
        print(f'{row["machine"]}: {row["total_tokens"]:,} tokens ({row["share_percent"]}%)')
    print("Diagnostics:", json.dumps(diagnostics.totals, ensure_ascii=False))


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--from", dest="start", type=date.fromisoformat, help="inclusive YYYY-MM-DD")
    common.add_argument("--to", dest="end", type=date.fromisoformat, help="inclusive YYYY-MM-DD")
    common.add_argument("--utc-offset", type=utc_offset, default=utc_offset("+08:00"),
                        help="date boundary, default +08:00; use --utc-offset=-05:00 for negative offsets")
    exp = commands.add_parser("export", parents=[common], help="export this computer's local sessions")
    exp.add_argument("--machine", default=socket.gethostname(), help="unique computer name")
    exp.add_argument("--codex-home", type=Path,
                     default=Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))))
    exp.add_argument("--sessions-dir", type=Path, action="append", help="override roots; repeat to include multiple")
    exp.add_argument("--out", type=Path, default=Path("exports"))
    exp.set_defaults(run=export)
    mer = commands.add_parser("merge", parents=[common], help="merge exports and calculate computer shares")
    mer.add_argument("inputs", type=Path, nargs="+", help="export files or folders (recursive)")
    mer.add_argument("--out", type=Path, default=Path("report"))
    mer.set_defaults(run=merge)
    return result


def main(argv=None):
    command_parser = parser()
    args = command_parser.parse_args(argv)
    if args.start and args.end and args.start > args.end:
        command_parser.error("--from must be on or before --to")
    try:
        args.run(args)
    except (OSError, ValueError, TypeError) as exc:
        print("Error:", exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
