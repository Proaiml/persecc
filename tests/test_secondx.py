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

    def test_equal_values_share_rank_and_are_not_dropped_at_the_cutoff(self):
        # Real Windows sample: CPU time is counted in clock ticks, so several processes show 0.233 %.
        data = {"chrome.exe": 5.78, "Taskmgr.exe": 0.47, "audiodg.exe": 0.37, "DCv2.exe": 0.33,
                "pycharm64.exe": 0.233, "claude.exe": 0.233, "lghub_agent.exe": 0.233, "SnippingTool.exe": 0.233,
                "tiny.exe": 0.1}
        self.assertEqual([(1, "chrome.exe", 5.78), (2, "Taskmgr.exe", 0.47), (3, "audiodg.exe", 0.37),
                          (4, "DCv2.exe", 0.33), (5, "claude.exe", 0.233), (5, "lghub_agent.exe", 0.233),
                          (5, "pycharm64.exe", 0.233), (5, "SnippingTool.exe", 0.233)], ls.ranked(data, 6))

    def test_ties_do_not_depend_on_the_order_the_os_lists_processes(self):
        a = {"x.exe": 1.0, "b.exe": 2.0, "a.exe": 2.0, "c.exe": 2.0}
        b = dict(reversed(list(a.items())))
        self.assertEqual(ls.ranked(a, 2), ls.ranked(b, 2))
        self.assertEqual([(1, "a.exe", 2.0), (1, "b.exe", 2.0), (1, "c.exe", 2.0)], ls.ranked(a, 2))

    def test_large_tie_is_capped(self):
        data = {f"p{i:02d}.exe": 1.0 for i in range(50)}
        out = ls.ranked(data, 6)
        self.assertEqual(12, len(out))                              # at most 2 x top_n
        self.assertEqual({1}, {rank for rank, _, _ in out})
        self.assertEqual(6, len(ls.ranked(data, 6, max_ties=0)))


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


def parse_lines(text):
    """Minimal line-protocol reader for the test points (no tags, one field)."""
    out = []
    for line in text.splitlines():
        if line.strip():
            head, fields, ts = line.split(" ")
            key, value = fields.split("=")
            out.append({"measurement": head.split(",")[0], "tags": {}, "fields": {key: float(value)}, "time_ns": int(ts)})
    return out


class FlakySink:
    name = "flaky"

    def __init__(self, fail_times):
        self.fail_times = fail_times
        self.received = []
        self.lock = threading.Lock()

    def write(self, points):
        if self.fail_times > 0:
            self.fail_times -= 1
            raise ConnectionError("down")
        with self.lock:
            self.received.extend(points)

    def write_lines(self, text):
        self.write(parse_lines(text))

    def close(self):
        pass


def pts(start, n):
    return [{"measurement": "m", "tags": {}, "fields": {"i": float(i)}, "time_ns": i} for i in range(start, start + n)]


def wait_until(cond, timeout=10.0):
    end = time.time() + timeout
    while not cond() and time.time() < end:
        time.sleep(0.05)
    return cond()


class SwitchSink(FlakySink):
    """Fails while ``down`` is True."""

    def __init__(self):
        super().__init__(0)
        self.down = False

    def write(self, points):
        if self.down:
            raise ConnectionError("influx down")
        with self.lock:
            self.received.extend(points)


class ExporterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.spool = Path(self.tmp.name) / "spool"

    def tearDown(self):
        self.tmp.cleanup()

    def test_short_outage_keeps_all_data(self):
        sink = FlakySink(fail_times=2)
        exp = ife.Exporter(sink, backlog_min_pause_seconds=0)
        for i in range(5):
            exp.submit(pts(i, 1))
        self.assertTrue(wait_until(lambda: len(sink.received) == 5))
        exp.close(timeout=1)
        self.assertEqual([0, 1, 2, 3, 4], sorted(int(p["fields"]["i"]) for p in sink.received))
        self.assertIsNone(exp.fatal_reason)

    def test_long_outage_spools_blocks_to_disk_and_keeps_ram_small(self):
        sink = SwitchSink()
        sink.down = True
        exp = ife.Exporter(sink, max_memory_points=2000, spool_dir=self.spool, spool_block_points=100,
                           backlog_min_pause_seconds=0, min_free_disk_bytes=0)
        for k in range(30):                           # 30 "seconds" x 50 points
            exp.submit(pts(k * 50, 50))
        self.assertTrue(wait_until(lambda: len(list(self.spool.glob("block-*.lp"))) >= 10))
        self.assertLess(exp.pending, 200)                 # RAM stays bounded
        sink.down = False
        exp._wake.set()
        self.assertTrue(wait_until(lambda: len(sink.received) == 1500))
        self.assertEqual([], list(self.spool.glob("block-*.lp")))
        exp.close(timeout=1)
        self.assertEqual(list(range(1500)), sorted(int(p["fields"]["i"]) for p in sink.received))
        self.assertIsNone(exp.fatal_reason)

    def test_unsent_points_survive_restart_via_spool(self):
        sink = SwitchSink()
        sink.down = True
        exp = ife.Exporter(sink, spool_dir=self.spool, min_free_disk_bytes=0)
        exp.submit(pts(0, 250))
        exp.close(timeout=0.5)                             # "service stopped" during outage
        self.assertTrue(list(self.spool.glob("block-*.lp")))
        sink2 = SwitchSink()
        exp2 = ife.Exporter(sink2, spool_dir=self.spool, backlog_min_pause_seconds=0, min_free_disk_bytes=0)
        self.assertTrue(wait_until(lambda: len(sink2.received) == 250))
        exp2.close(timeout=1)

    def test_outage_longer_than_limit_is_fatal(self):
        sink = SwitchSink()
        sink.down = True
        exp = ife.Exporter(sink, spool_dir=self.spool, max_outage_seconds=0.5, min_free_disk_bytes=0)
        exp.submit(pts(0, 10))
        self.assertTrue(wait_until(lambda: exp.fatal_reason is not None, timeout=5))
        self.assertIn("unreachable", exp.fatal_reason)
        exp.close(timeout=0.2)

    def test_spool_size_limit_is_fatal(self):
        sink = SwitchSink()
        sink.down = True
        exp = ife.Exporter(sink, spool_dir=self.spool, spool_block_points=100, max_spool_bytes=2000,
                           min_free_disk_bytes=0)
        exp.submit(pts(0, 500))
        self.assertTrue(wait_until(lambda: exp.fatal_reason is not None, timeout=5))
        self.assertIn("spool size", exp.fatal_reason)
        exp.close(timeout=0.2)

    def test_full_ram_buffer_is_fatal_instead_of_dropping(self):
        sink = SwitchSink()
        sink.down = True
        exp = ife.Exporter(sink, max_memory_points=1000)       # no spool configured
        exp.submit(pts(0, 1500))
        self.assertIn("RAM buffer full", exp.fatal_reason)
        self.assertEqual(1500, exp.pending)                    # nothing silently dropped
        exp.close(timeout=0.2)

    def test_submit_never_blocks_while_sink_hangs(self):
        gate = threading.Event()

        class Hanging(FlakySink):
            def write(self, points):
                gate.wait(5)
                super().write(points)

        exp = ife.Exporter(Hanging(0))
        t = time.perf_counter()
        for i in range(200):
            exp.submit(pts(i, 1))
        self.assertLess(time.perf_counter() - t, 0.5)
        gate.set()
        exp.close(timeout=2)

    def test_crashed_sender_thread_is_reported(self):
        class Boom(BaseException):
            pass

        class Crashing(FlakySink):
            def write(self, points):
                raise Boom("unexpected")

        exp = ife.Exporter(Crashing(0))
        self.assertIsNone(exp.health())
        exp.submit(pts(0, 5))
        self.assertTrue(wait_until(lambda: exp.health() is not None))
        self.assertIn("secondx-live thread crashed", exp.health())
        exp.close(timeout=0.2)

    def test_hanging_write_is_reported_and_its_batch_is_not_lost(self):
        gate = threading.Event()
        started = threading.Event()

        class Hanging(FlakySink):
            def write(self, points):
                started.set()
                gate.wait(10)

        exp = ife.Exporter(Hanging(0), spool_dir=self.spool, stall_seconds=1, min_free_disk_bytes=0)
        exp.submit(pts(0, 40))
        self.assertTrue(started.wait(5))
        self.assertIsNone(exp.health())                                  # not yet over the limit
        self.assertTrue(wait_until(lambda: exp.health() is not None, timeout=5))
        self.assertIn("hanging", exp.health())
        exp.close(timeout=0.2)                                           # agent stops while the write hangs
        saved = [p for f in sorted(self.spool.glob("block-*.lp")) for p in parse_lines(ife.Exporter._read_block(f)[0])]
        self.assertEqual(list(range(40)), sorted(int(p["fields"]["i"]) for p in saved))
        gate.set()

    def test_corrupt_spool_block_is_set_aside_and_the_rest_is_sent(self):
        self.spool.mkdir(parents=True)
        (self.spool / "block-00000000000000000001-000001.jsonl").write_text('{"broken\n', encoding="utf-8")
        legacy = self.spool / "block-00000000000000000002-000002.jsonl"          # 2.1.0 format still read
        legacy.write_text("".join(json.dumps(p) + "\n" for p in pts(0, 10)), encoding="utf-8")
        sink = FlakySink(0)
        exp = ife.Exporter(sink, spool_dir=self.spool, backlog_min_pause_seconds=0, min_free_disk_bytes=0)
        self.assertTrue(wait_until(lambda: len(sink.received) == 10))
        exp.close(timeout=1)
        self.assertEqual([], list(self.spool.glob("block-*.lp")))
        self.assertEqual(1, len(list(self.spool.glob("block-*.bad"))))
        self.assertIsNone(exp.fatal_reason)

    def write_blocks(self, n, per_block=50):
        self.spool.mkdir(parents=True, exist_ok=True)
        for b in range(n):
            lines = [ife.to_line(p) for p in pts(b * per_block, per_block)]
            (self.spool / f"block-{b:020d}-{b:06d}.lp").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_backlog_is_uploaded_in_parallel_when_writes_wait_on_the_server(self):
        self.write_blocks(40)

        class SlowServer(FlakySink):                  # 50 ms per write, almost no CPU (like a remote InfluxDB)
            def __init__(self):
                super().__init__(0)
                self.active = self.peak = 0

            def write_lines(self, text):
                with self.lock:
                    self.active += 1
                    self.peak = max(self.peak, self.active)
                time.sleep(0.05)
                with self.lock:
                    self.active -= 1
                super().write_lines(text)

        sink = SlowServer()
        exp = ife.Exporter(sink, spool_dir=self.spool, backlog_max_workers=4, min_free_disk_bytes=0)
        self.assertTrue(wait_until(lambda: len(sink.received) == 2000, timeout=20))
        self.assertTrue(wait_until(lambda: exp.backlog_workers == 0, timeout=5))    # workers exit when done
        exp.close(timeout=1)
        self.assertGreaterEqual(sink.peak, 2)                                       # scaled out
        self.assertLessEqual(sink.peak, 4)                                          # never above the limit
        self.assertEqual(list(range(2000)), sorted(int(p["fields"]["i"]) for p in sink.received))
        self.assertEqual([], list(self.spool.glob("block-*")))

    def test_live_data_is_not_queued_behind_the_backlog(self):
        self.write_blocks(30)
        gate = threading.Event()

        class SlowBacklog(FlakySink):
            def write_lines(self, text):              # backlog upload is slow ...
                gate.wait(5)
                super().write_lines(text)

        sink = SlowBacklog(0)
        exp = ife.Exporter(sink, spool_dir=self.spool, backlog_max_workers=1, min_free_disk_bytes=0)
        live = [{"measurement": "live", "tags": {}, "fields": {"v": 1.0}, "time_ns": time.time_ns()}]
        exp.submit(live)
        # ... but the live point is written at once, while the backlog is still waiting
        self.assertTrue(wait_until(lambda: any(p["measurement"] == "live" for p in sink.received), timeout=2))
        self.assertEqual(1, len(sink.received))
        gate.set()
        self.assertTrue(wait_until(lambda: len(sink.received) == 1 + 30 * 50, timeout=20))
        exp.close(timeout=1)

    def test_rejected_block_is_kept_aside_and_does_not_block_the_queue(self):
        self.write_blocks(3)

        class Picky(FlakySink):
            def write_lines(self, text):
                if text.startswith("m i=50.0"):       # second block: the server refuses its content
                    raise ife.Rejected("HTTP 400: field type conflict")
                super().write_lines(text)

        sink = Picky(0)
        exp = ife.Exporter(sink, spool_dir=self.spool, min_free_disk_bytes=0)
        self.assertTrue(wait_until(lambda: len(sink.received) == 100))
        self.assertTrue(wait_until(lambda: exp.spool_bytes() == 0))
        exp.close(timeout=1)
        self.assertEqual(1, len(list(self.spool.glob("block-*.rejected"))))
        self.assertIsNone(exp.fatal_reason)

    def test_sink_without_line_protocol_cannot_use_a_spool(self):
        class DictOnly:
            name = "dict-only"

            def write(self, points):
                pass

            def close(self):
                pass

        with self.assertRaises(TypeError):
            ife.Exporter(DictOnly(), spool_dir=self.spool)

    def test_delivery_delay_is_measured(self):
        sink = FlakySink(0)
        exp = ife.Exporter(sink)
        now = time.time_ns()
        exp.submit([{"measurement": "m", "tags": {}, "fields": {"v": 1.0}, "time_ns": now}])
        self.assertTrue(wait_until(lambda: len(sink.received) == 1))
        delay = exp.take_max_delay_ms()
        self.assertGreaterEqual(delay, 0)
        self.assertLess(delay, 1000)
        self.assertEqual(0.0, exp.take_max_delay_ms())                   # window resets
        exp.close(timeout=1)

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


class FakeProc:
    def __init__(self, rss_mb=50.0, cpu_rate=0.01):
        self.rss_mb = rss_mb
        self.cpu_rate = cpu_rate          # CPU seconds consumed per wall second
        self.cpu_s = 0.0

    def memory_info(self):
        return SimpleNamespace(rss=self.rss_mb * 2**20)

    def cpu_times(self):
        return SimpleNamespace(user=self.cpu_s, system=0.0)


LIMITS = {"max_cpu_percent": 25.0, "max_memory_mb": 200, "cpu_window_seconds": 3}


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def tick(guard, proc, clock, seconds=1.0):
    clock.t += seconds
    proc.cpu_s += proc.cpu_rate * seconds
    guard.check()


class GuardTest(unittest.TestCase):
    def test_memory_breach_stops_immediately_even_during_warmup(self):
        proc, clock = FakeProc(), Clock()
        g = sx.ResourceGuard(LIMITS, 1.0, proc, clock)
        tick(g, proc, clock)
        proc.rss_mb = 201
        with self.assertRaises(sx.StopAgent) as ctx:
            tick(g, proc, clock)
        self.assertEqual(sx.EXIT_RESOURCE, ctx.exception.code)

    def test_startup_burst_is_ignored_but_steady_overuse_stops(self):
        proc, clock = FakeProc(cpu_rate=0.9), Clock()      # 90% of a core during start-up
        g = sx.ResourceGuard(LIMITS, 1.0, proc, clock)
        for _ in range(4):
            tick(g, proc, clock)                           # warm-up: no stop
        proc.cpu_rate = 0.05
        for _ in range(10):
            tick(g, proc, clock)                           # 5% steady: fine
        proc.cpu_rate = 0.6
        with self.assertRaises(sx.StopAgent) as ctx:
            for _ in range(4):
                tick(g, proc, clock)                       # 60% for 3 s -> stop
        self.assertIn("CPU", ctx.exception.reason)

    def test_short_spike_inside_window_is_tolerated(self):
        proc, clock = FakeProc(cpu_rate=0.05), Clock()
        g = sx.ResourceGuard(LIMITS, 1.0, proc, clock)
        for _ in range(8):
            tick(g, proc, clock)
        proc.cpu_rate = 0.5
        tick(g, proc, clock)                               # one 50% second: 3 s average = 20% -> fine
        proc.cpu_rate = 0.05
        for _ in range(5):
            tick(g, proc, clock)

    def test_precision_guard(self):
        p = sx.PrecisionGuard({"max_missed_slots": 3, "slot_tolerance_ms": 500}, 1.0)
        self.assertFalse(p.check(0.1, 0.05))
        p.check(0.6, 0.05)
        p.check(0.0, 1.2)
        self.assertFalse(p.check(0.0, 0.05))          # recovered: counter reset
        self.assertEqual(0, p.missed)
        self.assertTrue(p.check(45.0, 0.05))          # clock jump: resync, no stop
        p.check(0.7, 0.05)
        p.check(0.8, 0.05)
        with self.assertRaises(sx.StopAgent) as ctx:
            p.check(0.9, 0.05)
        self.assertEqual(sx.EXIT_PRECISION, ctx.exception.code)


class BrokenCollector:
    def __init__(self, fail_on=3):
        self.n = 0
        self.fail_on = fail_on

    def sample(self):
        self.n += 1
        if self.n == self.fail_on:
            raise RuntimeError("boom")
        return collector.Sample(timestamp_ns=time.time_ns(), interval_s=None if self.n == 1 else 0.2,
                                system={"cpu_percent": 1.0},
                                processes={"cpu": {}, "ram": {}, "disk_read": {}, "disk_write": {}})


class AgentTest(unittest.TestCase):
    def cfg(self, **kw):
        with tempfile.TemporaryDirectory() as tmp:
            cfg, _ = sx.load_config(Path(tmp) / "none.json")
        cfg["interval_seconds"] = 0.2
        cfg["local_output"]["enabled"] = False
        cfg.update(kw)
        return cfg

    def run_agent(self, cfg, coll, cycles=8):
        return sx.run_agent(cfg, [], True, Path(tempfile.gettempdir()), collector=coll,
                            resource_guard=sx.ResourceGuard(cfg["limits"], cfg["interval_seconds"], FakeProc(), Clock()),
                            max_cycles=cycles)

    def test_strict_mode_stops_on_first_error(self):
        coll = BrokenCollector(fail_on=3)
        self.assertEqual(sx.EXIT_ERROR, self.run_agent(self.cfg(), coll))
        self.assertEqual(3, coll.n)

    def test_non_strict_mode_continues(self):
        coll = BrokenCollector(fail_on=3)
        self.assertEqual(sx.EXIT_OK, self.run_agent(self.cfg(strict_mode=False), coll, cycles=6))
        self.assertEqual(6, coll.n)

    def test_resource_breach_stops_agent(self):
        cfg = self.cfg()
        proc = FakeProc(rss_mb=500)
        code = sx.run_agent(cfg, [], True, Path(tempfile.gettempdir()), collector=BrokenCollector(fail_on=99),
                            resource_guard=sx.ResourceGuard(cfg["limits"], 0.2, proc, Clock()), max_cycles=5)
        self.assertEqual(sx.EXIT_RESOURCE, code)

    def test_export_safety_limit_stops_agent_even_when_not_strict(self):
        exp = self.fake_exporter()
        exp.submit = lambda points: setattr(exp, "fatal_reason", "RAM buffer full")
        self.assertEqual(sx.EXIT_EXPORT, self.run_with_exporter(exp))

    def test_dead_or_hanging_sender_stops_agent_even_when_not_strict(self):
        exp = self.fake_exporter()
        exp.submit = lambda points: setattr(exp, "health", lambda: "secondx-live thread stopped unexpectedly")
        self.assertEqual(sx.EXIT_ERROR, self.run_with_exporter(exp))       # restartable

    @staticmethod
    def fake_exporter():
        return SimpleNamespace(fatal_reason=None, written=0, pending=0, spooled=0, close=lambda timeout: None,
                               health=lambda: None, take_max_delay_ms=lambda: 0.0)

    def run_with_exporter(self, exp):
        cfg = self.cfg(strict_mode=False)
        with mock.patch.object(sx, "make_exporter", return_value=exp):
            return sx.run_agent(cfg, [], False, Path(tempfile.gettempdir()), collector=BrokenCollector(fail_on=99),
                                resource_guard=sx.ResourceGuard(cfg["limits"], 0.2, FakeProc(), Clock()),
                                max_cycles=5)


class SupervisorTest(unittest.TestCase):
    POLICY = {"max_attempts": 5, "delay_seconds": 300, "reset_after_seconds": 3600}

    def child(self, code):
        return [sys.executable, "-c", f"import sys; sys.exit({code})"]

    def test_restartable_error_is_retried_five_times_then_gives_up(self):
        waits = []
        code = sx.supervise(self.child(1), self.POLICY, sleep=lambda s: waits.append(s) or False)
        self.assertEqual(1, code)
        self.assertEqual([300.0] * 5, waits)               # 5 restarts, 5 minutes apart

    def test_resource_limit_is_never_restarted(self):
        waits = []
        self.assertEqual(sx.EXIT_RESOURCE, sx.supervise(self.child(3), self.POLICY,
                                                        sleep=lambda s: waits.append(s) or False))
        self.assertEqual([], waits)

    def test_config_and_export_limits_are_never_restarted(self):
        for c in (sx.EXIT_CONFIG, sx.EXIT_EXPORT, sx.EXIT_OK):
            waits = []
            self.assertEqual(c, sx.supervise(self.child(c), self.POLICY, sleep=lambda s: waits.append(s) or False))
            self.assertEqual([], waits)


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
