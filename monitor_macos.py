#!/usr/bin/env python3
"""
monitor_macos.py — AIO Display Driver para macOS
Hardware: VID 0x5131 / PID 0x2007 (FBB)
Protocolo: ingeniería inversa de PC Monitor All.exe (Windows)

Packet format (65 bytes):
  [0]     = 0x00  Report ID
  [1..3]  = 00 01 02  header fijo
  [4]     = CPU core temp (°C)
  [5]     = GPU usage (%)
  [6]     = GPU power (W)
  [7]     = CPU package power (W)
  [8]     = CPU hotspot/package temp (°C)
  [9]     = CPU max thread usage (%)
  [10]    = GPU clock MHz ÷ 10
  [11]    = CPU clock MHz ÷ 48
  [12]    = 0x01  constante
  [13]    = CPU VID voltage × 100
  [14]    = GPU temp (°C)
  [15..18]= 0x00
  [19]    = 0x0A  flag Celsius
  [20]    = 0x00
  [21]    = CPU total usage promedio (%)
  [22]    = CPU fan RPM ÷ 100  (parte alta)
  [23]    = CPU fan RPM % 100  (parte baja)
  [24]    = Pump RPM ÷ 100
  [25]    = Pump RPM % 100
  [26..34]= display config (umbrales + contador)
  [35..64]= 0x00  padding

Dependencias:
    pip3 install hid psutil
    brew install hidapi

Temperatura CPU sin sudo:
    brew install osx-cpu-temp      # recomendado
    -- o --
    El lector SMC via IOKit (incluido en este script) funciona sin sudo ni CLI externo.

Potencia CPU (requiere sudo sin contraseña para powermetrics):
    sudo visudo  →  añadir la línea:
    <usuario> ALL=(ALL) NOPASSWD: /usr/bin/powermetrics

GPU (AMD discreta):
    No requiere nada extra — usa ioreg (incluido en macOS).
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import json
import logging
import signal
import struct as _struct
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import hid
import psutil

# ──────────────────────────────────────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("aio-display")

# ──────────────────────────────────────────────────────────────────────────────
# Constantes del dispositivo
# ──────────────────────────────────────────────────────────────────────────────
VENDOR_ID  = 0x5131
PRODUCT_ID = 0x2007
INTERVAL   = 0.200  # 200 ms, igual que PC Monitor All.exe

# ──────────────────────────────────────────────────────────────────────────────
# SMC via IOKit  (sin sudo, sin CLI externo)
# ──────────────────────────────────────────────────────────────────────────────
_IOKIT_OK = False

try:
    _iokit = ctypes.cdll.LoadLibrary(ctypes.util.find_library("IOKit"))
    _iokit.IOServiceGetMatchingService.restype  = ctypes.c_uint32
    _iokit.IOServiceGetMatchingService.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
    _iokit.IOServiceMatching.restype            = ctypes.c_void_p
    _iokit.IOServiceMatching.argtypes           = [ctypes.c_char_p]
    _iokit.IOServiceOpen.restype                = ctypes.c_int32
    _iokit.IOServiceOpen.argtypes               = [
        ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
    ]
    _iokit.IOServiceClose.argtypes              = [ctypes.c_uint32]
    _iokit.IOObjectRelease.argtypes             = [ctypes.c_uint32]
    _iokit.IOConnectCallStructMethod.restype    = ctypes.c_int32
    _iokit.IOConnectCallStructMethod.argtypes   = [
        ctypes.c_uint32, ctypes.c_uint32,
        ctypes.c_void_p, ctypes.c_size_t,
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t),
    ]
    _libkernel     = ctypes.CDLL("/usr/lib/system/libsystem_kernel.dylib")
    _mach_task_self = ctypes.c_uint32.in_dll(_libkernel, "mach_task_self_").value
    _IOKIT_OK = True
except Exception as _e:
    log.debug("IOKit no disponible: %s", _e)


if _IOKIT_OK:
    # Estructuras SMC — layout exacto del struct C (80 bytes total)
    # Usamos _pack_ = 1 en todas para control total del layout y añadimos
    # bytes de padding explícitos que coinciden con la alineación de C.

    class _SMCVers(ctypes.Structure):
        _pack_ = 1
        _fields_ = [
            ("major",    ctypes.c_uint8),
            ("minor",    ctypes.c_uint8),
            ("build",    ctypes.c_uint8),
            ("reserved", ctypes.c_uint8),
            ("release",  ctypes.c_uint16),
        ]   # 6 bytes

    class _SMCPLimit(ctypes.Structure):
        _pack_ = 1
        _fields_ = [
            ("version",   ctypes.c_uint16),
            ("length",    ctypes.c_uint16),
            ("cpuPLimit", ctypes.c_uint32),
            ("gpuPLimit", ctypes.c_uint32),
            ("memPLimit", ctypes.c_uint32),
        ]   # 16 bytes

    class _SMCKeyInfo(ctypes.Structure):
        _pack_ = 1
        _fields_ = [
            ("dataSize",       ctypes.c_uint32),
            ("dataType",       ctypes.c_uint32),
            ("dataAttributes", ctypes.c_uint8),
        ]   # 9 bytes

    class _SMCKeyData(ctypes.Structure):
        # Offsets:
        #  0: key(4)  4: vers(6)  [+2pad]  12: pLimitData(16)
        # 28: keyInfo(9) [+3pad]  40: result(1) status(1) data8(1) [+1pad]
        # 44: data32(4)  48: bytes(32)   total = 80
        _pack_ = 1
        _fields_ = [
            ("key",        ctypes.c_uint32),
            ("vers",       _SMCVers),
            ("_pad0",      ctypes.c_uint8 * 2),
            ("pLimitData", _SMCPLimit),
            ("keyInfo",    _SMCKeyInfo),
            ("_pad1",      ctypes.c_uint8 * 3),
            ("result",     ctypes.c_uint8),
            ("status",     ctypes.c_uint8),
            ("data8",      ctypes.c_uint8),
            ("_pad2",      ctypes.c_uint8),
            ("data32",     ctypes.c_uint32),
            ("bytes",      ctypes.c_uint8 * 32),
        ]   # 80 bytes

    assert ctypes.sizeof(_SMCKeyData) == 80, (
        f"Layout _SMCKeyData incorrecto: {ctypes.sizeof(_SMCKeyData)} bytes (esperado 80)"
    )

    _KERN_INDEX_SMC = 2
    _SMC_CMD_INFO   = 9
    _SMC_CMD_READ   = 5


class SMCReader:
    """
    Lee claves SMC directamente via IOKit (sin sudo, sin CLI externo).

    Claves útiles en Intel Mac:
        TC0P  — CPU proximity temp (°C, sp78)
        TCXC  — CPU package temp    (°C, sp78) — disponible en algunos modelos
        Th0H  — CPU heatsink temp   (°C, sp78)
        Tg0P  — GPU proximity temp  (°C, sp78)
        TG0P  — GPU proximity temp  (°C, sp78, alternativa)
        F0Ac  — Fan 0 velocidad actual (RPM, fpe2)
        F1Ac  — Fan 1 / bomba (RPM, fpe2)
        FNum  — número de fans (ui8)
        VC0C  — CPU core 0 VID voltage (V, fp2E)
        VCTR  — CPU VID target voltage (V, fp2E)
    """

    def __init__(self):
        if not _IOKIT_OK:
            raise RuntimeError("IOKit no disponible en este sistema")
        self._conn = ctypes.c_uint32(0)
        svc = _iokit.IOServiceGetMatchingService(0, _iokit.IOServiceMatching(b"AppleSMC"))
        if not svc:
            raise RuntimeError("Servicio AppleSMC no encontrado")
        ret = _iokit.IOServiceOpen(svc, _mach_task_self, 0, ctypes.byref(self._conn))
        _iokit.IOObjectRelease(svc)
        if ret:
            raise RuntimeError(f"IOServiceOpen error {ret:#010x}")

    # ── bajo nivel ────────────────────────────────────────────────────────────

    def _call(self, inp: "_SMCKeyData") -> "_SMCKeyData":
        out      = _SMCKeyData()
        out_size = ctypes.c_size_t(ctypes.sizeof(_SMCKeyData))
        ret = _iokit.IOConnectCallStructMethod(
            self._conn, _KERN_INDEX_SMC,
            ctypes.byref(inp), ctypes.sizeof(inp),
            ctypes.byref(out), ctypes.byref(out_size),
        )
        if ret:
            raise IOError(f"SMC call error {ret:#010x}")
        return out

    def read_raw(self, key: str) -> bytes:
        # 1) Obtener info (tamaño y tipo de dato)
        inp       = _SMCKeyData()
        inp.key   = _struct.unpack(">I", key.encode())[0]
        inp.data8 = _SMC_CMD_INFO
        info      = self._call(inp).keyInfo

        # 2) Leer bytes
        inp2              = _SMCKeyData()
        inp2.key          = inp.key
        inp2.keyInfo.dataSize = info.dataSize
        inp2.data8        = _SMC_CMD_READ
        out               = self._call(inp2)
        return bytes(out.bytes[: info.dataSize])

    # ── decodificadores ───────────────────────────────────────────────────────

    def read_temp(self, key: str) -> Optional[float]:
        """Temperatura en °C desde clave sp78 (signed 8.8 fixed-point)."""
        try:
            d = self.read_raw(key)
            # sp78: byte alto = parte entera, byte bajo = fracción /256
            return d[0] + d[1] / 256.0
        except Exception:
            return None

    def read_fan_rpm(self, idx: int = 0) -> Optional[float]:
        """Velocidad de fan en RPM desde clave F{idx}Ac (fpe2: unsigned 14.2 fixed-point)."""
        try:
            d = self.read_raw(f"F{idx}Ac")
            return ((d[0] << 8) | d[1]) / 4.0
        except Exception:
            return None

    def read_voltage(self, key: str = "VC0C") -> Optional[float]:
        """Voltaje CPU en V desde clave fp2E (unsigned 2.14 fixed-point)."""
        try:
            d = self.read_raw(key)
            return ((d[0] << 8) | d[1]) / 16384.0
        except Exception:
            return None

    def fan_count(self) -> int:
        try:
            return self.read_raw("FNum")[0]
        except Exception:
            return 0

    def close(self):
        if self._conn.value:
            _iokit.IOServiceClose(self._conn)
            self._conn = ctypes.c_uint32(0)

    def __enter__(self):  return self
    def __exit__(self, *_): self.close()


# ──────────────────────────────────────────────────────────────────────────────
# Hilo de fondo genérico para lecturas lentas
# ──────────────────────────────────────────────────────────────────────────────

class _BackgroundPoller(threading.Thread):
    """Ejecuta `fn()` cada `interval` segundos y guarda el resultado en caché."""

    def __init__(self, fn, interval: float, name: str = "poller"):
        super().__init__(daemon=True, name=name)
        self._fn       = fn
        self._interval = interval
        self._result   = None
        self._lock     = threading.Lock()
        self._stop_evt = threading.Event()

    @property
    def result(self):
        with self._lock:
            return self._result

    def run(self):
        while True:
            try:
                v = self._fn()
                if v is not None:
                    with self._lock:
                        self._result = v
            except Exception as e:
                log.debug("%s error: %s", self.name, e)
            if self._stop_evt.wait(timeout=self._interval):
                break

    def stop(self):
        self._stop_evt.set()


# ──────────────────────────────────────────────────────────────────────────────
# Lectura de sensores CPU
# ──────────────────────────────────────────────────────────────────────────────

def _cpu_temp_osx_cpu_temp() -> Optional[float]:
    """Lee temperatura CPU via 'osx-cpu-temp' (brew install osx-cpu-temp)."""
    try:
        r = subprocess.run(
            ["osx-cpu-temp"], capture_output=True, text=True, timeout=2
        )
        if r.returncode == 0:
            raw = r.stdout.strip().replace("°C", "").replace("C", "").strip()
            return float(raw)
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        pass
    return None


def _powermetrics_sample() -> Optional[dict]:
    """
    Ejecuta powermetrics una vez y devuelve el JSON parseado.
    Requiere: sudo visudo → NOPASSWD para /usr/bin/powermetrics
    """
    try:
        r = subprocess.run(
            [
                "sudo", "-n", "powermetrics",
                "--sample-count", "1",
                "--samplers", "cpu_power,gpu_power",
                "-f", "json",
                "-i", "500",
            ],
            capture_output=True, text=True, timeout=8,
        )
        if r.returncode == 0:
            text  = r.stdout.strip()
            start = text.find("{")
            if start != -1:
                return json.loads(text[start:])
    except Exception as e:
        log.debug("powermetrics: %s", e)
    return None


def _cpu_power_from_pm(pm: dict) -> Optional[float]:
    """Potencia del paquete CPU en W desde un sample de powermetrics."""
    try:
        proc     = pm.get("processor", {})
        pkgs     = proc.get("packages", [])
        elapsed  = pm.get("elapsed_ns", 0)
        if pkgs and elapsed > 0:
            joules = pkgs[0].get("package_joules")
            if joules is not None:
                return float(joules) / (elapsed / 1e9)
    except (KeyError, TypeError, ZeroDivisionError):
        pass
    return None


def _cpu_freq_from_pm(pm: dict) -> Optional[float]:
    """Frecuencia CPU media en MHz desde un sample de powermetrics."""
    try:
        proc  = pm.get("processor", {})
        pkgs  = proc.get("packages", [])
        if pkgs:
            cores = pkgs[0].get("cores", [])
            freqs = [c["effective_clock"] for c in cores if "effective_clock" in c]
            if freqs:
                return sum(freqs) / len(freqs)
    except (KeyError, TypeError):
        pass
    return None


def _cpu_voltage_from_pm(pm: dict) -> Optional[float]:
    """Voltaje VID del primer core en V desde un sample de powermetrics."""
    try:
        proc = pm.get("processor", {})
        pkgs = proc.get("packages", [])
        if pkgs:
            cores = pkgs[0].get("cores", [])
            if cores:
                v = cores[0].get("volt")
                if v and v > 0:
                    # powermetrics reporta en mV en Intel
                    return float(v) / 1000.0 if v > 10 else float(v)
    except (KeyError, TypeError):
        pass
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Lectura de sensores GPU (AMD discreta via ioreg)
# ──────────────────────────────────────────────────────────────────────────────

import re as _re


def _ioreg_accelerator() -> Optional[str]:
    try:
        r = subprocess.run(
            ["ioreg", "-r", "-c", "IOAccelerator", "-d", "5"],
            capture_output=True, text=True, timeout=5,
        )
        return r.stdout if r.returncode == 0 else None
    except Exception:
        return None


def _parse_perf_stat(ioreg_out: str, key: str) -> Optional[float]:
    """Extrae un número de PerformanceStatistics en la salida de ioreg."""
    m = _re.search(rf'"{_re.escape(key)}"=(-?[\d.]+)', ioreg_out)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return None


@dataclass
class _GPUStats:
    temp_c:    Optional[float] = None
    usage_pct: Optional[float] = None
    power_w:   Optional[float] = None
    clock_mhz: Optional[float] = None


def _sample_gpu() -> Optional[_GPUStats]:
    out = _ioreg_accelerator()
    if not out:
        return None
    s = _GPUStats()
    s.temp_c    = _parse_perf_stat(out, "Temperature(C)")
    s.usage_pct = (_parse_perf_stat(out, "GPU Activity(%)")
                   or _parse_perf_stat(out, "Device Utilization %"))
    s.power_w   = _parse_perf_stat(out, "Total Power(W)")
    s.clock_mhz = _parse_perf_stat(out, "Core Clock(MHz)")
    # Si no hay ningún valor válido, devolver None para no reemplazar caché buena
    if all(v is None for v in (s.temp_c, s.usage_pct, s.power_w, s.clock_mhz)):
        return None
    return s


# ──────────────────────────────────────────────────────────────────────────────
# Agregador de sensores
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class Sensors:
    cpu_temp_c:         float = 0.0
    cpu_hotspot_c:      float = 0.0
    cpu_usage_pct:      float = 0.0
    cpu_max_thread_pct: float = 0.0
    cpu_power_w:        float = 0.0
    cpu_clock_mhz:      float = 0.0
    cpu_voltage_v:      float = 1.0
    gpu_temp_c:         float = 0.0
    gpu_usage_pct:      float = 0.0
    gpu_power_w:        float = 0.0
    gpu_clock_mhz:      float = 0.0
    cpu_fan_rpm:        int   = 0
    pump_rpm:           int   = 0


class SensorCollector:
    """
    Recopila lecturas de todos los backends disponibles.
    Las fuentes lentas (powermetrics, ioreg) corren en hilos de fondo;
    SMC y psutil se leen en el hilo del loop principal (rápidos).
    """

    def __init__(self):
        self._smc: Optional[SMCReader] = None
        self._pm_bg:       Optional[_BackgroundPoller] = None
        self._gpu_bg:      Optional[_BackgroundPoller] = None
        self._cputemp_bg:  Optional[_BackgroundPoller] = None
        self._init()

    def _init(self):
        # SMC
        if _IOKIT_OK:
            try:
                self._smc = SMCReader()
                log.info("SMC: OK (IOKit directo, sin sudo)")
            except Exception as e:
                log.warning("SMC no disponible: %s", e)

        # powermetrics en hilo de fondo (1 s de refresh)
        self._pm_bg = _BackgroundPoller(
            _powermetrics_sample, interval=1.0, name="powermetrics"
        )
        self._pm_bg.start()

        # GPU ioreg en hilo de fondo (1 s de refresh)
        self._gpu_bg = _BackgroundPoller(
            _sample_gpu, interval=1.0, name="gpu-ioreg"
        )
        self._gpu_bg.start()

        # osx-cpu-temp como fallback de temperatura (si SMC falla)
        self._cputemp_bg = _BackgroundPoller(
            _cpu_temp_osx_cpu_temp, interval=1.0, name="osx-cpu-temp"
        )
        self._cputemp_bg.start()

        # Calentar psutil (primera llamada siempre devuelve 0.0)
        psutil.cpu_percent(percpu=True)

        log.info(
            "Sensor backends: SMC=%s  powermetrics=async  GPU-ioreg=async  psutil=OK",
            "OK" if self._smc else "no disponible",
        )

    def stop(self):
        for bg in (self._pm_bg, self._gpu_bg, self._cputemp_bg):
            if bg:
                bg.stop()
        if self._smc:
            self._smc.close()

    # ── lectura rápida (llamada cada 200 ms) ─────────────────────────────────

    def collect(self) -> Sensors:
        s = Sensors()

        # ── psutil: uso y frecuencia CPU ──────────────────────────────────────
        per_cpu         = psutil.cpu_percent(percpu=True)
        s.cpu_usage_pct      = sum(per_cpu) / len(per_cpu) if per_cpu else 0.0
        s.cpu_max_thread_pct = max(per_cpu) if per_cpu else 0.0

        freq = psutil.cpu_freq()
        if freq and freq.current:
            s.cpu_clock_mhz = float(freq.current)

        # ── SMC: temperatura, fans y voltaje ──────────────────────────────────
        if self._smc:
            # Temperatura CPU — probar varias claves en orden de preferencia
            for key in ("TC0P", "TCXC", "TC0C", "Th0H", "TC1C"):
                t = self._smc.read_temp(key)
                if t and t > 20.0:
                    s.cpu_temp_c = t
                    break

            # Hotspot — clave más "caliente" (package)
            for key in ("TCXC", "TC0P", "Th0H", "TC4C"):
                t = self._smc.read_temp(key)
                if t and t >= s.cpu_temp_c:
                    s.cpu_hotspot_c = t
                    break
            if s.cpu_hotspot_c == 0:
                s.cpu_hotspot_c = s.cpu_temp_c

            # Voltaje CPU (fp2E)
            for key in ("VC0C", "VCTR", "VP0R"):
                v = self._smc.read_voltage(key)
                if v and 0.5 < v < 2.0:
                    s.cpu_voltage_v = v
                    break

            # GPU temp por SMC (si está disponible)
            for key in ("Tg0P", "TG0P", "Tg0T", "TGDD", "TH0a"):
                t = self._smc.read_temp(key)
                if t and t > 20.0:
                    s.gpu_temp_c = t
                    break

            # Fans
            n_fans = self._smc.fan_count()
            if n_fans >= 1:
                rpm = self._smc.read_fan_rpm(0)
                if rpm is not None:
                    s.cpu_fan_rpm = int(rpm)
            if n_fans >= 2:
                rpm = self._smc.read_fan_rpm(1)
                if rpm is not None:
                    s.pump_rpm = int(rpm)

        # ── osx-cpu-temp fallback ────────────────────────────────────────────
        if s.cpu_temp_c == 0.0:
            t = self._cputemp_bg.result
            if t:
                s.cpu_temp_c    = t
                s.cpu_hotspot_c = t

        # ── powermetrics: potencia, frecuencia, voltaje ───────────────────────
        pm = self._pm_bg.result
        if pm:
            pw = _cpu_power_from_pm(pm)
            if pw is not None:
                s.cpu_power_w = pw

            freq_pm = _cpu_freq_from_pm(pm)
            if freq_pm:
                s.cpu_clock_mhz = freq_pm

            if s.cpu_voltage_v == 1.0:   # solo si SMC no lo leyó
                v_pm = _cpu_voltage_from_pm(pm)
                if v_pm and 0.5 < v_pm < 2.0:
                    s.cpu_voltage_v = v_pm

        # ── GPU ioreg ─────────────────────────────────────────────────────────
        gpu = self._gpu_bg.result
        if gpu:
            if gpu.temp_c is not None and s.gpu_temp_c == 0.0:
                s.gpu_temp_c = gpu.temp_c
            if gpu.usage_pct is not None:
                s.gpu_usage_pct = max(0.0, min(100.0, gpu.usage_pct))
            if gpu.power_w is not None:
                s.gpu_power_w = gpu.power_w
            if gpu.clock_mhz is not None:
                s.gpu_clock_mhz = gpu.clock_mhz

        return s


# ──────────────────────────────────────────────────────────────────────────────
# Protocolo — construcción del paquete de 65 bytes
# ──────────────────────────────────────────────────────────────────────────────

def _clamp(v: float, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(v)))


def build_packet(s: Sensors, counter: int = 0) -> bytes:
    """
    Construye el paquete HID de 65 bytes según el protocolo reverse-engineered.
    Todos los valores son directos (no escalados), con encodings específicos por campo.
    """
    buf = bytearray(65)

    # ── Header (4 bytes) ──────────────────────────────────────────────────────
    buf[0] = 0x00   # HID Report ID
    buf[1] = 0x00   # header fijo
    buf[2] = 0x01   # header fijo
    buf[3] = 0x02   # header fijo

    # ── Sensores principales (SVA[0..10]) ────────────────────────────────────
    buf[4]  = _clamp(s.cpu_temp_c,         0, 255)   # SVA[0]  CPU core temp (°C)
    buf[5]  = _clamp(s.gpu_usage_pct,      0, 100)   # SVA[1]  GPU usage (%)
    buf[6]  = _clamp(s.gpu_power_w,        0, 255)   # SVA[2]  GPU power (W)
    buf[7]  = _clamp(s.cpu_power_w,        0, 255)   # SVA[3]  CPU package power (W)
    buf[8]  = _clamp(s.cpu_hotspot_c,      0, 255)   # SVA[4]  CPU hotspot temp (°C)
    buf[9]  = _clamp(s.cpu_max_thread_pct, 0, 100)   # SVA[5]  CPU max thread (%)
    buf[10] = _clamp(s.gpu_clock_mhz / 10, 0, 255)  # SVA[6]  GPU clock (MHz÷10)
    buf[11] = _clamp(s.cpu_clock_mhz / 48, 0, 255)  # SVA[7]  CPU clock (MHz÷48)
    buf[12] = 0x01                                    # SVA[8]  constante
    buf[13] = _clamp(s.cpu_voltage_v * 100, 0, 255)  # SVA[9]  CPU VID (V×100)
    buf[14] = _clamp(s.gpu_temp_c,         0, 255)   # SVA[10] GPU temp (°C)

    # ── Reserved + constantes (SVA[11..16]) ──────────────────────────────────
    buf[15] = 0x00   # SVA[11]
    buf[16] = 0x00   # SVA[12]
    buf[17] = 0x00   # SVA[13]
    buf[18] = 0x00   # SVA[14]
    buf[19] = 0x0A   # SVA[15] — flag Celsius (10)
    buf[20] = 0x00   # SVA[16]

    # ── Uso total CPU (SVA[17]) ───────────────────────────────────────────────
    buf[21] = _clamp(s.cpu_usage_pct, 0, 100)

    # ── Fan RPM — encoding: hi = rpm÷100, lo = rpm%100 (SVA[18..21]) ─────────
    fan  = max(0, min(25500, s.cpu_fan_rpm))
    buf[22] = fan // 100    # SVA[18]
    buf[23] = fan % 100     # SVA[19]

    pump = max(0, min(25500, s.pump_rpm))
    buf[24] = pump // 100   # SVA[20]
    buf[25] = pump % 100    # SVA[21]

    # ── Display config — umbrales de color y constantes (SVA[22..30]) ─────────
    # Valores por defecto observados en capturas reales de PC Monitor All.exe
    buf[26] = 0x14   # SVA[22] = 20  — umbral CPU Temp color
    buf[27] = 0x1A   # SVA[23] = 26  — umbral CPU Usage color
    buf[28] = 0x03   # SVA[24] =  3  — umbral CPU Power color
    buf[29] = 0x0E   # SVA[25] = 14  — umbral CPU Freq color
    buf[30] = 0x12   # SVA[26] = 18  — umbral CPU Voltage color
    buf[31] = 0x20   # SVA[27] = 32  — umbral GPU
    buf[32] = counter & 0xFF  # SVA[28] — contador incremental (wraps en 255)
    buf[33] = 0x06   # SVA[29] = 6   — constante
    buf[34] = 0x19   # SVA[30] = 25  — constante

    # buf[35..64] = 0x00  (ya inicializado)
    return bytes(buf)


# ──────────────────────────────────────────────────────────────────────────────
# HID device
# ──────────────────────────────────────────────────────────────────────────────

class HIDDevice:
    def __init__(self):
        self._dev: Optional[hid.device] = None

    def connect(self, retries: int = 3, delay: float = 1.0) -> bool:
        for attempt in range(retries):
            try:
                dev = hid.device()
                dev.open(VENDOR_ID, PRODUCT_ID)
                dev.set_nonblocking(1)
                self._dev = dev
                name = dev.get_product_string() or "AIO Display"
                log.info("Display conectado: %s (VID=%04X PID=%04X)", name, VENDOR_ID, PRODUCT_ID)
                return True
            except Exception as e:
                log.debug("Intento %d/%d: %s", attempt + 1, retries, e)
                if attempt < retries - 1:
                    time.sleep(delay)
        return False

    def send(self, packet: bytes) -> bool:
        if self._dev is None:
            return False
        try:
            n = self._dev.write(packet)
            if n < 0:
                log.warning("HID write devolvió %d — reconectando", n)
                self.close()
                return False
            log.debug("TX %d bytes: %s", n, packet.hex(" "))
            return True
        except Exception as e:
            log.warning("Error HID write (%s) — reconectando", e)
            self.close()
            return False

    def close(self):
        if self._dev is not None:
            try:
                self._dev.close()
            except Exception:
                pass
            self._dev = None

    @property
    def connected(self) -> bool:
        return self._dev is not None


# ──────────────────────────────────────────────────────────────────────────────
# Driver principal
# ──────────────────────────────────────────────────────────────────────────────

class Driver:
    def __init__(self, dry_run: bool = False, verbose: bool = False):
        self._dry_run   = dry_run
        self._verbose   = verbose
        self._running   = True
        self._counter   = 0
        self._sensors   = SensorCollector()
        self._hid       = HIDDevice() if not dry_run else None
        self._last_reconn = 0.0

    def stop(self):
        log.info("Cerrando driver...")
        self._running = False

    def run(self):
        log.info(
            "Driver iniciado (VID=%04X PID=%04X, intervalo=%dms, dry_run=%s)",
            VENDOR_ID, PRODUCT_ID, int(INTERVAL * 1000), self._dry_run,
        )

        if not self._dry_run:
            if not self._hid.connect():
                log.error("Display no encontrado. Verifica la conexión USB.")
                self._sensors.stop()
                sys.exit(1)

        try:
            while self._running:
                t0 = time.monotonic()

                # Reconexión automática
                if self._hid and not self._hid.connected:
                    now = time.monotonic()
                    if now - self._last_reconn >= 5.0:
                        self._last_reconn = now
                        if self._hid.connect(retries=1):
                            log.info("Display reconectado.")
                        else:
                            log.warning("Display no disponible, reintentando en 5 s...")

                # Recopilar sensores
                s = self._sensors.collect()

                if self._verbose:
                    log.info(
                        "CPU: %.0f°C (hotspot %.0f°C) uso %.0f%% (max %.0f%%) "
                        "%.0fMHz %.1fW %.2fV | "
                        "GPU: %.0f°C uso %.0f%% %.0fMHz %.1fW | "
                        "Fan: %d RPM  Pump: %d RPM",
                        s.cpu_temp_c, s.cpu_hotspot_c,
                        s.cpu_usage_pct, s.cpu_max_thread_pct,
                        s.cpu_clock_mhz, s.cpu_power_w, s.cpu_voltage_v,
                        s.gpu_temp_c, s.gpu_usage_pct, s.gpu_clock_mhz, s.gpu_power_w,
                        s.cpu_fan_rpm, s.pump_rpm,
                    )

                packet = build_packet(s, self._counter)
                self._counter = (self._counter + 1) & 0xFF

                if self._dry_run:
                    log.info("DRY-RUN packet: %s", packet.hex(" "))
                elif self._hid.connected:
                    self._hid.send(packet)

                elapsed   = time.monotonic() - t0
                sleep_for = max(0.0, INTERVAL - elapsed)
                time.sleep(sleep_for)

        except KeyboardInterrupt:
            pass
        finally:
            log.info("Cerrando...")
            self._sensors.stop()
            if self._hid:
                self._hid.close()


# ──────────────────────────────────────────────────────────────────────────────
# Modo test — envía un paquete de prueba y verifica el display
# ──────────────────────────────────────────────────────────────────────────────

def run_test():
    """Envía un paquete de prueba al display para verificar conexión y protocolo."""
    dev = HIDDevice()
    if not dev.connect():
        print("Error: display no encontrado (VID:0x5131 PID:0x2007)")
        sys.exit(1)

    # Valores de prueba similares a los paquetes capturados reales
    s = Sensors(
        cpu_temp_c=42, cpu_hotspot_c=55, cpu_usage_pct=15, cpu_max_thread_pct=30,
        cpu_power_w=25, cpu_clock_mhz=4176, cpu_voltage_v=1.0,
        gpu_temp_c=34, gpu_usage_pct=0, gpu_power_w=0, gpu_clock_mhz=520,
        cpu_fan_rpm=1200, pump_rpm=0,
    )
    packet = build_packet(s, counter=0)
    dev.send(packet)
    print("Paquete de prueba enviado correctamente.")
    print(f"  Packet ({len(packet)} bytes): {packet.hex(' ')}")
    dev.close()


# ──────────────────────────────────────────────────────────────────────────────
# Modo dump — muestra lecturas de sensores sin enviar al display
# ──────────────────────────────────────────────────────────────────────────────

def run_dump():
    """Muestra lecturas de sensores cada segundo durante 30 s (Ctrl+C para salir)."""
    sc = SensorCollector()
    print("Leyendo sensores (Ctrl+C para salir)...\n")
    print(f"{'CPU Temp':>8} {'Hotspot':>7} {'Uso%':>5} {'MaxT%':>5} "
          f"{'MHz':>6} {'W':>5} {'V':>5} | "
          f"{'GPU T':>5} {'Uso%':>5} {'MHz':>6} {'W':>5} | "
          f"{'Fan':>5} {'Pump':>5}")
    print("-" * 95)
    try:
        while True:
            s = sc.collect()
            print(
                f"{s.cpu_temp_c:>7.0f}°  {s.cpu_hotspot_c:>6.0f}°  "
                f"{s.cpu_usage_pct:>5.1f}  {s.cpu_max_thread_pct:>5.1f}  "
                f"{s.cpu_clock_mhz:>6.0f}  {s.cpu_power_w:>4.1f}W  {s.cpu_voltage_v:.2f}V | "
                f"{s.gpu_temp_c:>5.0f}°  {s.gpu_usage_pct:>5.1f}  "
                f"{s.gpu_clock_mhz:>6.0f}  {s.gpu_power_w:>4.1f}W | "
                f"{s.cpu_fan_rpm:>5}  {s.pump_rpm:>5}"
            )
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        sc.stop()


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description="AIO Display Driver para macOS — VID:0x5131 PID:0x2007"
    )
    p.add_argument(
        "--test", action="store_true",
        help="Enviar un paquete de prueba y salir",
    )
    p.add_argument(
        "--dump", action="store_true",
        help="Mostrar lecturas de sensores sin enviar al display",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Ejecutar loop completo pero sin abrir el HID (imprime paquetes en log)",
    )
    p.add_argument(
        "--verbose", "-v", action="store_true",
        help="Loguear valores de sensores en cada ciclo",
    )
    p.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = p.parse_args()

    logging.getLogger().setLevel(getattr(logging, args.log_level))
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.test:
        run_test()
        return
    if args.dump:
        run_dump()
        return

    driver = Driver(dry_run=args.dry_run, verbose=args.verbose)
    signal.signal(signal.SIGTERM, lambda *_: driver.stop())
    signal.signal(signal.SIGINT,  lambda *_: driver.stop())
    driver.run()


if __name__ == "__main__":
    main()
