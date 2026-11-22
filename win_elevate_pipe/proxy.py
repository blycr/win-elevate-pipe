"""Atomic Windows system proxy (manual + PAC) control via WinINet API.

Writing HKCU\\...\\Internet Settings alone does not reliably take effect in running
WinINet/Chromium/Unity apps until INTERNET_OPTION_PER_CONNECTION_OPTION (75) +
INTERNET_OPTION_SETTINGS_CHANGED (39) + INTERNET_OPTION_REFRESH (37) are committed.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from typing import Optional

if os.name == "nt":
    import winreg

IE_KEY = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"

PROXY_TYPE_DIRECT = 0x00000001
PROXY_TYPE_PROXY = 0x00000002
PROXY_TYPE_AUTO_PROXY_URL = 0x00000004
PROXY_TYPE_AUTO_DETECT = 0x00000008

INTERNET_PER_CONN_FLAGS = 1
INTERNET_PER_CONN_PROXY_SERVER = 2
INTERNET_PER_CONN_PROXY_BYPASS = 3
INTERNET_PER_CONN_AUTOCONFIG_URL = 4
INTERNET_PER_CONN_FLAGS_UI = 10

INTERNET_OPTION_REFRESH = 37
INTERNET_OPTION_SETTINGS_CHANGED = 39
INTERNET_OPTION_PER_CONNECTION_OPTION = 75


class _ConnectionValue(ctypes.Union):
    _fields_ = [
        ("number", wintypes.DWORD),
        ("string", wintypes.LPWSTR),
        ("time", wintypes.FILETIME),
    ]


class _ConnectionOption(ctypes.Structure):
    _fields_ = [
        ("option", wintypes.DWORD),
        ("value", _ConnectionValue),
    ]


class _ConnectionOptions(ctypes.Structure):
    _fields_ = [
        ("size", wintypes.DWORD),
        ("connection", wintypes.LPWSTR),
        ("count", wintypes.DWORD),
        ("error", wintypes.DWORD),
        ("options", ctypes.POINTER(_ConnectionOption)),
    ]


def _wininet():
    api = ctypes.WinDLL("wininet", use_last_error=True)
    api.InternetSetOptionW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    api.InternetSetOptionW.restype = wintypes.BOOL
    api.InternetQueryOptionW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.DWORD),
    ]
    api.InternetQueryOptionW.restype = wintypes.BOOL
    return api


def _reg_get(name: str, default=None):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, IE_KEY) as k:
            val, _ = winreg.QueryValueEx(k, name)
            return val
    except OSError:
        return default


def _reg_set(name: str, value, kind: int) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, IE_KEY, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, name, 0, kind, value)


def _reg_delete(name: str) -> None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, IE_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, name)
    except OSError:
        pass


def compute_flags(proxy_server: Optional[str], pac_url: Optional[str]) -> int:
    """Compute INTERNET_PER_CONN_FLAGS bitmask from desired manual proxy and PAC URL."""
    flags = PROXY_TYPE_DIRECT
    if proxy_server:
        flags |= PROXY_TYPE_PROXY
    if pac_url:
        flags |= PROXY_TYPE_AUTO_PROXY_URL
    return flags


def get_proxy_flags() -> int:
    """Query the active WinINet per-connection proxy flags."""
    api = _wininet()
    options = (_ConnectionOption * 1)()
    settings = _ConnectionOptions(ctypes.sizeof(_ConnectionOptions), None, 1, 0, options)
    for opt in (INTERNET_PER_CONN_FLAGS_UI, INTERNET_PER_CONN_FLAGS):
        options[0].option = opt
        size = wintypes.DWORD(ctypes.sizeof(settings))
        if api.InternetQueryOptionW(None, INTERNET_OPTION_PER_CONNECTION_OPTION, ctypes.byref(settings), ctypes.byref(size)):
            return int(options[0].value.number)
    raise ctypes.WinError(ctypes.get_last_error())


def apply_proxy(
    proxy_server: Optional[str] = None,
    pac_url: Optional[str] = None,
    bypass: Optional[str] = None,
    flags: Optional[int] = None,
) -> None:
    """Commit proxy and/or PAC configuration atomically via WinINet."""
    eff_flags = compute_flags(proxy_server, pac_url) if flags is None else flags
    options = (_ConnectionOption * 4)()
    options[0].option = INTERNET_PER_CONN_FLAGS
    options[0].value.number = eff_flags
    options[1].option = INTERNET_PER_CONN_PROXY_SERVER
    options[1].value.string = proxy_server or None
    options[2].option = INTERNET_PER_CONN_PROXY_BYPASS
    options[2].value.string = bypass or None
    options[3].option = INTERNET_PER_CONN_AUTOCONFIG_URL
    options[3].value.string = (pac_url or None) if (eff_flags & PROXY_TYPE_AUTO_PROXY_URL) else None

    settings = _ConnectionOptions(ctypes.sizeof(_ConnectionOptions), None, 4, 0, options)
    api = _wininet()
    if not api.InternetSetOptionW(None, INTERNET_OPTION_PER_CONNECTION_OPTION, ctypes.byref(settings), ctypes.sizeof(settings)):
        raise ctypes.WinError(ctypes.get_last_error())
    for opt in (INTERNET_OPTION_SETTINGS_CHANGED, INTERNET_OPTION_REFRESH):
        if not api.InternetSetOptionW(None, opt, None, 0):
            raise ctypes.WinError(ctypes.get_last_error())


class SystemProxy:
    """Context manager that applies a temporary WinINet proxy/PAC and restores on exit."""

    def __init__(
        self,
        proxy_server: Optional[str] = None,
        pac_url: Optional[str] = None,
        bypass: str = "<local>",
    ) -> None:
        self.proxy_server = proxy_server
        self.pac_url = pac_url
        self.bypass = bypass
        self._saved: Optional[dict] = None

    def __enter__(self) -> "SystemProxy":
        self._saved = {
            "flags": get_proxy_flags(),
            "ProxyEnable": _reg_get("ProxyEnable"),
            "ProxyServer": _reg_get("ProxyServer"),
            "ProxyOverride": _reg_get("ProxyOverride"),
            "AutoConfigURL": _reg_get("AutoConfigURL"),
        }
        apply_proxy(self.proxy_server, self.pac_url, self.bypass)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if not self._saved:
            return
        saved = self._saved
        self._saved = None
        for key, kind in (
            ("ProxyEnable", winreg.REG_DWORD),
            ("ProxyServer", winreg.REG_SZ),
            ("ProxyOverride", winreg.REG_SZ),
            ("AutoConfigURL", winreg.REG_SZ),
        ):
            val = saved.get(key)
            if val is None:
                _reg_delete(key)
            else:
                _reg_set(key, val, kind)
        apply_proxy(
            proxy_server=saved.get("ProxyServer"),
            pac_url=saved.get("AutoConfigURL"),
            bypass=saved.get("ProxyOverride"),
            flags=saved["flags"],
        )
