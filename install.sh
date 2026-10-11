#!/bin/sh
# Install or upgrade the DE360 LCD dashboard as a systemd service.
#   sudo ./install.sh
# Offline install: put the .deb files and the psutil / nvidia-ml-py wheels in ./deps
# first (see README, "Offline install"); then nothing is downloaded.
# This script never installs, removes or upgrades GPU driver / CUDA packages.
set -eu
cd "$(dirname "$0")"
[ "$(id -u)" -eq 0 ] || { echo "run as root: sudo $0" >&2; exit 1; }

PREFIX=/opt/de360-lcd
ETC=/etc/de360-lcd

echo "== system packages (Pillow, PyYAML, Inter font, venv)"
if ls deps/*.deb >/dev/null 2>&1; then
    dpkg -i deps/*.deb
else
    apt-get install -y python3-venv python3-pil python3-yaml fonts-inter
fi

echo "== program files -> $PREFIX"
install -d "$PREFIX" "$ETC"
install -m 644 src/de360_lcd.py src/lcd_pages.py src/de360_dash.py "$PREFIX/"
[ -x "$PREFIX/venv/bin/python" ] || python3 -m venv --system-site-packages "$PREFIX/venv"
if ls deps/*.whl >/dev/null 2>&1; then
    "$PREFIX/venv/bin/pip" install -q --no-index --find-links deps -r requirements.txt
else
    "$PREFIX/venv/bin/pip" install -q -r requirements.txt
fi

echo "== config -> $ETC (an existing config.yaml is kept)"
[ -e "$ETC/config.yaml" ] || install -m 644 config/config.yaml "$ETC/config.yaml"
[ -e "$ETC/logo_lcd.png" ] || install -m 644 assets/logo_lcd.png "$ETC/logo_lcd.png"

echo "== udev rule + systemd unit"
install -m 644 udev/99-de360-lcd.rules /etc/udev/rules.d/
udevadm control --reload
udevadm trigger --subsystem-match=hidraw
install -m 644 systemd/de360-lcd.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable de360-lcd
systemctl restart de360-lcd
sleep 3
systemctl --no-pager status de360-lcd | head -12
