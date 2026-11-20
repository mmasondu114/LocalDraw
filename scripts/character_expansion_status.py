"""Print download progress without loading model weights or credentials."""
import collections
import json
from pathlib import Path
import sys
import ctypes


def process_alive(pid):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x1000, False, int(pid or 0))
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
    finally:
        kernel.CloseHandle(handle)

sys.stdout.reconfigure(encoding='utf-8')
root = Path(__file__).resolve().parents[1]
path = root / 'data/character-expansion-20261031/status.json'
if not path.exists():
    print('正在建立已有文件索引；尚未开始新增下载。')
    raise SystemExit(0)
data = json.loads(path.read_text(encoding='utf-8'))
rows = list(data['files'].values())
alive = process_alive(data.get('pid'))
status = '进程已退出，记录未更新' if data['status']=='running' and not alive else data['status']
print('任务状态:', status, '| 阶段:', data['stage'])
print('文件状态:', dict(collections.Counter(row['status'] for row in rows)))
print('已接收: %.3f GB / 上限 %.0f GB' % (sum(r.get('received_bytes',0) for r in rows)/1e9, data['limit_bytes']/1e9))
print('已分配队列: %.3f GB' % (data['reserved_bytes']/1e9))
print('更新时间:', data.get('updated_at'))
print('进程号:', data.get('pid'))
print('进程存活:', alive)
if data.get('search_progress'): print('检索进度:', data['search_progress'])
for row in rows:
    if row['status'] == 'downloading':
        print('正在下载:' if alive else '已中断:', row['group'], row['name'], '%.1f%%' % (100*row.get('received_bytes',0)/row['bytes']))
if data.get('roster_warning'): print('名单提示:', data['roster_warning'])
print('检索失败页数:', len(data.get('search_errors', [])))
