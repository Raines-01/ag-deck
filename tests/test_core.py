"""Unit tests for the parts that do not need a running Antigravity."""

from __future__ import annotations

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


class ProgressTest(unittest.TestCase):
    def test_finished_reply_wins_over_stale_running_status(self):
        events = [
            {"index": 0, "kind": "user", "text": "go"},
            {"index": 1, "kind": "assistant", "status": "DONE", "text": "all set", "toolCalls": []},
        ]
        progress = client.build_progress(events, "CASCADE_RUN_STATUS_RUNNING", 2)
        self.assertEqual(progress["state"], "idle")
        self.assertEqual(progress["turnSteps"], 1)

    def test_working_when_tool_call_pending(self):
        events = [
            {"index": 0, "kind": "user", "text": "go"},
            {"index": 1, "kind": "assistant", "status": "GENERATING", "text": "", "toolCalls": [{"name": "x"}]},
        ]
        progress = client.build_progress(events, "CASCADE_RUN_STATUS_RUNNING", 2)
        self.assertEqual(progress["state"], "working")
        self.assertEqual(progress["currentAction"], "思考中…")

    def test_waiting_on_question(self):
        events = [{"index": 0, "kind": "question", "text": "continue?"}]
        progress = client.build_progress(events, "CASCADE_RUN_STATUS_IDLE", 1)
        self.assertEqual(progress["state"], "waiting")


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
