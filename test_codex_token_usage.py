import argparse
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

import codex_token_usage as usage


def tokens(input_tokens, output_tokens=0, cached=0, reasoning=0):
    return dict(input_tokens=input_tokens, output_tokens=output_tokens,
                cached_input_tokens=cached, cache_write_input_tokens=0,
                reasoning_output_tokens=reasoning, total_tokens=input_tokens + output_tokens)


def event(total, last, when="2026-11-01T16:00:00Z"):
    return {"type": "event_msg", "timestamp": when,
            "payload": {"type": "token_count", "info": {
                "total_token_usage": total, "last_token_usage": last}}}


def write_log(path, events, session="session-a", trailing=b""):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = [{"type": "session_meta", "payload": {"id": session}}, *events]
    path.write_bytes(b"".join((json.dumps(item) + "\n").encode() for item in data) + trailing)


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def scan(self, events, trailing=b""):
        path = self.root / "sample.jsonl"
        write_log(path, events, trailing=trailing)
        diagnostics = usage.Diagnostics()
        return list(usage.scan_file(path, diagnostics)), diagnostics

    def run_cli(self, *args):
        with contextlib.redirect_stdout(io.StringIO()):
            status = usage.main(list(args))
        self.assertEqual(status, 0)

    def test_cumulative_not_summed_and_subsets_not_double_counted(self):
        first, second = tokens(100, 10, 50, 5), tokens(300, 30, 150, 15)
        records, diagnostics = self.scan([
            event(first, first),
            event(first, first, "2026-11-01T16:00:01Z"),
            {"type": "event_msg", "payload": {"type": "token_count", "info": None}},
            event(second, tokens(200, 20, 100, 10), "2026-11-01T16:01:00Z")])
        self.assertEqual(len(records), 2)
        self.assertEqual(sum(row["total_tokens"] for row in records), 330)
        self.assertEqual(sum(row["cached_input_tokens"] for row in records), 150)
        self.assertEqual(diagnostics.totals, {})

    def test_reset_can_reuse_an_earlier_counter_value(self):
        records, diagnostics = self.scan([
            event(tokens(20), tokens(20)),
            event(tokens(40), tokens(20), "2026-11-01T16:01:00Z"),
            event(tokens(20), tokens(20), "2026-11-01T16:02:00Z")])
        self.assertEqual(sum(row["total_tokens"] for row in records), 60)
        self.assertEqual(diagnostics.totals["counter_reset_used_last"], 1)

    def test_compaction_placeholder_is_ignored_if_cumulative_did_not_change(self):
        placeholder = tokens(0)
        placeholder["total_tokens"] = 50
        records, diagnostics = self.scan([
            event(tokens(100), tokens(100)),
            event(tokens(100), placeholder, "2026-11-01T16:01:00Z"),
            event(tokens(300), tokens(200), "2026-11-01T16:02:00Z")])
        self.assertEqual(sum(row["total_tokens"] for row in records), 300)
        self.assertEqual(diagnostics.totals, {})

    def test_missing_history_only_counts_the_observed_request(self):
        records, diagnostics = self.scan([
            event(tokens(1000), tokens(100)),
            event(tokens(2000), tokens(200), "2026-11-01T16:01:00Z")])
        self.assertEqual(sum(row["total_tokens"] for row in records), 300)
        self.assertIn("initial_history_gap_only_last_counted", diagnostics.totals)
        self.assertIn("history_gap_only_last_counted", diagnostics.totals)

    def test_missing_last_can_use_valid_cumulative_differences(self):
        records, diagnostics = self.scan([
            event(tokens(100), None),
            event(tokens(300), None, "2026-11-01T16:01:00Z"),
            event(tokens(50), None, "2026-11-01T16:02:00Z"),
            event(tokens(80), None, "2026-11-01T16:03:00Z")])
        self.assertEqual(sum(row["total_tokens"] for row in records), 330)
        self.assertIn("counter_reset_without_last_skipped", diagnostics.totals)

    def test_bad_record_and_partial_tail_do_not_stop_good_records(self):
        bad = tokens(5)
        bad["total_tokens"] = -1
        records, diagnostics = self.scan([event(bad, bad), event(tokens(100), tokens(100))],
                                         trailing=b'{"type":"token_count"')
        self.assertEqual(len(records), 1)
        self.assertEqual(diagnostics.totals["invalid_token_or_metadata_record"], 1)
        self.assertEqual(diagnostics.totals["incomplete_trailing_line"], 1)

    def test_export_reads_archives_and_filters_after_counter_calculation(self):
        home = self.root / "home"
        write_log(home / "sessions" / "a.jsonl", [
            event(tokens(100), tokens(100), "2026-11-01T15:59:00Z"),
            event(tokens(300), tokens(200), "2026-11-01T16:01:00Z")])
        write_log(home / "archived_sessions" / "b.jsonl", [event(tokens(50), tokens(50))], "session-b")
        output = self.root / "exports"
        self.run_cli("export", "--codex-home", str(home), "--machine", "PC-A", "--out", str(output),
                     "--from", "2026-11-02", "--to", "2026-11-02")
        summary = json.loads(next(output.glob("*.summary.json")).read_text(encoding="utf-8"))
        self.assertEqual(summary["total_tokens"], 250)
        self.assertEqual(summary["files_scanned"], 2)
        exported = next(output.glob("*.usage.jsonl")).read_text(encoding="utf-8")
        self.assertNotIn("creator_account_id", exported)
        self.assertNotIn("payload", exported)

    def export_fixture(self, machine, events, session="session-a", name=None):
        logs = self.root / (name or machine) / "logs"
        write_log(logs / "a.jsonl", events, session)
        output = self.root / (name or machine) / "exports"
        self.run_cli("export", "--sessions-dir", str(logs), "--machine", machine, "--out", str(output))
        return next(output.glob("*.usage.jsonl"))

    def test_repeated_exports_deduplicate_and_cross_computer_copies_are_unknown(self):
        common = event(tokens(100), tokens(100))
        a = self.export_fixture("PC-A", [common])
        repeat = self.export_fixture("PC-A", [common], name="repeat")
        b = self.export_fixture("PC-B", [common])
        c = self.export_fixture("PC-C", [event(tokens(300), tokens(300))], session="unique-session")
        output = self.root / "report"
        self.run_cli("merge", str(a), str(repeat), str(b), str(c), "--out", str(output))
        summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
        by_machine = {row["machine"]: row for row in summary["machines"]}
        self.assertEqual(summary["total_tokens"], 400)
        self.assertEqual(by_machine[usage.SHARED]["share_percent"], "25.000000")
        self.assertEqual(by_machine["PC-C"]["share_percent"], "75.000000")
        self.assertEqual(summary["diagnostics"]["counts"]["duplicate_event_removed"], 2)

    def test_same_session_continued_on_two_devices_keeps_new_events_attributed(self):
        first = event(tokens(100), tokens(100))
        second = event(tokens(300), tokens(200), "2026-11-01T16:01:00Z")
        a = self.export_fixture("PC-A", [first])
        b = self.export_fixture("PC-B", [first, second])
        diagnostics = usage.Diagnostics()
        records, _ = usage.load_exports([a, b], diagnostics)
        by_machine = {record["machine"]: record["total_tokens"] for record in records}
        self.assertEqual(by_machine, {usage.SHARED: 100, "PC-B": 200})

    def test_conflicting_exports_fail_instead_of_picking_one(self):
        a = self.export_fixture("PC-A", [event(tokens(100), tokens(100))])
        lines = a.read_text(encoding="utf-8").splitlines()
        record = json.loads(lines[1])
        record["input_tokens"] = record["total_tokens"] = 200
        altered = self.root / "altered.usage.jsonl"
        altered.write_text(lines[0] + "\n" + json.dumps(record) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "conflicting exports"):
            usage.load_exports([a, altered], usage.Diagnostics())

    def test_date_offset_and_daily_and_period_shares(self):
        args = argparse.Namespace(start=None, end=None, utc_offset=usage.utc_offset("+08:00"))
        records = [dict(machine="A", timestamp_utc="2026-11-01T15:59:00+00:00", **tokens(100)),
                   dict(machine="A", timestamp_utc="2026-11-01T16:00:00+00:00", **tokens(300)),
                   dict(machine="B", timestamp_utc="2026-11-01T16:00:00+00:00", **tokens(100))]
        summary = usage.report(records, self.root, args)
        self.assertEqual(summary["total_tokens"], 500)
        self.assertEqual(summary["machines"][0]["share_percent"], "80.000000")
        import csv
        with (self.root / "daily.csv").open(encoding="utf-8-sig", newline="") as handle:
            daily = list(csv.DictReader(handle))
        self.assertEqual([row["date"] for row in daily], ["2026-11-01", "2026-11-02", "2026-11-02"])
        self.assertEqual([row["day_share_percent"] for row in daily], ["100.000000", "75.000000", "25.000000"])

    def test_empty_export_is_valid_but_missing_source_is_an_error(self):
        path = self.export_fixture("PC-A", [])
        records, machines = usage.load_exports([path], usage.Diagnostics())
        self.assertEqual(records, [])
        self.assertEqual(machines, ["PC-A"])
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(usage.main(["export", "--codex-home", str(self.root / "missing")]), 1)


if __name__ == "__main__":
    unittest.main()
