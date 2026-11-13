# Katalog souborů

Aplikace pro macOS, která projde zadané složky (i síťové disky) a uloží katalog všech souborů a složek do souboru `.katalog`. V katalogu se pak dá rychle hledat, i když disk není připojený, a nalezenou položku zobrazit ve Finderu.

- Hledání v názvech souborů i složek: více výrazů najednou (A / NEBO), přesná fráze v uvozovkách, vyloučení `-výraz`; velikost písmen a diakritika nehrají roli.
- Výsledky jako seznam (název, upraveno, vytvořeno, velikost, cesta) s řazením, nebo jako strom složek.
- Náhled souboru mezerníkem, dvojklik = zobrazit ve Finderu.
- Aktualizace katalogu se zálohou předchozí verze do složky `Katalog archive`.
- Zvládá katalogy s miliony položek.
- Sama upozorní, když je na GitHubu nová verze.

Pro Macy s procesorem Apple (M1 a novější), macOS 26 Tahoe.

## Instalace

1. Na stránce [Releases](https://github.com/Luboshort/KatalogSouboru/releases) stáhni nejnovější `KatalogSouboru-<verze>.dmg`.
2. Otevři DMG a přetáhni `KatalogSouboru` do složky Aplikace.
3. Při prvním spuštění macOS napíše, že aplikaci nemůže ověřit: klikni na „Hotovo“, otevři **Nastavení systému → Soukromí a zabezpečení**, sjeď dolů a klikni na **Přesto otevřít**. Stačí jednou.

## Aktualizace

Aplikace při spuštění (a pak jednou denně) zkontroluje GitHub. Když je k dispozici nová verze, nahoře v okně se objeví žlutý pruh s tlačítkem **Stáhnout**. Ručně: tlačítko **Zkontrolovat aktualizace** na záložce „Vytvořit katalog“.

## Sestavení ze zdrojových kódů

Potřebuješ Python z [python.org](https://www.python.org/downloads/) (nativní pro Apple Silicon) a připojení k internetu.

```
bash vytvor_samostatnou_aplikaci.sh
```

Výsledek: `~/Applications/KatalogSouboru.app` a `~/Desktop/KatalogSouboru-<verze>.dmg`.

## Vydání nové verze

1. V `katalog.py` zvyš `APP_VERSION` (např. `"1.13"`) a doplň změny do `CHANGELOG.md`.
2. Sestav aplikaci (`bash vytvor_samostatnou_aplikaci.sh`) a vyzkoušej ji.
3. Nahraj změněné soubory do tohoto repozitáře.
4. **Releases → Draft a new release**: tag `v1.13` (písmeno v + přesně číslo z `APP_VERSION`), název `Katalog souborů 1.13`, do popisu změny z `CHANGELOG.md`, přilož `KatalogSouboru-1.13.dmg` a **Publish release**.

Aplikace porovnává číslo verze z tagu s vlastním `APP_VERSION`; DMG přiložené k vydání nabídne ke stažení.

## Soubory

| Soubor | Co obsahuje |
| --- | --- |
| `katalog.py` | celá aplikace (Python + Tkinter) |
| `vytvor_samostatnou_aplikaci.sh` | sestavení samostatné `.app` a instalačního DMG (PyInstaller) |
| `vyber_python.sh` | výběr vhodného Pythonu pro sestavení |
| `tkinterdnd2/` | knihovna pro přetahování myší ([tkinterdnd2](https://github.com/Eliav2/tkinterdnd2), licence MIT) |
| `CHANGELOG.md` | historie verzí |
