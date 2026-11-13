#!/bin/bash
# Vytvoří SAMOSTATNOU aplikaci "KatalogSouboru.app" – obsahuje vlastní Python i všechny knihovny,
# takže na jiném Macu není potřeba nic instalovat.
# Spuštění v Terminálu:  bash vytvor_samostatnou_aplikaci.sh
set -e
SRC="$(cd "$(dirname "$0")" && pwd)"
NAME="KatalogSouboru"         # název aplikace a DMG – bez diakritiky
BUILD_NAME="KatalogSouboru"
VERSION="$(sed -n 's/^APP_VERSION = "\(.*\)"/\1/p' "$SRC/katalog.py")"

# 1) Najít Python – na Apple Silicon nativní (arm64), na Intelu s Tcl/Tk 8.x
. "$SRC/vyber_python.sh"
if ! vyber_python; then
  echo "Chybí Python s Tkinter. Nainstaluj Python z python.org a spusť skript znovu."
  exit 1
fi
RUN=""; [ "$PYARCH" = "arm64" ] && RUN="arch -arm64"
echo "▶ Sestavuji $NAME $VERSION pomocí $PY ($PYARCH, Tcl/Tk $PYTCL)"
if [ "$PYARCH" != "arm64" ] && [ "$(sysctl -n hw.optional.arm64 2>/dev/null)" = "1" ]; then
  echo "⚠︎  Tento Mac má Apple Silicon, ale nenašel jsem nativní Python – aplikace by běžela přes Rosettu."
  echo "   Nainstaluj Python z python.org (podporuje Apple Silicon) a spusť skript znovu."
  exit 1
fi
case "$PYARCH/$PYTCL" in
  x86_64/9*) echo "⚠︎  Na Intelu s Tcl/Tk $PYTCL nebude fungovat přetahování myší do okna (doporučuji Python 3.13)."
             echo "   Pokračuji za 5 s (Ctrl+C = zrušit)…"; sleep 5 ;;
esac

# 2) Pracovní složka s vlastním prostředím a PyInstallerem
WORK="$(mktemp -d /tmp/katalog-build.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT
cp "$SRC/katalog.py" "$WORK/"
cp -R "$SRC/tkinterdnd2" "$WORK/"
xattr -dr com.apple.quarantine "$WORK" 2>/dev/null || true
echo "▶ Instaluji nástroj PyInstaller (jen do dočasné složky)…"
$PYRUN -m venv "$WORK/venv"
$RUN "$WORK/venv/bin/python" -m pip install --quiet --upgrade pip pyinstaller

# 3) Sestavení
echo "▶ Sestavuji aplikaci (může trvat 1–2 minuty)…"
cd "$WORK"
$RUN "$WORK/venv/bin/python" -m PyInstaller --noconfirm --clean --log-level WARN --windowed \
  --name "$BUILD_NAME" \
  --osx-bundle-identifier local.katalog-souboru \
  --paths "$WORK" \
  --hidden-import tkinterdnd2 \
  --add-data "$WORK/tkinterdnd2/tkdnd:tkinterdnd2/tkdnd" \
  katalog.py

APP="$WORK/dist/$BUILD_NAME.app"
PL="$APP/Contents/Info.plist"

# 4) Verze, podpora přetažení složek / katalogů na ikonu
plutil -replace CFBundleDisplayName -string "$NAME" "$PL"
plutil -replace CFBundleName -string "$NAME" "$PL"
plutil -replace CFBundleShortVersionString -string "$VERSION" "$PL"
plutil -replace CFBundleVersion -string "$VERSION" "$PL"
plutil -replace NSHighResolutionCapable -bool YES "$PL"
plutil -replace CFBundleDocumentTypes -json '[
  {"CFBundleTypeName":"Složka","CFBundleTypeRole":"Viewer","LSItemContentTypes":["public.folder"]},
  {"CFBundleTypeName":"Katalog souborů","CFBundleTypeRole":"Viewer","CFBundleTypeExtensions":["katalog"]}
]' "$PL"

# 5) Lokální podpis (na Apple Silicon nutný) – postupně zevnitř ven, bez --deep
echo "▶ Podepisuji aplikaci…"
xattr -cr "$APP"
find "$APP/Contents" -type f -print0 | while IFS= read -r -d '' F; do
  if file -b "$F" | grep -q "Mach-O"; then
    codesign --force --sign - "$F" >/dev/null 2>&1 || echo "   (nelze podepsat: ${F#$APP/})"
  fi
done
find "$APP/Contents" -type d -name "*.framework" -depth -print0 | while IFS= read -r -d '' F; do
  codesign --force --sign - "$F" >/dev/null 2>&1 || true
done
codesign --force --sign - "$APP"
codesign --verify "$APP" && echo "   podpis OK"


# 6) Výsledek: aplikace do ~/Applications + DMG na Plochu pro jiné Macy
mkdir -p "$HOME/Applications"
rm -rf "$HOME/Applications/$NAME.app"
rm -rf "$HOME/Applications/Katalog souborů.app"     # starší verze s diakritikou v názvu
ditto "$APP" "$HOME/Applications/$NAME.app"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$HOME/Applications/$NAME.app" 2>/dev/null || true

# DMG – jeden soubor, ve kterém aplikace zůstane neporušená při jakémkoli přenosu
# (OneDrive, Teams, e-mail, síťový disk z Windows… jinak ničí oprávnění a odkazy uvnitř aplikace)
echo "▶ Vytvářím instalační DMG…"
DMGDIR="$WORK/dmg"
mkdir -p "$DMGDIR"
ditto "$APP" "$DMGDIR/$NAME.app"
ln -s /Applications "$DMGDIR/Aplikace"
cat > "$DMGDIR/Jak nainstalovat.txt" <<TXT
$NAME $VERSION

1) Přetáhni ikonu „$NAME“ na složku „Aplikace“.
2) Spusť aplikaci ze složky Aplikace.

Pokud macOS při prvním spuštění napíše, že aplikaci nemůže ověřit:
   klikni na „Hotovo“, otevři Nastavení systému → Soukromí a zabezpečení,
   sjeď dolů a klikni na „Přesto otevřít“. Stačí jednou.

Aplikace je pro Macy s procesorem Apple (M1 a novější).

Aktualizace: aplikace sama zjistí, že je na GitHubu nová verze, a nahoře v okně
nabídne její stažení. Novou verzi nainstaluješ stejně – přetažením do Aplikací.
TXT
DMG="$HOME/Desktop/$NAME-$VERSION.dmg"
rm -f "$DMG"
hdiutil create -quiet -volname "$NAME $VERSION" -srcfolder "$DMGDIR" -fs HFS+ -format UDZO -ov "$DMG"
hdiutil verify -quiet "$DMG" && echo "   DMG OK"

echo
echo "✅ Hotovo."
echo "   Aplikace:          $HOME/Applications/$NAME.app"
echo "   Pro jiné Macy:     $DMG"
open -R "$HOME/Applications/$NAME.app"
