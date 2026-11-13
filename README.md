# Katalog souborů

Aplikace pro macOS, která projde zadané složky (i síťové disky) a uloží katalog všech souborů a složek do souboru `.katalog`. V katalogu se pak dá rychle hledat, i když disk není připojený, a nalezenou položku zobrazit ve Finderu.

## Instalace

Otevři **Terminál** a vlož tento příkaz:

```
curl -fsSL https://raw.githubusercontent.com/Luboshort/KatalogSouboru/main/install.sh | bash
```

Stáhne nejnovější verzi, nainstaluje ji do složky Aplikace (bez práv správce do `~/Applications`) a spustí.

Proč ne stažením DMG v prohlížeči: soubor stažený prohlížečem dostane od macOS značku „staženo z internetu“
a aplikace bez placeného podpisu Apple se pak zablokuje s varováním o možném malwaru. Na firemních Macech
ji často nejde ani povolit. Soubor stažený z Terminálu tuto značku nedostane.

## Co umí

- Hledání v názvech souborů i složek: více výrazů najednou (A / NEBO), přesná fráze v uvozovkách, vyloučení `-výraz`; velikost písmen a diakritika nehrají roli.
- Výsledky jako seznam (název, upraveno, vytvořeno, velikost, cesta) s řazením, nebo jako strom složek.
- Náhled souboru mezerníkem, dvojklik = zobrazit ve Finderu.
- Aktualizace katalogu se zálohou předchozí verze do složky `Katalog archive`.
- Zvládá katalogy s miliony položek.
- Sama upozorní, když je na GitHubu nová verze.

Pro Macy s procesorem Apple (M1 a novější), macOS 26 Tahoe.

## Aktualizace

Aplikace při spuštění (a pak jednou denně) zkontroluje GitHub. Když je k dispozici nová verze, nahoře v okně
se objeví žlutý pruh s tlačítkem **Nainstalovat a restartovat** – aplikace si novou verzi stáhne, ověří,
vymění se a znovu spustí. Ručně: **Zkontrolovat aktualizace** vpravo ve spodní liště okna.

## Sestavení ze zdrojových kódů

Potřebuješ Python z [python.org](https://www.python.org/downloads/) (nativní pro Apple Silicon) a připojení k internetu.

```
bash vytvor_samostatnou_aplikaci.sh
```

Výsledek: `~/Applications/KatalogSouboru.app` a `~/Desktop/KatalogSouboru-<verze>.dmg`.

## Vydání nové verze

Potřebuješ jednou: GitHub CLI (`brew install gh` a `gh auth login`).

1. V `katalog.py` zvyš `APP_VERSION` (např. `"1.13"`) a do `CHANGELOG.md` přidej oddíl `## 1.13` se změnami.
2. V Terminálu ve složce projektu spusť:
   ```
   ./publish_release.sh
   ```
   Skript sestaví aplikaci a DMG, nahraje zdrojové soubory do repozitáře a vytvoří vydání `v1.13`
   s přiloženým DMG. Popis změn vezme z `CHANGELOG.md`, případně `--notes "…"`.

Aplikace porovnává číslo verze z tagu vydání s vlastním `APP_VERSION` a nabídne přiložené DMG ke stažení.

## Soubory

| Soubor | Co obsahuje |
| --- | --- |
| `katalog.py` | celá aplikace (Python + Tkinter) |
| `vytvor_samostatnou_aplikaci.sh` | sestavení samostatné `.app` a instalačního DMG (PyInstaller) |
| `vyber_python.sh` | výběr vhodného Pythonu pro sestavení |
| `publish_release.sh` | vydání nové verze na GitHub (sestavení, nahrání, Release) |
| `install.sh` | instalace nejnovější verze jedním příkazem v Terminálu |
| `tkinterdnd2/` | knihovna pro přetahování myší ([tkinterdnd2](https://github.com/Eliav2/tkinterdnd2), licence MIT) |
| `CHANGELOG.md` | historie verzí |
