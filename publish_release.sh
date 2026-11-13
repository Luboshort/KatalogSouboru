#!/bin/bash
# Vydá novou verzi Katalogu souborů na GitHub. Aplikace u uživatelů ji pak sama nabídne ke stažení.
#
#   ./publish_release.sh                         popis změn se vezme z CHANGELOG.md (oddíl „## <verze>“)
#   ./publish_release.sh --notes "Co je nového"  vlastní popis
#   ./publish_release.sh --notes-file soubor.md
#   (--full se přijímá kvůli jednotnosti s ostatními projekty – DMG je vždy celá aplikace)
#
# Postup: 1) zkontroluje verzi  2) sestaví aplikaci + DMG  3) nahraje zdrojové soubory do repozitáře
#         4) vytvoří vydání v<verze> s přiloženým DMG
# Potřebuje GitHub CLI (brew install gh && gh auth login) a git.
set -euo pipefail
cd "$(dirname "$0")"
SRC="$(pwd)"

NOTES=""
while [ $# -gt 0 ]; do
  case "$1" in
    --full) shift ;;
    --notes) NOTES="$2"; shift 2 ;;
    --notes-file) NOTES="$(cat "$2")"; shift 2 ;;
    *) echo "Neznámá volba $1"; exit 1 ;;
  esac
done

REPO=$(sed -n 's/^GITHUB_REPO = "\(.*\)"/\1/p' katalog.py)
VER=$(sed -n 's/^APP_VERSION = "\(.*\)"/\1/p' katalog.py)
GIT_URL="${KAT_GIT_URL:-https://github.com/$REPO.git}"
if [ -z "$REPO" ] || [ -z "$VER" ]; then echo "CHYBA: v katalog.py chybí GITHUB_REPO nebo APP_VERSION."; exit 1; fi
if ! command -v gh >/dev/null 2>&1; then echo "CHYBA: chybí GitHub CLI – brew install gh && gh auth login"; exit 1; fi
if ! command -v git >/dev/null 2>&1; then echo "CHYBA: chybí git – spusť 'xcode-select --install'"; exit 1; fi
if ! gh auth status >/dev/null 2>&1; then echo "CHYBA: GitHub CLI není přihlášené – spusť 'gh auth login'"; exit 1; fi

echo "› Katalog souborů $VER → $REPO"
if gh release view "v$VER" --repo "$REPO" >/dev/null 2>&1; then
  echo "CHYBA: vydání v$VER už na GitHubu existuje – zvyš APP_VERSION v katalog.py (a doplň CHANGELOG.md)."
  exit 1
fi

# popis změn: z parametru, jinak z CHANGELOG.md
if [ -z "$NOTES" ] && [ -f CHANGELOG.md ]; then
  NOTES=$(awk -v v="## $VER" '$0==v{f=1;next} /^## /{if(f)exit} f' CHANGELOG.md | sed '/./,$!d')
fi
if [ -z "$NOTES" ]; then
  echo "CHYBA: chybí popis změn – doplň do CHANGELOG.md oddíl „## ${VER}“, nebo použij --notes \"…\"."
  exit 1
fi

# 1) sestavení aplikace a DMG
echo "› Sestavuji aplikaci…"
bash "$SRC/vytvor_samostatnou_aplikaci.sh"
DMG="$HOME/Desktop/KatalogSouboru-$VER.dmg"
if [ ! -f "$DMG" ]; then echo "CHYBA: nenašel jsem $DMG – sestavení se nepovedlo."; exit 1; fi

# 2) zdrojové soubory do repozitáře (větev main)
echo "› Nahrávám zdrojové soubory do repozitáře…"
gh auth setup-git >/dev/null 2>&1 || true
TMP=$(mktemp -d /tmp/katalog-release.XXXXXX)
trap 'rm -rf "$TMP"' EXIT
git clone -q "$GIT_URL" "$TMP/repo" 2>/dev/null || { echo "CHYBA: repozitář $GIT_URL nejde stáhnout."; exit 1; }
cd "$TMP/repo"
git checkout -q -B main
find . -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
for f in katalog.py vyber_python.sh vytvor_samostatnou_aplikaci.sh publish_release.sh README.md CHANGELOG.md .gitignore tkinterdnd2; do
  if [ -e "$SRC/$f" ]; then cp -R "$SRC/$f" .; fi
done
find . -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
find . -name .DS_Store -delete 2>/dev/null || true
git add -A
if git diff --cached --quiet; then
  echo "  (zdrojové soubory beze změny)"
else
  git -c user.name="$(gh api user --jq .login 2>/dev/null || echo katalog)" \
      -c user.email="$(gh api user --jq .login 2>/dev/null || echo katalog)@users.noreply.github.com" \
      commit -q -m "Katalog souborů $VER"
fi
git push -q -u origin main
cd "$SRC"

# 3) vydání
echo "› Vytvářím vydání v${VER}…"
gh release create "v$VER" "$DMG" --repo "$REPO" --target main \
  --title "Katalog souborů $VER" --notes "$NOTES" --latest
echo
echo "✅ Vydáno: https://github.com/$REPO/releases/tag/v$VER"
echo "   Aplikace verze nižší než $VER nabídnou aktualizaci při příštím spuštění (nebo do 24 hodin)."
