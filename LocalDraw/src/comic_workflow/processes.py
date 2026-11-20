"""Own child-process trees on Windows, including Python venv redirector children."""

import os
import subprocess


def terminate_tree(process):
    if process is None or process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=20,
        )
    else:
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def attach_exit_job(process):
    """The kernel closes this job and its children if the owning studio crashes."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes as w

    class Basic(ctypes.Structure):
        _fields_ = [
            ("ProcessTime", ctypes.c_int64),
            ("JobTime", ctypes.c_int64),
            ("Flags", w.DWORD),
            ("MinWorkingSet", ctypes.c_size_t),
            ("MaxWorkingSet", ctypes.c_size_t),
            ("ActiveProcessLimit", w.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", w.DWORD),
            ("SchedulingClass", w.DWORD),
        ]

    class IO(ctypes.Structure):
        _fields_ = [
            (name, ctypes.c_uint64)
            for name in [
                "ReadOps",
                "WriteOps",
                "OtherOps",
                "ReadBytes",
                "WriteBytes",
                "OtherBytes",
            ]
        ]

    class Extended(ctypes.Structure):
        _fields_ = [
            ("Basic", Basic),
            ("IO", IO),
            ("ProcessMemory", ctypes.c_size_t),
            ("JobMemory", ctypes.c_size_t),
            ("PeakProcessMemory", ctypes.c_size_t),
            ("PeakJobMemory", ctypes.c_size_t),
        ]

    dll = ctypes.WinDLL("kernel32", use_last_error=True)
    dll.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
    dll.CreateJobObjectW.restype = w.HANDLE
    dll.SetInformationJobObject.argtypes = [
        w.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        w.DWORD,
    ]
    dll.SetInformationJobObject.restype = w.BOOL
    dll.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
    dll.AssignProcessToJobObject.restype = w.BOOL
    dll.CloseHandle.argtypes = [w.HANDLE]
    dll.CloseHandle.restype = w.BOOL
    handle = dll.CreateJobObjectW(None, None)
    info = Extended()
    info.Basic.Flags = 0x2000
    if (
        not handle
        or not dll.SetInformationJobObject(
            handle, 9, ctypes.byref(info), ctypes.sizeof(info)
        )
        or not dll.AssignProcessToJobObject(handle, w.HANDLE(int(process._handle)))
    ):
        if handle:
            dll.CloseHandle(handle)
        terminate_tree(process)
        raise OSError("无法建立 Windows 子进程管理，已终止本次启动，避免模型进程遗留")
    return (dll, handle)


def close_job(job):
    if job:
        job[0].CloseHandle(job[1])
