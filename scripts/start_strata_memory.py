"""Run the local Strata memory stack without installing Windows services."""

import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/strata-memory"
LOGS = ROOT / "data/logs"
PG = ROOT / "runtime/strata-postgres/pgsql/bin"
REDIS = ROOT / "runtime/strata-redis/Redis-8.10.2-Windows-x64-cygwin"
OLLAMA = ROOT / "runtime/strata-ollama/ollama.exe"
APP = ROOT / "runtime/strata-memory"
FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def request(path, body=None, timeout=10):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    headers = {"Content-Type": "application/json"}
    if path.startswith("http://127.0.0.1:8093"):
        headers["Authorization"] = "Bearer " + (DATA / "auth-token").read_text().strip()
    with opener.open(urllib.request.Request(path,
            data=json.dumps(body).encode() if body is not None else None,
            headers=headers), timeout=timeout) as response:
        return json.load(response)


def listening(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def launch(args, label, *, cwd=ROOT, env=None):
    with (LOGS / f"strata-{label}.log").open("ab") as log:
        process = subprocess.Popen([str(a) for a in args], cwd=cwd, env=env,
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            creationflags=FLAGS | getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    (DATA / f"{label}.pid").write_text(str(process.pid))
    return process


def wait_port(port, process=None):
    deadline = time.monotonic() + 45
    while not listening(port):
        if process is not None and process.poll() is not None:
            raise RuntimeError(f"Service on {port} exited; see data/logs/strata-*.log")
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Service on {port} did not start")
        time.sleep(0.5)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare-models", action="store_true")
    args = parser.parse_args()
    DATA.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    secret_path = DATA / "postgres-password"
    if not secret_path.exists():
        secret_path.write_text(secrets.token_hex(24), encoding="utf-8")
    password = secret_path.read_text().strip()
    token_path = DATA / "auth-token"
    if not token_path.exists():
        token_path.write_text(secrets.token_hex(32), encoding="utf-8")
    pgdata = DATA / "postgres"
    if not (pgdata / "PG_VERSION").exists():
        subprocess.run([str(PG / "initdb.exe"), "-D", str(pgdata), "-U", "strata",
            "-A", "scram-sha-256", "--pwfile", str(secret_path), "--encoding", "UTF8",
            "--locale", "C"], check=True, creationflags=FLAGS)
    if not listening(54329):
        process = launch([PG / "postgres.exe", "-D", pgdata, "-h", "127.0.0.1",
            "-p", "54329", "-c", "max_connections=30"], "postgres")
        wait_port(54329, process)
    pg_env = {**os.environ, "PGPASSWORD": password}
    psql = [str(PG / "psql.exe"), "-h", "127.0.0.1", "-p", "54329", "-U", "strata", "-d", "postgres", "-v", "ON_ERROR_STOP=1"]
    exists = subprocess.check_output(psql + ["-tAc", "SELECT 1 FROM pg_database WHERE datname='strata'"],
        env=pg_env, creationflags=FLAGS, text=True).strip()
    if exists != "1":
        subprocess.run(psql + ["-c", "CREATE DATABASE strata"], env=pg_env, check=True, creationflags=FLAGS)
    redis_config = DATA / "redis.conf"
    redis_config.write_text('bind 127.0.0.1\nport 63790\nprotected-mode yes\nsave ""\nappendonly no\n', encoding="utf-8")
    if not listening(63790):
        process = launch([REDIS / "redis-server.exe", redis_config], "redis", cwd=REDIS)
        wait_port(63790, process)
    ollama_env = {**os.environ, "OLLAMA_HOST": "127.0.0.1:11435",
        "OLLAMA_MODELS": str(DATA / "models"), "CUDA_VISIBLE_DEVICES": "-1",
        "OLLAMA_MAX_LOADED_MODELS": "2", "OLLAMA_NUM_PARALLEL": "1"}
    if not listening(11435):
        process = launch([OLLAMA, "serve"], "ollama", env=ollama_env)
        wait_port(11435, process)
    if args.prepare_models:
        for model in ("nomic-embed-text", "qwen2.5:3b-instruct"):
            print(f"Preparing memory model: {model}", flush=True)
            result = request("http://127.0.0.1:11435/api/pull", {"model": model, "stream": False}, timeout=3600)
            if result.get("status") != "success":
                raise RuntimeError(f"Model download failed: {model}")
    env_file = DATA / "runtime.env"
    env_file.write_text("\n".join([
        f"POSTGRES_URL=postgres://strata:{password}@127.0.0.1:54329/strata",
        "REDIS_URL=redis://127.0.0.1:63790", "OLLAMA_URL=http://127.0.0.1:11435",
        "EMBEDDING_MODEL=nomic-embed-text", "INSTRUCT_MODEL=qwen2.5:3b-instruct",
        "OLLAMA_TIMEOUT_MS=180000", "STRATA_TRANSPORT=http", "HTTP_HOST=127.0.0.1",
        "HTTP_PORT=8093", f"MCP_AUTH_TOKEN={token_path.read_text().strip()}",
        "COMPACTION_ENABLED=false", "STRATA_LOG_LEVEL=info", "",
    ]), encoding="utf-8")
    if not listening(8093):
        process = launch(["node", f"--env-file={env_file}", APP / "dist/main.js"], "memory", cwd=APP)
        wait_port(8093, process)
    print(json.dumps(request("http://127.0.0.1:8093/v1/health"), ensure_ascii=True))


if __name__ == "__main__":
    main()
