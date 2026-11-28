"""Own child-process trees on Windows, including Python venv redirector children."""

import os
import subprocess
import time


def is_pid_running(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes as w

        dll = ctypes.WinDLL("kernel32", use_last_error=True)
        dll.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        dll.OpenProcess.restype = w.HANDLE
        dll.GetExitCodeProcess.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]
        dll.GetExitCodeProcess.restype = w.BOOL
        dll.CloseHandle.argtypes = [w.HANDLE]
        dll.CloseHandle.restype = w.BOOL

        handle = dll.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = w.DWORD()
            return bool(dll.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            dll.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def find_listening_pid(port):
    """Return the PID currently listening on local IPv4 TCP `port`, or None."""
    try:
        target_port = int(port)
    except (TypeError, ValueError):
        return None
    if target_port <= 0 or target_port > 65535:
        return None
    if os.name == "nt":
        import ctypes
        import socket
        from ctypes import wintypes as w

        class _TcpRowOwnerPid(ctypes.Structure):
            _fields_ = [
                ("dwState", w.DWORD),
                ("dwLocalAddr", w.DWORD),
                ("dwLocalPort", w.DWORD),
                ("dwRemoteAddr", w.DWORD),
                ("dwRemotePort", w.DWORD),
                ("dwOwningPid", w.DWORD),
            ]

        iphlpapi = ctypes.WinDLL("iphlpapi", use_last_error=True)
        iphlpapi.GetExtendedTcpTable.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(w.DWORD),
            w.BOOL,
            w.ULONG,
            ctypes.c_int,
            w.ULONG,
        ]
        iphlpapi.GetExtendedTcpTable.restype = w.DWORD

        af_inet = 2
        tcp_table_owner_pid_listener = 3
        size = w.DWORD(0)
        iphlpapi.GetExtendedTcpTable(None, ctypes.byref(size), False, af_inet, tcp_table_owner_pid_listener, 0)
        if size.value < 4:
            return None
        buf = (ctypes.c_ubyte * size.value)()
        rc = iphlpapi.GetExtendedTcpTable(buf, ctypes.byref(size), False, af_inet, tcp_table_owner_pid_listener, 0)
        if rc != 0:
            return None
        count = w.DWORD.from_buffer(buf, 0).value
        row_size = ctypes.sizeof(_TcpRowOwnerPid)
        offset = ctypes.sizeof(w.DWORD)
        fallback_pid = None
        for i in range(count):
            pos = offset + i * row_size
            if pos + row_size > size.value:
                break
            row = _TcpRowOwnerPid.from_buffer(buf, pos)
            if socket.ntohs(row.dwLocalPort & 0xFFFF) != target_port:
                continue
            pid = int(row.dwOwningPid)
            if pid <= 0:
                continue
            # Prefer explicit 127.0.0.1 listener over 0.0.0.0 wildcard if both exist.
            if row.dwLocalAddr == 0x0100007F:
                return pid
            if fallback_pid is None:
                fallback_pid = pid
        return fallback_pid
    return None


class AttachedProcess:
    """Lightweight handle for a detached ComfyUI process inherited across LocalDraw restarts."""

    def __init__(self, pid, preloaded_checkpoint=None):
        self.pid = int(pid)
        self.preloaded_checkpoint = preloaded_checkpoint

    def poll(self):
        return None if is_pid_running(self.pid) else 0

    def wait(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + float(timeout)
        while self.poll() is None:
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(cmd=f"pid:{self.pid}", timeout=timeout)
            time.sleep(0.05)
        return 0

    def kill(self):
        if self.poll() is not None:
            return
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes as w

            dll = ctypes.WinDLL("kernel32", use_last_error=True)
            dll.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
            dll.OpenProcess.restype = w.HANDLE
            dll.TerminateProcess.argtypes = [w.HANDLE, w.UINT]
            dll.TerminateProcess.restype = w.BOOL
            dll.CloseHandle.argtypes = [w.HANDLE]
            dll.CloseHandle.restype = w.BOOL

            handle = dll.OpenProcess(0x0001, False, self.pid)  # PROCESS_TERMINATE
            if handle:
                try:
                    dll.TerminateProcess(handle, 1)
                finally:
                    dll.CloseHandle(handle)
        else:
            import signal

            try:
                os.kill(self.pid, signal.SIGKILL)
            except OSError:
                pass


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


def force_exit_current_process(code=0):
    """Exit immediately after app state is flushed, avoiding Qt DLL teardown hangs/crashes."""
    rc = int(code or 0)
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes as w

        dll = ctypes.WinDLL("kernel32", use_last_error=True)
        dll.GetCurrentProcess.restype = w.HANDLE
        dll.TerminateProcess.argtypes = [w.HANDLE, w.UINT]
        dll.TerminateProcess.restype = w.BOOL
        dll.TerminateProcess(dll.GetCurrentProcess(), rc)
    os._exit(rc)
