# Historie verzí

## 1.22
- Oprava: rámeček tlačítek záložek se vždy vykreslí celý (ztmavená plocha a linka do něj už nezasahují).

## 1.21
- Odstraněn tmavší pruh mezi linkou a obsahem záložek (plocha má přesně odstín obsahu).
- Zaoblené rohy tenké linky kolem plochy záložek.

## 1.20
- Oprava: tenká linka kolem plochy záložek je vidět (byla zakrytá výplní).
- Oprava: zmizel tmavší pruh kolem obsahu záložek – plocha má jednotný systémový odstín macOS.

## 1.19
- Linka kolem plochy záložek vede po jejím vnějším okraji a nahoře středem záložek; ztmavená je celá plocha až k lince.
- Odstraněna dvojitá linka nad výpisem (nad sloupci zůstává jen čára záhlaví).

## 1.18
- Vzhled okna vrácen jako ve verzi 1.16. Přidána jen jemně ztmavená plocha záložek, tenká linka kolem ní a tenká linka kolem výpisu s posuvníky.

## 1.17
- Místo „Aktuální před…“ se ukazuje „Vytvořeno před …“: hodinami (do 24 h), dny (1–6), týdny (1–4), měsíci, lety. Barvy zůstávají (zeleně 0–3 dny, oranžově 4–6, červeně 7+).
- Nový vzhled: bílé okno, ztmavená plocha záložek s tenkým rámečkem, rámeček kolem výpisu i posuvníků.

## 1.16
- Aktualizace jedním klikem: aplikace sama nabídne novou verzi, po „Ano“ ji stáhne, nainstaluje a restartuje se bez dalších dotazů.
- Během skenování nebo načítání katalogu nabídka počká, až práce doběhne.
- Po restartu se nahoře krátce ukáže, na jakou verzi se aplikace aktualizovala.

## 1.15
- Teploměr při aktualizaci katalogu: procenta a odhad zbývajícího času. Odhad = dosavadní počet položek + průměrný denní přírůstek za posledních 30 dní × počet dní od poslední aktualizace.
- U nového katalogu se ukazuje počet nalezených položek, rychlost a doba běhu.
- Průběh je vidět i ve spodní liště na všech záložkách; po dokončení se ukáže, jak dlouho aktualizace trvala.

## 1.14
- Aktualizace se instaluje přímo v aplikaci tlačítkem „Nainstalovat a restartovat“ – bez prohlížeče a bez varování macOS.
- Instalace jedním příkazem v Terminálu (install.sh) – funguje i na firemních Macech, kde nejde povolit aplikaci z internetu.

## 1.13
- Tlačítko „Zkontrolovat aktualizace“ a číslo verze jsou ve spodní liště, vidět na všech záložkách.

## 1.12
- Automatická kontrola aktualizací na GitHubu: při nové verzi se nahoře v okně objeví pruh s tlačítkem „Stáhnout“.
- Tlačítko „Zkontrolovat aktualizace“ na záložce „Vytvořit katalog“.

## 1.11
- Prázdný nebo neúplný katalog už nepřepíše dobrý: při nedostupné složce se nic neuloží, při úbytku víc než 20 % položek se aplikace zeptá.
- Chyba čtení na síťovém disku nezastaví skenování; nepřečtené složky jsou v souboru „<katalog> - nectene slozky.txt“.
- Katalog z „Katalog archive“ je jen pro čtení.
- Pojistka proti zamrznutí a protokol chyb v ~/Library/Logs/KatalogSouboru.log.
- Hledání a řazení velkých katalogů běží na pozadí.

## 1.10
- Před každou aktualizací se současný katalog zálohuje do složky „Katalog archive“.

## 1.9
- Šipka vlevo zavře všechny vybrané složky.
- Barevný údaj „Aktuální před X dny“ (zeleně 0–3, oranžově 4–6, červeně 7+).

## 1.8
- Ukazatel průběhu při otevírání katalogu.

## 1.7
- Strom celého katalogu bez omezení (obsah složky se načte po rozbalení).
- Výběr více položek, rozbalování a sbalování šipkami, Cmd+A.

## 1.6
- Přepínač „Zobrazit strom“.
- Název aplikace a DMG bez diakritiky.

## 1.5
- Sloupec Velikost a řazení podle velikosti.
- „Hledat i složky“, složky vždy nad soubory.
- Náhled mezerníkem v panelu okna (šipky dál fungují), velký náhled Shift+mezerník.

## 1.4
- Po otevření katalogu se hned vypíše obsah.
- Rychlý náhled mezerníkem.

## 1.3
- Nativní běh na Apple Silicon.

## 1.2
- Číslo verze v titulku, přetažení složky na ikonu v Docku.

## 1.1
- Přetahování složek myší, výsledky jako seznam s řazením podle sloupců.

## 1.0
- První verze: katalog složek, hledání v názvech, zobrazení ve Finderu.
