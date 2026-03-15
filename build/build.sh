#!/bin/bash
set -euo pipefail

VERSION="1.0.0"
PROJECT_DIR="/Users/rene/hydrotemp-aio-mac"
VENV="/Users/rene/monitor-env"
BUILD_DIR="$PROJECT_DIR/build"

echo "=== AIO Display Driver — Build DMG Installer ==="
echo "Version: $VERSION"
echo ""

# Step 1: Instalar PyInstaller
echo "[1/7] Instalando PyInstaller..."
"$VENV/bin/pip" install pyinstaller -q

# Step 2: Compilar con PyInstaller
echo "[2/7] Compilando binario standalone..."
"$VENV/bin/pyinstaller" "$BUILD_DIR/aio-display.spec" \
    --distpath "$BUILD_DIR/dist" \
    --workpath "$BUILD_DIR/work" \
    --clean --noconfirm 2>&1 | tail -5

# Step 3: Verificar que el binario existe
echo "[3/7] Verificando binario..."
if [ ! -f "$BUILD_DIR/dist/aio-display/aio-display" ]; then
    echo "ERROR: Binario no encontrado"
    exit 1
fi
echo "  OK: $(du -sh "$BUILD_DIR/dist/aio-display" | cut -f1) total"

# Step 4: Montar payload del pkg
echo "[4/7] Preparando payload del instalador..."
PKG_ROOT="$BUILD_DIR/pkg-root"
rm -rf "$PKG_ROOT"
mkdir -p "$PKG_ROOT/usr/local/lib/aio-display"
cp -R "$BUILD_DIR/dist/aio-display/"* "$PKG_ROOT/usr/local/lib/aio-display/"
mkdir -p "$PKG_ROOT/Library/LaunchAgents"
cp "$BUILD_DIR/com.rene.aio-display.plist" "$PKG_ROOT/Library/LaunchAgents/"

# Step 5: Permisos de scripts
chmod +x "$BUILD_DIR/scripts/postinstall" "$BUILD_DIR/scripts/preinstall"

# Step 6: Crear .pkg
echo "[5/7] Creando instalador .pkg..."
pkgbuild \
    --root "$PKG_ROOT" \
    --scripts "$BUILD_DIR/scripts" \
    --identifier com.rene.aio-display \
    --version "$VERSION" \
    --install-location / \
    "$BUILD_DIR/AIO-Display-Driver.pkg"

# Step 7: Crear .dmg
echo "[6/7] Creando imagen .dmg..."
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
echo "[7/7] Build completado!"
echo "  DMG: $BUILD_DIR/AIO-Display-Driver-${VERSION}.dmg"
echo "  PKG: $BUILD_DIR/AIO-Display-Driver.pkg"
ls -lh "$BUILD_DIR/AIO-Display-Driver-${VERSION}.dmg"
