#!/bin/bash
# Instalace (nebo přeinstalace) Katalogu souborů – nejnovější verze z GitHubu.
# Spuštění v Terminálu:
#   curl -fsSL https://raw.githubusercontent.com/Luboshort/KatalogSouboru/main/install.sh | bash
#
# Soubor stažený takto (ne prohlížečem) nedostane od macOS značku „staženo z internetu“,
# takže se aplikace spustí bez varování. Další verze pak aplikace instaluje sama.
set -euo pipefail
URL="https://github.com/Luboshort/KatalogSouboru/releases/latest/download/KatalogSouboru.dmg"
NAME="KatalogSouboru.app"

T="$(mktemp -d /tmp/katalog-install.XXXXXX)"
cleanup() { hdiutil detach -quiet -force "${T}/mnt" >/dev/null 2>&1 || true; rm -rf "${T}"; }
trap cleanup EXIT

echo "› Stahuji nejnovější verzi…"
curl -fL --progress-bar -o "${T}/katalog.dmg" "${URL}"

mkdir -p "${T}/mnt"
hdiutil attach -quiet -nobrowse -readonly -noautoopen -mountpoint "${T}/mnt" "${T}/katalog.dmg"
SRC_APP="${T}/mnt/${NAME}"
if [ ! -d "${SRC_APP}" ]; then echo "CHYBA: v DMG není ${NAME}"; exit 1; fi

# do /Applications, když tam jde zapisovat (správce), jinak do ~/Applications
if [ -w /Applications ]; then DEST="/Applications"; else DEST="${HOME}/Applications"; mkdir -p "${DEST}"; fi
APP="${DEST}/${NAME}"

osascript -e 'tell application "KatalogSouboru" to quit' >/dev/null 2>&1 || true
sleep 1
rm -rf "${APP}"
ditto "${SRC_APP}" "${APP}"
xattr -cr "${APP}" 2>/dev/null || true

VER="$(defaults read "${APP}/Contents/Info" CFBundleShortVersionString 2>/dev/null || echo "?")"
echo "✅ Nainstalováno: ${APP} (verze ${VER})"
open "${APP}"
