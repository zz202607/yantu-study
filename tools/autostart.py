"""Install or remove a per-user Startup shortcut for the local server.

    py -3 tools/autostart.py --install
    py -3 tools/autostart.py --status
    py -3 tools/autostart.py --remove

The shortcut lives in the current user's Startup folder and launches
`pyw.exe -3 tools/serve_background.py`, so no console window and no admin
rights.  serve_background.py redirects output to `.cache/server.log` because
pythonw has no stdout and the server prints its LAN address at startup.
"""

from __future__ import annotations

import argparse
import base64
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LNK_NAME = "YantuStudy.lnk"

SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$startup = [Environment]::GetFolderPath('Startup')
$lnk = Join-Path $startup '__LNK__'
if ('__ACTION__' -eq 'status') {
  if (Test-Path $lnk) {
    $s = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
    Write-Output ('installed: ' + $s.TargetPath + ' ' + $s.Arguments)
  } else {
    Write-Output 'not-installed'
  }
  exit 0
}
if ('__ACTION__' -eq 'remove') {
  if (Test-Path $lnk) { Remove-Item $lnk -Force; Write-Output 'removed' }
  else { Write-Output 'not-installed' }
  exit 0
}
$pyw = (Get-Command pyw.exe -ErrorAction SilentlyContinue).Source
if (-not $pyw) { throw 'pyw.exe not found; install the Python launcher.' }
$shell = New-Object -ComObject WScript.Shell
$s = $shell.CreateShortcut($lnk)
$s.TargetPath = $pyw
$s.Arguments = '-3 "' + (Join-Path $env:YANTU_ROOT 'tools\serve_background.py') + '"'
$s.WorkingDirectory = $env:YANTU_ROOT
$s.WindowStyle = 7
$s.Description = 'Yantu study cockpit local server'
$s.Save()
Write-Output ('installed: ' + $lnk)
"""


def run(action):
    if os.name != "nt":
        print("autostart is only wired for Windows.")
        return 1
    script = SCRIPT.replace("__ACTION__", action).replace("__LNK__", LNK_NAME)
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    out = subprocess.run(
        ["powershell", "-NoProfile", "-EncodedCommand", encoded],
        env={**os.environ, "YANTU_ROOT": str(ROOT)},
        capture_output=True,
        text=True,
    )
    message = (out.stdout or out.stderr).strip()
    print(message or "powershell returned no output")
    return 0 if out.returncode == 0 else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--install", action="store_true", help="install the Startup shortcut")
    group.add_argument("--remove", action="store_true", help="remove the Startup shortcut")
    group.add_argument("--status", action="store_true", help="show whether it is installed")
    args = parser.parse_args()
    action = "install" if args.install else "remove" if args.remove else "status"
    return run(action)


if __name__ == "__main__":
    sys.exit(main())
