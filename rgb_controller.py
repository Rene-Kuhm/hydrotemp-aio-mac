#!/usr/bin/env python3
"""
rgb_controller.py — Control RGB para Gigabyte ITE8297 en macOS
Hardware: VID 0x048D / PID 0x5702

Protocolo: OpenRGB ITE8297
  - HID Feature Reports, report_id = 0xCC
  - Zonas 0x20-0x27 (8 zonas independientes)
  - Effect 0x01 = Estático
  - Apply: zona 0x28

Uso:
  rgb_controller.py                     # Aplica color guardado (para LaunchAgent)
  rgb_controller.py --color '#F56BED'   # Aplica color y guarda
  rgb_controller.py --gui               # Abre selector de color nativo de macOS
  rgb_controller.py --off               # Apaga RGB (negro)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, Tuple

import hid

# ──────────────────────────────────────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("aio-rgb")

# ──────────────────────────────────────────────────────────────────────────────
# Constantes
# ──────────────────────────────────────────────────────────────────────────────
VENDOR_ID  = 0x048D
PRODUCT_ID = 0x5702
REPORT_ID  = 0xCC
ZONES      = list(range(0x20, 0x28))   # 8 zonas
APPLY_ZONE = 0x28

DEFAULT_COLOR = (0xF5, 0x6B, 0xED)  # #F56BED — violeta/rosa

CONFIG_DIR  = Path.home() / ".config" / "aio-rgb"
CONFIG_FILE = CONFIG_DIR / "color.json"

# ──────────────────────────────────────────────────────────────────────────────
# Configuración persistente
# ──────────────────────────────────────────────────────────────────────────────

def load_color() -> Tuple[int, int, int]:
    """Lee el color guardado. Si no existe, devuelve DEFAULT_COLOR."""
    try:
        data = json.loads(CONFIG_FILE.read_text())
        hex_color = data["color"].lstrip("#")
        r = int(hex_color[0:2], 16)
        g = int(hex_color[2:4], 16)
        b = int(hex_color[4:6], 16)
        return (r, g, b)
    except Exception:
        return DEFAULT_COLOR


def save_color(r: int, g: int, b: int):
    """Guarda el color en el archivo de configuración."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    data = {"color": f"#{r:02X}{g:02X}{b:02X}"}
    CONFIG_FILE.write_text(json.dumps(data, indent=2) + "\n")
    log.info("Color guardado: %s", data["color"])


# ──────────────────────────────────────────────────────────────────────────────
# Control HID — ITE8297
# ──────────────────────────────────────────────────────────────────────────────

def set_rgb(r: int, g: int, b: int, retries: int = 3) -> bool:
    """
    Configura todas las zonas de la motherboard al color (r, g, b).
    Protocolo ITE8297: feature report 0xCC, effect=0x01 (estático).
    """
    for attempt in range(retries):
        try:
            dev = hid.Device(VENDOR_ID, PRODUCT_ID)
            log.info("ITE8297 conectado: %s", dev.product)

            # Configurar cada zona
            for zone_id in ZONES:
                pkt = bytearray(64)
                pkt[0] = REPORT_ID     # report ID
                pkt[1] = zone_id       # zona (0x20 - 0x27)
                pkt[2] = 0x01          # efecto: estático
                pkt[3] = 0x00          # velocidad (N/A)
                pkt[4] = 0x64          # brillo 100%
                pkt[5] = 0x00          # reservado
                pkt[6] = 0x01          # color_count = 1
                pkt[7] = r             # Red
                pkt[8] = g             # Green
                pkt[9] = b             # Blue
                dev.send_feature_report(bytes(pkt))
                time.sleep(0.02)

            # Apply
            apply_pkt = bytearray(64)
            apply_pkt[0] = REPORT_ID
            apply_pkt[1] = APPLY_ZONE
            dev.send_feature_report(bytes(apply_pkt))

            dev.close()
            log.info("Color aplicado: #%02X%02X%02X", r, g, b)
            return True

        except Exception as e:
            log.warning("Intento %d/%d: %s", attempt + 1, retries, e)
            if attempt < retries - 1:
                time.sleep(2.0)

    log.error("No se pudo conectar al ITE8297 después de %d intentos", retries)
    return False


# ──────────────────────────────────────────────────────────────────────────────
# Selector de color nativo de macOS (via osascript)
# ──────────────────────────────────────────────────────────────────────────────

def pick_color_gui(current: Tuple[int, int, int]) -> Optional[Tuple[int, int, int]]:
    """
    Abre el selector de color nativo de macOS.
    Devuelve (r, g, b) o None si el usuario cancela.
    """
    # macOS 'choose color' usa valores 0-65535
    r16 = current[0] * 257
    g16 = current[1] * 257
    b16 = current[2] * 257

    script = f'choose color default color {{{r16}, {g16}, {b16}}}'

    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            log.info("Selector de color cancelado")
            return None

        # Resultado: "R, G, B" (valores 0-65535)
        parts = result.stdout.strip().split(", ")
        r = int(parts[0]) // 257
        g = int(parts[1]) // 257
        b = int(parts[2]) // 257
        return (r, g, b)

    except subprocess.TimeoutExpired:
        log.warning("Timeout en selector de color")
        return None
    except Exception as e:
        log.error("Error en selector de color: %s", e)
        return None


# ──────────────────────────────────────────────────────────────────────────────
# Modo interactivo — loop con selector de color
# ──────────────────────────────────────────────────────────────────────────────

def run_gui():
    """Modo interactivo: abre el selector de color en loop."""
    print("=== AIO RGB Controller ===")
    print("Se abrirá el selector de color de macOS.")
    print("Selecciona un color y haz clic en OK para aplicarlo.")
    print("Cancela el diálogo para salir.\n")

    current = load_color()

    while True:
        chosen = pick_color_gui(current)
        if chosen is None:
            print("Saliendo.")
            break

        r, g, b = chosen
        print(f"\nColor seleccionado: #{r:02X}{g:02X}{b:02X}")
        print("Aplicando...")

        if set_rgb(r, g, b):
            save_color(r, g, b)
            current = chosen
            print(f"Color #{r:02X}{g:02X}{b:02X} aplicado y guardado.\n")
            print("Abriendo selector de nuevo (cancela para salir)...")
        else:
            print("Error aplicando color. Reintentando...\n")


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def parse_hex_color(color_str: str) -> Tuple[int, int, int]:
    """Parsea '#RRGGBB' o 'RRGGBB' a tupla (r, g, b)."""
    hex_color = color_str.lstrip("#")
    if len(hex_color) != 6:
        raise ValueError(f"Color inválido: {color_str} (usar formato #RRGGBB)")
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)
    return (r, g, b)


def main():
    p = argparse.ArgumentParser(
        description="AIO RGB Controller — Gigabyte ITE8297 (VID:048D PID:5702)"
    )
    p.add_argument(
        "--color", "-c", type=str, metavar="#RRGGBB",
        help="Aplicar color hex (ej: '#F56BED')",
    )
    p.add_argument(
        "--gui", "-g", action="store_true",
        help="Abrir selector de color nativo de macOS",
    )
    p.add_argument(
        "--off", action="store_true",
        help="Apagar RGB (negro)",
    )
    p.add_argument(
        "--save-only", action="store_true",
        help="Solo guardar el color sin aplicar",
    )
    p.add_argument(
        "--verbose", "-v", action="store_true",
        help="Logging detallado",
    )
    args = p.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Modo GUI interactivo
    if args.gui:
        run_gui()
        return

    # Apagar RGB
    if args.off:
        r, g, b = 0, 0, 0
        set_rgb(r, g, b)
        save_color(r, g, b)
        return

    # Color específico
    if args.color:
        try:
            r, g, b = parse_hex_color(args.color)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

        if args.save_only:
            save_color(r, g, b)
        else:
            if set_rgb(r, g, b):
                save_color(r, g, b)
            else:
                sys.exit(1)
        return

    # Sin argumentos: aplicar color guardado (modo LaunchAgent)
    r, g, b = load_color()
    log.info("Aplicando color guardado: #%02X%02X%02X", r, g, b)
    if not set_rgb(r, g, b):
        sys.exit(1)


if __name__ == "__main__":
    main()
