#!/bin/bash
set -euo pipefail

VERSION="1.1.0"
PROJECT_DIR="/Users/rene/hydrotemp-aio-mac"
VENV="/Users/rene/monitor-env"
BUILD_DIR="$PROJECT_DIR/build"

echo "=== AIO Display Driver + RGB Controller — Build DMG Installer ==="
echo "Version: $VERSION"
echo ""

# Step 1: Instalar PyInstaller
echo "[1/8] Instalando PyInstaller..."
"$VENV/bin/pip" install pyinstaller -q

# Step 2: Compilar AIO Display
echo "[2/8] Compilando aio-display..."
"$VENV/bin/pyinstaller" "$BUILD_DIR/aio-display.spec" \
    --distpath "$BUILD_DIR/dist" \
    --workpath "$BUILD_DIR/work" \
    --clean --noconfirm 2>&1 | tail -5

# Step 3: Compilar AIO RGB
echo "[3/8] Compilando aio-rgb..."
"$VENV/bin/pyinstaller" "$BUILD_DIR/aio-rgb.spec" \
    --distpath "$BUILD_DIR/dist" \
    --workpath "$BUILD_DIR/work" \
    --clean --noconfirm 2>&1 | tail -5

# Step 4: Verificar binarios
echo "[4/8] Verificando binarios..."
if [ ! -f "$BUILD_DIR/dist/aio-display/aio-display" ]; then
    echo "ERROR: aio-display no encontrado"
    exit 1
fi
if [ ! -f "$BUILD_DIR/dist/aio-rgb/aio-rgb" ]; then
    echo "ERROR: aio-rgb no encontrado"
    exit 1
fi
echo "  OK: aio-display $(du -sh "$BUILD_DIR/dist/aio-display" | cut -f1)"
echo "  OK: aio-rgb     $(du -sh "$BUILD_DIR/dist/aio-rgb" | cut -f1)"

# Step 5: Montar payload del pkg
echo "[5/8] Preparando payload del instalador..."
PKG_ROOT="$BUILD_DIR/pkg-root"
rm -rf "$PKG_ROOT"

# AIO Display
mkdir -p "$PKG_ROOT/usr/local/lib/aio-display"
cp -R "$BUILD_DIR/dist/aio-display/"* "$PKG_ROOT/usr/local/lib/aio-display/"

# AIO RGB
mkdir -p "$PKG_ROOT/usr/local/lib/aio-rgb"
cp -R "$BUILD_DIR/dist/aio-rgb/"* "$PKG_ROOT/usr/local/lib/aio-rgb/"

# LaunchAgents
mkdir -p "$PKG_ROOT/Library/LaunchAgents"
cp "$BUILD_DIR/com.rene.aio-display.plist" "$PKG_ROOT/Library/LaunchAgents/"
cp "$BUILD_DIR/com.rene.aio-rgb.plist" "$PKG_ROOT/Library/LaunchAgents/"

# Step 6: Permisos de scripts
chmod +x "$BUILD_DIR/scripts/postinstall" "$BUILD_DIR/scripts/preinstall"

# Step 7: Crear .pkg
echo "[6/8] Creando instalador .pkg..."
pkgbuild \
    --root "$PKG_ROOT" \
    --scripts "$BUILD_DIR/scripts" \
    --identifier com.rene.aio-display \
    --version "$VERSION" \
    --install-location / \
    "$BUILD_DIR/AIO-Display-Driver.pkg"

# Step 8: Crear .dmg
echo "[7/8] Creando imagen .dmg..."
DMG_STAGE="$BUILD_DIR/dmg-staging"
rm -rf "$DMG_STAGE"
mkdir -p "$DMG_STAGE"
cp "$BUILD_DIR/AIO-Display-Driver.pkg" "$DMG_STAGE/"
cp "$BUILD_DIR/uninstall.sh" "$DMG_STAGE/"
chmod +x "$DMG_STAGE/uninstall.sh"
cp "$PROJECT_DIR/README.md" "$DMG_STAGE/"

hdiutil create \
    -volname "AIO Display Driver" \
    -srcfolder "$DMG_STAGE" \
    -ov -format UDZO \
    "$BUILD_DIR/AIO-Display-Driver-${VERSION}.dmg"

echo ""
echo "[8/8] Build completado!"
echo "  DMG: $BUILD_DIR/AIO-Display-Driver-${VERSION}.dmg"
echo "  PKG: $BUILD_DIR/AIO-Display-Driver.pkg"
echo ""
echo "  Contenido:"
echo "    - aio-display: Driver del display AIO (temperatura, RPM, etc.)"
echo "    - aio-rgb:     Control RGB de motherboard (ITE8297)"
echo "    - LaunchAgents para auto-inicio"
echo "    - Uninstaller"
ls -lh "$BUILD_DIR/AIO-Display-Driver-${VERSION}.dmg"
