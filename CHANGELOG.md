# Changelog

All notable changes to `win-elevate-pipe` are documented in this file.

---

## [v0.1.0] - 2026-11-22

### Overview
Initial public release of `win-elevate-pipe`: a zero-dependency Python toolkit for running elevated Windows commands inline without spawning a UAC popup console and managing WinINet system proxy settings atomically.

### Added
- **Inline UAC elevation (`win_elevate_pipe.elevate`)**: Spawns a single hidden elevated daemon (`ShellExecuteExW("runas", SW_HIDE)`) bound to `127.0.0.1` with a per-session random bearer token, streams `stdout`/`stderr` line-by-line back to the caller terminal, and propagates `Ctrl+C` (`CANCEL`) via `taskkill /T /F`.
- **Atomic WinINet system proxy (`win_elevate_pipe.proxy`)**: `SystemProxy` context manager, `apply_proxy()`, and `get_proxy_flags()` using `InternetSetOptionW` (`INTERNET_OPTION_PER_CONNECTION_OPTION`, `SETTINGS_CHANGED`, `REFRESH`) with automatic rollback on exit.
- **CLI entrypoint (`win-elevate`)**: Supports `run -- <cmd...>`, `status`, and `stop`.
- **Unit tests (`tests/test_protocol.py`)**: Covers token authentication, streaming output/exit codes, clean daemon shutdown, and proxy flag computation.

### Fixed
- Deterministically close child process `stdout` pipes using `with subprocess.Popen(...)` to eliminate `ResourceWarning` under strict warning checks.
