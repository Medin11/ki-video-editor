#!/bin/zsh
# ------------------------------------------------------------------
#  build.sh – baut "Video Editor.app" und legt sie in /Applications.
#
#  Aufruf (im Terminal):   ./mac-app/build.sh
#  Erneut ausführen, wenn main.swift geändert oder der Projektordner
#  verschoben wurde (der Pfad wird in die App eingetragen).
# ------------------------------------------------------------------
set -e

HERE="$(cd "$(dirname "$0")" && pwd)"          # .../Video Editor/mac-app
PROJECT="$(cd "$HERE/.." && pwd)"              # .../Video Editor
BUILD="$HERE/build"
APP="$BUILD/Video Editor.app"

echo "🔨 Baue Video Editor.app…"
rm -rf "$BUILD"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

# 1. Programm kompilieren
swiftc -O -swift-version 5 "$HERE/main.swift" -o "$APP/Contents/MacOS/Video Editor" \
  -framework Cocoa -framework WebKit

# 2. Icon zeichnen und in alle nötigen Größen umwandeln
ICONSET="$BUILD/AppIcon.iconset"
mkdir -p "$ICONSET"
swift "$HERE/make_icon.swift" "$BUILD/icon.png" >/dev/null
for s in 16 32 128 256 512; do
  sips -z $s $s "$BUILD/icon.png" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
  sips -z $((s*2)) $((s*2)) "$BUILD/icon.png" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"

# 3. Info.plist (Name, Icon, Projektordner …)
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>               <string>Video Editor</string>
  <key>CFBundleDisplayName</key>        <string>Video Editor</string>
  <key>CFBundleExecutable</key>         <string>Video Editor</string>
  <key>CFBundleIdentifier</key>         <string>local.ki-video-editor</string>
  <key>CFBundleIconFile</key>           <string>AppIcon</string>
  <key>CFBundlePackageType</key>        <string>APPL</string>
  <key>CFBundleShortVersionString</key> <string>1.0</string>
  <key>CFBundleVersion</key>            <string>1</string>
  <key>CFBundleDevelopmentRegion</key>  <string>de</string>
  <key>LSMinimumSystemVersion</key>     <string>13.0</string>
  <key>LSApplicationCategoryType</key>  <string>public.app-category.video</string>
  <key>NSHighResolutionCapable</key>    <true/>
  <key>NSHumanReadableCopyright</key>   <string>Lokaler KI-Video-Editor</string>
  <key>NSAppTransportSecurity</key>
  <dict><key>NSAllowsLocalNetworking</key><true/></dict>
  <key>VEProjectDir</key>               <string>$PROJECT</string>
</dict>
</plist>
PLIST

# 4. Lokal signieren (nötig, damit macOS die App startet)
codesign --force --deep --sign - "$APP" 2>/dev/null

# 5. In den Programme-Ordner legen (alte Version ersetzen)
TARGET="/Applications"
[[ -w "$TARGET" ]] || { TARGET="$HOME/Applications"; mkdir -p "$TARGET"; }
rm -rf "$TARGET/Video Editor.app"
ditto "$APP" "$TARGET/Video Editor.app"
touch "$TARGET/Video Editor.app"   # Finder/Dock sollen das neue Icon sofort zeigen

echo "✅ Fertig: $TARGET/Video Editor.app"
