#!/bin/bash
# Desinstalador de AIO Display Driver
set -e

LABEL="com.rene.aio-display"
CONSOLE_UID=$(id -u)

echo "Deteniendo servicio..."
launchctl bootout gui/"$CONSOLE_UID"/"$LABEL" 2>/dev/null || true

echo "Eliminando archivos..."
sudo rm -rf /usr/local/lib/aio-display
sudo rm -f /Library/LaunchAgents/com.rene.aio-display.plist

echo "AIO Display Driver desinstalado correctamente."
