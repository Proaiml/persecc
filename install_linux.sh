#!/usr/bin/env bash
# Installs SecondX as a systemd service under /opt/secondx (run with sudo from the repo folder).
#   sudo ./install_linux.sh                 # install / upgrade (keeps an existing config.json)
#   sudo ./install_linux.sh --token <TOKEN> # also store the InfluxDB token in /etc/secondx/secondx.env
set -euo pipefail

PREFIX=/opt/secondx
ENV_DIR=/etc/secondx
TOKEN=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --token) TOKEN="${2:-}"; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

[[ $(id -u) -eq 0 ]] || { echo "Please run as root: sudo $0" >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }
python3 -c 'import ensurepip, venv' 2>/dev/null || { echo "python3-venv is required (e.g. apt install python3-venv)" >&2; exit 1; }
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

id secondx &>/dev/null || useradd --system --no-create-home --shell /usr/sbin/nologin secondx
install -d -o secondx -g secondx "$PREFIX" "$PREFIX/logs" "$PREFIX/data" "$PREFIX/spool"
install -m 644 "$SRC/SecondX.py" "$SRC/collector.py" "$SRC/lissozis.py" "$SRC/influx_exporter.py" \
               "$SRC/win_snapshot.py" "$SRC/requirements.txt" "$PREFIX/"
[[ -f "$PREFIX/config.json" ]] || install -m 644 "$SRC/config.json" "$PREFIX/config.json"

[[ -x "$PREFIX/venv/bin/python" ]] || python3 -m venv "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install --disable-pip-version-check -q -r "$PREFIX/requirements.txt"

install -d -m 750 "$ENV_DIR"
[[ -f "$ENV_DIR/secondx.env" ]] || install -m 600 "$SRC/secondx.env.example" "$ENV_DIR/secondx.env"
if [[ -n "$TOKEN" ]]; then
  sed -i "s|^SECONDX_INFLUX_TOKEN=.*|SECONDX_INFLUX_TOKEN=${TOKEN}|" "$ENV_DIR/secondx.env"
fi
chown -R secondx:secondx "$PREFIX"

if command -v systemctl >/dev/null && [[ -d /run/systemd/system ]]; then
  install -m 644 "$SRC/secondx.service" /etc/systemd/system/secondx.service
  systemctl daemon-reload
  systemctl enable secondx >/dev/null
  systemctl restart secondx
  echo "Service started. Status: systemctl status secondx | Logs: journalctl -u secondx -f"
else
  echo "systemd not found: run manually with  $PREFIX/venv/bin/python $PREFIX/SecondX.py"
fi

export PYTHONDONTWRITEBYTECODE=1
set -a; . "$ENV_DIR/secondx.env"; set +a
"$PREFIX/venv/bin/python" "$PREFIX/SecondX.py" --config "$PREFIX/config.json" --check || true
