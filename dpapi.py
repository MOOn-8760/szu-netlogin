"""Windows DPAPI 加解密（无第三方依赖）。

为什么需要它：开机自启的后台任务没有控制台，没法弹窗让人输密码，所以密码
必须以某种形式落盘。用 DPAPI（``CryptProtectData``）加密后，密文只能被
**同一台电脑上的同一个 Windows 账户** 解开 —— 就算配置文件被拷走、被同步到
网盘、被误传到 GitHub，别人也还原不出明文。

非 Windows 平台没有等价的免依赖系统接口，退化为明文 + 0600 权限，并明确警告。
"""

from __future__ import annotations

import base64
import ctypes
import os
from ctypes import wintypes

PREFIX = "dpapi:"


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def available() -> bool:
    return os.name == "nt"


if available():  # pragma: no cover - 平台相关
    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DATA_BLOB),
    ]
    _crypt32.CryptProtectData.restype = wintypes.BOOL

    _crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DATA_BLOB),
    ]
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL


def _blob(data: bytes):
    size = max(len(data), 1)
    buf = ctypes.create_string_buffer(data, size)
    return _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _dpapi(data: bytes, decrypt: bool) -> bytes:
    if not available():  # pragma: no cover
        raise OSError("DPAPI 仅在 Windows 上可用")

    blob_in, _keep = _blob(data)
    blob_out = _DATA_BLOB()
    fn = _crypt32.CryptUnprotectData if decrypt else _crypt32.CryptProtectData
    if not fn(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        _kernel32.LocalFree(blob_out.pbData)


def protect(plain: str) -> str:
    """加密；环境不支持时原样返回（调用方负责提示风险）。"""
    if not plain:
        return ""
    if plain.startswith(PREFIX):
        return plain
    if not available():
        return plain
    try:
        return PREFIX + base64.b64encode(_dpapi(plain.encode("utf-8"), False)).decode("ascii")
    except OSError:
        return plain


def unprotect(value: str) -> str:
    """解密；遇到明文（旧数据或其它平台写入）原样返回。"""
    if not value:
        return ""
    if not value.startswith(PREFIX):
        return value
    if not available():  # pragma: no cover
        raise RuntimeError("该密码由 Windows DPAPI 加密，只能在加密它的那台电脑上解密")
    return _dpapi(base64.b64decode(value[len(PREFIX):]), True).decode("utf-8")
