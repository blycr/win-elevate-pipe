import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest

from win_elevate_pipe.elevate import daemon_status, run_elevated, serve_daemon, stop_daemon
from win_elevate_pipe.proxy import (
    PROXY_TYPE_AUTO_PROXY_URL,
    PROXY_TYPE_DIRECT,
    PROXY_TYPE_PROXY,
    compute_flags,
)


class TestElevateDaemonProtocol(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.state_file = Path(self._tmp.name) / "daemon.json"
        self.ready = threading.Event()
        self.stop_evt = threading.Event()
        self.thread = threading.Thread(
            target=serve_daemon,
            kwargs={
                "state_file": self.state_file,
                "ready_event": self.ready,
                "stop_event": self.stop_evt,
            },
            daemon=True,
        )
        self.thread.start()
        self.assertTrue(self.ready.wait(timeout=3.0))

    def tearDown(self) -> None:
        stop_daemon(self.state_file)
        self.stop_evt.set()
        self.thread.join(timeout=3.0)
        self._tmp.cleanup()

    def test_ping_and_bad_token_rejected(self) -> None:
        st = daemon_status(self.state_file)
        self.assertIsNotNone(st)
        port, _token, _pid = st  # type: ignore[misc]
        with socket.create_connection(("127.0.0.1", port), timeout=2.0) as s:
            s.sendall((json.dumps({"token": "wrong", "action": "ping"}) + "\n").encode("utf-8"))
            resp = s.recv(64).decode("utf-8").strip()
            self.assertEqual(resp, "ERR:AUTH")

    def test_streaming_exec_and_exit_code(self) -> None:
        lines = []
        rc = run_elevated(
            [sys.executable, "-c", "print('hello'); print('world'); raise SystemExit(7)"],
            on_line=lines.append,
            state_file=self.state_file,
            force_daemon=True,
        )
        self.assertEqual(rc, 7)
        self.assertEqual("".join(lines).splitlines(), ["hello", "world"])

    def test_stop_daemon_removes_state_file(self) -> None:
        self.assertTrue(stop_daemon(self.state_file))
        self.thread.join(timeout=3.0)
        self.assertFalse(self.state_file.exists())
        self.assertIsNone(daemon_status(self.state_file))


class TestProxyFlags(unittest.TestCase):
    def test_compute_flags(self) -> None:
        self.assertEqual(compute_flags(None, None), PROXY_TYPE_DIRECT)
        self.assertEqual(compute_flags("127.0.0.1:8080", None), PROXY_TYPE_DIRECT | PROXY_TYPE_PROXY)
        self.assertEqual(compute_flags(None, "http://127.0.0.1:8082/proxy.pac"), PROXY_TYPE_DIRECT | PROXY_TYPE_AUTO_PROXY_URL)
        self.assertEqual(
            compute_flags("127.0.0.1:8080", "http://127.0.0.1:8082/proxy.pac"),
            PROXY_TYPE_DIRECT | PROXY_TYPE_PROXY | PROXY_TYPE_AUTO_PROXY_URL,
        )


if __name__ == "__main__":
    unittest.main()
