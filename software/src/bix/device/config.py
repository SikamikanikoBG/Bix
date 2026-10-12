"""Device configuration (device.toml) and credentials (credentials.json).

Policy (language, live transcription, auto-stop, notice text) comes from the
server with every heartbeat; this file only holds what the device needs to
find the server and its own hardware.
"""

from __future__ import annotations

import json
import os
import platform
import sys
from dataclasses import dataclass, field
from pathlib import Path

from bix.common import settings


@dataclass
class AudioCfg:
    # "" = системният микрофон; иначе част от името или номер. На устройството: "XVF3800".
    input_device: str = ""
    # Нива под този праг са тишина, ако няма Silero VAD модел.
    energy_threshold_db: float = -48.0
    # Откъси за качване: целева дължина и максимум (сек). Режат се в паузите.
    chunk_target_s: float = 20.0
    chunk_max_s: float = 45.0


@dataclass
class SpoolCfg:
    # Колко запис се пази локално, ако няма мрежа (blueprint: 2 часа).
    max_hours: float = 2.0


@dataclass
class HardwareCfg:
    # auto | xvf3800 | none
    leds: str = "auto"
    led_brightness_pct: int = 25
    # XVF3800 voice direction (degrees) that points straight at the screen; the face looks at the speaker
    doa_front_deg: int = 0
    # none | ld2410
    presence: str = "none"
    presence_port: str = "/dev/ttyAMA0"
    presence_empty_minutes: int = 10


@dataclass
class WakeCfg:
    enabled: bool = False
    model_path: str = ""
    threshold: float = 0.6


@dataclass
class PanelCfg:
    # Локален панел на залата в браузъра (за лаптоп / киоск).
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8711
    open_browser: bool = False
    # Virtual device at /sim (3D model, radar and mute button): for trying Bix without the hardware.
    simulator: bool = True


@dataclass
class DeviceConfig:
    server_url: str = ""
    name: str = field(default_factory=platform.node)
    serial: str = ""
    # Път до CA сертификат за сървъра, ако е частен; клиентски сертификат за mTLS.
    ca_bundle: str = ""
    client_cert: str = ""
    client_key: str = ""
    heartbeat_s: float = 15.0
    audio: AudioCfg = field(default_factory=AudioCfg)
    spool: SpoolCfg = field(default_factory=SpoolCfg)
    hardware: HardwareCfg = field(default_factory=HardwareCfg)
    wake: WakeCfg = field(default_factory=WakeCfg)
    panel: PanelCfg = field(default_factory=PanelCfg)

    data_dir: str = ""

    @property
    def dir(self) -> Path:
        return Path(self.data_dir) if self.data_dir else settings.data_dir("device")

    @property
    def spool_dir(self) -> Path:
        return self.dir / "spool"

    @property
    def models_dir(self) -> Path:
        return self.dir / "models"

    @property
    def credentials_path(self) -> Path:
        return self.dir / "credentials.json"

    def credentials(self) -> dict:
        p = self.credentials_path
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    def save_credentials(self, data: dict) -> None:
        p = self.credentials_path
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        if sys.platform != "win32":
            os.chmod(tmp, 0o600)
        tmp.replace(p)

    def serial_number(self) -> str:
        if self.serial:
            return self.serial
        try:  # Raspberry Pi
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if line.startswith("Serial"):
                    return line.split(":")[1].strip()
        except OSError:
            pass
        try:
            return Path("/etc/machine-id").read_text().strip()[:16]
        except OSError:
            import uuid

            return f"{uuid.getnode():012x}"


def config_path(path: str | Path | None = None) -> Path:
    if path:
        return Path(path)
    if env := os.environ.get("BIX_DEVICE_CONFIG"):
        return Path(env)
    return settings.data_dir("device") / "device.toml"


def load(path: str | Path | None = None) -> tuple[DeviceConfig, Path]:
    p = config_path(path)
    cfg = settings.load(DeviceConfig, p, env_prefix="BIX_DEVICE")
    if not cfg.data_dir:
        cfg.data_dir = str(p.parent)
    cfg.dir.mkdir(parents=True, exist_ok=True)
    return cfg, p
