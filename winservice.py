"""
Windows service helpers for SecondX
Author: İlhan Koçaslan (Proaiml)

Used by the installer (SecondX-Setup.exe, Inno Setup) after it has copied the
program files, and usable on its own:

    SecondX.exe --service install   [--url U --org O --bucket B --token-file F] [--local] [--host H] [--keep-config]
    SecondX.exe --service uninstall [--remove-data]

install:   settings + token -> %ProgramData%\\SecondX (only SYSTEM and Administrators can open it),
           Scheduled Task "SecondX" (at boot, SYSTEM, built-in supervisor), start, verify from the log.
uninstall: stop the agent, remove the Scheduled Task (and optionally the data folder).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

TASK = "SecondX"
URL = "https://github.com/Proaiml/persecc"
SYSTEM_SID, ADMINS_SID = "*S-1-5-18", "*S-1-5-32-544"     # language independent ("Yöneticiler" = Administrators)
NO_WINDOW = 0x08000000


_report: list[str] = []


def say(text: str = "") -> None:
    """Prints (never fails on a console that cannot show a character) and keeps the line for --report-file."""
    _report.append(text)
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        import sys
        enc = getattr(sys.stdout, "encoding", None) or "ascii"
        print(text.encode(enc, "replace").decode(enc), flush=True)


def run(cmd: list[str], timeout: float = 120) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, creationflags=NO_WINDOW if os.name == "nt" else 0)


def powershell(script: str, timeout: float = 120) -> subprocess.CompletedProcess:
    return run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
               timeout)


def ps_quote(value) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def restrict(path: Path, directory: bool) -> None:
    """Only SYSTEM and Administrators may open ``path`` (inherited permissions removed)."""
    rights = "(OI)(CI)F" if directory else "F"
    res = run(["icacls", str(path), "/inheritance:r", "/grant:r", f"{SYSTEM_SID}:{rights}",
               f"{ADMINS_SID}:{rights}"] + (["/T"] if directory else []) + ["/Q"])
    if res.returncode != 0:
        raise RuntimeError(f"icacls failed for {path}: {res.stdout.strip()} {res.stderr.strip()}")


def default_data_dir() -> Path:
    return Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "SecondX"


# ---------------------------------------------------------------- configuration
def build_config(template: dict, *, local: bool, url: str, org: str, bucket: str, host: str) -> dict:
    cfg = json.loads(json.dumps(template))
    inf = cfg.setdefault("influx", {})
    inf["enabled"] = not local
    inf["url"], inf["org"], inf["bucket"] = url, org, bucket
    inf["token"] = ""
    inf["token_file"] = "" if local else "secondx.token"
    cfg["host"] = host
    return cfg


def load_template(program: Path, defaults: dict) -> dict:
    for candidate in (program / "config.default.json", program / "config.json"):
        if candidate.exists():
            return json.loads(candidate.read_text(encoding="utf-8-sig"))
    return json.loads(json.dumps(defaults))                          # built-in defaults


def write_settings(program: Path, data: Path, args, protect: bool) -> Path:
    config = data / "config.json"
    if args.keep_config and config.exists():
        say(f"  ✓ mevcut ayarlar korundu: {config}")
        return config
    token = ""
    if not args.local:
        if not args.token_file:
            raise RuntimeError("InfluxDB için --token-file gerekli (ya da --local)")
        token = Path(args.token_file).read_text(encoding="utf-8-sig").strip()
        if not token:
            raise RuntimeError(f"token dosyası boş: {args.token_file}")
    cfg = build_config(load_template(program, args.defaults), local=args.local, url=args.url, org=args.org,
                       bucket=args.bucket, host=args.host)
    config.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if token:
        path = data / "secondx.token"
        path.write_text(token + "\n", encoding="utf-8")
        if protect:
            restrict(path, directory=False)
        say("  ✓ token yalnızca SYSTEM ve Yöneticiler'in açabildiği dosyaya yazıldı (config.json'da yer almaz)")
    if args.delete_token_file and args.token_file:
        try:
            Path(args.token_file).unlink()
        except OSError:
            pass
    say(f"  ✓ ayarlar: {config}")
    return config


# ---------------------------------------------------------------- task and processes
def agent_processes(program: Path) -> list:
    import psutil

    exe = str(program / "SecondX.exe").lower()
    me = os.getpid()
    found = []
    for proc in psutil.process_iter(["exe"]):
        try:
            if proc.pid != me and (proc.info["exe"] or "").lower() == exe:
                found.append(proc)
        except Exception:  # noqa: BLE001
            continue
    return found


def stop_agent(program: Path, use_task: bool) -> None:
    import psutil

    if use_task:
        powershell(f"Stop-ScheduledTask -TaskName {ps_quote(TASK)} -ErrorAction SilentlyContinue")
    procs = agent_processes(program)
    _, alive = psutil.wait_procs(procs, timeout=20)             # the agent exits ~1 s after its supervisor
    for proc in alive:
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
    psutil.wait_procs(alive, timeout=10)


def register_task(program: Path, config: Path, data: Path) -> None:
    script = f"""
$ErrorActionPreference = 'Stop'
$action   = New-ScheduledTaskAction -Execute {ps_quote(program / 'SecondX.exe')} `
            -Argument {ps_quote('--supervise --config "' + str(config) + '"')} -WorkingDirectory {ps_quote(data)}
$trigger  = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable -MultipleInstances IgnoreNew
$system   = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName {ps_quote(TASK)} -Action $action -Trigger $trigger -Settings $settings `
    -Principal $system -Description 'SecondX second-level telemetry agent ({URL})' -Force | Out-Null
Start-ScheduledTask -TaskName {ps_quote(TASK)}
"""
    res = powershell(script)
    if res.returncode != 0:
        raise RuntimeError(f"zamanlanmış görev kaydedilemedi: {res.stderr.strip()[:400]}")


def wait_for_start(data: Path, since: float, timeout: float = 25) -> tuple[bool, list[str]]:
    log = data / "logs" / "secondx.log"
    end = time.time() + timeout
    while time.time() < end:
        time.sleep(1)
        if not log.exists() or log.stat().st_mtime < since:
            continue
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()[-30:]
        if any("STOPPING" in line or "Traceback" in line for line in lines):
            return False, lines[-6:]
        if any(" started on host " in line for line in lines):
            return True, [line[20:] for line in lines if "started on host" in line or "Exporting to" in line
                          or "Writing metrics to" in line][-2:]
    return False, ["(günlükte henüz başlama satırı yok)"]


# ---------------------------------------------------------------- commands
def install(args) -> int:
    program, data = Path(args.program_dir), Path(args.data_dir)
    use_task = not args.no_task
    started = time.time()
    say(f"SecondX kurulumu: program {program}, ayarlar {data}")
    stop_agent(program, use_task)
    data.mkdir(parents=True, exist_ok=True)
    if use_task:
        restrict(data, directory=True)
    config = write_settings(program, data, args, protect=use_task)
    if not use_task:
        say("  ✓ --no-task: zamanlanmış görev atlandı (test)")
        return 0
    register_task(program, config, data)
    say("  ✓ zamanlanmış görev 'SecondX' kaydedildi ve başlatıldı (açılışta SYSTEM hesabıyla)")
    running, lines = wait_for_start(data, started)
    for line in lines:
        say(f"    {line}")
    if not running:
        say(f"  ! ajan başlamadı, günlüğe bakın: {data / 'logs' / 'secondx.log'}")
        return 1
    say("  ✓ SecondX çalışıyor")
    return 0


def uninstall(args) -> int:
    program, data = Path(args.program_dir), Path(args.data_dir)
    use_task = not args.no_task
    stop_agent(program, use_task)
    if use_task:
        powershell(f"Unregister-ScheduledTask -TaskName {ps_quote(TASK)} -Confirm:$false -ErrorAction SilentlyContinue")
        say("  ✓ zamanlanmış görev kaldırıldı")
    if args.remove_data and data.exists():
        shutil.rmtree(data, ignore_errors=True)
        say(f"  ✓ silindi: {data}")
    return 0


def main(argv: list[str], program_dir: Path, defaults: dict) -> int:
    """``program_dir``: the folder of SecondX.exe; ``defaults``: SecondX.DEFAULT_CONFIG."""
    parser = argparse.ArgumentParser(prog="SecondX --service")
    parser.add_argument("action", choices=["install", "uninstall"])
    parser.add_argument("--program-dir", default=str(program_dir))
    parser.add_argument("--data-dir", default=str(default_data_dir()))
    parser.add_argument("--url", default="http://localhost:8086")
    parser.add_argument("--org", default="secondx")
    parser.add_argument("--bucket", default="secondx")
    parser.add_argument("--token-file", default="")
    parser.add_argument("--delete-token-file", action="store_true", help="remove --token-file after reading it")
    parser.add_argument("--host", default="", help="name shown on the dashboard (default: computer name)")
    parser.add_argument("--local", action="store_true", help="write to local files instead of InfluxDB")
    parser.add_argument("--keep-config", action="store_true", help="keep an existing config.json (upgrade)")
    parser.add_argument("--remove-data", action="store_true", help="uninstall: also delete settings, logs, spool")
    parser.add_argument("--report-file", default="", help="also write the result as UTF-8 text (for the installer)")
    parser.add_argument("--no-task", action="store_true", help=argparse.SUPPRESS)      # tests without admin rights
    args = parser.parse_args(argv)
    args.defaults = defaults
    _report.clear()
    try:
        code = install(args) if args.action == "install" else uninstall(args)
    except Exception as exc:  # noqa: BLE001 - the installer shows this text to the user
        say(f"HATA: {exc}")
        code = 1
    if args.report_file:
        try:
            Path(args.report_file).write_text("\n".join(_report) + "\n", encoding="utf-8")
        except OSError:
            pass
    return code
