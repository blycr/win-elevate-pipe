"""Inline Windows UAC elevation without spawning a separate terminal window.

Spawns a single hidden elevated Python daemon via ShellExecuteExW("runas", SW_HIDE)
listening on an ephemeral 127.0.0.1 TCP port with a per-session random bearer token.
Elevated commands stream stdout/stderr line-by-line back to the caller's terminal,
and Ctrl+C cancels the remote process tree cleanly.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
from typing import Callable, Optional, Sequence, TextIO, Tuple

CREATE_NO_WINDOW = 0x08000000
SW_HIDE = 0
SEE_MASK_NOCLOSEPROCESS = 0x00000040


def default_state_file() -> Path:
    return Path(tempfile.gettempdir()) / "win_elevate_pipe_daemon.json"


def is_admin() -> bool:
    """Return True if the current process already holds Administrator privileges."""
    if os.name != "nt":
        return os.geteuid() == 0  # type: ignore[attr-defined]
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


class _SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("fMask", wintypes.ULONG),
        ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR),
        ("lpFile", wintypes.LPCWSTR),
        ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR),
        ("nShow", ctypes.c_int),
        ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", wintypes.LPVOID),
        ("lpClass", wintypes.LPCWSTR),
        ("hkeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD),
        ("hIconOrMonitor", wintypes.HANDLE),
        ("hProcess", wintypes.HANDLE),
    ]


def _kill_tree(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            creationflags=CREATE_NO_WINDOW,
        )
    else:
        try:
            os.kill(pid, 9)
        except OSError:
            pass


def daemon_status(state_file: Optional[Path] = None) -> Optional[Tuple[int, str, int]]:
    """Return (port, token, pid) if the elevated daemon responds to ping, else None."""
    sf = Path(state_file) if state_file else default_state_file()
    if not sf.exists():
        return None
    try:
        data = json.loads(sf.read_text(encoding="utf-8"))
        port = int(data["port"])
        token = str(data["token"])
        pid = int(data["pid"])
        with socket.create_connection(("127.0.0.1", port), timeout=1.0) as s:
            req = json.dumps({"token": token, "action": "ping"}) + "\n"
            s.sendall(req.encode("utf-8"))
            resp = s.recv(1024).decode("utf-8", errors="replace").strip()
            if resp == "PONG":
                return (port, token, pid)
    except Exception:
        pass
    return None


def serve_daemon(
    state_file: Optional[Path] = None,
    ready_event: Optional[threading.Event] = None,
    stop_event: Optional[threading.Event] = None,
) -> int:
    """Run the loopback daemon loop (normally invoked inside an elevated hidden process)."""
    sf = Path(state_file) if state_file else default_state_file()
    sf.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(16)

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.listen(8)

    state = {"port": port, "token": token, "pid": os.getpid(), "started_at": time.time()}
    sf.write_text(json.dumps(state, indent=2), encoding="utf-8")

    local_stop = stop_event or threading.Event()
    if ready_event is not None:
        ready_event.set()

    def handle_client(conn: socket.socket) -> None:
        try:
            with conn.makefile("r", encoding="utf-8", errors="replace") as f:
                line = f.readline()
            if not line:
                return
            req = json.loads(line)
            if not secrets.compare_digest(str(req.get("token", "")), token):
                conn.sendall(b"ERR:AUTH\n")
                return

            action = req.get("action")
            if action == "ping":
                conn.sendall(b"PONG\n")
                return
            if action == "stop":
                conn.sendall(b"OK\n")
                local_stop.set()
                return
            if action == "exec":
                cmd = [str(x) for x in req.get("cmd", [])]
                if not cmd:
                    conn.sendall((json.dumps({"type": "exit", "code": 2}) + "\n").encode("utf-8"))
                    return
                cwd = req.get("cwd") or os.getcwd()
                kwargs = {
                    "cwd": cwd,
                    "stdin": subprocess.DEVNULL,
                    "stdout": subprocess.PIPE,
                    "stderr": subprocess.STDOUT,
                    "text": True,
                    "encoding": "utf-8",
                    "errors": "replace",
                    "bufsize": 1,
                }
                if os.name == "nt":
                    kwargs["creationflags"] = CREATE_NO_WINDOW
                proc = subprocess.Popen(cmd, **kwargs)

                def watch_cancel() -> None:
                    while proc.poll() is None:
                        try:
                            conn.setblocking(False)
                            data = conn.recv(16)
                            if data == b"" or b"CANCEL" in data:
                                _kill_tree(proc.pid)
                                break
                        except BlockingIOError:
                            pass
                        except Exception:
                            _kill_tree(proc.pid)
                            break
                        finally:
                            try:
                                conn.setblocking(True)
                            except Exception:
                                pass
                        time.sleep(0.1)

                threading.Thread(target=watch_cancel, daemon=True).start()

                assert proc.stdout is not None
                for out_line in proc.stdout:
                    conn.sendall((json.dumps({"type": "out", "data": out_line}) + "\n").encode("utf-8"))
                rc = proc.wait()
                conn.sendall((json.dumps({"type": "exit", "code": rc}) + "\n").encode("utf-8"))
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    srv.settimeout(0.5)
    try:
        while not local_stop.is_set():
            try:
                conn, _ = srv.accept()
                threading.Thread(target=handle_client, args=(conn,), daemon=True).start()
            except socket.timeout:
                continue
    finally:
        try:
            srv.close()
        except Exception:
            pass
        try:
            sf.unlink(missing_ok=True)
        except Exception:
            pass
    return 0


def ensure_daemon(
    state_file: Optional[Path] = None,
    timeout: float = 15.0,
    python_exe: Optional[str] = None,
) -> Optional[Tuple[int, str, int]]:
    """Ensure a hidden elevated daemon is running; prompt UAC once if needed."""
    sf = Path(state_file) if state_file else default_state_file()
    alive = daemon_status(sf)
    if alive:
        return alive

    if os.name != "nt":
        raise RuntimeError("UAC elevation is only supported on Windows.")

    sf.parent.mkdir(parents=True, exist_ok=True)
    sf.unlink(missing_ok=True)

    exe = python_exe or sys.executable
    params = f'-m win_elevate_pipe _daemon --state-file "{sf}"'

    sei = _SHELLEXECUTEINFOW()
    sei.cbSize = ctypes.sizeof(_SHELLEXECUTEINFOW)
    sei.fMask = SEE_MASK_NOCLOSEPROCESS
    sei.hwnd = None
    sei.lpVerb = "runas"
    sei.lpFile = exe
    sei.lpParameters = params
    sei.lpDirectory = os.getcwd()
    sei.nShow = SW_HIDE

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(_SHELLEXECUTEINFOW)]
    shell32.ShellExecuteExW.restype = wintypes.BOOL

    if not shell32.ShellExecuteExW(ctypes.byref(sei)):
        return None
    if sei.hProcess:
        ctypes.windll.kernel32.CloseHandle(sei.hProcess)

    deadline = time.time() + timeout
    while time.time() < deadline:
        alive = daemon_status(sf)
        if alive:
            return alive
        time.sleep(0.15)
    return None


def stop_daemon(state_file: Optional[Path] = None) -> bool:
    """Gracefully stop the running elevated daemon. Returns True if one was stopped."""
    sf = Path(state_file) if state_file else default_state_file()
    alive = daemon_status(sf)
    if not alive:
        sf.unlink(missing_ok=True)
        return False
    port, token, _ = alive
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2.0) as s:
            s.sendall((json.dumps({"token": token, "action": "stop"}) + "\n").encode("utf-8"))
            s.recv(64)
    except Exception:
        pass
    sf.unlink(missing_ok=True)
    return True


def run_elevated(
    cmd: Sequence[str],
    cwd: Optional[str | Path] = None,
    stream: Optional[TextIO] = None,
    on_line: Optional[Callable[[str], None]] = None,
    state_file: Optional[Path] = None,
    force_daemon: bool = False,
) -> int:
    """Execute cmd with Administrator privileges and stream output inline.

    If the current process is already elevated (and force_daemon is False),
    runs cmd directly without spawning a daemon.
    """
    cmd_list = [str(x) for x in cmd]
    work_dir = str(cwd) if cwd is not None else os.getcwd()
    out_stream = stream if stream is not None else sys.stdout

    def emit(chunk: str) -> None:
        if on_line is not None:
            on_line(chunk)
        else:
            out_stream.write(chunk)
            out_stream.flush()

    if is_admin() and not force_daemon:
        proc: Optional[subprocess.Popen[str]] = None
        try:
            proc = subprocess.Popen(
                cmd_list,
                cwd=work_dir,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                emit(line)
            return proc.wait()
        except KeyboardInterrupt:
            if proc is not None:
                _kill_tree(proc.pid)
            return 130

    alive = ensure_daemon(state_file=state_file)
    if not alive:
        return 1
    port, token, _ = alive

    with socket.create_connection(("127.0.0.1", port), timeout=5.0) as s:
        req = json.dumps({"token": token, "action": "exec", "cmd": cmd_list, "cwd": work_dir}) + "\n"
        s.sendall(req.encode("utf-8"))
        s.settimeout(None)
        with s.makefile("r", encoding="utf-8", errors="replace") as f:
            try:
                for line in f:
                    if not line.strip():
                        continue
                    msg = json.loads(line)
                    if msg.get("type") == "out":
                        emit(msg.get("data", ""))
                    elif msg.get("type") == "exit":
                        return int(msg.get("code", 0))
            except KeyboardInterrupt:
                try:
                    s.sendall(b"CANCEL\n")
                except Exception:
                    pass
                return 130
    return 1
