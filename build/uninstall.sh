#!/bin/bash
# Desinstalador de AIO Display Driver + RGB Controller
set -e

CONSOLE_UID=$(id -u)

echo "Deteniendo servicios..."
launchctl bootout gui/"$CONSOLE_UID"/com.rene.aio-display 2>/dev/null || true
launchctl bootout gui/"$CONSOLE_UID"/com.rene.aio-rgb 2>/dev/null || true

echo "Eliminando archivos..."
sudo rm -rf /usr/local/lib/aio-display
sudo rm -rf /usr/local/lib/aio-rgb
sudo rm -f /Library/LaunchAgents/com.rene.aio-display.plist
sudo rm -f /Library/LaunchAgents/com.rene.aio-rgb.plist

echo "AIO Display Driver + RGB Controller desinstalado correctamente."
echo ""
echo "Nota: La configuración de color se conserva en ~/.config/aio-rgb/"
echo "Para eliminarla: rm -rf ~/.config/aio-rgb"
