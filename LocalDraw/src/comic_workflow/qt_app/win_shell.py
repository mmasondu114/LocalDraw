"""Windows native taskbar identity (AppUserModelID, Relaunch properties & HICON)."""

from __future__ import annotations

import ctypes
import sys
from ctypes import POINTER, Structure, byref, c_long, c_ubyte, c_ulong, c_ushort, c_void_p, c_wchar_p
from pathlib import Path

APP_USER_MODEL_ID = "LocalDraw.Studio.Desktop.2"
_KEEP_HICONS: list[int] = []


class _GUID(Structure):
    _fields_ = [
        ("Data1", c_ulong),
        ("Data2", c_ushort),
        ("Data3", c_ushort),
        ("Data4", c_ubyte * 8),
    ]

    @classmethod
    def from_str(cls, text: str) -> "_GUID":
        s = text.strip("{}").replace("-", "")
        d1 = int(s[0:8], 16)
        d2 = int(s[8:12], 16)
        d3 = int(s[12:16], 16)
        d4 = (c_ubyte * 8)(*[int(s[16 + i * 2 : 18 + i * 2], 16) for i in range(8)])
        return cls(d1, d2, d3, d4)


class _PROPERTYKEY(Structure):
    _fields_ = [("fmtid", _GUID), ("pid", c_ulong)]


class _PROPVARIANT(Structure):
    _fields_ = [
        ("vt", c_ushort),
        ("wReserved1", c_ushort),
        ("wReserved2", c_ushort),
        ("wReserved3", c_ushort),
        ("pwszVal", c_wchar_p),
        ("_pad", c_void_p),
    ]


_IID_IPROPERTYSTORE = _GUID.from_str("{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}")
_FMTID_APPUSERMODEL = _GUID.from_str("{9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}")
_PKEY_RELAUNCH_CMD = _PROPERTYKEY(_FMTID_APPUSERMODEL, 2)
_PKEY_RELAUNCH_ICON = _PROPERTYKEY(_FMTID_APPUSERMODEL, 3)
_PKEY_RELAUNCH_NAME = _PROPERTYKEY(_FMTID_APPUSERMODEL, 4)
_PKEY_AUMID = _PROPERTYKEY(_FMTID_APPUSERMODEL, 5)
_VT_LPWSTR = 31


def set_process_app_id(app_id: str = APP_USER_MODEL_ID) -> bool:
    """Bind process AppUserModelID BEFORE QApplication() so taskbar never groups under IDLE."""
    if sys.platform != "win32":
        return False
    try:
        fn = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
        fn.argtypes = [c_wchar_p]
        fn.restype = c_long
        return fn(app_id) == 0
    except Exception:
        return False


def apply_window_identity(
    hwnd: int,
    exe_path: Path,
    ico_path: Path | None = None,
    *,
    display_name: str = "LocalDraw",
    app_id: str = APP_USER_MODEL_ID,
) -> dict[str, bool]:
    """Attach LocalDraw's AppUserModelID, relaunch command/name/icon, and Win32 HICON to HWND."""
    result = {"property_store": False, "hicon": False}
    if sys.platform != "win32" or not hwnd:
        return result

    # 1. Set per-window IPropertyStore taskbar properties (overrides pythonw.exe / IDLE identity)
    try:
        sh_get = ctypes.windll.shell32.SHGetPropertyStoreForWindow
        sh_get.argtypes = [c_void_p, POINTER(_GUID), POINTER(c_void_p)]
        sh_get.restype = c_long
        store_ptr = c_void_p()
        hr = sh_get(c_void_p(int(hwnd)), byref(_IID_IPROPERTYSTORE), byref(store_ptr))
        if hr == 0 and store_ptr.value:
            vtable = ctypes.cast(store_ptr, POINTER(POINTER(c_void_p))).contents
            fn_release = ctypes.WINFUNCTYPE(c_ulong, c_void_p)(vtable[2])
            fn_set_value = ctypes.WINFUNCTYPE(
                c_long, c_void_p, POINTER(_PROPERTYKEY), POINTER(_PROPVARIANT)
            )(vtable[6])
            fn_commit = ctypes.WINFUNCTYPE(c_long, c_void_p)(vtable[7])
            try:
                exe_str = str(exe_path.resolve())
                # Prefer direct .ico file path so Windows Jump List never reuses stale EXE icon cache
                icon_res = (
                    f"{ico_path.resolve()},0"
                    if (ico_path and ico_path.is_file())
                    else (f"{exe_str},0" if exe_path.is_file() else "")
                )
                pairs = [
                    (_PKEY_AUMID, app_id),
                    (_PKEY_RELAUNCH_NAME, display_name),
                    (_PKEY_RELAUNCH_CMD, f'"{exe_str}"'),
                ]
                if icon_res:
                    pairs.append((_PKEY_RELAUNCH_ICON, icon_res))
                ok_all = True
                for key, val in pairs:
                    pv = _PROPVARIANT()
                    pv.vt = _VT_LPWSTR
                    pv.pwszVal = val
                    if fn_set_value(store_ptr, byref(key), byref(pv)) != 0:
                        ok_all = False
                if fn_commit(store_ptr) != 0:
                    ok_all = False
                result["property_store"] = ok_all
            finally:
                fn_release(store_ptr)
    except Exception:
        result["property_store"] = False

    # 2. Attach crisp native HICON (small + large) directly to HWND
    try:
        if ico_path and ico_path.is_file():
            user32 = ctypes.windll.user32
            load_image = user32.LoadImageW
            load_image.argtypes = [c_void_p, c_wchar_p, c_ulong, c_long, c_long, c_ulong]
            load_image.restype = c_void_p
            send_msg = user32.SendMessageW
            send_msg.argtypes = [c_void_p, c_ulong, c_void_p, c_void_p]
            send_msg.restype = c_void_p

            IMAGE_ICON = 1
            LR_LOADFROMFILE = 0x0010
            WM_SETICON = 0x0080
            ICON_SMALL = 0
            ICON_BIG = 1

            h_small = load_image(None, str(ico_path), IMAGE_ICON, 24, 24, LR_LOADFROMFILE)
            h_big = load_image(None, str(ico_path), IMAGE_ICON, 64, 64, LR_LOADFROMFILE)
            if h_small:
                _KEEP_HICONS.append(int(h_small))
                send_msg(c_void_p(int(hwnd)), WM_SETICON, c_void_p(ICON_SMALL), c_void_p(h_small))
            if h_big:
                _KEEP_HICONS.append(int(h_big))
                send_msg(c_void_p(int(hwnd)), WM_SETICON, c_void_p(ICON_BIG), c_void_p(h_big))
            result["hicon"] = bool(h_small or h_big)
    except Exception:
        result["hicon"] = False

    return result


def notify_shell_icon_change() -> None:
    """Tell Windows Explorer to flush cached icon thumbnails for updated EXE/LNK files."""
    if sys.platform != "win32":
        return
    try:
        fn = ctypes.windll.shell32.SHChangeNotify
        fn.argtypes = [c_long, c_ulong, c_void_p, c_void_p]
        fn(0x08000000, 0x0000, None, None)  # SHCNE_ASSOCCHANGED, SHCNF_IDLIST
    except Exception:
        pass
