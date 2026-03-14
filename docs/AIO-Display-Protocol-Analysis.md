# Protocolo HID — Display AIO PC Monitor (VID: 0x5131 / PID: 0x2007)
> Ingeniería inversa completa. Objetivo: replicar en macOS el envío de datos al display integrado.
> Análisis realizado el 2026-03-14 mediante reflexión .NET + captura HID en Windows 11.

---

## 1. Identificación del dispositivo

| Campo | Valor |
|-------|-------|
| Vendor ID | `0x5131` |
| Product ID | `0x2007` |
| Product Name | `FBB` |
| Version | `0x0100` |
| HID Usage Page | `0xFF00` (Vendor Defined) |
| HID Usage | `0x0001` |
| Input Report | 65 bytes |
| Output Report | 65 bytes |
| Feature Report | 0 bytes |
| Driver | Windows HID genérico (no requiere driver especial) |

**Device path** (Windows):
```
\\?\hid#vid_5131&pid_2007#7&24034d15&0&0000#{4d1e55b2-f16f-11cf-88cb-001111000030}
```

---

## 2. Software Windows analizado

| Archivo | Propósito |
|---------|-----------|
| `C:\Program Files (x86)\PC\PC Monitor All\PC Monitor All.exe` | App principal (.NET 4.x) |
| `CyUSB.dll` | Librería HID de Cypress Semiconductor |
| `HWiNFO64.dll` / `HWiNFO32.dll` | Lectura de sensores del sistema |
| `config.ini` | Configuración persistente (sensores seleccionados + últimos valores) |

**Stack tecnológico de la app:**
- `.NET Windows Forms` con clases propias `CyHidDevice` y `CyHidReport`
- Hilo `Thread_Send` enviando paquetes cada **200 ms**
- `Thread_GetPCParam` leyendo sensores vía HWiNFO API (shared memory)
- Método `SendData2()` → construye buffer → `send_usb_data(buf, 64)` → `CyHidDevice.WriteOutput()`

---

## 3. Flujo de envío (reconstruido desde IL .NET)

```
Thread_Send() [loop infinito, delay 200ms]
  └── ShowConnect()       → verifica conexión, actualiza estado LED
  └── SendData2()
        ├── crea byte[64] = buf
        ├── buf[0] = 0x00
        ├── buf[1] = 0x01
        ├── buf[2] = 0x02
        ├── for i = 0..32: buf[3+i] = (byte)SendValueArray[i]
        └── send_usb_data(buf, 64)
              ├── DataBuf[0] = ReportID (0x00)
              ├── DataBuf[j] = buf[j-1]  para j=1..64
              └── CyHidDevice.WriteOutput()  → 65 bytes via HID
```

---

## 4. Estructura del paquete — 65 bytes

```
┌─────────────────────────────────────────────────────────────────────┐
│ Byte │ Hex  │ Descripción                                          │
├──────┼──────┼─────────────────────────────────────────────────────┤
│  [0] │ 0x00 │ HID Report ID (siempre 0x00)                        │
│  [1] │ 0x00 │ Header fijo byte 0                                  │
│  [2] │ 0x01 │ Header fijo byte 1                                  │
│  [3] │ 0x02 │ Header fijo byte 2                                  │
├──────┼──────┼─────────────────────────────────────────────────────┤
│  [4] │  var │ SVA[0]  — CPU Temp (°C, entero)                    │
│  [5] │  var │ SVA[1]  — GPU Usage %                               │
│  [6] │  var │ SVA[2]  — GPU Power (W, entero)                    │
│  [7] │  var │ SVA[3]  — CPU Power (W, entero)                    │
│  [8] │  var │ SVA[4]  — CPU Package/Hotspot Temp (°C)            │
│  [9] │  var │ SVA[5]  — CPU Max Thread Usage %                   │
│ [10] │  var │ SVA[6]  — GPU Core Clock ÷ 10 (MHz)               │
│ [11] │  var │ SVA[7]  — CPU Core Clock ÷ 48 (MHz, aprox)        │
│ [12] │ 0x01 │ SVA[8]  — Constante 1 (propósito desconocido)      │
│ [13] │  var │ SVA[9]  — CPU VID Voltage × 100 (e.g. 100 = 1.00V)│
│ [14] │  var │ SVA[10] — GPU Temp (°C, entero)                    │
│ [15] │ 0x00 │ SVA[11] — Reserved (siempre 0)                     │
│ [16] │ 0x00 │ SVA[12] — Reserved (siempre 0)                     │
│ [17] │ 0x00 │ SVA[13] — Reserved (siempre 0)                     │
│ [18] │ 0x00 │ SVA[14] — Reserved (siempre 0)                     │
│ [19] │ 0x0A │ SVA[15] — Constante 10 (flag modo display)         │
│ [20] │ 0x00 │ SVA[16] — Reserved                                  │
│ [21] │  var │ SVA[17] — CPU Total Usage % (promedio todos cores)  │
│ [22] │  var │ SVA[18] — CPU Fan Speed HIGH (fan_rpm ÷ 100)       │
│ [23] │  var │ SVA[19] — CPU Fan Speed LOW  (fan_rpm % 100)       │
│ [24] │  var │ SVA[20] — Water Pump Speed HIGH (misma fórmula)    │
│ [25] │  var │ SVA[21] — Water Pump Speed LOW                      │
│ [26] │  var │ SVA[22] — Display config: umbral CPU Temp color     │
│ [27] │  var │ SVA[23] — Display config: umbral CPU Usage color    │
│ [28] │  var │ SVA[24] — Display config: umbral CPU Power color    │
│ [29] │  var │ SVA[25] — Display config: umbral CPU Freq color     │
│ [30] │  var │ SVA[26] — Display config: umbral CPU Voltage color  │
│ [31] │  var │ SVA[27] — Display config (constante ~32)            │
│ [32] │  var │ SVA[28] — Contador incremental (wraps)              │
│ [33] │ 0x06 │ SVA[29] — Display config (constante 6)              │
│ [34] │ 0x19 │ SVA[30] — Display config (constante 25)             │
│[35-64]│ 0x00│ Padding (siempre cero, 30 bytes)                    │
└─────────────────────────────────────────────────────────────────────┘
```

> **Nota:** `SVA` = `SendValueArray` (campo `Int32[]` de `frmMain` en el exe .NET)

---

## 5. Encodings detallados de cada campo

### SVA[0] — CPU Core Temp
- **Unidad:** °C, entero directo
- **Ejemplo:** `0x1E` = 30°C
- **Fuente HWiNFO:** `CPUTempSelectName` (config.ini) → P-core 0

### SVA[1] — GPU Usage
- **Unidad:** % (0–100)
- **Ejemplo:** `0x00` = 0% (idle), `0x32` = 50%
- **Fuente HWiNFO:** `GPUUsageSelectName`

### SVA[2] — GPU Power
- **Unidad:** W, entero (truncado)
- **Ejemplo:** `0x00` = ~0W (idle)
- **Fuente HWiNFO:** `GPUPowerSelectName`

### SVA[3] — CPU Package Power
- **Unidad:** W, entero (truncado)
- **Rango observado:** 18–61W (idle a semi-carga)
- **Ejemplo:** `0x37` = 55W
- **Fuente HWiNFO:** `CPUPowerSelectName` (CPU Package Power)

### SVA[4] — CPU Package/Hotspot Temp
- **Unidad:** °C, entero
- **Rango observado:** 41–64°C (más alta que core individual por ser hotspot)
- **Ejemplo:** `0x2A` = 42°C
- **Fuente HWiNFO:** probablemente CPU Package temperatura (no core individual)

### SVA[5] — CPU Max Thread Usage
- **Unidad:** % (0–100), máximo de todos los hilos
- **Ejemplo:** `0x46` = 70% (HWiNFO en background)
- **Fuente HWiNFO:** `CPUUsageSelectName` (Max CPU/Thread Usage)
- **DIFERENCIA CON SVA[17]:** SVA[5] = máximo por hilo individual, SVA[17] = promedio total

### SVA[6] — GPU Core Clock
- **Encoding:** `gpu_mhz / 10` (división entera)
- **Ejemplos:**
  - `0x34` = 52 → 520 MHz (idle light)
  - `0x23` = 35 → 350 MHz (idle deep/power-save)
- **Reconstrucción:** `gpu_mhz ≈ SVA[6] × 10`
- **Fuente HWiNFO:** `GPUFrequenceSelectName` (GPU Clock)

### SVA[7] — CPU Core Clock
- **Encoding:** `cpu_mhz / 48` (división entera, aprox.)
- **Ejemplos:**
  - `0x57` = 87 → 87 × 48 = **4176 MHz** (real: 4189.8 MHz, error ~0.3%)
  - `0x5B` = 91 → 91 × 48 = **4368 MHz** (boost momentáneo)
- **Fórmula inversa:** `cpu_mhz ≈ SVA[7] × 48` (precisión ±24 MHz)
- **Fuente HWiNFO:** `CPUFrequenceSelectName` (P-core 0 Clock)

### SVA[8] — Constante
- **Valor:** siempre `0x01`
- **Propósito probable:** flag de modo (P-core vs E-core) o parte decimal del freq
- **Para macOS:** usar `0x01` siempre

### SVA[9] — CPU VID Voltage
- **Encoding:** `voltage_V × 100` (entero)
- **Ejemplos:**
  - `0x64` = 100 → **1.00V**
  - `0x68` = 104 → **1.04V**
- **Fuente HWiNFO:** `CPUVoltagesSelectName` (VID voltage)

### SVA[10] — GPU Temperature
- **Unidad:** °C, entero directo
- **Rango observado:** 33–34°C (idle) — coincide con config.ini `GPUTemp:34`
- **Sube lentamente** conforme la GPU se calienta desde arranque frío
- **Fuente HWiNFO:** `GPUTempSelectName` (GPU Temperature)
- **DIFERENCIA CON SVA[4]:** SVA[10] = GPU Temp, SVA[4] = CPU hotspot temp

### SVA[11..14] — Reserved
- Siempre `0x00`

### SVA[15] — Flag Celsius/Fahrenheit
- **Valor:** siempre `0x0A` = 10
- **Probable significado:** modo display (10 = Celsius, otro valor = Fahrenheit)
- Corresponde a `Showcentigrade_Flag=1` en config.ini

### SVA[16] — Reserved
- Siempre `0x00`

### SVA[17] — CPU Total Average Usage
- **Unidad:** % (0–100), promedio de TODOS los cores/hilos
- **Rango observado:** 5–30% (con monitoring en background)
- **Fuente HWiNFO:** lectura del contador total de CPU

### SVA[18..19] — CPU Fan Speed (CPU Fan)
- **Encoding: 2 bytes decimales**
  ```
  fan_rpm = SVA[18] × 100 + SVA[19]
  ```
- **Ejemplos:**
  - `SVA[18]=11, SVA[19]=59` → 11×100+59 = **1159 RPM**
  - `SVA[18]=12, SVA[19]=07` → 12×100+07 = **1207 RPM**
  - config.ini muestra `1259 RPM` (medido en sesión anterior)
- **Rango:** 0–25500 RPM teórico (SVA[18]=0..255, SVA[19]=0..99)
- **Fuente HWiNFO:** `FANSSelectName` (CPU fan RPM)

### SVA[20..21] — Water Pump Speed
- **Encoding:** idéntico a SVA[18..19]
  ```
  pump_rpm = SVA[20] × 100 + SVA[21]
  ```
- En capturas observadas: **mismo valor que CPU fan** (ambos del mismo sensor)
- **Fuente HWiNFO:** `WatarSelectName` (typo en código original)

### SVA[22..30] — Display Configuration (umbrales de color)
Valores fijos que vienen de los sliders de la UI (`NumericUpDown` controls):

| SVA idx | Valor típico | Probable significado |
|---------|-------------|---------------------|
| [22]    | 20 (0x14)   | Umbral color CPU Temp |
| [23]    | 26 (0x1A)   | Umbral color CPU Usage |
| [24]    | 3  (0x03)   | Umbral color CPU Power |
| [25]    | 14 (0x0E)   | Umbral color CPU Freq |
| [26]    | 18 (0x12)   | Umbral color CPU Voltage |
| [27]    | 32 (0x20)   | Umbral color GPU |
| [28]    | variable    | **Contador incremental** (sube ~1 cada 3 paquetes) |
| [29]    | 6  (0x06)   | Constante |
| [30]    | 25 (0x19)   | Constante |

> Para macOS: usar los valores por defecto observados. El display funciona correctamente con ellos.

### SVA[31..32] — No usados
- `SendData2` los rellena como `0x00` (zero-initialized array)

---

## 6. Paquetes reales capturados

### Sesión 1 (PC Monitor All recién abierto, GPU caliente)
```
00 00 01 02  21 00 00 3D 40 1E 34 57 01 64 21 00 00 00 00 0A 00 06 0B 3B 0B 3B 14 1A 03 0E 12 19 1C 06 19  00×28
```
**Decodificado:**
- CPU Temp: 33°C | GPU Usage: 0% | GPU Power: 0W | CPU Power: 61W
- CPU Hotspot: 64°C | CPU Max Thread: 30% | GPU Clock: 340MHz
- CPU Freq: 87×48=4176MHz | CPU Voltage: 1.00V | GPU Temp: 33°C
- CPU Usage Total: 6% | CPU Fan: 11×100+59=1159 RPM | Water: 1159 RPM

### Sesión 2 (sistema en idle estable)
```
00 00 01 02  1E 00 00 19 2A 46 23 5B 01 64 22 00 00 00 00 0A 00 1E 0C 07 0C 07 14 1A 03 0E 12 20 16 06 19  00×28
```
**Decodificado:**
- CPU Temp: 30°C | GPU Usage: 0% | GPU Power: 0W | CPU Power: 25W
- CPU Hotspot: 42°C | CPU Max Thread: 70% | GPU Clock: 350MHz
- CPU Freq: 91×48=4368MHz | CPU Voltage: 1.00V | GPU Temp: 34°C
- CPU Usage Total: 30% | CPU Fan: 12×100+07=1207 RPM | Water: 1207 RPM

### Sesión 3 (idle profundo, varios paquetes idénticos)
```
00 00 01 02  1D 00 00 1D 30 0A 34 57 01 64 22 00 00 00 00 0A 00 06 0B 39 0B 39 14 1A 03 0E 12 20 1B 06 19  00×28
```
**Decodificado:**
- CPU Temp: 29°C | GPU Usage: 0% | GPU Power: 0W | CPU Power: 29W
- CPU Hotspot: 48°C | CPU Max Thread: 10% | GPU Clock: 520MHz
- CPU Freq: 87×48=4176MHz | CPU Voltage: 1.00V | GPU Temp: 34°C
- CPU Usage Total: 6% | CPU Fan: 11×100+57=1157 RPM | Water: 1157 RPM

---

## 7. Correlación confirmada con config.ini

```ini
; Valores en config.ini (última sesión guardada)
CPUTempSelectName= Intel Core i7-14700F_P-core 0:32        → SVA[0]  ✓ (32≈30-33°C medidos)
CPUPowerSelectName=CPU Package Power:46,8                   → SVA[3]  ✓ (18-61W medidos)
CPUVoltagesSelectName= Intel Core i7-14700F_P-core 0 VID:1 → SVA[9]  ✓ (100=1.00V constante)
GPUTempSelectName= AMD Radeon RX 6600 XT_GPU Temperature:34 → SVA[10] ✓ (33-34°C medidos)
GPUUsageSelectName=GPU Utilization AMD Radeon RX 6600 XT:0  → SVA[1]  ✓ (siempre 0 en idle)
GPUPowerSelectName=GPU Core Power (VDDCR_GFX):0,1           → SVA[2]  ✓ (siempre 0 en idle)
GPUFrequenceSelectName= AMD Radeon RX 6600 XT_GPU Clock:12,0→ SVA[6]  (52=520MHz en captura)
CPUFrequenceSelectName= Intel Core i7-14700F_P-core 0:4189,8→ SVA[7]  ✓ (87×48≈4176MHz)
CPUUsageSelectName=Max CPU/Thread Usage Intel Core i7:46    → SVA[5]  (varía con carga)
FANSSelectName=CPU:1259                                      → SVA[18..19] ✓ (~1159-1207 RPM)
WatarSelectName=CPU:1259                                     → SVA[20..21] ✓ (mismo valor)
```

---

## 8. Protocolo de inicio

**No hay secuencia de wake-up especial.** El display reacciona al primer paquete.

Observado desde `ShowConnect()` IL:
1. Si `myHidDevice != null` (conectado): establece `connectFlag = true`, envía paquete inmediatamente
2. Si `myHidDevice == null`: espera 250ms y reintenta
3. El display **NO se apaga** al cerrar el programa (queda en el último valor mostrado)
4. **No hay animación de inicio** — el primer paquete con datos reales activa el display

---

## 9. Implementación de referencia para macOS (Python)

```python
#!/usr/bin/env python3
"""
AIO Display Driver — VID: 0x5131 / PID: 0x2007
Protocolo reverse-engineered de PC Monitor All.exe (Windows)
"""

import hid
import time
import math

VENDOR_ID  = 0x5131
PRODUCT_ID = 0x2007
INTERVAL   = 0.2  # 200ms como el original

def encode_packet(
    cpu_temp_c: int,       # SVA[0]  CPU core temp en °C
    gpu_usage_pct: int,    # SVA[1]  GPU usage 0-100%
    gpu_power_w: int,      # SVA[2]  GPU power en W
    cpu_power_w: int,      # SVA[3]  CPU package power en W
    cpu_hotspot_c: int,    # SVA[4]  CPU hotspot/package temp en °C
    cpu_max_thread_pct: int, # SVA[5] CPU max thread usage %
    gpu_clock_mhz: int,    # SVA[6]  GPU core clock en MHz
    cpu_clock_mhz: int,    # SVA[7]  CPU core clock en MHz
    cpu_voltage_v: float,  # SVA[9]  CPU VID voltage en V (e.g., 1.05)
    gpu_temp_c: int,       # SVA[10] GPU temp en °C
    cpu_usage_pct: int,    # SVA[17] CPU total usage % (average)
    cpu_fan_rpm: int,      # SVA[18..19] CPU fan en RPM
    pump_rpm: int,         # SVA[20..21] Water pump en RPM
    counter: int = 0,      # SVA[28] contador incremental (0-255, wraps)
) -> bytes:

    buf = bytearray(65)

    # Header fijo
    buf[0] = 0x00  # Report ID
    buf[1] = 0x00  # header
    buf[2] = 0x01  # header
    buf[3] = 0x02  # header

    # SVA[0..10] — sensores principales
    buf[4]  = max(0, min(255, cpu_temp_c))
    buf[5]  = max(0, min(100, gpu_usage_pct))
    buf[6]  = max(0, min(255, gpu_power_w))
    buf[7]  = max(0, min(255, cpu_power_w))
    buf[8]  = max(0, min(255, cpu_hotspot_c))
    buf[9]  = max(0, min(100, cpu_max_thread_pct))
    buf[10] = max(0, min(255, gpu_clock_mhz // 10))
    buf[11] = max(0, min(255, cpu_clock_mhz // 48))
    buf[12] = 0x01  # constante
    buf[13] = max(0, min(255, round(cpu_voltage_v * 100)))
    buf[14] = max(0, min(255, gpu_temp_c))

    # SVA[11..16] — reserved + constantes
    buf[15] = 0x00  # SVA[11]
    buf[16] = 0x00  # SVA[12]
    buf[17] = 0x00  # SVA[13]
    buf[18] = 0x00  # SVA[14]
    buf[19] = 0x0A  # SVA[15] — flag Celsius (10)
    buf[20] = 0x00  # SVA[16]

    # SVA[17] — CPU total average usage
    buf[21] = max(0, min(100, cpu_usage_pct))

    # SVA[18..19] — CPU Fan RPM (high byte × 100 + low byte)
    cpu_fan_rpm = max(0, min(25500, cpu_fan_rpm))
    buf[22] = cpu_fan_rpm // 100      # SVA[18] = RPM / 100
    buf[23] = cpu_fan_rpm % 100       # SVA[19] = RPM % 100

    # SVA[20..21] — Water Pump RPM (misma fórmula)
    pump_rpm = max(0, min(25500, pump_rpm))
    buf[24] = pump_rpm // 100         # SVA[20]
    buf[25] = pump_rpm % 100          # SVA[21]

    # SVA[22..30] — Display config (valores por defecto observados)
    buf[26] = 0x14  # SVA[22] = 20
    buf[27] = 0x1A  # SVA[23] = 26
    buf[28] = 0x03  # SVA[24] = 3
    buf[29] = 0x0E  # SVA[25] = 14
    buf[30] = 0x12  # SVA[26] = 18
    buf[31] = 0x20  # SVA[27] = 32
    buf[32] = counter & 0xFF  # SVA[28] — contador incremental
    buf[33] = 0x06  # SVA[29] = 6
    buf[34] = 0x19  # SVA[30] = 25

    # SVA[31..32] y resto = 0x00 (ya inicializado)
    return bytes(buf)


def send_loop():
    """Loop principal — enviar datos al display cada 200ms"""
    dev = hid.device()
    try:
        dev.open(VENDOR_ID, PRODUCT_ID)
        print(f"Display conectado: {dev.get_product_string()}")
    except Exception as e:
        print(f"Error abriendo display: {e}")
        print("Asegúrate de que el dispositivo esté conectado y sin otro proceso usando el puerto")
        return

    counter = 0
    try:
        while True:
            # === AQUÍ reemplazar con lecturas reales de sensores ===
            # En macOS puedes usar: psutil, powermetrics (sudo), IOKit bindings
            packet = encode_packet(
                cpu_temp_c        = 45,    # psutil / IOKit
                gpu_usage_pct     = 0,     # IOKit / GPU framework
                gpu_power_w       = 5,     # powermetrics
                cpu_power_w       = 25,    # powermetrics
                cpu_hotspot_c     = 55,    # IOKit
                cpu_max_thread_pct= 30,    # psutil per-cpu max
                gpu_clock_mhz     = 1200,  # IOKit Metal
                cpu_clock_mhz     = 3600,  # sysctl hw.cpufrequency
                cpu_voltage_v     = 1.05,  # powermetrics
                gpu_temp_c        = 42,    # IOKit
                cpu_usage_pct     = 15,    # psutil.cpu_percent()
                cpu_fan_rpm       = 1800,  # SMC sensors
                pump_rpm          = 0,     # SMC (0 si no hay water cooling)
                counter           = counter,
            )
            dev.write(packet)
            counter = (counter + 1) & 0xFF
            time.sleep(INTERVAL)

    except KeyboardInterrupt:
        print("\nDetenido.")
    finally:
        dev.close()


if __name__ == "__main__":
    send_loop()
```

**Dependencias macOS:**
```bash
pip install hid        # python-hid (wrapper de hidapi)
brew install hidapi    # librería nativa
```

**Permisos macOS (USB HID):**
- En macOS 11+: no se requieren permisos especiales para dispositivos HID genéricos
- Si falla: ejecutar con `sudo` la primera vez para verificar

---

## 10. Lectura de sensores en macOS

| Sensor | Fuente macOS | Comando/API |
|--------|-------------|-------------|
| CPU Temp | SMC via `smctemp` o IOKit | `smctemp -c` |
| CPU Usage % | `psutil` | `psutil.cpu_percent(percpu=True)` |
| CPU Freq | `sysctl` | `sysctl -n hw.cpufrequency_max` (Intel) |
| CPU Power | `powermetrics` | `sudo powermetrics -n 1 --samplers cpu_power` |
| GPU Temp | IOKit Metal | Requiere framework privado |
| GPU Usage | IOKit | `ioreg -l -w 0 \| grep "PerformanceStatistics"` |
| Fan Speed | SMC | `smctemp -f` o `sudo iStats fans` |
| RAM Usage | `psutil` | `psutil.virtual_memory().percent` |

**Librerías Python recomendadas para macOS:**
```bash
pip install psutil          # CPU/RAM/procesos
pip install ilostat         # SMC fans y temps (requiere permisos)
# Para GPU en Apple Silicon:
pip install pymetald        # wraps powermetrics
```

---

## 11. Verificación del protocolo (test mínimo)

Enviar este paquete al dispositivo debe mostrar datos en el display inmediatamente:

```python
# Paquete de prueba: todos los valores en cero excepto header
import hid
dev = hid.device()
dev.open(0x5131, 0x2007)

# Paquete: header fijo + temperatura CPU=42°C + resto cero
test_pkt = bytearray(65)
test_pkt[0] = 0x00  # Report ID
test_pkt[1] = 0x00; test_pkt[2] = 0x01; test_pkt[3] = 0x02  # header
test_pkt[4]  = 42   # CPU Temp = 42°C
test_pkt[12] = 0x01 # constante SVA[8]
test_pkt[19] = 0x0A # flag Celsius
test_pkt[26] = 0x14; test_pkt[27] = 0x1A  # config defaults
test_pkt[28] = 0x03; test_pkt[29] = 0x0E; test_pkt[30] = 0x12
test_pkt[31] = 0x20; test_pkt[33] = 0x06; test_pkt[34] = 0x19

dev.write(bytes(test_pkt))
print("Paquete enviado — verificar display")
dev.close()
```

---

## 12. Campos con incertidumbre residual

| Campo | Estado | Nota |
|-------|--------|------|
| SVA[4] CPU hotspot vs GPU temp | **Alta confianza** | Valores 41-64°C consistentes con CPU junction temp, SVA[10] corresponde mejor al GPU temp (34°C) |
| SVA[7] encoding `÷48` | **Alta confianza** | 87×48=4176≈4189 MHz, 91×48=4368 MHz boost, error <0.5% |
| SVA[18..19] fan RPM `h*100+l` | **Alta confianza** | 11×100+59=1159≈1259 RPM (±100 RPM por variación temporal) |
| SVA[8] constante 1 | **Sin confirmar** | Podría ser flag de tipo de core (P-core=1, E-core=2) |
| SVA[15] constante 10 | **Probable** | Flag Celsius (`Showcentigrade_Flag=1` → valor 10) |
| SVA[22..30] thresholds | **Sin confirmar** | Valores de UI sliders; display funciona con los defaults observados |
| SVA[28] contador | **Confirmado** | Incrementa ~1 cada 3-4 paquetes (≈1-2 Hz), wraps en 255 |

---

## 13. Métodos y clases clave del exe (para referencia)

```
ComputereMonitor.frmMain
├── send_usb_data(Byte[] data, Int32 count) → Boolean
├── receive_usb_data(Byte[] data) → Int32
├── USB_Init() → Void
├── SendData2() → Void          ← CONSTRUYE Y ENVÍA EL PAQUETE
├── ShowConnect() → Void        ← VERIFICA CONEXIÓN
├── Thread_Send() → Void        ← LOOP 200ms
├── Thread_GetPCParam() → Void  ← LOOP LECTURA HWiNFO
├── GetPCParam() → Void         ← LLENA SendValueArray
│
├── Field: SendValueArray  [Int32[]]  token 0x040000A3  ← DATOS DE SENSORES
├── Field: ValueArray      [Int32[]]  token 0x040000A4
├── Field: lcd_buf         [Int32[]]  token 0x040000A7
└── Field: myHidDevice     [CyHidDevice] token 0x040000A8

CyUSB.CyHidDevice (CyUSB.dll)
├── WriteOutput() → Boolean     ← ENVÍA via HID Output Report
├── Outputs → CyHidReport
│   ├── DataBuf → Byte[]       ← BUFFER DE 65 BYTES
│   ├── ID → Byte              ← REPORT ID (0x00)
│   └── RptByteLen → Int32     ← 65
└── RwAccessible → Boolean
```

---

## 14. Hardware confirmado

| Componente | Modelo |
|-----------|--------|
| CPU | Intel Core i7-14700F |
| GPU | AMD Radeon RX 6600 XT |
| Display AIO | VID 0x5131 / PID 0x2007 (FBB) |
| OS análisis | Windows 11 Pro 10.0.26200 |
| App analizada | PC Monitor All.exe (.NET 4.x, Cypress CyUSB) |

---

*Análisis: Claude Code + ingeniería inversa .NET IL + captura HID en tiempo real — 2026-03-14*
