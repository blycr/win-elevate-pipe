"""Zero-dependency Windows inline UAC elevation & atomic WinINet proxy toolkit."""

from .elevate import (
    daemon_status,
    ensure_daemon,
    is_admin,
    run_elevated,
    serve_daemon,
    stop_daemon,
)
from .proxy import (
    PROXY_TYPE_AUTO_PROXY_URL,
    PROXY_TYPE_DIRECT,
    PROXY_TYPE_PROXY,
    SystemProxy,
    apply_proxy,
    get_proxy_flags,
)

__all__ = [
    "is_admin",
    "daemon_status",
    "ensure_daemon",
    "serve_daemon",
    "run_elevated",
    "stop_daemon",
    "SystemProxy",
    "get_proxy_flags",
    "apply_proxy",
    "PROXY_TYPE_DIRECT",
    "PROXY_TYPE_PROXY",
    "PROXY_TYPE_AUTO_PROXY_URL",
]
