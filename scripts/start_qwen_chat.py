"""Standalone local Qwen chat; shares model files, not the studio's server."""
import argparse
import json
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from comic_workflow.processes import attach_exit_job, close_job, terminate_tree

PORT = 8092
URL = f'http://127.0.0.1:{PORT}'
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def read_json(url):
    with OPENER.open(url, timeout=2) as response:
        return json.load(response)


def port_open(port):
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(('127.0.0.1', port)) == 0


def main():
    parser = argparse.ArgumentParser(description='单独打开本地 Qwen 聊天网页')
    parser.add_argument('--check', action='store_true', help='只检查启动文件，不加载模型')
    args = parser.parse_args()
    cfg = json.loads((ROOT / 'config/settings.json').read_text(encoding='utf-8-sig'))
    model = ROOT / cfg['model_path']
    runtime = ROOT / cfg['runtime_path']
    if not runtime.is_file() or not model.is_file():
        raise RuntimeError('找不到本地模型或推理程序，请检查 models 和 runtime 文件夹。')
    if model.stat().st_size != cfg['model_size']:
        raise RuntimeError('模型文件大小不符，请检查下载是否完整。')
    command = [str(runtime), '-m', str(model), '--alias', 'qwen-local-chat',
               '--host', '127.0.0.1', '--port', str(PORT), '--ctx-size', str(cfg['context_size']),
               '--parallel', '1', '--gpu-layers', 'all', '--device', 'CUDA0',
               '--flash-attn', 'on', '--cache-type-k', cfg['cache_type_k'],
               '--cache-type-v', cfg['cache_type_v'], '--batch-size', str(cfg['batch_size']),
               '--ubatch-size', str(cfg['ubatch_size']), '--jinja', '--reasoning', 'off',
               '--fit', 'off', '--webui']
    if args.check:
        print(f'启动文件检查通过：{model.name}\n聊天地址：{URL}\n未加载模型。')
        return 0
    if port_open(PORT):
        try:
            props = read_json(URL + '/props')
            same = Path(props.get('model_path', '')).resolve() == model.resolve()
            ready = read_json(URL + '/health').get('status') == 'ok'
        except Exception:
            same = ready = False
        if same and ready:
            webbrowser.open(URL)
            print('已打开正在运行的 Qwen 聊天窗口。')
            return 0
        raise RuntimeError('8092 端口正在使用或模型尚在加载，请查看已经打开的 015 窗口。')
    if port_open(cfg['model_port']):
        raise RuntimeError('工作室的 Qwen 服务正在运行。请先在工作室点击“卸载模型”，再打开 015。')
    gpu = subprocess.run(['nvidia-smi', '--id=0', '--query-gpu=memory.free',
                          '--format=csv,noheader,nounits'], capture_output=True, text=True,
                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), timeout=10)
    if gpu.returncode != 0:
        raise RuntimeError('无法读取 NVIDIA 显卡状态，请检查显卡驱动。')
    free_mib = int(gpu.stdout.strip().splitlines()[0])
    if free_mib < model.stat().st_size / 1024**2 + 768:
        raise RuntimeError('当前空闲显存不足。请先结束绘图并卸载模型，或关闭占用显存的程序，再运行 015。')
    log_path = ROOT / 'data/logs/qwen-chat-015.log'
    log_path.parent.mkdir(parents=True, exist_ok=True)
    print(f'正在加载 {model.name}，就绪后自动打开聊天网页。', flush=True)
    print('默认关闭思考模式；仅在本机运行。关闭此窗口或按 Ctrl+C 可卸载本次模型。', flush=True)
    process = job = None
    try:
        with log_path.open('w', encoding='utf-8') as log:
            process = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            job = attach_exit_job(process)
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f'模型未能启动，详情见：{log_path}')
                try:
                    if read_json(URL + '/health').get('status') == 'ok':
                        break
                except Exception:
                    pass
                time.sleep(1)
            else:
                raise RuntimeError(f'模型加载超时，详情见：{log_path}')
            webbrowser.open(URL)
            print(f'Qwen 已就绪：{URL}\n请保留此窗口；只关闭浏览器不会卸载模型。', flush=True)
            while process.poll() is None:
                time.sleep(0.5)
            if process.returncode:
                raise RuntimeError(f'模型服务已退出，详情见：{log_path}')
    except KeyboardInterrupt:
        print('\n正在关闭 Qwen…', flush=True)
    finally:
        terminate_tree(process)
        close_job(job)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f'\n{exc}', file=sys.stderr)
        raise SystemExit(1)
