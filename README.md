# hydrotemp-aio-mac

Driver macOS para el display AIO integrado en gabinetes **HydroTemp / PC Monitor All**,
reverse-engineered a partir del software oficial de Windows.

> **Hardware:** Display USB — VID `0x5131` / PID `0x2007` (FBB)
> **Sistema analizado:** Windows 11 Pro + PC Monitor All.exe (.NET 4.x / CyUSB)
> **Driver macOS:** Python 3, sin drivers adicionales, compatible Intel Mac y Hackintosh

---

## ¿Qué hace este proyecto?

1. **Ingeniería inversa completa** del protocolo HID que usa `PC Monitor All.exe` para enviar
   métricas de hardware al display integrado en el gabinete.
2. **Driver macOS nativo** que lee sensores reales (CPU, GPU, fans) y los envía al display
   cada 200 ms usando el mismo protocolo.
3. **LaunchAgent** para autoarranque en login sin intervención manual.

---

## Estructura del repositorio

```
hydrotemp-aio-mac/
├── monitor_macos.py                        ← driver principal
├── launchagent/
│   └── com.rene.aio-display.plist          ← autoarranque macOS
├── docs/
│   └── AIO-Display-Protocol-Analysis.md   ← protocolo reverse-engineered (completo)
└── README.md
```

---

## Protocolo en síntesis

El display recibe paquetes HID Output Report de **65 bytes** cada 200 ms:

```
[0]     = 0x00        HID Report ID
[1..3]  = 00 01 02    header fijo
[4]     = CPU temp (°C, directo)
[5]     = GPU usage (%, directo)
[6]     = GPU power (W, directo)
[7]     = CPU package power (W, directo)
[8]     = CPU hotspot/package temp (°C, directo)
[9]     = CPU max thread usage (%, directo)
[10]    = GPU clock MHz ÷ 10
[11]    = CPU clock MHz ÷ 48
[12]    = 0x01  (constante)
[13]    = CPU VID voltage × 100  (ej. 1.05V → 105)
[14]    = GPU temp (°C, directo)
[15..18]= 0x00  (reservados)
[19]    = 0x0A  (flag Celsius)
[20]    = 0x00
[21]    = CPU total usage promedio (%)
[22]    = CPU fan RPM ÷ 100  (byte alto)
[23]    = CPU fan RPM % 100  (byte bajo)
[24]    = Pump RPM ÷ 100
[25]    = Pump RPM % 100
[26..34]= config display (umbrales de color + contador incremental)
[35..64]= 0x00  (padding)
```

El análisis completo con todas las capturas reales, decodificaciones y
el código de referencia está en [`docs/AIO-Display-Protocol-Analysis.md`](docs/AIO-Display-Protocol-Analysis.md).

---

## Instalación

### 1. Dependencias

```bash
# Librería HID nativa
brew install hidapi

# Paquetes Python
pip3 install hid psutil

# Temperatura CPU sin sudo (recomendado)
brew install osx-cpu-temp
```

### 2. Potencia CPU — powermetrics sin contraseña (opcional pero recomendado)

```bash
sudo visudo
```

Añadir al final (reemplaza `<usuario>` con tu usuario de macOS):

```
<usuario> ALL=(ALL) NOPASSWD: /usr/bin/powermetrics
```

### 3. Probar que el display responde

```bash
python3 monitor_macos.py --test
```

Deberías ver datos en el display y en consola:

```
Paquete de prueba enviado correctamente.
  Packet (65 bytes): 00 00 01 02 2a 00 00 19 37 1e 34 57 01 69 22 ...
```

---

## Uso

```bash
# Loop continuo (driver normal)
python3 monitor_macos.py

# Con log de sensores cada ciclo
python3 monitor_macos.py --verbose

# Solo ver lecturas de sensores (sin enviar al display)
python3 monitor_macos.py --dump

# Modo debug sin abrir HID
python3 monitor_macos.py --dry-run --verbose
```

---

## Autoarranque (LaunchAgent)

```bash
# Copiar el plist a tu carpeta de LaunchAgents
cp launchagent/com.rene.aio-display.plist ~/Library/LaunchAgents/

# Activar (arranca ahora y en cada login)
launchctl load ~/Library/LaunchAgents/com.rene.aio-display.plist

# Verificar estado
launchctl list | grep aio-display

# Ver logs
tail -f /tmp/aio-display.log

# Desactivar
launchctl unload ~/Library/LaunchAgents/com.rene.aio-display.plist
```

> Si usas un virtualenv, edita el plist y cambia `/usr/bin/python3`
> por la ruta del Python de tu entorno (ej. `/Users/rene/monitor-env/bin/python3`).

---

## Backends de sensores

| Sensor | Backend | Requiere |
|---|---|---|
| CPU temp | SMC IOKit (directo) | nada — incluido en el driver |
| CPU temp (fallback) | `osx-cpu-temp` CLI | `brew install osx-cpu-temp` |
| CPU uso % | `psutil` | `pip3 install psutil` |
| CPU frecuencia | `psutil` + `sysctl` | nada |
| CPU potencia | `powermetrics` (hilo fondo) | `sudo -n` sin contraseña |
| CPU voltaje VID | SMC IOKit (VC0C) | nada |
| Fan / pump RPM | SMC IOKit (F0Ac, F1Ac) | nada |
| GPU temp | SMC IOKit (Tg0P) + ioreg | nada |
| GPU uso % | `ioreg IOAccelerator` (hilo fondo) | nada |
| GPU potencia | `ioreg IOAccelerator` | nada |
| GPU frecuencia | `ioreg IOAccelerator` | nada |

Las fuentes lentas (`powermetrics`, `ioreg`) corren en hilos de fondo con caché de 1 s.
El loop principal a 200 ms nunca bloquea.

---

## Arquitectura del driver

```
Driver (200 ms loop)
├── SensorCollector
│   ├── SMCReader (IOKit ctypes) ← CPU/GPU temp, fans, voltaje — síncrono, rápido
│   ├── psutil                   ← CPU uso y frecuencia — síncrono, rápido
│   ├── BackgroundPoller[powermetrics] ← potencia/freq CPU — hilo fondo, 1 s
│   ├── BackgroundPoller[ioreg GPU]    ← stats GPU AMD — hilo fondo, 1 s
│   └── BackgroundPoller[osx-cpu-temp] ← fallback temp — hilo fondo, 1 s
├── build_packet(Sensors, counter) → bytes[65]  ← protocolo exacto
└── HIDDevice.send(packet)         ← hid.device().write()
```

---

## Hardware de referencia (análisis original)

| Componente | Modelo |
|---|---|
| CPU | Intel Core i7-14700F |
| GPU | AMD Radeon RX 6600 XT |
| Display AIO | VID 0x5131 / PID 0x2007 (FBB) |
| OS análisis | Windows 11 Pro 10.0.26200 |
| App analizada | PC Monitor All.exe (.NET 4.x, Cypress CyUSB) |

---

## Paquetes reales capturados (verificación)

```
# Sistema recién arrancado, GPU caliente:
00 00 01 02  21 00 00 3D 40 1E 34 57 01 64 21 00 00 00 00 0A 00 06 0B 3B 0B 3B 14 1A 03 0E 12 19 1C 06 19  [00×28]
# CPU:33°C hotspot:64°C uso:6% max:30% 4176MHz 61W 1.00V | GPU:33°C 0% 340MHz | Fan:1159RPM

# Sistema en idle estable:
00 00 01 02  1E 00 00 19 2A 46 23 5B 01 64 22 00 00 00 00 0A 00 1E 0C 07 0C 07 14 1A 03 0E 12 20 16 06 19  [00×28]
# CPU:30°C hotspot:42°C uso:30% max:70% 4368MHz 25W 1.00V | GPU:34°C 0% 350MHz | Fan:1207RPM
```

---

## Créditos

- Protocolo: ingeniería inversa vía reflexión .NET IL + captura HID en tiempo real — 2026-03-14
- Driver macOS: Claude Code (Anthropic) + Rene Kuhm
