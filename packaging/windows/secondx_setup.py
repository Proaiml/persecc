"""
SecondX Windows setup (built into SecondX-Setup.exe)
Author: İlhan Koçaslan (Proaiml)

Interactive:   SecondX-Setup.exe
Unattended:    SecondX-Setup.exe --quiet --url http://influx:8086 --org secondx --bucket secondx --token-file C:\\t.txt
Local files:   SecondX-Setup.exe --quiet --local
Remove:        SecondX-Setup.exe --uninstall [--keep-data] [--quiet]

What it does (nothing else):
  * program files  -> %ProgramFiles%\\SecondX   (SecondX.exe, no Python needed)
  * settings, logs -> %ProgramData%\\SecondX    (only SYSTEM and Administrators can open it)
  * the token      -> %ProgramData%\\SecondX\\secondx.token (same restriction, never in config.json)
  * a Scheduled Task "SecondX" that starts at boot as SYSTEM under the built-in supervisor
  * an entry in "Apps & features" so it can be removed like any other program
"""
from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import getpass
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

APP = "SecondX"
TASK = "SecondX"
UNINSTALL_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\SecondX"
PUBLISHER = "İlhan Koçaslan (Proaiml)"
URL = "https://github.com/Proaiml/persecc"
SYSTEM_SID, ADMINS_SID = "*S-1-5-18", "*S-1-5-32-544"     # language independent ("Yöneticiler" = Administrators)
NO_WINDOW = 0x08000000


def resource(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / name


VERSION = resource("VERSION").read_text(encoding="utf-8").strip() if resource("VERSION").exists() else "dev"


# ---------------------------------------------------------------- console helpers
def say(text: str = "") -> None:
    print(text, flush=True)


def step(text: str) -> None:
    say(f"  • {text}")


def ok(text: str) -> None:
    say(f"    ✓ {text}")


def warn(text: str) -> None:
    say(f"    ! {text}")


def ask(question: str, default: str = "") -> str:
    hint = f" [{default}]" if default else ""
    answer = input(f"  {question}{hint}: ").strip()
    return answer or default


def yes(question: str, default: bool = True) -> bool:
    hint = "E/h" if default else "e/H"
    answer = input(f"  {question} ({hint}): ").strip().lower()
    return default if not answer else answer in ("e", "evet", "y", "yes")


def run(cmd: list[str], timeout: float = 120) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, creationflags=NO_WINDOW)


def powershell(script: str, timeout: float = 120) -> subprocess.CompletedProcess:
    return run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
               timeout)


def ps_quote(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------- building blocks
def restrict(path: Path, directory: bool) -> None:
    """Only SYSTEM and Administrators may open ``path`` (inheritance removed)."""
    rights = "(OI)(CI)F" if directory else "F"
    res = run(["icacls", str(path), "/inheritance:r", "/grant:r", f"{SYSTEM_SID}:{rights}",
               f"{ADMINS_SID}:{rights}"] + (["/T"] if directory else []) + ["/Q"])
    if res.returncode != 0:
        raise RuntimeError(f"icacls failed for {path}: {res.stdout.strip()} {res.stderr.strip()}")


def agent_processes(program: Path) -> list:
    import psutil

    exe = str(program / "SecondX.exe").lower()
    found = []
    for proc in psutil.process_iter(["exe"]):
        try:
            if (proc.info["exe"] or "").lower() == exe:
                found.append(proc)
        except Exception:  # noqa: BLE001
            continue
    return found


def stop_running(program: Path, use_task: bool) -> None:
    import psutil

    if use_task:
        powershell(f"Stop-ScheduledTask -TaskName {ps_quote(TASK)} -ErrorAction SilentlyContinue")
    procs = agent_processes(program)
    if not procs:
        return
    _, alive = psutil.wait_procs(procs, timeout=20)           # the agent exits ~1 s after its supervisor
    for proc in alive:
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
    psutil.wait_procs(alive, timeout=10)


def extract_payload(program: Path) -> None:
    payload = resource("payload.zip")
    if not payload.exists():
        raise RuntimeError("payload.zip is missing from the setup (broken build)")
    program.mkdir(parents=True, exist_ok=True)
    for old in (program / "_internal", program / "SecondX.exe"):          # no stale files from older versions
        if old.is_dir():
            shutil.rmtree(old)
        elif old.exists():
            old.unlink()
    with zipfile.ZipFile(payload) as z:
        z.extractall(program)


def write_config(data: Path, settings: dict) -> Path:
    template = json.loads(resource("config.json").read_text(encoding="utf-8"))
    inf = template["influx"]
    inf["enabled"] = settings["mode"] == "influx"
    inf["url"], inf["org"], inf["bucket"] = settings["url"], settings["org"], settings["bucket"]
    inf["token"] = ""
    inf["token_file"] = "secondx.token" if settings["mode"] == "influx" else ""
    template["host"] = settings["host"]
    path = data / "config.json"
    path.write_text(json.dumps(template, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def write_token(data: Path, token: str, protect: bool = True) -> None:
    path = data / "secondx.token"
    path.write_text(token.strip() + "\n", encoding="utf-8")
    if protect:
        restrict(path, directory=False)


def check_connection(program: Path, config: Path) -> tuple[bool, str]:
    res = run([str(program / "SecondX.exe"), "--check", "--config", str(config)], timeout=60)
    return res.returncode == 0, (res.stdout + res.stderr).strip()


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
"""
    res = powershell(script)
    if res.returncode != 0:
        raise RuntimeError(f"Scheduled Task could not be registered: {res.stderr.strip()[:400]}")


def start_task() -> None:
    res = powershell(f"Start-ScheduledTask -TaskName {ps_quote(TASK)}")
    if res.returncode != 0:
        raise RuntimeError(f"Scheduled Task could not be started: {res.stderr.strip()[:400]}")


def register_uninstall(program: Path) -> None:
    import winreg

    setup = program / "SecondX-Setup.exe"
    size_kb = sum(f.stat().st_size for f in program.rglob("*") if f.is_file()) // 1024
    with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, UNINSTALL_KEY, 0,
                            winreg.KEY_WRITE | winreg.KEY_WOW64_64KEY) as key:
        for name, value in {
            "DisplayName": "SecondX telemetry agent", "DisplayVersion": VERSION, "Publisher": PUBLISHER,
            "InstallLocation": str(program), "DisplayIcon": str(program / "SecondX.exe"), "URLInfoAbout": URL,
            "UninstallString": f'"{setup}" --uninstall', "QuietUninstallString": f'"{setup}" --uninstall --quiet',
            "InstallDate": dt.date.today().strftime("%Y%m%d"),
        }.items():
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        winreg.SetValueEx(key, "EstimatedSize", 0, winreg.REG_DWORD, int(size_kb))
        winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)


def remove_uninstall_entry() -> None:
    import winreg

    try:
        winreg.DeleteKeyEx(winreg.HKEY_LOCAL_MACHINE, UNINSTALL_KEY, winreg.KEY_WOW64_64KEY, 0)
    except OSError:
        pass


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
            return True, [line for line in lines if "started on host" in line or "Exporting to" in line][-2:]
    return False, ["(no start message in the log yet)"]


# ---------------------------------------------------------------- flows
def collect_settings(args, data: Path) -> dict | None:
    """Returns None to keep the existing configuration (upgrade)."""
    existing = data / "config.json"
    if args.quiet:
        if existing.exists() and not (args.url or args.local or args.token or args.token_file):
            return None
        token = args.token or (Path(args.token_file).read_text(encoding="utf-8-sig").strip() if args.token_file else "")
        if args.url and not token and not args.local:
            raise RuntimeError("--url needs --token-file (or --token); use --local to write local files instead")
        return {"mode": "local" if args.local or not token else "influx", "url": args.url or "http://localhost:8086",
                "org": args.org, "bucket": args.bucket, "token": token, "host": args.host or ""}
    if existing.exists():
        say("  Bu bilgisayarda SecondX ayarları zaten var (güncelleme).")
        if yes("Mevcut ayarlar korunsun mu?"):
            return None
    say("")
    say("  Veriler nereye yazılsın?")
    say("    1) InfluxDB  (önerilen: Grafana panosuyla izlenir)")
    say("    2) Yalnızca bu bilgisayardaki dosyalara (%ProgramData%\\SecondX\\data)")
    mode = "local" if ask("Seçiminiz", "1") == "2" else "influx"
    settings = {"mode": mode, "url": "http://localhost:8086", "org": "secondx", "bucket": "secondx", "token": "",
                "host": ""}
    if mode == "influx":
        settings["url"] = ask("InfluxDB adresi", settings["url"])
        settings["org"] = ask("Organization (org)", settings["org"])
        settings["bucket"] = ask("Bucket", settings["bucket"])
        while not settings["token"]:
            settings["token"] = getpass.getpass("  InfluxDB token (yazarken görünmez): ").strip()
    settings["host"] = ask("Bu sunucunun panoda görünecek adı", socket.gethostname())
    return settings


def install(args) -> int:
    program, data = Path(args.program_dir), Path(args.data_dir)
    use_system = not args.no_task
    say(f"\n  SecondX {VERSION} kurulumu")
    say("  " + "─" * 60)
    say("  Yapılacaklar (başka hiçbir şey yapılmaz):")
    say(f"    - Program : {program}")
    say(f"    - Ayarlar ve günlükler: {data}  (yalnızca SYSTEM ve Yöneticiler açabilir)")
    say("    - Açılışta SYSTEM hesabıyla başlayan 'SecondX' zamanlanmış görevi")
    say("    - 'Uygulamalar ve özellikler' listesine kaldırma kaydı")
    say("  Toplanan: CPU, RAM, disk, ağ sayıları ve süreç adları. Dosya içeriği, komut satırı,")
    say("  kullanıcı adı veya ağ trafiği içeriği TOPLANMAZ. Veri yalnızca sizin InfluxDB'nize gider.")
    say("  " + "─" * 60)
    if use_system and not is_admin():
        say("  HATA: Kurulum için yönetici yetkisi gerekiyor. Dosyaya sağ tıklayıp 'Yönetici olarak çalıştır'ı seçin.")
        return 1
    if not args.quiet and not yes("Devam edilsin mi?"):
        return 1

    settings = collect_settings(args, data)
    started = time.time()
    say("")
    step("Çalışan eski sürüm durduruluyor (varsa)")
    stop_running(program, use_system)
    step("Program dosyaları kopyalanıyor")
    extract_payload(program)
    setup_copy = program / "SecondX-Setup.exe"                             # used by "Apps & features" to remove
    if getattr(sys, "frozen", False) and Path(sys.executable).resolve() != setup_copy.resolve():
        shutil.copy2(sys.executable, setup_copy)
    ok(str(program))

    step("Ayarlar yazılıyor")
    data.mkdir(parents=True, exist_ok=True)
    if use_system:
        restrict(data, directory=True)
    if settings is None:
        ok(f"mevcut ayarlar korundu: {data / 'config.json'}")
        config = data / "config.json"
    else:
        config = write_config(data, settings)
        if settings["mode"] == "influx":
            write_token(data, settings["token"], protect=use_system)
            ok("token korumalı dosyaya yazıldı (config.json'da yer almaz)")
        ok(str(config))

    step("Yapılandırma ve bağlantı denetleniyor")
    good, output = check_connection(program, config)
    for line in output.splitlines():
        say(f"      {line}")
    if not good:
        warn("Denetim bir sorun bildirdi. Ajan kurulur ve InfluxDB'ye ulaşana kadar veriyi diskte bekletir.")
        if not args.quiet and not yes("Yine de kuruluma devam edilsin mi?", default=False):
            return 1

    if not use_system:
        ok("--no-task: zamanlanmış görev ve kaldırma kaydı atlandı (test kurulumu)")
        return 0
    step("Zamanlanmış görev kaydediliyor ve başlatılıyor")
    register_task(program, config, data)
    register_uninstall(program)
    start_task()
    running, lines = wait_for_start(data, started)
    for line in lines:
        say(f"      {line}")
    if not running:
        warn(f"Ajan başlamadı. Günlüğe bakın: {data / 'logs' / 'secondx.log'}")
        return 1
    ok("SecondX çalışıyor")
    say("")
    say("  Kurulum tamamlandı.")
    say(f"    Günlük   : {data / 'logs' / 'secondx.log'}")
    say(f"    Ayarlar  : {config}")
    say("    Durum    : Görev Zamanlayıcı > SecondX  (ya da: Get-ScheduledTask SecondX | Get-ScheduledTaskInfo)")
    say("    Kaldırma : Ayarlar > Uygulamalar > SecondX telemetry agent > Kaldır")
    return 0


def uninstall(args) -> int:
    program, data = Path(args.program_dir), Path(args.data_dir)
    use_system = not args.no_task
    say(f"\n  SecondX kaldırılıyor ({program})")
    if use_system and not is_admin():
        say("  HATA: Kaldırmak için yönetici yetkisi gerekiyor.")
        return 1
    keep = args.keep_data
    pending = list((data / "spool").glob("block-*")) if (data / "spool").exists() else []
    if pending and not keep:
        warn(f"Diskte InfluxDB'ye henüz gönderilmemiş {len(pending)} veri bloğu var"
             + (" (--keep-data ile korunabilirdi)." if args.quiet else "."))
    if not args.quiet and not keep:
        keep = not yes(f"Ayarlar, günlükler ve bekleyen veri de silinsin mi? ({data})", default=not pending)
    step("Ajan durduruluyor")
    stop_running(program, use_system)
    if use_system:
        powershell(f"Unregister-ScheduledTask -TaskName {ps_quote(TASK)} -Confirm:$false -ErrorAction SilentlyContinue")
        remove_uninstall_entry()
        ok("zamanlanmış görev ve kaldırma kaydı silindi")
    if not keep and data.exists():
        shutil.rmtree(data, ignore_errors=True)
        ok(f"silindi: {data}")
    elif data.exists():
        ok(f"korundu: {data}")
    running_from_program = getattr(sys, "frozen", False) and Path(sys.executable).resolve().parent == program.resolve()
    if running_from_program:
        # this exe cannot delete itself while running: remove the folder right after exit
        subprocess.Popen(f'cmd /c ping -n 3 127.0.0.1 >nul & rmdir /s /q "{program}"', shell=True,
                         creationflags=NO_WINDOW | 0x00000008, cwd=tempfile.gettempdir())
        ok(f"{program} kapanıştan hemen sonra silinecek")
    elif program.exists():
        shutil.rmtree(program, ignore_errors=True)
        ok(f"silindi: {program}")
    say("  SecondX kaldırıldı.")
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    parser = argparse.ArgumentParser(prog="SecondX-Setup", description="Installs or removes the SecondX agent.")
    parser.add_argument("--quiet", action="store_true", help="no questions (for mass deployment)")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--keep-data", action="store_true", help="with --uninstall: keep settings, logs, spool")
    parser.add_argument("--url", default="")
    parser.add_argument("--org", default="secondx")
    parser.add_argument("--bucket", default="secondx")
    parser.add_argument("--token", default="", help="prefer --token-file: command lines can be seen by others")
    parser.add_argument("--token-file", default="")
    parser.add_argument("--host", default="", help="name shown on the dashboard (default: computer name)")
    parser.add_argument("--local", action="store_true", help="write to local files instead of InfluxDB")
    parser.add_argument("--program-dir", default=str(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / APP))
    parser.add_argument("--data-dir", default=str(Path(os.environ.get("ProgramData", r"C:\ProgramData")) / APP))
    parser.add_argument("--no-task", action="store_true", help=argparse.SUPPRESS)      # tests without admin rights
    args = parser.parse_args(argv)
    try:
        code = uninstall(args) if args.uninstall else install(args)
    except KeyboardInterrupt:
        say("\n  İptal edildi.")
        code = 1
    except Exception as exc:  # noqa: BLE001
        say(f"\n  HATA: {exc}")
        code = 1
    if not args.quiet and sys.stdin and sys.stdin.isatty():
        try:
            input("\n  Kapatmak için Enter'a basın...")
        except EOFError:
            pass
    return code


if __name__ == "__main__":
    sys.exit(main())
