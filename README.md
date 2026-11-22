# win-elevate-pipe

Zero-dependency Python utilities for Windows CLI workflows:

1. **Inline UAC elevation (`elevate.py`)**: Run Administrator commands from a normal terminal without spawning a separate UAC console window. Spawns a single hidden elevated daemon via `ShellExecuteExW("runas", SW_HIDE)`, authenticates requests over `127.0.0.1` with a per-session random token, streams `stdout`/`stderr` line-by-line back to the current terminal, and propagates `Ctrl+C` (`taskkill /T /F`).
2. **Atomic WinINet system proxy (`proxy.py`)**: Apply manual proxy and/or PAC URL via `InternetSetOptionW` (`INTERNET_OPTION_PER_CONNECTION_OPTION` + `SETTINGS_CHANGED` + `REFRESH`) and automatically restore the previous settings on exit.

Requires Python 3.8+ on Windows. No third-party packages.

## Quick start

```powershell
# Install directly from the release tag:
pip install git+https://github.com/blycr/win-elevate-pipe.git@v0.1.0

# Or install locally in editable mode:
pip install -e .
```

### CLI

```powershell
# Prompt UAC once (hidden daemon, 0 popup windows), stream output inline:
win-elevate run -- net session

# Check or stop the background elevated daemon:
win-elevate status
win-elevate stop
```

### Python API

```python
from win_elevate_pipe import SystemProxy, run_elevated, stop_daemon

rc = run_elevated(["sc", "query", "wuauserv"])
stop_daemon()

# Temporarily enable both manual proxy and PAC, restoring previous state on exit:
with SystemProxy(proxy_server="127.0.0.1:8080", pac_url="http://127.0.0.1:8082/proxy.pac"):
    ...
```

## Testing

```powershell
python -m unittest discover -s tests -v
```

## License

MIT
