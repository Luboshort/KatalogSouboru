#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Katalog souborů pro macOS
-------------------------
1) Projde zadané složky (i více) a vytvoří katalog všech souborů a složek.
   Složky lze přidat tlačítkem nebo přetažením myší na okno.
2) Katalog uloží do souboru (*.katalog = komprimovaný JSON).
3) Katalog umí otevřít a hledat v názvech souborů i složek (více výrazů
   najednou, A / NEBO, vyloučení -výraz, bez ohledu na velikost písmen a diakritiku).
4) Výsledky ukáže jako seznam (název, upraveno, vytvořeno, cesta), řazení
   klikem na hlavičku sloupce; dvojklik = zobrazit ve Finderu.

Spuštění:  python3 katalog.py  [cesta/k/souboru.katalog]
"""

import os
import sys
import json
import gzip
import heapq
import hashlib
import shutil
import tempfile
import traceback
import re
import time
import queue
import shlex
import threading
import subprocess
import unicodedata
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import tkinter.font as tkfont

# Drag & drop (volitelné – knihovna tkinterdnd2 leží vedle tohoto souboru)
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    _DnDBase = TkinterDnD.DnDWrapper
except Exception:
    TkinterDnD, DND_FILES = None, None
    _DnDBase = object

APP_NAME = "Katalog souborů"
APP_VERSION = "1.16"
APP_TITLE = f"{APP_NAME} {APP_VERSION}"
EXT = ".katalog"
ARCHIVE_DIR = "Katalog archive"
MAX_RESULTS = 10000
SETTINGS_FILE = os.path.expanduser("~/.katalog_souboru_nastaveni.json")
LOG_FILE = os.path.expanduser("~/Library/Logs/KatalogSouboru.log")
SHRINK_LIMIT = 0.8          # nový katalog s méně než 80 % položek starého → zeptat se
BG_THRESHOLD = 50_000       # od tohoto počtu položek se hledá/řadí na pozadí

# Aktualizace: nová verze = nové „Release“ na GitHubu s tagem v<verze> (např. v1.13) a přiloženým DMG
GITHUB_REPO = "Luboshort/KatalogSouboru"
UPDATE_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases"
UPDATE_EVERY_H = 24         # při běhu kontrolovat jednou za den

# Složky, které macOS zobrazuje jako jeden soubor – jejich obsah se (volitelně) neprochází
PACKAGE_EXTS = {
    ".app", ".bundle", ".framework", ".plugin", ".kext", ".photoslibrary",
    ".musiclibrary", ".tvlibrary", ".imovielibrary", ".fcpbundle",
    ".photolibrary", ".aplibrary", ".lrlibrary", ".xcodeproj", ".xcworkspace",
    ".pages", ".numbers", ".key", ".rtfd", ".logicx", ".band", ".sparsebundle",
}

COLUMNS = {           # id: (nadpis, šířka, roztahovat)
    "name":  ("Název", 300, True),
    "mtime": ("Upraveno", 135, False),
    "ctime": ("Vytvořeno", 135, False),
    "size":  ("Velikost", 95, False),
    "path":  ("Cesta", 420, True),
}



# ---------------------------------------------------------------- pomocné funkce
def norm(text: str) -> str:
    """Malá písmena + bez diakritiky (macOS ukládá názvy v NFD, to řeší taky)."""
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c)).casefold()


def human_size(n: int) -> str:
    for unit in ("B", "kB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)


def fmt_n(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def fmt_time(ts) -> str:
    return time.strftime("%d.%m.%Y %H:%M", time.localtime(ts)) if ts else ""


def parse_query(q: str):
    """Rozdělí dotaz na výrazy. Uvozovky drží frázi pohromadě, -výraz vylučuje."""
    try:
        tokens = shlex.split(q)
    except ValueError:
        tokens = q.split()
    include, exclude = [], []
    for t in tokens:
        if t.startswith("-") and len(t) > 1:
            exclude.append(norm(t[1:]))
        elif t:
            include.append(norm(t))
    return include, exclude


def log_error(text):
    """Zapíše chybu do ~/Library/Logs/KatalogSouboru.log (u aplikace není vidět terminál)."""
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + text.rstrip() + "\n")
    except Exception:
        pass
    print(text, file=sys.stderr)


def is_archived(path):
    """Katalog uložený ve složce „Katalog archive“ = archivní verze, jen pro čtení."""
    return bool(path) and os.path.basename(os.path.dirname(os.path.abspath(path))) == ARCHIVE_DIR


def read_count(path):
    """Počet položek z hlavičky katalogu bez načítání celého souboru (None = neznámý)."""
    try:
        with gzip.open(path, "rb") as f:
            head = f.read(256 * 1024)
        m = re.search(rb'"count":(\d+)', head.split(b'"entries"', 1)[0])
        return int(m.group(1)) if m else None
    except Exception:
        return None


GROWTH_DAYS = 30            # přírůstek se počítá z posledních 30 dní


def growth_per_day(entries, ref_time):
    """Průměrný denní přírůstek položek za posledních GROWTH_DAYS dní před ref_time
    (podle data vytvoření; kde chybí, podle data úpravy)."""
    lo = ref_time - GROWTH_DAYS * 86400
    n = 0
    for e in entries:
        t = e[5] or e[4]
        if lo <= t <= ref_time:
            n += 1
    return n / GROWTH_DAYS


def read_header(path):
    """Hlavička katalogu (kořeny, datum, počet, přírůstek) bez načítání celého souboru."""
    try:
        with gzip.open(path, "rb") as f:
            head = f.read(512 * 1024)
        part = head.split(b',"entries":', 1)
        if len(part) < 2:
            return None
        return json.loads(part[0].decode("utf-8") + "}")
    except Exception:
        return None


def scan_estimate(old_entries, old_roots, old_created, old_count, old_growth, roots, now=None):
    """Odhad počtu položek nového skenování = dosavadní počet + denní přírůstek × dny od poslední
    aktualizace. None, když to nejde (nový katalog, jiné složky, neznámý počet)."""
    now = now or time.time()
    if not old_created or list(old_roots or []) != list(roots):
        return None
    if old_entries is not None:
        n0, growth = len(old_entries), growth_per_day(old_entries, old_created)
    else:
        n0, growth = old_count, (old_growth or 0.0)
    if not n0:
        return None
    days = max(0.0, (now - old_created) / 86400)
    return {"total": n0 + growth * days, "base": n0, "growth": growth, "days": days}


def fmt_dur(sec):
    sec = int(max(0, sec))
    if sec < 60:
        return f"{sec} s"
    if sec < 3600:
        return f"{round(sec / 60)} min"
    h, m = divmod(round(sec / 60), 60)
    return f"{h} h {m} min" if m else f"{h} h"


def version_tuple(v):
    """„v1.12“ → (1, 12) – čísla se porovnávají číselně, takže 1.12 > 1.9."""
    return tuple(int(x) for x in re.findall(r"\d+", v or "")) or (0,)


def fetch_latest_release(timeout=10):
    """Nejnovější vydání z GitHubu: {"version", "page", "dmg", "notes"}.
    None = na GitHubu zatím žádné vydání není. Při chybě sítě vyhodí výjimku."""
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "KatalogSouboru-update-check"}
    try:
        # systémový curl používá certifikáty macOS (Python z python.org je v aplikaci nemusí najít)
        r = subprocess.run(["curl", "-sS", "-L", "--max-time", str(timeout), "-w", "\n%{http_code}"]
                           + sum((["-H", f"{k}: {v}"] for k, v in headers.items()), []) + [UPDATE_API],
                           capture_output=True, text=True, timeout=timeout + 5)
        if r.returncode != 0:
            raise OSError(r.stderr.strip() or f"curl skončil s kódem {r.returncode}")
        body, _, code = r.stdout.rpartition("\n")
        code = int(code or 0)
    except FileNotFoundError:                       # bez curl – záložně přes Python
        import urllib.request, urllib.error
        try:
            with urllib.request.urlopen(urllib.request.Request(UPDATE_API, headers=headers),
                                        timeout=timeout) as resp:
                body, code = resp.read().decode("utf-8"), resp.status
        except urllib.error.HTTPError as ex:
            body, code = "", ex.code
    if code == 404:
        return None
    if code != 200:
        raise OSError(f"GitHub odpověděl kódem {code}")
    data = json.loads(body)
    asset = next((a for a in data.get("assets", [])
                  if str(a.get("name", "")).lower().endswith(".dmg")), {})
    return {"version": str(data.get("tag_name", "")).lstrip("vV"),
            "page": data.get("html_url") or RELEASES_PAGE,
            "dmg": asset.get("browser_download_url"), "digest": asset.get("digest") or "",
            "notes": data.get("body") or ""}


# ---------------------------------------------------------------- instalace aktualizace
# Aplikace si DMG stáhne sama přes curl – takový soubor nedostane od macOS značku „staženo
# z internetu“, takže nová verze se spustí bez varování Gatekeeperu (na rozdíl od stažení prohlížečem).

def current_app_bundle():
    """Cesta k běžící KatalogSouboru.app (None, když neběží jako sestavená aplikace)."""
    if not getattr(sys, "frozen", False):
        return None
    app = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(sys.executable))))
    return app if app.endswith(".app") else None


def install_target(app):
    """Kam nainstalovat novou verzi: na místo současné, když tam jde zapisovat, jinak do ~/Applications."""
    parent = os.path.dirname(app)
    if not app.startswith("/Volumes/") and os.access(parent, os.W_OK) and os.access(app, os.W_OK):
        return app
    return os.path.join(os.path.expanduser("~/Applications"), os.path.basename(app))


def prepare_update(rel, workdir):
    """Stáhne DMG, ověří kontrolní součet a podpis a vybalí aplikaci. Vrátí cestu k nové .app."""
    dmg = os.path.join(workdir, "update.dmg")
    r = subprocess.run(["curl", "-fsSL", "--max-time", "900", "-o", dmg, rel["dmg"]],
                       capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(dmg):
        raise OSError("Stažení se nepovedlo: " + (r.stderr.strip() or f"kód {r.returncode}"))
    if rel.get("digest", "").startswith("sha256:"):
        h = hashlib.sha256()
        with open(dmg, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        if h.hexdigest() != rel["digest"].split(":", 1)[1]:
            raise OSError("Stažený soubor je poškozený (nesouhlasí kontrolní součet). Zkus to znovu.")
    mnt = os.path.join(workdir, "mnt")
    os.makedirs(mnt, exist_ok=True)
    r = subprocess.run(["hdiutil", "attach", "-nobrowse", "-readonly", "-noautoopen", "-mountpoint", mnt, dmg],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise OSError("DMG nejde otevřít: " + r.stderr.strip())
    try:
        apps = [x for x in os.listdir(mnt) if x.endswith(".app")]
        if not apps:
            raise OSError("V DMG není aplikace.")
        new = os.path.join(workdir, "new", apps[0])
        os.makedirs(os.path.dirname(new), exist_ok=True)
        r = subprocess.run(["ditto", os.path.join(mnt, apps[0]), new], capture_output=True, text=True)
        if r.returncode != 0:
            raise OSError("Aplikaci nejde zkopírovat: " + r.stderr.strip())
    finally:
        subprocess.run(["hdiutil", "detach", "-quiet", "-force", mnt], capture_output=True)
    subprocess.run(["xattr", "-cr", new], capture_output=True)
    r = subprocess.run(["codesign", "--verify", new], capture_output=True, text=True)
    if r.returncode != 0:
        raise OSError("Podpis nové verze nesouhlasí: " + r.stderr.strip())
    return new


# Pomocný skript: počká, až se aplikace zavře, vymění ji za novou (při chybě vrátí původní) a spustí.
UPDATE_HELPER = r"""#!/bin/bash
PID="${1}"; NEW="${2}"; APP="${3}"; LOG="${4}"
WORK="$(dirname "$(dirname "${NEW}")")"
exec >>"${LOG}" 2>&1
echo "$(date '+%Y-%m-%d %H:%M:%S') [aktualizace] ${NEW} -> ${APP}"
for i in $(seq 1 240); do kill -0 "${PID}" 2>/dev/null || break; sleep 0.5; done
mkdir -p "$(dirname "${APP}")"
OLD="${APP}.puvodni"
rm -rf "${OLD}"
if [ -e "${APP}" ]; then
  if ! mv "${APP}" "${OLD}"; then echo "  puvodni verzi nejde presunout"; open "${APP}"; exit 1; fi
fi
if ditto "${NEW}" "${APP}"; then
  rm -rf "${OLD}"
  xattr -cr "${APP}" 2>/dev/null
  echo "  hotovo"
else
  echo "  chyba - vracim puvodni verzi"
  rm -rf "${APP}"
  if [ -e "${OLD}" ]; then mv "${OLD}" "${APP}"; fi
fi
open "${APP}"
rm -rf "${WORK}"
"""


def load_settings():
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(data):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


# ---------------------------------------------------------------- katalog
class Catalog:
    """entries: [index_kořene, relativní_cesta, je_složka, velikost, upraveno, vytvořeno]"""

    def __init__(self, roots=None, entries=None, created=None, options=None):
        self.roots = roots or []
        self.entries = entries or []
        self.created = created or int(time.time())
        self.options = options or {}
        self.path = None
        self._names = None
        self._paths = None

    def save(self, path):
        data = {
            "format": "katalog-souboru",
            "version": 2,
            "created": self.created,
            "roots": self.roots,
            "options": self.options,
            "growth_per_day": round(growth_per_day(self.entries, self.created), 2),
            "count": len(self.entries),
            "entries": self.entries,
        }
        tmp = path + ".tmp"
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, path)
        self.path = path

    @classmethod
    def load(cls, path, progress=None):
        """Načte katalog. progress(procenta, text) hlásí postup (volá se z vlákna)."""
        prog = progress or (lambda pct, txt: None)
        total = max(1, os.path.getsize(path))
        chunks = []
        prog(0, "Načítám soubor…")
        with open(path, "rb") as raw, gzip.GzipFile(fileobj=raw) as gz:
            while True:
                b = gz.read(8 << 20)
                if not b:
                    break
                chunks.append(b)
                prog(10 * raw.tell() / total, "Načítám soubor…")
        blob = b"".join(chunks)
        del chunks
        # Převod dat neumí hlásit postup – teploměr se posouvá podle odhadu času.
        est = max(0.5, len(blob) / 30e6)          # odhad ~30 MB/s
        done = threading.Event()

        def ticker():
            t0 = time.time()
            while not done.wait(0.2):
                prog(10 + 40 * min(0.97, (time.time() - t0) / est), "Zpracovávám data…")
        threading.Thread(target=ticker, daemon=True).start()
        try:
            data = json.loads(blob)
        finally:
            done.set()
        del blob
        if data.get("format") != "katalog-souboru":
            raise ValueError("Soubor není katalog vytvořený touto aplikací.")
        entries = data["entries"]
        prog(50, "Kontroluji položky…")
        for e in entries:            # starší katalogy neměly datum vytvoření
            if len(e) < 6:
                e.append(0)
        cat = cls(data["roots"], entries, data.get("created"), data.get("options"))
        cat.path = path
        cat.prepare(lambda f: prog(52 + 25 * f, "Připravuji hledání…"))
        cat.children_index(lambda f: prog(77 + 20 * f, "Připravuji strom složek…"))
        prog(97, "Počítám souhrn…")
        cat.compute_stats()
        prog(100, "Hotovo")
        return cat

    def prepare(self, progress=None):
        """Předpočítá normalizované názvy pro rychlé hledání."""
        ents, n = self.entries, len(self.entries)
        step = 100_000
        names = []
        for start in range(0, n, step):
            names.extend(norm(e[1].rsplit("/", 1)[-1]) for e in ents[start:start + step])
            if progress:
                progress(min(1.0, (start + step) / max(1, n)))
        self._names = names

    def compute_stats(self):
        self.n_dirs = sum(1 for e in self.entries if e[2])
        self.total_size = sum(e[3] for e in self.entries)

    def index_by_path(self):
        """Slovník (kořen, relativní cesta) → index položky; vytvoří se jednou."""
        if getattr(self, "_by_path", None) is None:
            self._by_path = {(e[0], e[1]): i for i, e in enumerate(self.entries)}
        return self._by_path

    def children_index(self, progress=None):
        """Slovník (kořen, cesta složky) → indexy přímého obsahu; vytvoří se jednou."""
        if getattr(self, "_children", None) is None:
            ch = {}
            n = max(1, len(self.entries))
            for i, e in enumerate(self.entries):
                if progress and i % 200_000 == 0:
                    progress(i / n)
                k = (e[0], e[1].rpartition("/")[0])
                lst = ch.get(k)
                if lst is None:
                    ch[k] = [i]
                else:
                    lst.append(i)
            self._children = ch
        return self._children

    def has_ctime(self):
        return any(e[5] for e in self.entries[:200])

    def full_path(self, i):
        e = self.entries[i]
        root = self.roots[e[0]]
        return os.path.join(root, e[1]) if e[1] else root

    def parent_path(self, i):
        return os.path.dirname(self.full_path(i))

    def name(self, i):
        return self.entries[i][1].rsplit("/", 1)[-1]

    def search(self, query, mode_all=True, kind="all", in_path=False):
        include, exclude = parse_query(query)
        if not include and not exclude:
            return []
        if self._names is None:
            self.prepare()
        if in_path and self._paths is None:
            self._paths = [norm(self.roots[e[0]] + "/" + e[1]) for e in self.entries]
        hay = self._paths if in_path else self._names
        match_inc = all if mode_all else any

        results = []
        for i, h in enumerate(hay):
            e = self.entries[i]
            if kind == "files" and e[2]:
                continue
            if kind == "dirs" and not e[2]:
                continue
            if include and not match_inc(t in h for t in include):
                continue
            if exclude and any(t in h for t in exclude):
                continue
            results.append(i)
        return results


def archive_catalog(path):
    """Zkopíruje existující katalog do složky „Katalog archive“ vedle něj.
    Do názvu přidá datum a čas poslední změny katalogu. Vrátí cestu k záloze
    (nebo None, pokud katalog ještě neexistuje)."""
    if not os.path.isfile(path):
        return None
    parent = os.path.dirname(os.path.abspath(path))
    folder = parent if is_archived(path) else os.path.join(parent, ARCHIVE_DIR)
    os.makedirs(folder, exist_ok=True)
    stem = os.path.basename(path)[:-len(EXT)] if path.endswith(EXT) else os.path.basename(path)
    stamp = time.strftime("%Y-%m-%d %H-%M", time.localtime(os.path.getmtime(path)))
    target = os.path.join(folder, f"{stem} {stamp}{EXT}")
    n = 2
    while os.path.exists(target):
        if os.path.getsize(target) == os.path.getsize(path):
            return target                 # tahle verze už v archivu je
        target = os.path.join(folder, f"{stem} {stamp} ({n}){EXT}")   # jiná verze ze stejné minuty
        n += 1
    tmp = target + ".tmp"
    shutil.copy2(path, tmp)               # zachová i datum změny
    os.replace(tmp, target)
    return target


def scan_folders(roots, skip_hidden, skip_packages, progress, cancel):
    """Projde složky. Vrátí slovník:
       entries      – položky katalogu (None = zrušeno)
       failed_dirs  – [(cesta, důvod)] složky, které nešlo (celé) přečíst
       failed_items – počet jednotlivých položek, které nešlo přečíst
       failed_roots – [(kořen, důvod)] katalogizované složky, které nejsou dostupné
    Chyba při čtení jedné složky skenování nezastaví."""
    entries, failed_dirs, failed_roots = [], [], []
    failed_items = 0
    last = time.time()
    for ri, root in enumerate(roots):
        prefix_len = len(root) if root.endswith("/") else len(root) + 1
        try:
            if not os.path.isdir(root):
                raise FileNotFoundError("složka neexistuje nebo disk není připojený")
            with os.scandir(root):
                pass
        except OSError as ex:
            failed_roots.append((root, ex.strerror or str(ex)))
            continue
        stack = [root]
        while stack:
            if cancel.is_set():
                return {"entries": None}
            d = stack.pop()
            try:
                it = os.scandir(d)
            except OSError as ex:
                failed_dirs.append((d, ex.strerror or str(ex)))
                continue
            with it:
                while True:
                    try:
                        e = next(it)
                    except StopIteration:
                        break
                    except OSError as ex:          # chyba uprostřed výpisu (síťový disk)
                        failed_dirs.append((d, ex.strerror or str(ex)))
                        break
                    if skip_hidden and e.name.startswith("."):
                        continue
                    try:
                        is_dir = e.is_dir(follow_symlinks=False)
                        st = e.stat(follow_symlinks=False)
                    except OSError:
                        failed_items += 1
                        continue
                    birth = getattr(st, "st_birthtime", 0) or 0
                    entries.append([ri, e.path[prefix_len:], 1 if is_dir else 0,
                                    0 if is_dir else st.st_size,
                                    int(st.st_mtime), int(birth)])
                    if is_dir:
                        if not (skip_packages and os.path.splitext(e.name)[1].lower() in PACKAGE_EXTS):
                            stack.append(e.path)
            now = time.time()
            if now - last > 0.2:
                progress(len(entries), d)
                last = now
        if not os.path.isdir(root):                    # disk se během skenování odpojil
            failed_roots.append((root, "disk se během skenování odpojil"))
    return {"entries": entries, "failed_dirs": failed_dirs,
            "failed_items": failed_items, "failed_roots": failed_roots}


def error_report_path(catalog_path):
    return catalog_path[:-len(EXT)] + " - nectene slozky.txt" if catalog_path.endswith(EXT) \
        else catalog_path + " - nectene slozky.txt"


def write_error_report(catalog_path, failed_dirs, failed_items):
    """Seznam nepřečtených složek vedle katalogu (nebo smazání starého seznamu)."""
    rp = error_report_path(catalog_path)
    if not failed_dirs and not failed_items:
        try:
            os.remove(rp)
        except OSError:
            pass
        return None
    with open(rp, "w", encoding="utf-8") as f:
        f.write(f"Katalog: {catalog_path}\nSkenováno: {time.strftime('%d.%m.%Y %H:%M')}\n\n")
        f.write(f"Tyto složky se nepodařilo přečíst (celé nebo zčásti), v katalogu jejich obsah chybí: "
                f"{len(failed_dirs)}\n\n")
        for d, why in failed_dirs:
            f.write(f"{d}\t{why}\n")
        if failed_items:
            f.write(f"\nDalší jednotlivé položky, které nešlo přečíst: {failed_items}\n")
    return rp


# ---------------------------------------------------------------- aplikace
class App(tk.Tk, _DnDBase):
    def __init__(self, open_path=None):
        tk.Tk.__init__(self)
        self.title(APP_TITLE)
        self.geometry("1100x700")
        self.minsize(800, 480)

        self.dnd = False
        self.dnd_error = ""
        self._init_dnd()
        # přetažení na ikonu aplikace v Docku / ve Finderu (macOS Apple Event)
        try:
            self.createcommand("::tk::mac::OpenDocument", self._on_open_documents)
        except Exception:
            pass

        self.catalog = None
        self.results = []              # indexy položek katalogu
        self.sort_col, self.sort_desc = "name", False
        self.msgs = queue.Queue()
        self.cancel_ev = threading.Event()
        self.busy = False
        self.settings = load_settings()

        self._build_ui()
        if self.dnd:
            # registrovat až po zobrazení okna – na macOS to jinak nemusí fungovat
            self.after(300, lambda: self._register_drop(self))
        self.after(100, self._poll)
        self.after(4000, self._update_tick)
        self._after_update_notice()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        path = open_path or self.settings.get("last_catalog")
        if path and os.path.exists(path):
            self.open_catalog(path)
        else:
            self.nb.select(self.tab_scan)

    # ---------- UI
    def _build_ui(self):
        self._build_update_bar()
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=8, pady=8)
        self.tab_search = ttk.Frame(self.nb, padding=10)
        self.tab_scan = ttk.Frame(self.nb, padding=10)
        self.nb.add(self.tab_search, text="  Hledat v katalogu  ")
        self.nb.add(self.tab_scan, text="  Vytvořit katalog  ")
        self._build_scan_tab()
        self._build_search_tab()


        # spodní lišta – stejná na všech záložkách: stav vlevo, verze a aktualizace vpravo
        self.status = tk.StringVar(value="Připraveno.")
        bar = ttk.Frame(self, padding=(10, 2, 10, 6))
        bar.pack(side="bottom", fill="x", before=self.nb)     # při zmenšení okna zůstane vidět
        ttk.Separator(self).pack(side="bottom", fill="x", before=self.nb)   # čára nad lištou
        ttk.Button(bar, text="Zkontrolovat aktualizace",
                   command=lambda: self.check_updates(manual=True)).pack(side="right")
        ttk.Label(bar, text=f"verze {APP_VERSION}", foreground="gray").pack(side="right", padx=(10, 8))
        ttk.Label(bar, textvariable=self.status, anchor="w").pack(side="left", fill="x", expand=True)

    def _build_scan_tab(self):
        t = self.tab_scan
        ttk.Label(t, text="Složky, které se zařadí do katalogu – přidej je tlačítkem nebo přetáhni "
                          "myší z Finderu sem do okna (případně na ikonu aplikace v Docku):").pack(anchor="w")

        box = ttk.Frame(t)
        box.pack(fill="both", expand=True, pady=(4, 6))
        self.folder_list = tk.Listbox(box, height=8, selectmode="extended", activestyle="none")
        self.folder_list.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(box, orient="vertical", command=self.folder_list.yview)
        sb.pack(side="left", fill="y")
        self.folder_list.configure(yscrollcommand=sb.set)

        btns = ttk.Frame(box, padding=(8, 0, 0, 0))
        btns.pack(side="left", fill="y")
        ttk.Button(btns, text="Přidat složku…", command=self.add_folder).pack(fill="x")
        ttk.Button(btns, text="Odebrat vybrané", command=self.remove_folders).pack(fill="x", pady=4)
        ttk.Button(btns, text="Vyčistit", command=lambda: self.folder_list.delete(0, "end")).pack(fill="x")

        opts = ttk.Frame(t)
        opts.pack(fill="x", pady=4)
        self.var_hidden = tk.BooleanVar(value=True)
        self.var_packages = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="Přeskočit skryté soubory (.DS_Store apod.)",
                        variable=self.var_hidden).pack(anchor="w")
        ttk.Checkbutton(opts, text="Neprocházet obsah balíčků (.app, knihovny Fotek, .pages…)",
                        variable=self.var_packages).pack(anchor="w")

        run = ttk.Frame(t)
        run.pack(fill="x", pady=(10, 0))
        self.btn_scan = ttk.Button(run, text="Skenovat a uložit katalog…", command=self.start_scan)
        self.btn_scan.pack(side="left")
        self.btn_cancel = ttk.Button(run, text="Zrušit", command=self.cancel_ev.set, state="disabled")
        self.btn_cancel.pack(side="left", padx=6)
        self.progress = ttk.Progressbar(run, mode="indeterminate")
        self.progress.pack(side="left", fill="x", expand=True, padx=(6, 0))

        self.scan_info = tk.StringVar()
        ttk.Label(t, textvariable=self.scan_info, foreground="gray").pack(anchor="w", pady=(6, 0))

        foot = ttk.Frame(t)
        foot.pack(fill="x", side="bottom")
        if self.dnd:
            ttk.Label(foot, text="✓ Přetahování myší je zapnuté", foreground="green").pack(side="left")
        else:
            ttk.Label(foot, foreground="red", wraplength=900, justify="left",
                      text="Přetahování do okna nefunguje: " + self.dnd_error +
                           "\nŘešení: na Macu s Apple Silicon spusť aplikaci nativním Pythonem z python.org, na Intel Macu Pythonem 3.13. "
                           "Do té doby lze složky přetáhnout na ikonu aplikace v Docku nebo přidat tlačítkem."
                      ).pack(side="left")

    def _build_search_tab(self):
        t = self.tab_search

        top = ttk.Frame(t)
        top.pack(fill="x")
        ttk.Button(top, text="Otevřít katalog…", command=self.ask_open).pack(side="left")
        self.btn_rescan = ttk.Button(top, text="Aktualizovat katalog", command=self.rescan, state="disabled")
        self.btn_rescan.pack(side="left", padx=6)
        self.load_lbl = ttk.Label(top, foreground="gray")
        self.load_bar = ttk.Progressbar(top, mode="determinate", maximum=100, length=320)
        info_row = ttk.Frame(t)
        info_row.pack(anchor="w", pady=(6, 8), fill="x")
        self._info_row = info_row
        self.cat_info = tk.StringVar(value="Není otevřený žádný katalog.")
        ttk.Label(info_row, textvariable=self.cat_info, foreground="gray").pack(side="left")
        age_font = tkfont.nametofont("TkDefaultFont").copy()
        age_font.configure(weight="bold")
        self._age_font = age_font
        self.cat_age = ttk.Label(info_row, font=age_font)
        self.cat_age.pack(side="left")
        self.cat_info2 = tk.StringVar()
        ttk.Label(info_row, textvariable=self.cat_info2, foreground="gray").pack(side="left")
        self.cat_warn = ttk.Label(t, foreground="#c0392b")

        row = ttk.Frame(t)
        row.pack(fill="x")
        ttk.Label(row, text="Hledat:").pack(side="left")
        self.var_query = tk.StringVar()
        self.entry = ttk.Entry(row, textvariable=self.var_query)
        self.entry.pack(side="left", fill="x", expand=True, padx=6)
        self.entry.bind("<Return>", lambda e: self.do_search())
        ttk.Button(row, text="Hledat", command=self.do_search).pack(side="left")

        opts = ttk.Frame(t)
        opts.pack(fill="x", pady=6)
        self.var_mode = tk.StringVar(value="all")
        ttk.Radiobutton(opts, text="Obsahuje všechny výrazy (A)", value="all",
                        variable=self.var_mode).pack(side="left")
        ttk.Radiobutton(opts, text="Kterýkoli výraz (NEBO)", value="any",
                        variable=self.var_mode).pack(side="left", padx=(10, 20))
        self.var_dirs = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="Hledat i složky", variable=self.var_dirs,
                        command=self.do_search).pack(side="left")
        self.var_inpath = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="Hledat i v názvech nadřazených složek",
                        variable=self.var_inpath).pack(side="left", padx=(16, 0))

        ttk.Label(t, foreground="gray",
                  text='Tip: klient formát → obsahuje oba výrazy · "výroční zpráva" = přesná fráze · '
                       '-koncept = vyloučit · velikost písmen a diakritika nehrají roli'
                  ).pack(anchor="w", pady=(0, 6))

        # Seznam výsledků – hlavička je pevná, roluje se jen obsah
        above = ttk.Frame(t)
        above.pack(fill="x", pady=(0, 4))
        self.var_tree = tk.BooleanVar(value=False)
        ttk.Checkbutton(above, text="Zobrazit strom", variable=self.var_tree,
                        command=self._refill).pack(side="left")
        self.work_bar = ttk.Progressbar(above, mode="indeterminate", length=160)
        self.work_lbl = ttk.Label(above, foreground="gray")
        self._work_gen = 0

        self.split = ttk.PanedWindow(t, orient="horizontal")
        self.split.pack(fill="both", expand=True)
        tf = ttk.Frame(self.split)
        self.split.add(tf, weight=3)
        self._build_preview_pane()
        tf.rowconfigure(0, weight=1)
        tf.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(tf, columns=list(COLUMNS), show="headings", selectmode="extended")
        for col, (title, width, stretch) in COLUMNS.items():
            self.tree.heading(col, text=title, anchor="w", command=lambda c=col: self.sort_by(c))
            self.tree.column(col, width=width, minwidth=70, stretch=stretch,
                             anchor="e" if col == "size" else "w")
        ys = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        xs = ttk.Scrollbar(tf, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        self.tree.heading("#0", text="Název", anchor="w", command=lambda: self.sort_by("name"))
        self.tree.column("#0", width=420, minwidth=150, stretch=True)
        bold = tkfont.nametofont("TkDefaultFont").copy()
        bold.configure(weight="bold")
        self._bold = bold
        self.tree.tag_configure("match", font=bold)
        self.tree.tag_configure("ctx", foreground="gray50")
        self._update_headings()

        self.tree.bind("<Double-1>", self.on_double)
        self.tree.bind("<Return>", lambda e: self.reveal_selected())
        self.tree.bind("<space>", self.toggle_preview)
        self.tree.bind("<Shift-space>", self.big_quicklook)
        self.tree.bind("<Escape>", lambda e: self.hide_preview())
        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        self.tree.bind("<<TreeviewOpen>>", self._on_tree_open)
        self.tree.bind("<Right>", self._on_right)
        self.tree.bind("<Left>", self._on_left)
        for seq in ("<Command-a>", "<Command-A>", "<Control-a>"):
            self.tree.bind(seq, self.select_all)
        self.tree.bind("<Button-2>", self.context_menu)   # pravé tlačítko na Macu
        self.tree.bind("<Button-3>", self.context_menu)
        self.tree.bind("<Control-Button-1>", self.context_menu)

        self.menu = tk.Menu(self, tearoff=0)
        self.menu.add_command(label="Zobrazit ve Finderu", command=self.reveal_selected)
        self.menu.add_command(label="Náhled v okně (mezerník)", command=self.toggle_preview)
        self.menu.add_command(label="Velký náhled Quick Look (Shift + mezerník)", command=self.big_quicklook)
        self.menu.add_command(label="Otevřít", command=self.open_selected)
        self.menu.add_command(label="Kopírovat cestu", command=self.copy_path)

        bottom = ttk.Frame(t)
        bottom.pack(fill="x", pady=(6, 0))
        ttk.Button(bottom, text="Zobrazit ve Finderu", command=self.reveal_selected).pack(side="left")
        ttk.Label(bottom, text="Mezerník = náhled · Shift+mezerník = velký náhled · dvojklik = Finder · klik na nadpis = řazení",
                  foreground="gray").pack(side="right")

    # ---------- drag & drop
    def _init_dnd(self):
        if TkinterDnD is None:
            self.dnd_error = "chybí knihovna tkinterdnd2 (spusť znovu vytvor_aplikaci.sh)."
            return
        try:
            TkinterDnD.require(self)
            self.dnd = True
        except Exception as ex:
            detail = str(ex)
            try:   # zjistit skutečný důvod od Tcl
                self.tk.call("package", "require", "tkdnd")
            except tk.TclError as e2:
                detail = str(e2)
            self.dnd_error = (f"{detail} (Tcl/Tk {self.tk.call('info', 'patchlevel')}, "
                              f"Python {sys.version.split()[0]}, {sys.executable})")
            print("DnD:", self.dnd_error, file=sys.stderr)

    def _register_drop(self, widget):
        try:
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind("<<Drop>>", self._on_drop)
        except Exception as ex:
            print("DnD registrace:", widget, ex, file=sys.stderr)
        for child in widget.winfo_children():
            if not isinstance(child, tk.Menu):
                self._register_drop(child)

    def _on_open_documents(self, *paths):
        self._handle_paths(list(paths))

    def _on_drop(self, event):
        self._handle_paths([p for p in self.tk.splitlist(event.data) if p])
        return "copy"

    def _handle_paths(self, paths):
        catalogs = [p for p in paths if p.endswith(EXT) and os.path.isfile(p)]
        folders = [os.path.abspath(p) for p in paths if os.path.isdir(p)]
        if catalogs and not folders:
            self.open_catalog(catalogs[0])
            return
        added = 0
        current = set(self.folder_list.get(0, "end"))
        for d in folders:
            if d not in current:
                self.folder_list.insert("end", d)
                current.add(d)
                added += 1
        ignored = len(paths) - len(folders) - len(catalogs)
        if folders:
            self.nb.select(self.tab_scan)
            msg = f"Přidáno složek: {added}. Klikni na „Skenovat a uložit katalog…“."
            if ignored:
                msg += f" ({ignored} souborů ignorováno – přetahuj složky.)"
            self.status.set(msg)
        else:
            self.status.set("Přetáhni sem složku (nebo soubor .katalog).")
        self.lift()

    # ---------- skenování
    def add_folder(self):
        d = filedialog.askdirectory(title="Vyber složku do katalogu", mustexist=True)
        if d:
            d = os.path.abspath(d)
            if d not in self.folder_list.get(0, "end"):
                self.folder_list.insert("end", d)

    def remove_folders(self):
        for i in reversed(self.folder_list.curselection()):
            self.folder_list.delete(i)

    def start_scan(self, save_path=None):
        if self.busy:
            return
        roots = list(self.folder_list.get(0, "end"))
        if not roots:
            messagebox.showinfo(APP_NAME, "Nejdřív přidej aspoň jednu složku (tlačítkem nebo přetažením).")
            return
        if save_path is None:
            save_path = filedialog.asksaveasfilename(
                title="Uložit katalog jako", defaultextension=EXT,
                initialfile="Muj katalog" + EXT,
                filetypes=[("Katalog souborů", "*" + EXT)])
            if not save_path:
                return
        if not save_path.endswith(EXT):
            save_path += EXT
        if is_archived(save_path):
            messagebox.showwarning(APP_NAME, f"Do složky „{ARCHIVE_DIR}“ se katalog ukládat nedá – "
                                   "jsou v ní jen archivní verze.\nVyber jiné umístění.")
            return

        skip_hidden, skip_pkg = self.var_hidden.get(), self.var_packages.get()
        # počet položek starého katalogu (pro kontrolu, jestli nový není podezřele menší)
        old_n, old_cat = None, None
        if os.path.exists(save_path):
            if self.catalog and self.catalog.path and os.path.abspath(self.catalog.path) == os.path.abspath(save_path):
                old_n, old_cat = len(self.catalog.entries), self.catalog
            # kontrola, že půjde vytvořit záloha – ještě před dlouhým skenováním
            arch = os.path.join(os.path.dirname(os.path.abspath(save_path)), ARCHIVE_DIR)
            try:
                os.makedirs(arch, exist_ok=True)
                if not os.access(arch, os.W_OK):
                    raise PermissionError("chybí oprávnění k zápisu")
            except OSError as ex:
                messagebox.showerror(APP_NAME, f"Nejde vytvořit zálohu ve složce\n{arch}\n\n{ex}\n\n"
                                     "Aktualizace se proto nespustila, katalog zůstal beze změny.")
                return
        self._launch_scan(roots, save_path, skip_hidden, skip_pkg, old_n, old_cat)

    def _launch_scan(self, roots, save_path, skip_hidden, skip_pkg, old_n, old_cat=None):
        """Spustí skenování na pozadí."""
        self._set_busy(True)
        self.cancel_ev.clear()
        self._scan_t0 = time.time()
        self._scan_plan = None
        self.scan_info.set("Skenuji…")

        def ask(kind, *args):
            """Zeptá se v hlavním vlákně a počká na odpověď."""
            ev, box = threading.Event(), {}
            self.msgs.put((kind, ev, box) + args)
            ev.wait()
            return box.get("ok", False)

        def worker():
            def progress(n, d):
                self.msgs.put(("progress", n, d))
            try:
                # odhad počtu položek pro teploměr (jen u aktualizace existujícího katalogu)
                try:
                    if old_cat is not None:
                        plan = scan_estimate(old_cat.entries, old_cat.roots, old_cat.created, None, None, roots)
                    elif os.path.exists(save_path):
                        h = read_header(save_path) or {}
                        plan = scan_estimate(None, h.get("roots"), h.get("created"), h.get("count"),
                                             h.get("growth_per_day"), roots)
                    else:
                        plan = None
                except Exception:
                    plan = None
                self.msgs.put(("scan_plan", plan))
                r = scan_folders(roots, skip_hidden, skip_pkg, progress, self.cancel_ev)
                entries = r["entries"]
                if entries is None:
                    self.msgs.put(("cancelled",))
                    return
                if r["failed_roots"]:
                    lst = "\n".join(f"• {d} ({why})" for d, why in r["failed_roots"])
                    self.msgs.put(("not_saved", "Tyto katalogizované složky nejsou dostupné:\n\n" + lst +
                                   "\n\nNejspíš je odpojený síťový disk, nebo macOS aplikaci nepovolil přístup "
                                   "(Nastavení systému → Soukromí a zabezpečení → Soubory a složky).\n\n"
                                   "Katalog se NEULOŽIL, původní zůstal beze změny."))
                    return
                n_new = len(entries)
                n_old = old_n if old_n is not None else (read_count(save_path) if os.path.exists(save_path) else None)
                if n_old and n_new < n_old * SHRINK_LIMIT:
                    if not ask("ask_shrink", n_old, n_new, len(r["failed_dirs"])):
                        self.msgs.put(("not_saved", "Katalog se neuložil, původní zůstal beze změny."))
                        return
                if os.path.exists(save_path):
                    self.msgs.put(("archiving",))
                    try:
                        backup = archive_catalog(save_path)
                    except Exception as ex:
                        self.msgs.put(("not_saved", f"Zálohu katalogu se nepodařilo vytvořit:\n{ex}\n\n"
                                                    "Katalog se proto NEULOŽIL, původní zůstal beze změny."))
                        return
                    self.msgs.put(("archived", backup))
                cat = Catalog(roots, entries,
                              options={"skip_hidden": skip_hidden, "skip_packages": skip_pkg})
                self.msgs.put(("saving", n_new))
                cat.save(save_path)
                try:
                    report = write_error_report(save_path, r["failed_dirs"], r["failed_items"])
                except OSError:
                    report = None
                self.msgs.put(("saving_done",))
                cat.prepare()
                cat.children_index()
                cat.compute_stats()
                self.msgs.put(("scan_done", cat, len(r["failed_dirs"]), r["failed_items"], report))
            except Exception:
                log_error("Skenování selhalo:\n" + traceback.format_exc())
                self.msgs.put(("error", f"Skenování selhalo:\n{sys.exc_info()[1]}\n\n"
                                        f"Podrobnosti: {LOG_FILE}"))

        threading.Thread(target=worker, daemon=True).start()

    def rescan(self):
        if not self.catalog:
            return
        if is_archived(self.catalog.path):
            messagebox.showinfo(APP_NAME, "Tohle je archivní verze katalogu – je jen pro čtení.\n"
                                "Aktualizovat jde jen aktuální katalog (o složku výš).")
            return
        if not messagebox.askyesno(APP_NAME, "Znovu projít stejné složky a aktualizovat katalog\n"
                                             f"{self.catalog.path}?\n\n"
                                             f"Současná verze se nejdřív zálohuje do složky „{ARCHIVE_DIR}“."):
            return
        missing = [r for r in self.catalog.roots if not os.path.isdir(r)]
        if missing:
            messagebox.showwarning(APP_NAME, "Tyto složky teď nejsou dostupné (odpojený disk?):\n\n"
                                   + "\n".join(missing))
            return
        self.folder_list.delete(0, "end")
        for r in self.catalog.roots:
            self.folder_list.insert("end", r)
        opts = self.catalog.options or {}
        self.var_hidden.set(opts.get("skip_hidden", True))
        self.var_packages.set(opts.get("skip_packages", True))
        self.nb.select(self.tab_scan)
        self.start_scan(save_path=self.catalog.path)

    # ---------- otevření katalogu
    def ask_open(self):
        p = filedialog.askopenfilename(title="Otevřít katalog",
                                       filetypes=[("Katalog souborů", "*" + EXT), ("Všechny soubory", "*")])
        if p:
            self.open_catalog(p)

    def open_catalog(self, path):
        if self.busy:
            return
        self._set_busy(True)
        self.status.set(f"Otevírám {os.path.basename(path)}…")
        self.nb.select(self.tab_search)
        self._load_progress(0, "Načítám soubor…")
        self.load_bar.pack(side="left", padx=(16, 6))
        self.load_lbl.pack(side="left")
        last = [0.0]

        def progress(pct, txt):
            now = time.time()
            if now - last[0] > 0.1 or pct >= 100:
                last[0] = now
                self.msgs.put(("load_progress", pct, txt))

        def worker():
            try:
                self.msgs.put(("loaded", Catalog.load(path, progress)))
            except Exception as ex:
                self.msgs.put(("error", f"Katalog se nepodařilo otevřít:\n{ex}"))

        threading.Thread(target=worker, daemon=True).start()

    def _update_age(self):
        """„Aktuální před X dny“ – zeleně 0–3 dny, oranžově 4–6, červeně 7 a více."""
        if getattr(self, "_age_after", None):
            self.after_cancel(self._age_after)
            self._age_after = None
        if not self.catalog:
            self.cat_age.configure(text="")
            return
        days = max(0, int((time.time() - self.catalog.created) // 86400))
        if days == 0:
            txt = "Aktuální dnes"
        elif days == 1:
            txt = "Aktuální před 1 dnem"
        else:
            txt = f"Aktuální před {days} dny"
        color = "#2e9d3a" if days <= 3 else ("#e67e00" if days <= 6 else "#d0021b")
        self.cat_age.configure(text=txt, foreground=color)
        self._age_after = self.after(30 * 60 * 1000, self._update_age)   # obnovit každou půlhodinu

    def _load_progress(self, pct, txt):
        self.load_bar["value"] = pct
        self.load_lbl.configure(text=f"{txt} {pct:.0f} %")

    def _load_done(self):
        self.load_bar.pack_forget()
        self.load_lbl.pack_forget()

    def _set_catalog(self, cat):
        self.catalog = cat
        self.settings["last_catalog"] = cat.path
        save_settings(self.settings)
        if not hasattr(cat, "n_dirs"):
            cat.compute_stats()
        n_dirs = cat.n_dirs
        n_files = len(cat.entries) - n_dirs
        total = cat.total_size
        self.cat_info.set(f"📒 {os.path.basename(cat.path)}  ·  vytvořen {fmt_time(cat.created)}  ·  ")
        self.cat_info2.set(f"  ·  {fmt_n(n_files)} souborů, {fmt_n(n_dirs)} složek, {human_size(total)}")
        self._update_age()
        self._work_gen += 1                      # rozpracované hledání ze starého katalogu zahodit
        self._work_done()
        archived = is_archived(cat.path)
        warn = []
        if archived:
            warn.append("🔒 Archivní verze katalogu – jen pro čtení. Aktualizovat jde jen aktuální katalog "
                        f"(o složku výš než „{ARCHIVE_DIR}“).")
        elif cat.entries and not cat.has_ctime():
            warn.append("⚠︎ Starší katalog bez data vytvoření – klikni na „Aktualizovat katalog“.")
        if warn:
            self.cat_warn.configure(text="\n".join(warn))
            self.cat_warn.pack(anchor="w", after=self._info_row, pady=(0, 6))
        else:
            self.cat_warn.pack_forget()
        self.btn_rescan.configure(state="disabled" if archived else "normal")
        self.title(f"{APP_TITLE} – {os.path.basename(cat.path)}")
        self.var_query.set("")
        self.browse = True
        self.nb.select(self.tab_search)
        self.tree.delete(*self.tree.get_children())
        self.show_all()
        self.entry.focus_set()

    def show_all(self, keep=None):
        """Vypíše celý obsah katalogu. Strom = procházení jako ve Finderu (bez omezení),
        seznam = prvních MAX_RESULTS podle aktuálního řazení."""
        if not self.catalog:
            return
        self.browse = True
        ents = self.catalog.entries
        if self.var_tree.get():
            self.results = []
            n_dirs = getattr(self.catalog, "n_dirs", None)
            if n_dirs is None:
                n_dirs = sum(1 for e in ents if e[2])
            self._fill(keep, msg=f"Celý katalog: {fmt_n(len(ents) - n_dirs)} souborů, {fmt_n(n_dirs)} složek – "
                                 "obsah složky se načte po rozbalení.")
            return
        if self.var_dirs.get():
            self.results = list(range(len(ents)))
        else:
            self.results = [i for i, e in enumerate(ents) if not e[2]]
        total = len(self.results)
        what = "Položek" if self.var_dirs.get() else "Souborů"
        msg = f"{what} v katalogu: {fmt_n(total)}."
        if total > MAX_RESULTS:
            msg += f" Zobrazeno prvních {fmt_n(MAX_RESULTS)} podle řazení – pro zbytek použij hledání."
        self._fill(keep, msg=msg)

    # ---------- hledání a řazení
    def do_search(self):
        if not self.catalog:
            if self.var_query.get().strip():
                messagebox.showinfo(APP_NAME, "Nejdřív otevři nebo vytvoř katalog.")
            return
        q = self.var_query.get().strip()
        if not q:              # prázdné hledání = zobrazit vše
            self.show_all()
            return
        self.browse = False
        cat = self.catalog
        mode_all, kind = self.var_mode.get() == "all", ("all" if self.var_dirs.get() else "files")
        in_path = self.var_inpath.get()

        def compute():
            t0 = time.time()
            return cat.search(q, mode_all, kind, in_path), time.time() - t0

        def done(r):
            res, dt = r
            self.results = res
            msg = f"Nalezeno: {fmt_n(len(res))} ({dt:.2f} s)."
            if len(res) > MAX_RESULTS:
                msg += f" Zobrazeno prvních {fmt_n(MAX_RESULTS)} podle řazení – zpřesni hledání."
            self._fill(msg=msg)

        slow = len(cat.entries) > BG_THRESHOLD
        if in_path and cat._paths is None and slow:
            label = "Připravuji hledání v cestách (poprvé déle)…"
        else:
            label = "Hledám…"
        self._run(label, compute, done, background=slow)

    # ---------- práce na pozadí (velké katalogy nesmí zamrazit okno)
    def _run(self, label, fn, on_done, background):
        if not background:
            self._work_gen += 1                # případný starší výsledek z pozadí zahodit
            self._work_done()
            on_done(fn())
            return
        self._work_gen += 1
        gen = self._work_gen
        self.work_lbl.configure(text=label)
        self.work_bar.pack(side="left", padx=(16, 6))
        self.work_lbl.pack(side="left")
        self.work_bar.start(12)
        self.configure(cursor="watch")

        def worker():
            try:
                res = fn()
            except Exception as ex:
                self.msgs.put(("work_error", gen, traceback.format_exc(), str(ex)))
                return
            self.msgs.put(("work_done", gen, res, on_done))
        threading.Thread(target=worker, daemon=True).start()

    def _work_done(self):
        if not hasattr(self, "work_bar"):
            return
        self.work_bar.stop()
        self.work_bar.pack_forget()
        self.work_lbl.pack_forget()
        if not self.busy:
            self.configure(cursor="")

    def _sort_key(self, col):
        cat = self.catalog
        if col == "name":
            return lambda i: (cat._names[i], i)
        if col == "mtime":
            return lambda i: (cat.entries[i][4], cat._names[i])
        if col == "ctime":
            return lambda i: (cat.entries[i][5], cat._names[i])
        if col == "size":
            return lambda i: (cat.entries[i][3], cat._names[i])
        cache = {}             # cesta ke složce se normalizuje jen jednou

        def path_key(i):
            e = cat.entries[i]
            parent = (e[0], e[1].rpartition("/")[0])
            k = cache.get(parent)
            if k is None:
                k = cache[parent] = norm(cat.parent_path(i))
            return (k, cat._names[i])
        return path_key

    def sort_by(self, col):
        if col == self.sort_col:
            self.sort_desc = not self.sort_desc
        else:
            self.sort_col, self.sort_desc = col, False
        self._update_headings()
        self._refill()

    def _update_headings(self):
        for col, (title, _, _) in COLUMNS.items():
            arrow = (" ▼" if self.sort_desc else " ▲") if col == self.sort_col else ""
            self.tree.heading(col, text=title + arrow)
            if col == "name":
                self.tree.heading("#0", text=title + arrow)

    def _refill(self):
        if not self.catalog:
            return
        keep = self._current_iid()
        if getattr(self, "browse", False):
            self.show_all(keep)
        else:
            self._fill(keep)

    def _ordered(self, idxs, limit, col=None, desc=None):
        col = self.sort_col if col is None else col
        desc = self.sort_desc if desc is None else desc
        key = self._sort_key(col)
        if len(idxs) > limit * 3:                  # velké množství – stačí vybrat prvních N
            pick = heapq.nlargest if desc else heapq.nsmallest
            return pick(limit, idxs, key=key)
        return sorted(idxs, key=key, reverse=desc)[:limit]

    def _sorted_children(self, idxs):
        """Seřadí podle zvoleného sloupce, složky vždy před soubory."""
        cat = self.catalog
        key = self._sort_key(self.sort_col)
        dirs = [i for i in idxs if cat.entries[i][2]]
        files = [i for i in idxs if not cat.entries[i][2]]
        return (sorted(dirs, key=key, reverse=self.sort_desc)
                + sorted(files, key=key, reverse=self.sort_desc))

    def _row_values(self, i):
        cat = self.catalog
        e = cat.entries[i]
        icon = "📁  " if e[2] else "📄  "
        return icon + cat.name(i), (icon + cat.name(i), fmt_time(e[4]), fmt_time(e[5]),
                                    "" if e[2] else human_size(e[3]), cat.parent_path(i))

    def _fill(self, keep=None, msg=None):
        cat, tree = self.catalog, self.tree
        tree_mode = self.var_tree.get()
        if tree_mode and getattr(self, "browse", False) and cat:
            self._work_gen += 1
            self._work_done()
            opened = self._open_items()
            tree.delete(*tree.get_children())
            tree.configure(show="tree headings", displaycolumns=("mtime", "ctime", "size"))
            self._fill_browse(keep, opened)
            if msg:
                self.status.set(msg)
            return
        results = self.results
        if not results or not cat:
            self._work_gen += 1
            self._work_done()
            tree.delete(*tree.get_children())
            tree.configure(show="tree headings" if tree_mode else "headings",
                           displaycolumns=("mtime", "ctime", "size") if tree_mode else "#all")
            self._preview_schedule()
            if msg:
                self.status.set(msg)
            return
        col, desc = self.sort_col, self.sort_desc

        def compute():
            # složky vždy nahoře, soubory pod nimi; zobrazit nejvýš MAX_RESULTS položek
            dirs = [i for i in results if cat.entries[i][2]]
            files = [i for i in results if not cat.entries[i][2]]
            order = self._ordered(dirs, MAX_RESULTS, col, desc)
            if len(order) < MAX_RESULTS:
                order += self._ordered(files, MAX_RESULTS - len(order), col, desc)
            if tree_mode:
                cat.index_by_path()              # pro strom výsledků (u velkého katalogu chvíli trvá)
            return order

        def done(order):
            self._show_order(order, tree_mode, keep, msg)

        slow = len(results) > BG_THRESHOLD or (tree_mode and getattr(cat, "_by_path", None) is None
                                                and len(cat.entries) > BG_THRESHOLD)
        self._run("Řadím…", compute, done, background=slow)

    def _show_order(self, order, tree_mode, keep, msg):
        tree = self.tree
        tree.delete(*tree.get_children())
        if tree_mode:
            tree.configure(show="tree headings", displaycolumns=("mtime", "ctime", "size"))
            self._fill_tree(order)
        else:
            tree.configure(show="headings", displaycolumns="#all")
            for i in order:
                _, vals = self._row_values(i)
                tree.insert("", "end", iid=str(i), values=vals)
        if msg:
            self.status.set(msg)
        if not order:
            self._preview_schedule()
            return
        target = keep if keep and tree.exists(keep) else str(order[0])
        tree.see(target)
        tree.selection_set(target)
        tree.focus(target)

    # ---------- strom celého katalogu (načítání složek až při rozbalení)
    def _open_items(self):
        out, stack = set(), list(self.tree.get_children())
        while stack:
            iid = stack.pop()
            if self.tree.item(iid, "open"):
                out.add(iid)
                stack.extend(self.tree.get_children(iid))
        return out

    def _fill_browse(self, keep, opened):
        cat, tree = self.catalog, self.tree
        if getattr(cat, "_children", None) is None:
            self.status.set("Připravuji strom katalogu…")
            self.update_idletasks()
        cat.children_index()
        reopen = opened if opened else {f"r{ri}" for ri in range(len(cat.roots))}
        for ri, root in enumerate(cat.roots):
            rid = f"r{ri}"
            tree.insert("", "end", iid=rid, text="🗂  " + root, open=False)
            tree.insert(rid, "end", iid="ph" + rid, text="…")
            if rid in reopen:
                self._load_children(rid, reopen)
                tree.item(rid, open=True)
        target = keep if keep and tree.exists(keep) else tree.get_children()[0]
        tree.see(target)
        tree.selection_set(target)
        tree.focus(target)

    def _node_key(self, iid):
        cat = self.catalog
        if iid.startswith("r"):
            return (int(iid[1:]), "")
        e = cat.entries[int(iid)]
        return (e[0], e[1])

    def _load_children(self, iid, reopen=None):
        """Vloží obsah složky (místo zástupné položky „…“)."""
        tree, cat = self.tree, self.catalog
        ph = "ph" + iid
        if not tree.exists(ph):
            return                                   # už načteno
        tree.delete(ph)
        cidx = cat.children_index()
        for k in self._sorted_children(cidx.get(self._node_key(iid), [])):
            text, vals = self._row_values(k)
            kid = str(k)
            tree.insert(iid, "end", iid=kid, text=text, values=vals)
            e = cat.entries[k]
            if e[2] and cidx.get((e[0], e[1])):
                tree.insert(kid, "end", iid="ph" + kid, text="…")
                if reopen and kid in reopen:
                    self._load_children(kid, reopen)
                    tree.item(kid, open=True)

    def _on_tree_open(self, _e=None):
        iid = self.tree.focus()
        if iid:
            self._load_children(iid)

    # ---------- výběr více položek, rozbalování šipkami, Cmd+A
    def _expandable(self, iid):
        return bool(self.tree.get_children(iid))

    def _on_right(self, _e=None):
        sel = self.tree.selection()
        for iid in sel:
            if self._expandable(iid):
                self._load_children(iid)
                self.tree.item(iid, open=True)
        return "break"

    def _on_left(self, _e=None):
        tree = self.tree
        sel = tree.selection()
        if len(sel) > 1:          # kořen katalogu při výběru více položek nezavírat (schoval by vše)
            sel = [i for i in sel if not i.startswith("r")] or sel
        closed_any = False
        for iid in sel:
            if tree.item(iid, "open") and self._expandable(iid):
                tree.item(iid, open=False)
                closed_any = True
        if not closed_any and len(sel) == 1:         # jako ve Finderu: skok na nadřazenou složku
            parent = tree.parent(sel[0])
            if parent:
                tree.selection_set(parent)
                tree.focus(parent)
                tree.see(parent)
        return "break"

    def select_all(self, _e=None):
        tree, items, stack = self.tree, [], list(reversed(self.tree.get_children()))
        while stack:
            iid = stack.pop()
            if iid.startswith("ph"):
                continue
            if not iid.startswith("r"):          # kořen katalogu do Cmd+A nezahrnovat
                items.append(iid)
            if tree.item(iid, "open"):
                stack.extend(reversed(tree.get_children(iid)))
        if items:
            tree.selection_set(items)
            self.status.set(f"Vybráno položek: {fmt_n(len(items))}")
        return "break"

    def _fill_tree(self, order):
        """Strom: nalezené položky (tučně) i s nadřazenými složkami (šedě)."""
        cat, tree = self.catalog, self.tree
        idx = cat.index_by_path()
        nodes, roots, children = set(), set(), {}

        for i in order:
            while i not in nodes:
                nodes.add(i)
                ri, rel = cat.entries[i][0], cat.entries[i][1]
                prel = rel.rpartition("/")[0]
                p = idx.get((ri, prel)) if prel else None
                parent = p if p is not None else ("r", ri)
                children.setdefault(parent, []).append(i)
                if p is None:
                    roots.add(ri)
                    break
                i = p

        matched = set(order)
        open_all = len(nodes) <= 3000

        def insert(parent_iid, parent_key, depth):
            for i in self._sorted_children(children.get(parent_key, [])):
                text, vals = self._row_values(i)
                tree.insert(parent_iid, "end", iid=str(i), text=text, values=vals,
                            open=open_all or depth == 0,
                            tags=("match",) if i in matched else ("ctx",))
                if i in children:
                    insert(str(i), i, depth + 1)

        for ri in sorted(roots):
            rid = f"r{ri}"
            tree.insert("", "end", iid=rid, text="🗂  " + cat.roots[ri], open=True, tags=("ctx",))
            insert(rid, ("r", ri), 0)

    # ---------- práce s výsledky
    def _current_iid(self):
        """Aktuální řádek (ten s kurzorem) – pro náhled a stavový řádek."""
        tree = self.tree
        f = tree.focus()
        sel = tree.selection()
        if f and tree.exists(f) and not f.startswith("ph") and (not sel or f in sel):
            return f
        sel = [s for s in sel if not s.startswith("ph")]
        return sel[0] if sel else None

    def _iid_path(self, iid):
        if not iid or not self.catalog or iid.startswith("ph"):
            return None
        if iid.startswith("r"):                         # kořenová složka katalogu
            return self.catalog.roots[int(iid[1:])]
        return self.catalog.full_path(int(iid))

    def _selected_index(self):
        iid = self._current_iid()
        if not iid or not self.catalog or iid.startswith("r"):
            return None
        return int(iid)

    def _selected_path(self):
        return self._iid_path(self._current_iid())

    def _selected_paths(self):
        return [p for p in (self._iid_path(i) for i in self.tree.selection()) if p]

    def on_select(self, _e=None):
        n = len(self.tree.selection())
        p = self._selected_path()
        if n > 1:
            self.status.set(f"Vybráno položek: {fmt_n(n)}")
        elif p:
            self.status.set(p)
        self._preview_schedule()

    # ---------- náhled v okně (jako mezerník ve Finderu, šipky dál fungují)
    def _build_preview_pane(self):
        self.pv = ttk.Frame(self.split, padding=(10, 0, 0, 0))
        self.pv_visible = False
        self.pv_img = ttk.Label(self.pv, anchor="center")
        self.pv_img.pack(fill="both", expand=True)
        self.pv_name = ttk.Label(self.pv, font=("TkDefaultFont", 13, "bold"), wraplength=360, justify="left")
        self.pv_name.pack(anchor="w", pady=(8, 2))
        self.pv_info = ttk.Label(self.pv, foreground="gray", wraplength=360, justify="left")
        self.pv_info.pack(anchor="w")
        self.pv.bind("<Configure>", self._preview_resized)
        self._pv_photo = None
        self._pv_req = 0
        self._pv_after = None
        self._pv_cache = {}
        self._pv_dir = tempfile.mkdtemp(prefix="katalog-nahled-")

    def toggle_preview(self, _e=None):
        if self.pv_visible:
            self.hide_preview()
        else:
            self.split.add(self.pv, weight=2)
            self.pv_visible = True
            self._preview_schedule(0)
        self.tree.focus_set()
        return "break"

    def hide_preview(self):
        if self.pv_visible:
            self.split.forget(self.pv)
            self.pv_visible = False
        return "break"

    def _preview_resized(self, e):
        w = max(150, e.width - 20)
        self.pv_name.configure(wraplength=w)
        self.pv_info.configure(wraplength=w)

    def _preview_schedule(self, delay=120):
        if not getattr(self, "pv_visible", False):
            return
        if self._pv_after:
            self.after_cancel(self._pv_after)
        self._pv_after = self.after(delay, self._preview_update)

    def _preview_update(self):
        self._pv_after = None
        i = self._selected_index()
        self._pv_req += 1
        req = self._pv_req
        self._pv_photo = None
        self.pv_img.configure(image="", text="")
        if i is None:
            self.pv_name.configure(text="")
            self.pv_info.configure(text="")
            return
        e = self.catalog.entries[i]
        p = self.catalog.full_path(i)
        self.pv_name.configure(text=("📁  " if e[2] else "📄  ") + self.catalog.name(i))
        lines = []
        if not e[2]:
            lines.append(f"Velikost: {human_size(e[3])}")
        lines += [f"Upraveno: {fmt_time(e[4])}", f"Vytvořeno: {fmt_time(e[5])}", f"Cesta: {os.path.dirname(p)}"]
        self.pv_info.configure(text="\n".join(lines))
        if not os.path.exists(p):
            self.pv_img.configure(text="Položka teď není dostupná\n(odpojený disk nebo přesunutý soubor)")
            return
        self.update_idletasks()
        size = max(200, min(1200, self.pv.winfo_width() - 20, self.pv.winfo_height() - 120))
        cached = self._pv_cache.get((p, size))
        if cached and os.path.exists(cached):
            self._preview_show(req, cached)
            return
        self.pv_img.configure(text="Načítám náhled…")
        out = os.path.join(self._pv_dir, str(req))

        def worker():
            png = None
            try:
                os.makedirs(out, exist_ok=True)
                subprocess.run(["qlmanage", "-t", "-s", str(size), "-o", out, p],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
                pngs = [f for f in os.listdir(out) if f.endswith(".png")]
                png = os.path.join(out, pngs[0]) if pngs else None
            except Exception:
                png = None
            self.msgs.put(("thumb", req, p, size, png))

        threading.Thread(target=worker, daemon=True).start()

    def _preview_show(self, req, png):
        if req != self._pv_req or not self.pv_visible:
            return
        if not png:
            self.pv_img.configure(image="", text="Náhled pro tento typ souboru není k dispozici")
            return
        try:
            img = tk.PhotoImage(file=png)
            maxw = max(100, self.pv.winfo_width() - 20)
            maxh = max(100, self.pv.winfo_height() - 120)
            f = max(1, -(-img.width() // maxw), -(-img.height() // maxh))
            if f > 1:
                img = img.subsample(f, f)
            self._pv_photo = img
            self.pv_img.configure(image=img, text="")
        except tk.TclError:
            self.pv_img.configure(image="", text="Náhled nelze zobrazit")

    # ---------- velký náhled (Quick Look v samostatném okně)
    def _ql_running(self):
        return getattr(self, "_ql", None) is not None and self._ql.poll() is None

    def _ql_close(self):
        if self._ql_running():
            self._ql.terminate()
        self._ql = None

    def big_quicklook(self, _e=None):
        p = self._selected_path()
        self._ql_close()
        if not p:
            return "break"
        if not os.path.exists(p):
            self.status.set(f"Náhled nelze zobrazit – položka teď na disku není: {p}")
            return "break"
        try:
            self._ql = subprocess.Popen(["qlmanage", "-p", p],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as ex:
            self.status.set(f"Náhled nelze spustit: {ex}")
        return "break"

    def on_double(self, event):
        if self.tree.identify_region(event.x, event.y) == "heading":
            return
        self.reveal_selected()

    def reveal_selected(self):
        paths = self._selected_paths()
        if not paths:
            return
        exist = [p for p in paths if os.path.exists(p)]
        if not exist:
            messagebox.showwarning(APP_NAME, "Položka teď na disku není.\n\n"
                                   f"{paths[0]}\n\nMožná je disk odpojený nebo byl soubor přesunut/smazán.")
            return
        if len(exist) > 20 and not messagebox.askyesno(APP_NAME, f"Zobrazit ve Finderu {len(exist)} položek?"):
            return
        subprocess.run(["open", "-R"] + exist)

    def open_selected(self):
        p = self._selected_path()
        if p and os.path.exists(p):
            subprocess.run(["open", p])
        elif p:
            messagebox.showwarning(APP_NAME, f"Položka teď na disku není:\n{p}")

    def copy_path(self):
        paths = self._selected_paths()
        if paths:
            self.clipboard_clear()
            self.clipboard_append("\n".join(paths))
            self.status.set(f"Zkopírováno cest: {len(paths)}" if len(paths) > 1 else f"Cesta zkopírována: {paths[0]}")

    def context_menu(self, e):
        iid = self.tree.identify_row(e.y)
        if iid:
            if iid not in self.tree.selection():
                self.tree.selection_set(iid)
                self.tree.focus(iid)
            self.menu.tk_popup(e.x_root, e.y_root)

    def _on_close(self):
        self._ql_close()
        shutil.rmtree(getattr(self, "_pv_dir", ""), ignore_errors=True)
        self.destroy()

    # ---------- kontrola aktualizací (GitHub Releases)
    def _build_update_bar(self):
        bg, fg = "#fff4d6", "#5c4400"
        self.upd_bar = tk.Frame(self, bg=bg, highlightthickness=1, highlightbackground="#e8c766")
        self.upd_text = tk.Label(self.upd_bar, bg=bg, fg=fg, font=("TkDefaultFont", 13, "bold"))
        self.upd_text.pack(side="left", padx=(12, 16), pady=8)
        self.btn_upd = ttk.Button(self.upd_bar, text="Nainstalovat a restartovat", command=self._update_install)
        self.btn_upd.pack(side="left")
        ttk.Button(self.upd_bar, text="Co je nového", command=self._update_notes).pack(side="left", padx=6)
        ttk.Button(self.upd_bar, text="Skrýt", command=self.upd_bar.pack_forget).pack(side="right", padx=8)
        self._upd = None
        self._offered = None          # verze, na kterou se už v tomto spuštění ptal
        self._offer_pending = False   # nabídka čeká, až doběhne skenování / načítání
        self._installing = False

    def _update_tick(self):
        self.check_updates(manual=False)
        self.after(UPDATE_EVERY_H * 3600 * 1000, self._update_tick)

    def check_updates(self, manual=False):
        if manual:
            self.status.set("Kontroluji aktualizace…")

        def worker():
            try:
                self.msgs.put(("update", manual, fetch_latest_release(), None))
            except Exception as ex:
                self.msgs.put(("update", manual, None, str(ex)))
        threading.Thread(target=worker, daemon=True).start()

    def _on_update(self, manual, rel, err):
        if err:
            log_error(f"Kontrola aktualizací selhala: {err}")
            if manual:
                self.status.set("Kontrola aktualizací se nepovedla.")
                messagebox.showwarning(APP_NAME, "Nepodařilo se zjistit, jestli je nová verze.\n\n"
                                       f"{err}\n\nZkontroluj připojení k internetu, nebo se podívej přímo na\n"
                                       f"{RELEASES_PAGE}")
            return
        if rel and version_tuple(rel["version"]) > version_tuple(APP_VERSION):
            self._upd = rel
            self.upd_text.configure(text=f"🔔  Je k dispozici nová verze {rel['version']}  "
                                         f"(máš {APP_VERSION})")
            if not self.upd_bar.winfo_ismapped() and not self._installing:
                self.upd_bar.pack(fill="x", padx=8, pady=(8, 0), before=self.nb)
            self.status.set(f"Je k dispozici nová verze {rel['version']}.")
            if manual or self._offered != rel["version"]:
                self._offer_update()
        elif manual:
            self.status.set("Máš nejnovější verzi.")
            messagebox.showinfo(APP_NAME, f"Máš nejnovější verzi ({APP_VERSION})." if rel else
                                "Na GitHubu zatím není vydaná žádná verze.")

    def _offer_update(self):
        """Jeden dotaz: Ano = stáhnout, nainstalovat a restartovat bez dalších otázek."""
        rel = self._upd
        if not rel or self._installing:
            return
        if self.busy:                                  # nerušit skenování – zeptá se, až doběhne
            self._offer_pending = True
            return
        self._offer_pending = False
        self._offered = rel["version"]
        notes = rel["notes"].split("\n---\n")[0].strip()
        if len(notes) > 600:
            notes = notes[:600] + "…"
        app = current_app_bundle()
        txt = f"Je k dispozici nová verze {rel['version']} (máš {APP_VERSION})."
        if notes:
            txt += f"\n\nNovinky:\n{notes}"
        if app and rel.get("dmg"):
            txt += ("\n\nNainstalovat teď? Aplikace si novou verzi stáhne, nainstaluje a sama se znovu "
                    "spustí. Katalogy ani nastavení se nemění.")
            target = install_target(app)
            if target != app:
                txt += (f"\n\n(Do současného umístění nemáš právo zápisu – nová verze bude v {target}.)")
        else:
            txt += "\n\nOtevřít stránku ke stažení?"
        if messagebox.askyesno(APP_NAME, txt, icon="info"):
            self._update_install()
        else:
            self.status.set("Novou verzi nainstaluješ kdykoli tlačítkem ve žlutém pruhu nahoře.")

    def _after_update_notice(self):
        """Po aktualizaci krátce ukáže, na jakou verzi se aplikace aktualizovala."""
        prev = self.settings.get("app_version")
        self.settings["app_version"] = APP_VERSION
        save_settings(self.settings)
        if not prev or version_tuple(APP_VERSION) <= version_tuple(prev):
            return
        note = tk.Label(self, text=f"✓  Aplikace byla aktualizována na verzi {APP_VERSION}",
                        bg="#e5f4e4", fg="#1e6b2a", font=("TkDefaultFont", 13, "bold"), pady=8)
        note.pack(fill="x", padx=8, pady=(8, 0), before=self.nb)
        self.after(15000, note.destroy)
        log_error(f"Aktualizováno z verze {prev} na {APP_VERSION}.")

    def _update_install(self):
        rel = self._upd
        if not rel or self._installing:
            return
        app = current_app_bundle()
        if not app or not rel.get("dmg"):              # spuštěno ze zdrojáků / vydání bez DMG
            subprocess.run(["open", rel["page"]])
            return
        if self.busy:
            messagebox.showinfo(APP_NAME, "Počkej, až doběhne skenování nebo načítání katalogu.")
            return
        target = install_target(app)
        self._installing = True
        self._set_busy(True)
        if not self.upd_bar.winfo_ismapped():
            self.upd_bar.pack(fill="x", padx=8, pady=(8, 0), before=self.nb)
        self.btn_upd.configure(state="disabled")
        self.upd_text.configure(text=f"⏳  Stahuji a připravuji verzi {rel['version']}…")
        self.status.set("Stahuji aktualizaci…")
        workdir = tempfile.mkdtemp(prefix="katalog-update-")

        def worker():
            try:
                new = prepare_update(rel, workdir)
                self.msgs.put(("upd_ready", new, target, workdir))
            except Exception as ex:
                log_error("Aktualizace selhala:\n" + traceback.format_exc())
                self.msgs.put(("upd_failed", str(ex), workdir))
        threading.Thread(target=worker, daemon=True).start()

    def _upd_ready(self, new, target, workdir):
        script = os.path.join(workdir, "instalace.sh")
        with open(script, "w", encoding="utf-8") as f:
            f.write(UPDATE_HELPER)
        subprocess.Popen(["/bin/bash", script, str(os.getpid()), new, target, LOG_FILE],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        log_error(f"Instaluji aktualizaci do {target}, aplikace se restartuje.")
        self._on_close()

    def _upd_failed(self, err, workdir):
        shutil.rmtree(workdir, ignore_errors=True)
        self._installing = False
        self._set_busy(False)
        self.btn_upd.configure(state="normal")
        rel = self._upd or {}
        self.upd_text.configure(text=f"🔔  Je k dispozici nová verze {rel.get('version', '')}  "
                                     f"(máš {APP_VERSION})")
        self.status.set("Aktualizace se nepovedla.")
        messagebox.showerror(APP_NAME, f"Aktualizaci se nepodařilo nainstalovat:\n\n{err}\n\n"
                             f"Současná verze zůstala beze změny. Podrobnosti: {LOG_FILE}")

    def _update_notes(self):
        rel = self._upd
        if not rel:
            return
        notes = rel["notes"].split("\n---\n")[0].strip() or "Popis změn není uvedený."   # bez návodu k instalaci
        if len(notes) > 1500:
            notes = notes[:1500] + "…"
        messagebox.showinfo(APP_NAME, f"Novinky ve verzi {rel['version']}:\n\n{notes}")

    # ---------- stav a zprávy z vláken
    def _set_busy(self, b):
        self.busy = b
        self.btn_scan.configure(state="disabled" if b else "normal")
        self.btn_cancel.configure(state="normal" if b else "disabled")
        if b:
            self.progress.configure(mode="indeterminate", value=0)
            self.progress.start(12)
            self.configure(cursor="watch")
        else:
            self.progress.stop()
            self.progress.configure(mode="indeterminate", value=0)
            self.configure(cursor="")
            if getattr(self, "_offer_pending", False):
                self.after(800, self._offer_update)

    def _progress_indeterminate(self):
        if str(self.progress.cget("mode")) != "indeterminate":
            self.progress.configure(mode="indeterminate", value=0)
            self.progress.start(12)

    def _scan_progress(self, n, folder):
        """Teploměr skenování: % z odhadu (u aktualizace) a zbývající čas, jinak počet a rychlost."""
        el = max(0.001, time.time() - getattr(self, "_scan_t0", time.time()))
        rate = n / el
        plan = getattr(self, "_scan_plan", None)
        if plan and plan["total"] > 0:
            total = plan["total"]
            pct = min(99.0, 100.0 * n / total)
            self.progress["value"] = pct
            if el < 10 or n < 500:
                eta = "odhaduji zbývající čas…"
            elif n >= total:
                eta = "dokončuji (přibylo víc položek, než se čekalo)"
            else:
                eta = f"zbývá asi {fmt_dur((total - n) / rate)}"
            line = f"{pct:.0f} %  ·  {fmt_n(n)} z asi {fmt_n(round(total))} položek  ·  {eta}"
        else:
            line = f"{fmt_n(n)} položek  ·  {fmt_n(round(rate))}/s  ·  běží {fmt_dur(el)}"
        short = folder if len(folder) < 90 else "…" + folder[-88:]
        self.scan_info.set(line + "\n" + short)
        self.status.set("Skenování: " + line)

    def _poll(self):
        """Zpracuje zprávy z vláken na pozadí. Chyba v jedné zprávě nesmí zastavit ostatní."""
        try:
            while True:
                try:
                    m = self.msgs.get_nowait()
                except queue.Empty:
                    break
                try:
                    self._handle_msg(m)
                except Exception:
                    self._report_error(f"Chyba při zpracování „{m[0]}“")
        finally:
            self.after(100, self._poll)

    def _report_error(self, title):
        log_error(title + ":\n" + traceback.format_exc())
        try:
            self._set_busy(False)
            self._load_done()
            self._work_done()
            self.status.set("Chyba – podrobnosti v " + LOG_FILE)
            messagebox.showerror(APP_NAME, f"{title}:\n{sys.exc_info()[1]}\n\nPodrobnosti: {LOG_FILE}")
        except Exception:
            pass

    def report_callback_exception(self, exc, val, tb):
        """Neošetřená chyba v okně: zapsat do logu a ukázat, místo tichého selhání."""
        log_error("Chyba v okně:\n" + "".join(traceback.format_exception(exc, val, tb)))
        try:
            self._work_done()
            self.status.set("Chyba – podrobnosti v " + LOG_FILE)
            messagebox.showerror(APP_NAME, f"Nastala chyba:\n{val}\n\nPodrobnosti: {LOG_FILE}")
        except Exception:
            pass

    def _handle_msg(self, m):
        kind = m[0]
        if kind == "progress":
            self._scan_progress(m[1], m[2])
        elif kind == "scan_plan":
            self._scan_plan = m[1]
            if m[1]:
                self.progress.stop()
                self.progress.configure(mode="determinate", maximum=100, value=0)
                p = m[1]
                log_error(f"Odhad skenování: {fmt_n(p['base'])} + {p['growth']:.0f}/den × {p['days']:.1f} dne "
                          f"= {fmt_n(round(p['total']))} položek")
        elif kind == "saving":
            self._progress_indeterminate()
            self.scan_info.set(f"Ukládám katalog ({fmt_n(m[1])} položek)…")
            self.status.set("Ukládám katalog…")
        elif kind == "ask_shrink":
            _, ev, box, n_old, n_new, n_failed = m
            try:
                pct = 100 - round(100 * n_new / n_old)
                txt = (f"Nový katalog má {fmt_n(n_new)} položek, původní měl {fmt_n(n_old)} "
                       f"(o {pct} % méně).\n\n")
                if n_failed:
                    txt += f"Nepodařilo se přečíst {fmt_n(n_failed)} složek – možná se odpojil disk.\n\n"
                txt += ("Může jít o skutečně smazané soubory, nebo o chybu čtení.\n\n"
                        "Uložit nový katalog? (Původní se před přepsáním zálohuje.)")
                box["ok"] = messagebox.askyesno(APP_NAME, txt, icon="warning", default="no")
            finally:
                ev.set()
        elif kind == "not_saved":
            self._set_busy(False)
            self.scan_info.set("Katalog se neuložil, původní zůstal beze změny.")
            self.status.set("Katalog se neuložil.")
            messagebox.showwarning(APP_NAME, m[1])
        elif kind == "scan_done":
            self._set_busy(False)
            _, cat, n_failed, n_items, report = m
            info = (f"Hotovo: {fmt_n(len(cat.entries))} položek uloženo do {cat.path}  ·  "
                    f"trvalo {fmt_dur(time.time() - getattr(self, '_scan_t0', time.time()))}")
            if getattr(self, "_last_backup", None):
                info += f"  ·  záloha: {ARCHIVE_DIR}/{os.path.basename(self._last_backup)}"
                self._last_backup = None
            self.scan_info.set(info)
            self._set_catalog(cat)
            self.status.set(info)
            if n_failed or n_items:
                what = []
                if n_failed:
                    what.append(f"{fmt_n(n_failed)} složek")
                if n_items:
                    what.append(f"{fmt_n(n_items)} položek")
                msg = (f"Katalog je uložený, ale nepodařilo se přečíst {' a '.join(what)} "
                       "(chybí oprávnění nebo chyba síťového disku). Jejich obsah v katalogu chybí.")
                if report:
                    msg += f"\n\nSeznam je v souboru:\n{report}"
                if messagebox.askyesno(APP_NAME, msg + "\n\nZobrazit seznam ve Finderu?") and report:
                    subprocess.run(["open", "-R", report])
        elif kind == "load_progress":
            self._load_progress(m[1], m[2])
        elif kind == "archiving":
            self._progress_indeterminate()
            self.status.set("Zálohuji a ukládám katalog…")
            self.scan_info.set(f"Zálohuji současný katalog do složky „{ARCHIVE_DIR}“…")
        elif kind == "archived":
            self._last_backup = m[1]
            self.scan_info.set(f"Záloha uložena: {ARCHIVE_DIR}/{os.path.basename(m[1])} – ukládám nový katalog…")
        elif kind == "saving_done":
            self.scan_info.set("Katalog uložen, připravuji zobrazení…")
        elif kind == "loaded":
            self._load_progress(100, "Zobrazuji…")
            self.update_idletasks()
            self._set_busy(False)
            self._load_done()
            self._set_catalog(m[1])
        elif kind == "work_done":
            _, gen, result, on_done = m
            if gen == self._work_gen:              # starší výsledek (mezitím nové hledání) zahodit
                self._work_done()
                on_done(result)
        elif kind == "work_error":
            if m[1] == self._work_gen:
                self._work_done()
                log_error("Chyba na pozadí:\n" + m[2])
                self.status.set("Chyba – podrobnosti v " + LOG_FILE)
                messagebox.showerror(APP_NAME, f"Nastala chyba:\n{m[3]}\n\nPodrobnosti: {LOG_FILE}")
        elif kind == "thumb":
            _, req, p, size, png = m
            if png:
                self._pv_cache[(p, size)] = png
            self._preview_show(req, png)
        elif kind == "upd_ready":
            self._upd_ready(m[1], m[2], m[3])
        elif kind == "upd_failed":
            self._upd_failed(m[1], m[2])
        elif kind == "update":
            self._on_update(m[1], m[2], m[3])
        elif kind == "cancelled":
            self._set_busy(False)
            self.scan_info.set("Skenování zrušeno, katalog zůstal beze změny.")
        elif kind == "error":
            self._set_busy(False)
            self._load_done()
            self.status.set("Chyba.")
            messagebox.showerror(APP_NAME, m[1])



if __name__ == "__main__":
    arg = next((a for a in sys.argv[1:] if a.endswith(EXT)), None)
    App(arg).mainloop()
