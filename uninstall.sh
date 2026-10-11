#!/bin/sh
# Remove the DE360 LCD dashboard service. Config in /etc/de360-lcd is kept
# unless you pass --purge. Apt packages (Pillow, Inter, ...) are left installed.
set -eu
[ "$(id -u)" -eq 0 ] || { echo "run as root: sudo $0" >&2; exit 1; }
systemctl disable --now de360-lcd 2>/dev/null || true
rm -f /etc/systemd/system/de360-lcd.service /etc/udev/rules.d/99-de360-lcd.rules
systemctl daemon-reload
udevadm control --reload
rm -rf /opt/de360-lcd
if [ "${1:-}" = "--purge" ]; then
    rm -rf /etc/de360-lcd
fi
echo "removed"
