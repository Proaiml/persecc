"""SecondX unit tests - no InfluxDB, no real process list needed."""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import collector  # noqa: E402
import influx_exporter as ife  # noqa: E402
import lissozis as ls  # noqa: E402
import SecondX as sx  # noqa: E402


class RankingTest(unittest.TestCase):
    def test_shower_keeps_legacy_behaviour(self):
        self.assertEqual(["b", "a", "c"], list(ls.shower({"a": 2, "b": 5, "c": "x"})))
        self.assertEqual({}, ls.shower(None))

    def test_top_excludes_limits_and_skips_idle(self):
        data = {"System Idle Process": 95, "chrome.exe": 3.5, "python.exe": 2.0, "idle.exe": 0.0, "nan": float("nan")}
        self.assertEqual([("chrome.exe", 3.5)], ls.top(data, 1, ["system idle process"]))
        self.assertEqual([("chrome.exe", 3.5), ("python.exe", 2.0)], ls.top(data, 6, ["System Idle Process"]))


class LineProtocolTest(unittest.TestCase):
    def test_escaping_and_format(self):
        line = ife.to_line({"measurement": "secondx process", "tags": {"host": "srv 1", "process": "a,b=c"},
                            "fields": {"value": 1}, "time_ns": 123})
        self.assertEqual(r"secondx\ process,host=srv\ 1,process=a\,b\=c value=1.0 123", line)

    def test_empty_tags_are_skipped(self):
        self.assertEqual("m,a=1 v=2.5 9", ife.to_line({"measurement": "m", "tags": {"a": "1", "b": ""},
                                                          "fields": {"v": 2.5}, "time_ns": 9}))


class LocalSinkTest(unittest.TestCase):
    def test_write_and_retention_only_touches_own_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            old = d / f"metrics-{(dt.date.today() - dt.timedelta(days=30)).isoformat()}.jsonl"
            old.write_text("{}\n")
            other = d / "notes.json"
            other.write_text("{}")
            sink = ife.LocalSink(d, retention_days=14)
            sink.write([{"measurement": "m", "tags": {"host": "h"}, "fields": {"v": 1.23456}, "time_ns": time.time_ns()}])
            today = d / f"metrics-{dt.date.today().isoformat()}.jsonl"
            row = json.loads(today.read_text().splitlines()[0])
            self.assertEqual({"v": 1.2346}, row["fields"])
            self.assertFalse(old.exists())
            self.assertTrue(other.exists())


class FlakySink:
    name = "flaky"

    def __init__(self, fail_times):
        self.fail_times = fail_times
        self.received = []

    def write(self, points):
        if self.fail_times > 0:
            self.fail_times -= 1
            raise ConnectionError("down")
        self.received.extend(points)

    def close(self):
        pass


class ExporterTest(unittest.TestCase):
    def test_outage_keeps_order_and_data(self):
        sink = FlakySink(fail_times=2)
        exp = ife.Exporter(sink)
        for i in range(5):
            exp.submit([{"measurement": "m", "tags": {}, "fields": {"i": i}, "time_ns": i}])
        end = time.time() + 10
        while len(sink.received) < 5 and time.time() < end:
            time.sleep(0.05)
        exp.close(timeout=1)
        self.assertEqual([0, 1, 2, 3, 4], [p["fields"]["i"] for p in sink.received])
        self.assertEqual(0, exp.dropped)

    def test_buffer_limit_drops_oldest(self):
        sink = FlakySink(fail_times=10**6)
        exp = ife.Exporter(sink, max_buffer_points=1000)
        exp.submit([{"measurement": "m", "tags": {}, "fields": {"i": i}, "time_ns": i} for i in range(1500)])
        self.assertEqual(1000, exp.pending)
        self.assertEqual(500, exp.dropped)
        exp.close(timeout=0.2)

    def test_submit_never_blocks_while_sink_hangs(self):
        gate = threading.Event()

        class Hanging(FlakySink):
            def write(self, points):
                gate.wait(5)
                super().write(points)

        exp = ife.Exporter(Hanging(0))
        t = time.perf_counter()
        for _ in range(200):
            exp.submit([{"measurement": "m", "tags": {}, "fields": {"v": 1}, "time_ns": 1}])
        self.assertLess(time.perf_counter() - t, 0.5)
        gate.set()
        exp.close(timeout=2)

    def test_legacy_influx_creator(self):
        self.assertFalse(ife.influx_creator("o", "b", "t", "f", "p", "34", 1))
        sink = FlakySink(0)
        exp = ife.Exporter(sink)
        ife.set_default_exporter(exp)
        try:
            self.assertTrue(ife.influx_creator("o", "b", "Custom_scripts", "EX134cpu", "cpu_usage", "36", 7))
        finally:
            ife.set_default_exporter(None)
            exp.close(timeout=2)
        self.assertEqual({"EX134cpu": 7.0}, sink.received[0]["fields"])


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        for k in sx.ENV_OVERRIDES:
            os.environ.pop(k, None)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_defaults_when_missing(self):
        cfg, notes = sx.load_config(self.dir / "config.json")
        self.assertEqual(1.0, cfg["interval_seconds"])
        self.assertTrue(cfg["host"])
        self.assertFalse(sx.influx_configured(cfg))
        self.assertTrue(any("not found" in n for n in notes))

    def test_legacy_v1_files(self):
        (self.dir / "config.json").write_text(json.dumps({"wait_time": 2, "description": "x", "log_retention_days": 7}))
        (self.dir / "token.json").write_text(json.dumps({"url": "http://db:8086", "token": "abc", "org": "H", "bucket": "SeconX"}))
        cfg, notes = sx.load_config(self.dir / "config.json")
        self.assertEqual(2.0, cfg["interval_seconds"])
        self.assertEqual(7, cfg["log_retention_days"])
        self.assertEqual(("http://db:8086", "abc", "H", "SeconX"),
                         tuple(cfg["influx"][k] for k in ("url", "token", "org", "bucket")))
        self.assertTrue(sx.influx_configured(cfg))
        self.assertTrue(any("wait_time" in n for n in notes))

    def test_placeholder_token_means_local_mode(self):
        (self.dir / "token.json").write_text(json.dumps({"token": "YOUR_INFLUXDB_TOKEN"}))
        cfg, _ = sx.load_config(self.dir / "config.json")
        self.assertFalse(sx.influx_configured(cfg))

    def test_env_overrides_and_validation(self):
        os.environ["SECONDX_INFLUX_TOKEN"] = "from-env"
        os.environ["SECONDX_HOST"] = "web-01"
        cfg, _ = sx.load_config(self.dir / "config.json")
        self.assertEqual("from-env", cfg["influx"]["token"])
        self.assertEqual("web-01", cfg["host"])
        (self.dir / "config.json").write_text('{"interval_seconds": 0.01}')
        with self.assertRaises(sx.ConfigError):
            sx.load_config(self.dir / "config.json")
        (self.dir / "config.json").write_text('{"top_n": 6,}')
        with self.assertRaises(sx.ConfigError):
            sx.load_config(self.dir / "config.json")


def fake_psutil(procs, disk=(0, 0), net=(0, 0)):
    """Minimal psutil replacement driven by mutable lists/tuples."""
    state = {"procs": procs, "disk": disk, "net": net}

    def process_iter(attrs=None, ad_value=None):
        for p in state["procs"]:
            yield SimpleNamespace(info=p, cpu_percent=lambda interval=None: 0.0)

    return state, SimpleNamespace(
        cpu_count=lambda: 2, cpu_percent=lambda interval=None: 25.0,
        virtual_memory=lambda: SimpleNamespace(percent=50.0, used=8e9, total=16e9),
        disk_io_counters=lambda: SimpleNamespace(read_bytes=state["disk"][0], write_bytes=state["disk"][1]),
        net_io_counters=lambda: SimpleNamespace(bytes_sent=state["net"][0], bytes_recv=state["net"][1]),
        process_iter=process_iter, Error=Exception,
    )


def proc(pid, name, cpu, ram, rb, wb):
    return {"pid": pid, "name": name, "cpu_percent": cpu, "memory_percent": ram,
            "io_counters": SimpleNamespace(read_bytes=rb, write_bytes=wb)}


class CollectorTest(unittest.TestCase):
    def test_aggregates_by_name_and_computes_rates(self):
        state, fake = fake_psutil([proc(1, "chrome.exe", 10, 5, 0, 0), proc(2, "chrome.exe", 20, 5, 0, 0),
                                   proc(3, "db", 4, 1, 0, 0)], disk=(0, 0), net=(0, 0))
        clock = [100.0]
        with mock.patch.object(collector, "psutil", fake), \
                mock.patch.object(collector.time, "monotonic", lambda: clock[0]):
            c = collector.Collector(use_fast_windows_path=False)
            first = c.sample()
            self.assertIsNone(first.interval_s)
            self.assertNotIn("disk_read_kbps", first.system)
            state["procs"] = [proc(1, "chrome.exe", 10, 5, 2048, 0), proc(2, "chrome.exe", 20, 5, 2048, 4096),
                              proc(3, "db", 4, 1, 0, 0)]
            state["disk"] = (4096, 8192)
            state["net"] = (2048, 1024)
            clock[0] += 2.0
            s = c.sample()
        self.assertEqual(2.0, s.interval_s)
        self.assertAlmostEqual(15.0, s.processes["cpu"]["chrome.exe"])      # (10+20)/2 cpus
        self.assertAlmostEqual(10.0, s.processes["ram"]["chrome.exe"])
        self.assertAlmostEqual(2.0, s.processes["disk_read"]["chrome.exe"])  # 4096 B / 1024 / 2 s
        self.assertAlmostEqual(2.0, s.processes["disk_write"]["chrome.exe"])  # 4096 B / 1024 / 2 s
        self.assertAlmostEqual(2.0, s.system["disk_read_kbps"])
        self.assertAlmostEqual(1.0, s.system["net_sent_kbps"])

    def test_pid_reuse_and_counter_reset_never_spike_or_go_negative(self):
        state, fake = fake_psutil([proc(7, "old.exe", 0, 0, 10**9, 10**9)])
        clock = [0.0]
        with mock.patch.object(collector, "psutil", fake), \
                mock.patch.object(collector.time, "monotonic", lambda: clock[0]):
            c = collector.Collector(use_fast_windows_path=False)
            c.sample()
            state["procs"] = [proc(7, "new.exe", 0, 0, 10**9 + 10**6, 0)]   # same pid, other program
            clock[0] += 1
            s = c.sample()
            self.assertNotIn("new.exe", s.processes["disk_read"])
            state["procs"] = [proc(7, "new.exe", 0, 0, 0, 0)]                 # counter reset
            clock[0] += 1
            s = c.sample()
        self.assertEqual(0.0, s.processes["disk_read"]["new.exe"])


class PointsTest(unittest.TestCase):
    def test_build_points(self):
        sample = collector.Sample(timestamp_ns=5, interval_s=1.0, system={"cpu_percent": 12.0},
                                  processes={"cpu": {"a": 3.0, "Idle": 90.0, "b": 1.0}, "ram": {}, "disk_read": {},
                                             "disk_write": {}})
        cfg = {"host": "h1", "top_n": 1, "exclude_processes": ["Idle"]}
        pts = sx.build_points(sample, cfg)
        self.assertEqual("secondx_system", pts[0]["measurement"])
        self.assertEqual([{"host": "h1", "metric": "cpu", "process": "a", "rank": "1"}],
                         [p["tags"] for p in pts[1:]])


@unittest.skipUnless(collector.win_snapshot.AVAILABLE, "Windows only")
class WindowsSnapshotTest(unittest.TestCase):
    def test_snapshot_matches_psutil_for_own_process(self):
        import psutil
        snap = collector.win_snapshot.snapshot()
        me = psutil.Process()
        name, cpu_s, rss, rb, wb = snap[me.pid]
        self.assertEqual(me.name(), name)
        self.assertAlmostEqual(sum(me.cpu_times()[:2]), cpu_s, delta=0.5)
        self.assertLess(abs(me.memory_info().rss - rss), 16 << 20)

    def test_fast_path_is_fast(self):
        c = collector.Collector()
        c.sample()
        t = time.perf_counter()
        c.sample()
        self.assertLess(time.perf_counter() - t, 0.5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
