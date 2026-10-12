#!/usr/bin/env bash
# Install the Bix device agent on Raspberry Pi OS (Bookworm, 64-bit) or Ubuntu 22.04+.
#
#   sudo ./install-device.sh                      # agent + room panel (localhost)
#   sudo ./install-device.sh --screen             # + the room screen on the 7" display (kiosk browser)
#   sudo ./install-device.sh --face               # + old SDL2 face instead (no browser; low memory)
#   sudo bix-device enroll --server https://bix.firma.eu --code XXXX-XXXX-XXXX
#
# Runs as the unprivileged user "bix", state in /var/lib/bix/device.
set -euo pipefail

FACE=0
SCREEN=0
for a in "$@"; do [[ "$a" == "--face" ]] && FACE=1; [[ "$a" == "--screen" ]] && SCREEN=1; done
SRC="$(cd "$(dirname "$0")/../.." && pwd)"   # software/
PREFIX=/opt/bix
DATA=/var/lib/bix/device

[[ $EUID -eq 0 ]] || { echo "Run as root (sudo)."; exit 1; }

echo "== packages"
apt-get update
apt-get install -y --no-install-recommends python3 python3-venv python3-dev libportaudio2 libsndfile1 ffmpeg libusb-1.0-0 ca-certificates
if [[ $FACE -eq 1 ]]; then apt-get install -y --no-install-recommends libsdl2-2.0-0 libsdl2-ttf-2.0-0 fonts-dejavu-core; fi
if [[ $SCREEN -eq 1 ]]; then
  # Raspberry Pi OS: chromium-browser; Debian: chromium. (Ubuntu's chromium is a snap: install a .deb build.)
  apt-get install -y --no-install-recommends cage curl fonts-dejavu-core fonts-noto-color-emoji
  apt-get install -y --no-install-recommends chromium-browser 2>/dev/null || apt-get install -y --no-install-recommends chromium
fi

echo "== user"
id bix >/dev/null 2>&1 || useradd --system --home-dir /var/lib/bix --shell /usr/sbin/nologin bix
# audio (ALSA), serial (radar on UART), video/render/input (display + touch)
usermod -aG audio,dialout,video,render,input bix 2>/dev/null || usermod -aG audio,dialout,video bix
install -d -o bix -g bix -m 0750 /var/lib/bix "$DATA"

echo "== python env"
python3 -m venv "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install --upgrade pip wheel
EXTRAS="device"
[[ $FACE -eq 1 ]] && EXTRAS="device,face"
"$PREFIX/venv/bin/pip" install "$SRC[$EXTRAS]"
ln -sf "$PREFIX/venv/bin/bix" /usr/local/bin/bix

cat > /usr/local/bin/bix-device <<EOF
#!/bin/sh
exec runuser -u bix -- env BIX_DATA_DIR=$DATA BIX_DEVICE_CONFIG=$DATA/device.toml $PREFIX/venv/bin/bix device "\$@"
EOF
chmod 0755 /usr/local/bin/bix-device

echo "== config"
if [[ ! -f "$DATA/device.toml" ]]; then
  install -o bix -g bix -m 0600 "$SRC/deploy/device.example.toml" "$DATA/device.toml"
  # On the Bix hardware the microphones are the XVF3800 and the radar is on UART0.
  if grep -qi "raspberry" /proc/device-tree/model 2>/dev/null; then
    sed -i 's/^input_device = ""/input_device = "XVF3800"/; s/^presence = "none"/presence = "ld2410"/; s/^simulator = true/simulator = false/' "$DATA/device.toml"
  fi
fi

echo "== udev (XVF3800 LEDs/DoA over USB without root)"
install -D -m 0644 "$SRC/deploy/linux/99-bix-xvf3800.rules" /etc/udev/rules.d/99-bix-xvf3800.rules
udevadm control --reload-rules && udevadm trigger || true

echo "== systemd"
install -D -m 0644 "$SRC/deploy/linux/bix-device.service" /etc/systemd/system/bix-device.service
if [[ $FACE -eq 1 ]]; then
  mkdir -p /etc/systemd/system/bix-device.service.d
  cat > /etc/systemd/system/bix-device.service.d/face.conf <<'EOF'
[Service]
Environment=SDL_VIDEODRIVER=kmsdrm
ExecStart=
ExecStart=/opt/bix/venv/bin/bix device --face
EOF
fi
if [[ $SCREEN -eq 1 ]]; then
  CHROMIUM="$(command -v chromium-browser || command -v chromium)"
  sed "s#@CHROMIUM@#$CHROMIUM#" "$SRC/deploy/linux/bix-screen.service" > /etc/systemd/system/bix-screen.service
  systemctl set-default graphical.target
fi
systemctl daemon-reload
systemctl enable --now bix-device.service
[[ $SCREEN -eq 1 ]] && systemctl enable --now bix-screen.service

if grep -qi "raspberry" /proc/device-tree/model 2>/dev/null; then
  echo
  echo "Raspberry Pi: add to /boot/firmware/config.txt (see deploy/linux/config.txt.snippet):"
  cat "$SRC/deploy/linux/config.txt.snippet"
fi
echo
echo "Done. Enroll the device:  sudo bix-device enroll --server https://<server> --code <code from the portal>"
echo "Logs:                     journalctl -u bix-device -f"
