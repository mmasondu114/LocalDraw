import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from refresh_wardrobe import check_idle, missing_catalog


class WardrobeRefreshTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows PowerShell desktop repair")
    def test_listener_check_reads_utf8_job_status(self):
        script = Path(__file__).resolve().parents[1] / "scripts/restart_studio_listener.ps1"
        with tempfile.TemporaryDirectory() as temp:
            fixture = Path(temp) / "status.json"
            label = "已完成 2 张，失败 0 张。"
            fixture.write_bytes(json.dumps({"jobs": [{"label": label}]}, ensure_ascii=False).encode("utf-8"))
            # Load only the reader; the test must never stop a desktop process.
            check = """
$ErrorActionPreference = 'Stop'
$tree = [System.Management.Automation.Language.Parser]::ParseFile($env:READER_FILE,[ref]$null,[ref]$null)
$fn = $tree.Find({param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Read-StudioJson'},$true)
. ([ScriptBlock]::Create($fn.Extent.Text))
$client = New-Object Net.WebClient
try {
    $state = Read-StudioJson $client ([uri]::new($env:STATUS_FIXTURE).AbsoluteUri)
    if ($state.jobs[0].label -cne $env:EXPECTED_LABEL) { throw 'Job label changed' }
} finally { $client.Dispose() }
"""
            subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", check],
                           env={**os.environ, "READER_FILE": str(script), "STATUS_FIXTURE": str(fixture),
                                "EXPECTED_LABEL": label}, check=True, timeout=15, capture_output=True)

    def test_only_a_missing_route_means_an_old_server(self):
        def error(message):
            return HTTPError("/api/wardrobe", 400, "", {},
                             io.BytesIO(json.dumps({"error": message}).encode()))
        self.assertTrue(missing_catalog(Mock(side_effect=error("页面不存在"))))
        self.assertFalse(missing_catalog(lambda path: {"characters": [{"tag": "amiya"}]}))
        with self.assertRaises(HTTPError):
            missing_catalog(Mock(side_effect=error("词库损坏")))

    def test_running_queue_and_unfinished_jobs_block_restart(self):
        for running, jobs in [(True, []), (False, [{"status": "running"}]),
                              (False, [{"status": "paused"}])]:
            with self.subTest(queue=running, jobs=jobs), self.assertRaises(RuntimeError):
                check_idle(lambda path: {"queue": {"running": running}, "jobs": jobs})
