"""Unit tests for the parts that do not need a running Antigravity."""

from __future__ import annotations

import datetime
import os
import tempfile
import unittest
from unittest import mock

from agdeck import client, config, platform_util
from agdeck.rpc import parse_language_server


def _step_cmd() -> dict:
    return {
        "type": "CORTEX_STEP_TYPE_RUN_COMMAND",
        "status": "CORTEX_STEP_STATUS_DONE",
        "metadata": {
            "createdAt": "2026-01-02T03:04:05Z",
            "startedAt": "2026-01-02T03:04:05Z",
            "completedAt": "2026-01-02T03:04:07Z",
            "modelUsage": {"model": "MODEL_X", "inputTokens": 10, "outputTokens": 4},
        },
        "runCommand": {
            "commandLine": "npm test",
            "cwd": "/tmp/demo",
            "exitCode": 1,
            "combinedOutput": {"full": "1 failing test"},
        },
    }


class NormalizeStepTest(unittest.TestCase):
    def test_command_fields(self):
        event = client.normalize_step(_step_cmd(), 7)
        self.assertEqual(event["index"], 7)
        self.assertEqual(event["kind"], "tool")
        self.assertEqual(event["label"], "执行命令")
        self.assertEqual(event["command"], "npm test")
        self.assertEqual(event["output"], "1 failing test")
        self.assertEqual(event["exitCode"], 1)
        self.assertEqual(event["tokensIn"], 10)
        self.assertEqual(event["ms"], 2000)

    def test_unknown_type_falls_back(self):
        event = client.normalize_step(
            {"type": "CORTEX_STEP_TYPE_SOMETHING_NEW", "metadata": {}, "somethingNew": {"a": 1}},
            0,
        )
        self.assertEqual(event["kind"], "tool")
        self.assertIn("Something New", event["label"])

    def test_view_file_range(self):
        event = client.normalize_step({
            "type": "CORTEX_STEP_TYPE_VIEW_FILE",
            "metadata": {},
            "viewFile": {
                "absolutePathUri": "file:///home/dev/app/main.py",
                "startLine": 10,
                "endLine": 19,
                "numLines": 400,
                "content": "print(1)",
            },
        }, 0)
        self.assertEqual(event["path"], "/home/dev/app/main.py")
        self.assertEqual(event["numLines"], 10)      # lines in this excerpt
        self.assertEqual(event["fileLines"], 400)    # lines in the whole file


class ListActivityCacheTest(unittest.TestCase):
    """The sidebar polls every couple of seconds; unchanged step counts must not refetch."""

    def setUp(self):
        client._ACTIVITY_CACHE.clear()
        self.summary = {
            "cid-1": {
                "status": "CASCADE_RUN_STATUS_RUNNING",
                "stepCount": 5,
                "annotations": {"title": "demo"},
                "workspaces": [{"workspaceFolderAbsoluteUri": "file:///tmp/demo",
                                "branchName": "main", "repository": {"computedName": "demo"}}],
            }
        }

    def _run(self):
        with mock.patch.object(client, "rpc",
                               return_value={"trajectorySummaries": self.summary}), \
             mock.patch.object(client, "get_steps",
                               return_value=[{"type": "CORTEX_STEP_TYPE_USER_INPUT",
                                              "userInput": {"userResponse": "go"}}]) as steps:
            rows = client.list_sessions(activity=True)
        return rows, steps

    def test_reuses_activity_when_the_step_count_is_unchanged(self):
        rows, steps = self._run()
        self.assertEqual(rows[0]["activity"], "已收到指令，正在启动…")
        self.assertEqual(steps.call_count, 1)

        rows, steps = self._run()
        self.assertEqual(rows[0]["activity"], "已收到指令，正在启动…")
        self.assertEqual(steps.call_count, 0, "步数没变时不该再拉步骤")

    def test_refetches_after_new_steps(self):
        self._run()
        self.summary["cid-1"]["stepCount"] = 6
        _, steps = self._run()
        self.assertEqual(steps.call_count, 1)

    def test_idle_sessions_skip_the_activity_probe(self):
        self.summary["cid-1"]["status"] = "CASCADE_RUN_STATUS_IDLE"
        rows, steps = self._run()
        self.assertNotIn("activity", rows[0])
        self.assertEqual(steps.call_count, 0)


class UriToPathTest(unittest.TestCase):
    """Session URIs must render the same on every host OS (see Windows CI)."""

    def test_posix_uri(self):
        self.assertEqual(client._uri_to_path("file:///home/dev/app/main.py"),
                         "/home/dev/app/main.py")

    def test_windows_drive_uri(self):
        self.assertEqual(client._uri_to_path("file:///C:/Users/me/app/main.py"),
                         "C:\\Users\\me\\app\\main.py")

    def test_percent_decoding(self):
        self.assertEqual(client._uri_to_path("file:///home/dev/my%20app/main.py"),
                         "/home/dev/my app/main.py")

    def test_non_file_uri_is_passed_through(self):
        self.assertEqual(client._uri_to_path("untitled:Untitled-1"), "untitled:Untitled-1")
        self.assertEqual(client._uri_to_path(""), "")


class CompactEventTest(unittest.TestCase):
    def test_strips_heavy_fields(self):
        full = client.normalize_step(_step_cmd(), 0)
        full["diff"] = [{"mark": "+", "text": "x"}]
        full["preview"] = "y" * 5000
        compact = client.compact_event(full)
        self.assertNotIn("output", compact)
        self.assertNotIn("diff", compact)
        self.assertNotIn("preview", compact)
        self.assertTrue(compact["hasBody"])
        # the light fields survive
        self.assertEqual(compact["command"], "npm test")

    def test_truncates_long_text(self):
        event = {"index": 0, "kind": "assistant", "text": "a" * (client.COMPACT_TEXT_LIMIT + 50)}
        compact = client.compact_event(event)
        self.assertTrue(compact["textTruncated"])
        self.assertEqual(compact["textLen"], client.COMPACT_TEXT_LIMIT + 50)
        self.assertEqual(len(compact["text"]), client.COMPACT_TEXT_LIMIT)

    def test_leaves_short_text_alone(self):
        compact = client.compact_event({"index": 0, "kind": "assistant", "text": "done"})
        self.assertNotIn("hasBody", compact)


def _finished_reply() -> dict:
    return {"index": 1, "kind": "assistant", "status": "DONE", "text": "all set",
            "toolCalls": [], "task": None}


def _task_step(index: int = 2, task_id: str = "cascade/task-7",
               title: str = "Run pytest on control_panel tests") -> dict:
    return {"index": index, "kind": "tool", "task": {"id": task_id, "title": title},
            "text": "", "result": ""}


class ProgressTest(unittest.TestCase):
    def test_stale_running_status_with_finished_reply_is_idle(self):
        """状态滞后：回复已经收尾，没有任务时按空闲处理。"""
        events = [{"index": 0, "kind": "user", "text": "go"}, _finished_reply()]
        progress = client.build_progress(events, "CASCADE_RUN_STATUS_RUNNING", 2)
        self.assertEqual(progress["state"], "idle")
        self.assertFalse(progress["background"])
        self.assertEqual(progress["turnSteps"], 1)

    def test_trailing_system_message_does_not_resurrect_the_turn(self):
        """本轮结束后插入的系统消息不代表新一轮，状态滞后时也不该显示为忙碌。"""
        events = [
            {"index": 0, "kind": "user", "text": "go"},
            _finished_reply(),
            {"index": 3, "kind": "system", "text": "System Message",
             "message": "Run sleep command finished"},
        ]
        progress = client.build_progress(events, "CASCADE_RUN_STATUS_RUNNING", 4)
        self.assertEqual(progress["state"], "idle")
        self.assertEqual(progress["currentAction"], "本轮回复完成")

    def test_open_task_keeps_the_session_working_even_when_idle(self):
        """后台任务在跑时 summary 也是 IDLE，必须靠任务完成标记判断。"""
        events = [{"index": 0, "kind": "user", "text": "go"},
                  _task_step(), _finished_reply()]
        progress = client.build_progress(events, "CASCADE_RUN_STATUS_IDLE", 3)
        self.assertEqual(progress["state"], "working")
        self.assertTrue(progress["background"])
        self.assertIn("后台任务", progress["currentAction"])
        self.assertIn("pytest", progress["currentAction"])

    def test_finished_task_is_closed_by_the_system_message(self):
        events = [
            {"index": 0, "kind": "user", "text": "go"},
            _task_step(),
            _finished_reply(),
            {"index": 4, "kind": "system", "text":
             'Task id "cascade/task-7" finished with result:\nThe command exited with code 0.'},
        ]
        progress = client.build_progress(events, "CASCADE_RUN_STATUS_IDLE", 4)
        self.assertEqual(progress["state"], "idle")
        self.assertFalse(progress["background"])

    def test_completion_message_keeps_the_id_separate_from_the_title(self):
        """系统消息的标题不含任务 id，完整 message 才带。"""
        events = [
            {"index": 0, "kind": "user", "text": "go"},
            _task_step(),
            _finished_reply(),
            {"index": 4, "kind": "system", "text": "Background sleep command finished",
             "message": '[Message] sender=cascade/task-7 content=Task id "cascade/task-7" '
                        'finished with result:\nThe command exited with code 0.'},
        ]
        self.assertEqual(client.open_tasks(events), [])
        progress = client.build_progress(events, "CASCADE_RUN_STATUS_IDLE", 4)
        self.assertFalse(progress["background"])

    def test_killed_task_is_closed_by_manage_task_result(self):
        events = [
            {"index": 0, "kind": "user", "text": "go"},
            _task_step(),
            {"index": 3, "kind": "tool", "text": "Killing task",
             "result": 'Task "cascade/task-7" cancelled.'},
            _finished_reply(),
        ]
        progress = client.build_progress(events, "CASCADE_RUN_STATUS_IDLE", 4)
        self.assertFalse(progress["background"])

    def test_status_running_probe_does_not_close_the_task(self):
        events = [
            {"index": 0, "kind": "user", "text": "go"},
            _task_step(),
            {"index": 3, "kind": "tool", "text": "Check task status",
             "result": 'Task: cascade/task-7\nStatus: RUNNING\nLog: ...'},
        ]
        self.assertEqual(len(client.open_tasks(events)), 1)

    def test_background_task_without_title(self):
        events = [{"index": 0, "kind": "user", "text": "go"},
                  _task_step(title=""), _finished_reply()]
        progress = client.build_progress(events, "CASCADE_RUN_STATUS_IDLE", 3)
        self.assertEqual(progress["currentAction"], "后台任务运行中…")


class CommandLineParsingTest(unittest.TestCase):
    MAC = ("/Applications/Antigravity.app/Contents/Resources/bin/language_server "
           "--csrf_token abc123 --app_data_dir \"/Users/me/Library/Application Support/Antigravity\"")
    WIN = ("\"C:\\Program Files\\Antigravity\\resources\\bin\\language_server.exe\" "
           "--csrf_token=xyz789 --override_ide_version 2.23.0")
    LINUX = ("/opt/antigravity/resources/bin/language_server --csrf_token tok_1 "
             "--override_ide_version=1.2.3")

    def test_macos(self):
        record = parse_language_server(42, self.MAC)
        self.assertEqual(record["csrf"], "abc123")
        self.assertTrue(record["appDataDir"].endswith("Application Support/Antigravity"))

    def test_windows(self):
        record = parse_language_server(7, self.WIN)
        self.assertEqual(record["csrf"], "xyz789")
        self.assertEqual(record["ideVersion"], "2.23.0")

    def test_linux(self):
        record = parse_language_server(9, self.LINUX)
        self.assertEqual(record["csrf"], "tok_1")
        self.assertEqual(record["ideVersion"], "1.2.3")

    def test_rejects_lookalikes(self):
        self.assertIsNone(parse_language_server(1, "python language_server.py --csrf_token x"))
        self.assertIsNone(parse_language_server(2, "grep --csrf_token language_server"))
        self.assertIsNone(parse_language_server(3, "/usr/bin/language_server --port 1"))


class ConfigTest(unittest.TestCase):
    def test_defaults_and_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"AGDECK_HOME": tmp}):
            cfg = config.load()
            self.assertEqual(cfg["port"], 8848)
            cfg["port"] = 9999
            cfg["unknown"] = "ignored"
            path = config.save(cfg)
            self.assertTrue(os.path.isfile(path))
            reloaded = config.load()
            self.assertEqual(reloaded["port"], 9999)
            self.assertNotIn("unknown", reloaded)
            if os.name != "nt":
                self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

    def test_env_override(self):
        with mock.patch.dict(os.environ, {"AGDECK_PORT": "7777"}):
            self.assertEqual(config.load()["port"], 7777)


class PlatformHelpersTest(unittest.TestCase):
    def test_dedupe_preserves_order(self):
        self.assertEqual(platform_util._dedupe([3, 1, 3, 2]), [3, 1, 2])

    def test_friendly_labels(self):
        self.assertEqual(platform_util._friendly("enp4s0"), "Ethernet")
        self.assertEqual(platform_util._friendly("wlp2s0"), "Wi-Fi")
        self.assertEqual(platform_util._friendly("tailscale0"), "Tailscale")

    def test_detect_urls_puts_tailscale_first_and_filters_loopback(self):
        with mock.patch.object(platform_util, "tailscale_state",
                               return_value={"ips": ["100.64.1.2"]}), \
                mock.patch.object(platform_util, "local_ipv4",
                                  return_value=[("enp4s0", "192.168.1.20"),
                                                ("lo", "127.0.1.1"),
                                                ("docker0", "172.17.0.1")]):
            urls = platform_util.detect_urls(8848)
        labels = [u["label"] for u in urls]
        # phones should see Tailscale first; loopback is only for the host
        self.assertEqual(labels[0], "Tailscale")
        self.assertEqual(labels[-1], "本机 / local")
        self.assertIn("Ethernet", labels)
        self.assertEqual(len(urls), 3)
        self.assertNotIn("172.17.0.1", " ".join(u["url"] for u in urls))


if __name__ == "__main__":
    unittest.main()


class PlatformCommandParsingTest(unittest.TestCase):
    """The OS-specific parsing is testable even on a different host."""

    def test_lsof_ports(self):
        out = (
            "language_ 7194 me 12u IPv4 0x1 0t0 TCP 127.0.0.1:35337 (LISTEN)\n"
            "language_ 7194 me 13u IPv6 0x2 0t0 TCP [::1]:40069 (LISTEN)\n"
            "language_ 7194 me 14u IPv4 0x3 0t0 TCP 127.0.0.1:51000->1.1.1.1:443 (ESTABLISHED)\n"
        )
        with mock.patch.object(platform_util, "run", return_value=out):
            self.assertEqual(platform_util._ports_lsof(7194), [35337, 40069])

    def test_netstat_ports(self):
        out = (
            "  TCP    127.0.0.1:35337    0.0.0.0:0    LISTENING    7194\n"
            "  TCP    0.0.0.0:8848       0.0.0.0:0    LISTENING    111\n"
            "  TCP    127.0.0.1:51000    127.0.0.1:35337  ESTABLISHED  7194\n"
            "  UDP    0.0.0.0:500        *:*            7194\n"
        )
        with mock.patch.object(platform_util, "run", return_value=out):
            self.assertEqual(platform_util._ports_netstat(7194), [35337])

    def test_ifconfig_macos(self):
        out = (
            "lo0: flags=8049<UP,LOOPBACK,RUNNING> mtu 16384\n"
            "\tinet 127.0.0.1 netmask 0xff000000\n"
            "en0: flags=8863<UP,BROADCAST> mtu 1500\n"
            "\tinet 192.168.1.5 netmask 0xffffff00 broadcast 192.168.1.255\n"
        )
        with mock.patch.object(platform_util, "run", return_value=out):
            self.assertEqual(platform_util._ipv4_macos(),
                             [("lo0", "127.0.0.1"), ("en0", "192.168.1.5")])

    def test_get_netipaddress_windows(self):
        out = "Wi-Fi\t192.168.1.5\nEthernet\t10.0.0.2\nLoopback Pseudo-Interface 1\t127.0.0.1\n"
        with mock.patch.object(platform_util, "run", return_value=out):
            self.assertEqual(platform_util._ipv4_windows(),
                             [("Wi-Fi", "192.168.1.5"), ("Ethernet", "10.0.0.2"),
                              ("Loopback Pseudo-Interface 1", "127.0.0.1")])

    def test_windows_process_list(self):
        out = "1234\tC:\\Program Files\\Antigravity\\resources\\bin\\language_server.exe --csrf_token abc\n"
        with mock.patch.object(platform_util, "run", return_value=out):
            self.assertEqual(list(platform_util._windows_processes()),
                             [(1234, "C:\\Program Files\\Antigravity\\resources\\bin\\language_server.exe --csrf_token abc")])

    def test_option_accepts_quoted_and_bare_values(self):
        from agdeck.rpc import option

        cmd = 'x --a="two words" --b plain --c'
        self.assertEqual(option(cmd, "a"), "two words")
        self.assertEqual(option(cmd, "b"), "plain")
        self.assertEqual(option(cmd, "missing"), "")
