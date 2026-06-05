#!/usr/bin/env python3
"""
aggiorna_iClip.py  v1.2
Importa ed esporta Clipset dal database iClip (bundle .iclipdb).

Funzionalità:
  - Importa Clipset da file JSON in _Config/AggiornaiClip/
  - Dry-run con rilevamento conflitti
  - Apply su backup .iclipdb (mai sul database live)
  - Salva copia _pre_import prima di modificare
  - Esporta tutti i bin del database in file JSON

Formato JSON clip:
  - "title"        → nome del bin in iClip (visibile nella UI)
  - "text"         → testo incollato (righe ## + comando, senza ripetere il titolo)
  - "sort_order"   → numero intero per ordinare le clip nel set (default: posizione nel JSON)
  - "previewStyle"         → 2 = mostra nome bin (default), 1 = mostra preview testo
  - "binTintColor"         → colore bin del clip es. "&h000433FF" (blu), ometti per default
  - "textColor"            → colore testo del clip es. "&h00000000" (nero), ometti per default

Campi opzionali a livello di Clipset (applicati ai clip senza colore proprio):
  - "defaultBinTintColor"  → colore bin di default per tutti i clip del set
  - "defaultTextColor"     → colore testo di default per tutti i clip del set

Retrocompatibilità v1.0: se text inizia con il title, la prima riga viene
rimossa automaticamente al momento dell'import.

Autore: Ignazio Rusconi-Clerici — Aprile 2026
"""

# --- IRC shared bootstrap ---
# Rende disponibili i moduli in Python/shared/ senza dipendere da PYTHONPATH.
# Saltato se eseguito da bundle PyInstaller (sys.frozen=True): in quel caso
# i moduli sono gia' inclusi nel bundle.
import sys as _sys
from pathlib import Path as _Path
if not getattr(_sys, 'frozen', False):
    _shared = _Path.home() / "Library/CloudStorage/Dropbox/Documenti_IRC/Python/shared"
    if str(_shared) not in _sys.path:
        _sys.path.insert(0, str(_shared))
# --- end IRC shared bootstrap ---


import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import sqlite3
import shutil
import json
import os
import re
import glob
import hashlib
import base64
import sys
from datetime import datetime, timezone

# ── Path canonici dell'ambiente IRC ────────────────────────────────────────────
# Centralizzati nel modulo shared/irc_paths.py. Vedi irc_paths.verify() per
# l'elenco di tutti i path disponibili.
from irc_paths import (
    BASE_USER,
    DROPBOX_USER_ROOT,
    app_config_dir,
    app_output_dir,
)

# ── Logging applicativo standard ───────────────────────────────────────────────
# setup_app_logger crea un logger che scrive in
# ~/Documents/log/AggiornaiClip/<timestamp>.log
from irc_logging import setup_app_logger

# ── Widget percorso condiviso: opzionale ───────────────────────────────────────
# Se il modulo condiviso non è disponibile, la GUI usa normali StringVar/Entry.
try:
    from path_widgets import PathVar, PathEntry
    _HAS_PW = True
except Exception:
    PathVar = tk.StringVar
    PathEntry = None
    _HAS_PW = False

# ── Identita' applicativa ──────────────────────────────────────────────────────
APP_NAME = "AggiornaiClip"
VERSION  = "1.3.0"

# ── Logger di sessione ─────────────────────────────────────────────────────────
# Creato all'avvio del modulo: ogni esecuzione produce un file di log dedicato
# in ~/Documents/log/AggiornaiClip/. La GUI continua ad usare _log()/_log_exp()
# per i widget Text (scelta "versione media": file log + GUI log indipendenti).
log = setup_app_logger(APP_NAME, also_to_console=False)

# ── Percorsi default ──────────────────────────────────────────────────────────
# Tutti i path canonici provengono ora da irc_paths (vedi import sopra).
# Le costanti HOME/DROPBOX usate dalla vecchia versione sono state rimosse.


def default_config_path() -> str:
    """Path del file di configurazione utente per questa app."""
    return str(app_config_dir(APP_NAME) / "config.json")


def load_config() -> dict:
    """
    Carica la configurazione utente. I default usano irc_paths per i percorsi
    canonici dell'ambiente IRC; i percorsi specifici di iClip (backup_dir,
    iclip_db_bundle) restano stringhe modificabili dall'utente.
    """
    path = default_config_path()
    defaults = {
        # iClip_backup vive nella radice della Dropbox utente, NON dentro
        # Documenti_IRC (e' un backup gestito dall'utente, separato dall'ambiente
        # di sviluppo)
        "backup_dir":      str(DROPBOX_USER_ROOT / "iClip_backup"),
        # Path di sistema dell'app iClip (fuori dal nostro controllo)
        "iclip_db_bundle": str(BASE_USER / "Library/Application Support"
                               / "iClip" / "iClip Clippings.iclipdb"),
        "skip_clipsets":   ["Recorder"],
        # _Config/AggiornaiClip/ — cartella canonica dei JSON da importare
        "json_dir":        str(app_config_dir(APP_NAME)),
        # Cartella di default per l'esportazione JSON. Persistito nel config
        # (v1.3.0) per ricordare la scelta dell'utente tra una sessione e
        # l'altra. Default: ~/Documents/output/AggiornaiClip/
        "export_dir":      str(app_output_dir(APP_NAME)),
    }
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        for k, v in defaults.items():
            if k not in cfg:
                cfg[k] = v
        for k in ("backup_dir", "iclip_db_bundle", "json_dir", "export_dir"):
            cfg[k] = os.path.expanduser(cfg[k])
        return cfg
    return {k: os.path.expanduser(v) if isinstance(v, str) else v
            for k, v in defaults.items()}


def save_config(cfg: dict) -> None:
    """
    Salva la configurazione utente. Usato per persistere l'export_dir
    quando l'utente lo modifica via "Sfoglia..." nel tab Esporta.
    """
    path = default_config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        log.info(f"Configurazione salvata in {path}")
    except Exception as e:
        log.warning(f"Impossibile salvare configurazione: {e}")

# ── Logica database ───────────────────────────────────────────────────────────

def db_path(bundle):
    return os.path.join(bundle, "master.db")

def decode_field(v):
    """Decodifica un campo che può essere bytes o str."""
    if isinstance(v, bytes):
        return v.decode("utf-8", errors="replace")
    return v or ""

def get_stato_db(bundle):
    con = sqlite3.connect(db_path(bundle))
    cur = con.cursor()
    cur.execute("SELECT ID, name FROM clippingSetTable ORDER BY the_position")
    sets_raw = cur.fetchall()
    sets = [(r[0], decode_field(r[1])) for r in sets_raw]
    cur.execute("SELECT MAX(ID) FROM clippingTable")
    max_clip = cur.fetchone()[0] or 0
    cur.execute("SELECT MAX(ID) FROM clippingFlavorTable")
    max_flavor = cur.fetchone()[0] or 0
    cur.execute("SELECT MAX(ID) FROM clippingBlobTable")
    max_blob = cur.fetchone()[0] or 0
    cur.execute("SELECT MAX(the_position) FROM clippingSetTable WHERE the_position >= 0")
    max_pos = cur.fetchone()[0] or 0
    cur.execute("SELECT lastPersistentID FROM clippingsSettingsTable")
    last_pid = cur.fetchone()[0] or 0
    con.close()
    return {
        "sets":          sets,
        "set_names":     {s[1] for s in sets},
        "max_set_id":    max(s[0] for s in sets) if sets else 0,
        "max_clip_id":   max_clip,
        "max_flavor_id": max_flavor,
        "max_blob_id":   max_blob,
        "max_pos":       max_pos,
        "last_pid":      last_pid,
    }

def now_ts():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S +0000")

def salva_copia(bundle):
    base, name = os.path.split(bundle.rstrip("/"))
    stem = name.replace(".iclipdb", "")
    ts   = datetime.now().strftime("%H%M%S")
    dest = os.path.join(base, f"{stem}_pre_import_{ts}.iclipdb")
    shutil.copytree(bundle, dest)
    return dest

def make_blob_key(data: bytes) -> bytes:
    return base64.b64encode(hashlib.md5(data).digest())

def inserisci_blob(cur, blob_id, data: bytes):
    """
    Inserisce il blob se non esiste già (chiave UNIQUE su key_md5_b64).
    Ritorna l'ID effettivo del blob nel database — che può essere diverso
    da blob_id se il blob esisteva già con un ID precedente.
    """
    key = make_blob_key(data)
    cur.execute("""
        INSERT OR IGNORE INTO clippingBlobTable (ID, key_md5_b64, mode, data, filename, size)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (blob_id, key, 0, data, None, len(data)))
    # Recupera l'ID reale (potrebbe essere diverso se INSERT OR IGNORE ha saltato)
    cur.execute("SELECT ID FROM clippingBlobTable WHERE key_md5_b64 = ?", (key,))
    row = cur.fetchone()
    return row[0] if row else blob_id

def inserisci_flavor(cur, flavor_id, clip_id, ftype, enc_bytes, uti_bytes, blob_id):
    cur.execute("""
        INSERT INTO clippingFlavorTable
            (ID, clippingTablePointer, type, the_data_encoding,
             the_data_b64, the_data_filename, the_data_filetype,
             uti, flags, data_clippingBlobTable_ID, nextItem_ID)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (flavor_id, clip_id, ftype, enc_bytes,
          None, None, None, uti_bytes, None, blob_id, None))

def clip_text(clip: dict) -> str:
    """
    Ritorna il testo da incollare.
    Retrocompatibilità v1.0: se la prima riga coincide con il title, viene rimossa.
    Eccezione: se dopo la rimozione il testo è vuoto, si usa il testo originale
    (es. clip Bridge dove title == text == simbolo).
    """
    title = clip.get("title", "").strip()
    text  = clip.get("text", "")
    lines = text.split("\n")
    if lines and lines[0].strip() == title:
        ridotto = "\n".join(lines[1:]).lstrip("\n")
        if ridotto:          # rimozione valida solo se rimane qualcosa
            text = ridotto
    return text

def importa_clipset(bundle, clipsets_da_importare, dry_run=True, log_fn=None, force_update=False):
    def log(msg):
        if log_fn:
            log_fn(msg)

    stato     = get_stato_db(bundle)
    conflitti = [cs["name"] for cs in clipsets_da_importare
                 if cs["name"] in stato["set_names"]]
    # In dry-run i conflitti vengono mostrati ma non saltati (per vedere i clip)

    set_id    = stato["max_set_id"]
    clip_id   = stato["max_clip_id"]
    flavor_id = stato["max_flavor_id"]
    blob_id   = stato["max_blob_id"]
    pos_set   = stato["max_pos"]
    pid       = stato["last_pid"]
    ts        = now_ts()

    nuovi    = 0
    saltati  = 0
    tot_clip = 0

    if not dry_run:
        copia = salva_copia(bundle)
        log(f"✅ Copia pre-import salvata: {os.path.basename(copia)}")
        con = sqlite3.connect(db_path(bundle))
        cur = con.cursor()

    for cs in clipsets_da_importare:
        nome       = cs["name"]
        esiste_gia = nome in stato["set_names"]
        n          = len(cs["clips"])   # definito subito, usato nel log

        if esiste_gia and not force_update:
            if dry_run:
                # In dry-run mostra comunque i clip così si vede cosa verrebbe importato
                log(f"⚠️  [GIÀ ESISTE] \"{ nome}\" ({n} clip) — in Apply: sceglierai se sovrascrivere o saltare")
            else:
                log(f"⚠️  SALTATO (già esiste): \"{nome}\"")
                saltati += 1
                continue

        tot_clip += n

        if esiste_gia and force_update:
            # Ricava ID e posizione del set esistente
            set_id_esistente = next(s[0] for s in stato["sets"] if s[1] == nome)
            pos_set_esistente = next(
                r[0] for r in (sqlite3.connect(db_path(bundle)).execute(
                    "SELECT the_position FROM clippingSetTable WHERE ID=?",
                    (set_id_esistente,)).fetchall())
            ) if not dry_run else pos_set + 1
            stato_tag = "[DRY-RUN AGGIORNA]" if dry_run else "[AGGIORNATO]"
            log(f"{stato_tag} Clipset \"{ nome}\" ({n} clip) — sovrascrive set esistente")
            if not dry_run:
                # Cancella flavors → blobs → clips del set esistente
                cur.execute("""
                    DELETE FROM clippingFlavorTable
                    WHERE clippingTablePointer IN (
                        SELECT ID FROM clippingTable
                        WHERE clippingSetTablePointer = ?
                    )
                """, (set_id_esistente,))
                cur.execute("DELETE FROM clippingTable WHERE clippingSetTablePointer = ?",
                            (set_id_esistente,))
                cur.execute("""
                    UPDATE clippingSetTable
                    SET modificationDateTime=?, binCount=?
                    WHERE ID=?
                """, (ts, n, set_id_esistente))
            set_id_per_clip = set_id_esistente
            nuovi += 1
        else:
            if not esiste_gia:
                # Set nuovo
                set_id  += 1
                pos_set += 1
                stato_tag = "[DRY-RUN]" if dry_run else "[IMPORTATO]"
                log(f"{stato_tag} Clipset pos={pos_set}: \"{nome}\" ({n} clip)")
                if not dry_run:
                    cur.execute("""
                        INSERT INTO clippingSetTable
                            (ID, creationDateTime, modificationDateTime, the_position,
                             name, binCount, sortby, noPreview, accessDateTime)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (set_id, ts, ts, pos_set, nome, n, 0, 0, ts))
                set_id_per_clip = set_id
                nuovi += 1
            else:
                # esiste_gia and not force_update and dry_run: già loggato sopra
                set_id_per_clip = None

        # Colori di default del clipset (applicati ai clip che non hanno colore proprio)
        default_bin_tint   = cs.get("defaultBinTintColor", None) or None
        default_text_color = cs.get("defaultTextColor",    None) or None

        # In dry-run su set esistente senza force: mostra i clip ma non scrivere
        dry_run_solo_log = (esiste_gia and not force_update and dry_run)

        # Ordina per sort_order se presente, altrimenti mantiene ordine JSON
        clips_sorted = sorted(cs["clips"],
                              key=lambda c: (c.get("sort_order", 9999),
                                             cs["clips"].index(c)))
        for pos, clip in enumerate(clips_sorted, start=1):
            clip_id  += 1
            pid      += 1
            testo_eff  = clip_text(clip)
            prima_riga = testo_eff.split("\n")[0] if testo_eff else ""
            # previewStyle: 2=mostra nome bin (Show), 1=mostra preview testo
            preview_style = clip.get("previewStyle", 2)
            # Colore bin: usa il valore del clip se presente, altrimenti il default del set
            # Se il default è valorizzato sovrascrive tutti i clip del set
            # Se è vuoto/None ogni clip mantiene il suo colore (o nessuno)
            bin_tint   = default_bin_tint   or clip.get("binTintColor",  None)
            text_color = default_text_color or clip.get("textColor",     None)

            colore_info = ""
            if bin_tint or text_color:
                colore_info = f"  [sfondo={bin_tint or '—'} testo={text_color or '—'}]"
            log(f"         [{clip_id}] pos={pos}: \"{clip['title']}\"{colore_info}")
            if prima_riga:
                log(f"                   → {prima_riga}")

            if dry_run_solo_log:
                continue   # mostra il log ma non scrivere nulla

            if not dry_run:
                cur.execute("""
                    INSERT INTO clippingTable
                        (ID, clippingSetTablePointer, creationDateTime, modificationDateTime,
                         the_position, name, previewStyle, type, appName, appBundleID,
                         kind, origFileName, previewScale, previewUnknown, previewFlavorIdx,
                         info, persistentID, compName, img_w, img_h, noPreview,
                         binTintColor, textColor)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (clip_id, set_id_per_clip, ts, ts, pos, clip["title"],
                      preview_style, 1, None, None, "Text", None,
                      1.0, 0, 0, None, pid, None, 0, 0, 0,
                      bin_tint, text_color))

                testo = clip_text(clip)

                # Flavor 1: UTF-8
                utf8_data = testo.encode("utf-8")
                blob_id += 1
                real_blob_id = inserisci_blob(cur, blob_id, utf8_data)
                flavor_id += 1
                inserisci_flavor(cur, flavor_id, clip_id,
                                 1970562616, b"UTF-8",
                                 b"public.utf8-plain-text", real_blob_id)

                # Flavor 2: UTF-16
                utf16_data = testo.encode("utf-16")
                blob_id += 1
                real_blob_id = inserisci_blob(cur, blob_id, utf16_data)
                flavor_id += 1
                inserisci_flavor(cur, flavor_id, clip_id,
                                 1970567284, b"UTF-16",
                                 b"public.utf16-plain-text", real_blob_id)

                # Flavor 3: Mac plain text
                ascii_data = testo.encode("ascii", errors="replace")
                blob_id += 1
                real_blob_id = inserisci_blob(cur, blob_id, ascii_data)
                flavor_id += 1
                inserisci_flavor(cur, flavor_id, clip_id,
                                 1413830740, b"US-ASCII",
                                 b"com.apple.traditional-mac-plain-text", real_blob_id)

    if not dry_run:
        cur.execute("UPDATE clippingsSettingsTable SET lastPersistentID = ?", (pid,))
        con.commit()
        con.close()

    return {
        "nuovi":     nuovi,
        "saltati":   saltati,
        "tot_clip":  tot_clip,
        "conflitti": conflitti,
        "dry_run":   dry_run,
    }

# ── Esportazione DB → JSON ────────────────────────────────────────────────────

def esporta_db_json(bundle, output_dir, skip_names=None, log_fn=None):
    """
    Legge tutti i Clipset dal bundle e salva un JSON per ognuno.
    Ritorna lista di dict con statistiche per set.
    """
    def log(msg):
        if log_fn:
            log_fn(msg)

    skip_names = set(skip_names or [])
    con = sqlite3.connect(db_path(bundle))
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    cur.execute("SELECT * FROM clippingSetTable ORDER BY the_position")
    sets = cur.fetchall()

    risultati = []
    os.makedirs(output_dir, exist_ok=True)

    for s in sets:
        nome = decode_field(s["name"])
        if nome in skip_names:
            log(f"⏭  SALTATO: \"{nome}\"")
            continue

        set_id   = s["ID"]
        position = s["the_position"]

        cur.execute("""
            SELECT ID, name, previewStyle, binTintColor, textColor, the_position
            FROM clippingTable
            WHERE clippingSetTablePointer = ?
            ORDER BY the_position
        """, (set_id,))
        clips_rows = cur.fetchall()

        clips = []
        for cr in clips_rows:
            clip_id_db    = cr["ID"]
            clip_name     = decode_field(cr["name"]) if cr["name"] else ""
            preview_style = cr["previewStyle"]
            bin_tint      = decode_field(cr["binTintColor"]) if cr["binTintColor"] else None
            text_col      = decode_field(cr["textColor"])    if cr["textColor"]    else None

            # Recupera testo dal blob UTF-8
            # Il campo uti può essere bytes o stringa a seconda della versione di iClip
            testo = ""
            for uti_val in (b"public.utf8-plain-text", "public.utf8-plain-text"):
                cur.execute("""
                    SELECT cb.data
                    FROM clippingFlavorTable cf
                    JOIN clippingBlobTable cb ON cb.ID = cf.data_clippingBlobTable_ID
                    WHERE cf.clippingTablePointer = ?
                      AND cf.uti = ?
                    LIMIT 1
                """, (clip_id_db, uti_val))
                row = cur.fetchone()
                if row and row[0]:
                    try:
                        testo = row[0].decode("utf-8")
                    except Exception:
                        testo = row[0].decode("utf-8", errors="replace")
                    break

            # Se il nome clip è vuoto, usa la prima riga del testo
            if not clip_name:
                clip_name = testo.split("\n")[0].strip() if testo else f"Clip {clip_id_db}"

            clip_dict = {
                "title": clip_name,
                "text":  testo,
            }
            # Campi opzionali solo se non sono i default
            if preview_style != 2:
                clip_dict["previewStyle"] = preview_style
            if bin_tint:
                clip_dict["binTintColor"] = bin_tint
            if text_col:
                clip_dict["textColor"] = text_col

            clips.append(clip_dict)

        clipset = {
            "name":                nome,
            "position":            position,
            "skip":                False,
            "defaultBinTintColor": "",
            "defaultTextColor":    "",
            "clips":               clips,
        }

        # Nome file: snake_case dal nome del clipset
        safe = re.sub(r'[^\w\s–-]', '', nome).strip().lower()
        safe = re.sub(r'[\s–\-]+', '_', safe)
        safe = safe.strip("_") or f"clipset_{set_id}"
        out_path = os.path.join(output_dir, f"{safe}.json")

        with open(out_path, "w", encoding="utf-8") as fp:
            json.dump(clipset, fp, ensure_ascii=False, indent=2)

        log(f"✅ \"{nome}\" → {os.path.basename(out_path)} ({len(clips)} clip)")
        risultati.append({"name": nome, "clips": len(clips), "file": out_path})

    con.close()
    return risultati

# ── Carica JSON Clipset ───────────────────────────────────────────────────────

def sanitize_json(raw: str) -> str:
    """
    Corregge caratteri problematici nei file JSON editati su macOS.
    - Virgolette tipografiche " " → "
    - Apostrofi tipografici ' ' → '
    - Tab letterali → spazi
    """
    return (raw
        .replace('\u201c', '"').replace('\u201d', '"')
        .replace('\u2018', "'").replace('\u2019', "'")
        .replace('\t', '  ')
    )

def carica_json_dir(json_dir, skip_names=None):
    skip_names = set(skip_names or [])
    files = sorted(glob.glob(os.path.join(json_dir, "*.json")))
    clipsets = []
    for f in files:
        if os.path.basename(f) in ("config.json", "build.json"):
            continue
        try:
            with open(f, "r", encoding="utf-8") as fp:
                data = json.loads(sanitize_json(fp.read()))
            if "name" not in data or "clips" not in data:
                continue
            if data.get("skip", False):
                continue
            if data.get("name", "") in skip_names:
                continue
            clipsets.append(data)
        except Exception:
            pass
    clipsets.sort(key=lambda x: x.get("position", 99))
    return clipsets

def _sort_key_backup(path):
    """Chiave di ordinamento: estrae YYYY-MM-DD_HHMM dal nome file."""
    name = os.path.basename(path)
    m = re.search(r'(\d{4}-\d{2}-\d{2})_(\d{4})(?!\d)', name)
    if m:
        return m.group(1) + m.group(2)   # es. "2026-04-261923"
    return str(int(os.path.getmtime(path)))

def lista_backup(backup_dir):
    """Backup .iclipdb ordinati per data/ora (più recente prima), esclusi i _pre_import."""
    pattern = os.path.join(backup_dir, "*.iclipdb")
    files = [f for f in glob.glob(pattern)
             if "_pre_import" not in os.path.basename(f)]
    files.sort(key=_sort_key_backup, reverse=True)
    return files



def reorder_clips_in_clipset(bundle: str, clipset_name: str,
                              new_order: list[str],
                              dry_run: bool = False) -> list[str]:
    """
    Riordina le clip di un clipset esistente senza cancellarle.
    new_order: lista di titoli clip nell'ordine desiderato.
    Le clip non presenti in new_order vengono messe in coda.
    Ritorna lista di messaggi log.
    """
    log_msgs = []
    db = db_path(bundle)
    conn = sqlite3.connect(db)
    cur  = conn.cursor()

    # Trova il clipset
    cur.execute("SELECT ID FROM clippingSetTable WHERE name=?", (clipset_name,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return [f"❌ Clipset '{clipset_name}' non trovato"]
    set_id = row[0]

    # Leggi tutte le clip del set con il loro ID e titolo
    cur.execute("""
        SELECT c.ID, decode_field_py(c.the_title) as title, c.the_position
        FROM clippingTable c
        WHERE c.clippingSetTablePointer=?
        ORDER BY c.the_position
    """, (set_id,))
    # the_title è blittato — usiamo query diretta sulla flavor
    cur.execute("""
        SELECT c.ID, f.the_data, c.the_position
        FROM clippingTable c
        JOIN clippingFlavorTable f ON f.clippingTablePointer=c.ID
        WHERE c.clippingSetTablePointer=?
          AND f.the_type LIKE '%string%'
        ORDER BY c.the_position
    """, (set_id,))
    clips = cur.fetchall()  # (ID, testo_raw, pos)

    # Decodifica titoli (prima riga del testo)
    import base64 as _b64
    def _decode(v):
        if isinstance(v, (bytes, memoryview)):
            try:
                return _b64.b64decode(bytes(v)).decode("utf-8", errors="replace").split("\n")[0].strip()
            except Exception:
                return ""
        return str(v or "").split("\n")[0].strip()

    clip_map = {}  # titolo → (ID, pos_attuale)
    for cid, raw, pos in clips:
        titolo = _decode(raw)
        clip_map[titolo] = (cid, pos)

    # Costruisci nuovo ordine
    ordered_ids   = [clip_map[t][0] for t in new_order if t in clip_map]
    remaining_ids = [cid for title, (cid, _) in clip_map.items()
                     if title not in new_order]
    final_order   = ordered_ids + remaining_ids

    if not dry_run:
        ts = int(datetime.now(timezone.utc).timestamp() * 1000)
        for new_pos, cid in enumerate(final_order, start=1):
            cur.execute("UPDATE clippingTable SET the_position=? WHERE ID=?",
                        (new_pos, cid))
        cur.execute("UPDATE clippingSetTable SET modificationDateTime=? WHERE ID=?",
                    (ts, set_id))
        conn.commit()

    conn.close()
    log_msgs.append(f"{'[DRY-RUN] ' if dry_run else ''}Riordinato clipset '{clipset_name}':")
    for i, cid in enumerate(final_order, 1):
        titolo = next((t for t,(c,_) in clip_map.items() if c==cid), f"ID={cid}")
        log_msgs.append(f"  {i}. {titolo}")
    return log_msgs

# ── GUI ───────────────────────────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"Aggiorna iClip  v{VERSION}")
        self.resizable(True, True)
        self.minsize(820, 580)
        self.protocol("WM_DELETE_WINDOW", self._esci)

        self.cfg = load_config()
        log.info(f"Avvio {APP_NAME} v{VERSION}")
        log.info(f"backup_dir:     {self.cfg['backup_dir']}")
        log.info(f"json_dir:       {self.cfg['json_dir']}")
        log.info(f"export_dir:     {self.cfg['export_dir']}")
        log.info(f"iclip_db:       {self.cfg['iclip_db_bundle']}")

        self._backup_files     = []
        self._exp_backup_files = []
        self._build_ui()
        self._aggiorna_lista_json()
        self._aggiorna_lista_backup(log_primo=True)

    def _esci(self):
        log.info(f"Chiusura {APP_NAME}")
        self.destroy()

    def _build_ui(self):
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=8)

        self.tab_importa = ttk.Frame(nb)
        self.tab_esporta = ttk.Frame(nb)

        nb.add(self.tab_importa, text="  Importa Clipset  ")
        nb.add(self.tab_esporta, text="  Esporta DB → JSON  ")

        self._build_tab_importa()
        self._build_tab_esporta()

        # Bottone Esci globale in fondo alla finestra
        frm_bottom = ttk.Frame(self)
        frm_bottom.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(frm_bottom, text="✖  Esci",
                   command=self._esci).pack(side="right", padx=4)

    # ── Tab Importa ───────────────────────────────────────────────────────────

    def _build_tab_importa(self):
        f   = self.tab_importa
        pad = {"padx": 8, "pady": 4}

        # Selezione backup con Combobox
        frm_db = ttk.LabelFrame(f, text="Database iClip (backup)")
        frm_db.pack(fill="x", **pad)

        frm_combo = ttk.Frame(frm_db)
        frm_combo.pack(fill="x", padx=6, pady=6)
        self.var_backup_label = tk.StringVar(value="— seleziona un backup —")
        self.combo_backup = ttk.Combobox(frm_combo,
                                          textvariable=self.var_backup_label,
                                          state="readonly", width=70)
        self.combo_backup.pack(side="left", fill="x", expand=True)
        self.combo_backup.bind("<<ComboboxSelected>>", self._on_backup_selezionato)
        ttk.Button(frm_combo, text="↻",
                   command=self._aggiorna_lista_backup).pack(side="left", padx=4)

        # Path completo read-only (per riferimento)
        self.var_db = PathVar() if _HAS_PW else tk.StringVar()
        if _HAS_PW:
            PathEntry(frm_db, self.var_db).pack(padx=6, pady=(0,6), fill="x")
        else:
            ttk.Entry(frm_db, textvariable=self.var_db, state="readonly",
                      width=80).pack(padx=6, pady=(0,6), fill="x")

        # Lista JSON disponibili
        frm_json = ttk.LabelFrame(f, text="Clipset da importare (JSON in _Config)")
        frm_json.pack(fill="x", **pad)

        self.lista_json = tk.Listbox(frm_json, selectmode="extended",
                                      height=6, font=("Menlo", 11))
        self.lista_json.pack(fill="x", padx=6, pady=6)

        frm_btn_json = ttk.Frame(frm_json)
        frm_btn_json.pack(fill="x", padx=6, pady=(0, 6))
        ttk.Button(frm_btn_json, text="Seleziona tutti",
                   command=self._seleziona_tutti).pack(side="left", padx=2)
        ttk.Button(frm_btn_json, text="Deseleziona tutti",
                   command=self._deseleziona_tutti).pack(side="left", padx=2)
        ttk.Button(frm_btn_json, text="↻ Ricarica lista",
                   command=self._aggiorna_lista_json).pack(side="left", padx=2)

        # Azioni
        frm_azioni = ttk.Frame(f)
        frm_azioni.pack(fill="x", **pad)
        ttk.Button(frm_azioni, text="🔍  Dry-run",
                   command=self._dry_run).pack(side="left", padx=4)
        ttk.Button(frm_azioni, text="✅  Apply",
                   command=self._apply).pack(side="left", padx=4)
        ttk.Button(frm_azioni, text="🗑  Pulisci log",
                   command=self._pulisci_log).pack(side="right", padx=4)

        # Log
        frm_log = ttk.LabelFrame(f, text="Log")
        frm_log.pack(fill="both", expand=True, **pad)
        self.log = scrolledtext.ScrolledText(frm_log, font=("Menlo", 11),
                                              state="disabled", height=12)
        self.log.pack(fill="both", expand=True, padx=4, pady=4)

    def _aggiorna_lista_backup(self, log_primo=False):
        self._updating_backup = True   # blocca _on_backup_selezionato durante refresh
        self._backup_files = lista_backup(self.cfg["backup_dir"])
        labels = [os.path.basename(f) for f in self._backup_files]
        self.combo_backup["values"] = labels
        if labels:
            self.combo_backup.current(0)
            self.var_db.set(self._backup_files[0])
            if log_primo:
                self._log(f"Backup selezionato: {labels[0]}")
        else:
            self.var_backup_label.set("— nessun backup trovato —")
            self.var_db.set("")
        # Aggiorna combo del tab Esporta se già costruito (senza riscrivere nel log)
        if hasattr(self, "combo_exp"):
            files  = lista_backup(self.cfg["backup_dir"])
            elabels = [os.path.basename(f) for f in files]
            self._exp_backup_files = files
            self.combo_exp["values"] = elabels
            if files:
                self.combo_exp.current(0)
                self.var_exp_db.set(files[0])
        # Resetta la flag solo dopo che tkinter ha processato tutti gli eventi pendenti
        self.after_idle(lambda: setattr(self, "_updating_backup", False))

    def _on_backup_selezionato(self, event=None):
        if getattr(self, "_updating_backup", False):
            return
        idx = self.combo_backup.current()
        if 0 <= idx < len(self._backup_files):
            path = self._backup_files[idx]
            self.var_db.set(path)
            self._log(f"Backup selezionato: {os.path.basename(path)}")

    def _aggiorna_lista_json(self):
        self.lista_json.delete(0, "end")
        self._clipsets_caricati = carica_json_dir(
            self.cfg["json_dir"], self.cfg.get("skip_clipsets", []))
        for cs in self._clipsets_caricati:
            n = len(cs.get("clips", []))
            self.lista_json.insert("end", f"{cs['name']}  ({n} clip)")
        self.lista_json.select_set(0, "end")

    def _seleziona_tutti(self):
        self.lista_json.select_set(0, "end")

    def _deseleziona_tutti(self):
        self.lista_json.select_clear(0, "end")

    def _clipset_selezionati(self):
        idx = self.lista_json.curselection()
        return [self._clipsets_caricati[i] for i in idx]

    def _valida(self):
        bundle = self.var_db.get().strip()
        if not bundle:
            messagebox.showwarning("Database mancante",
                                   "Seleziona un backup dalla lista.")
            return None
        bundle = os.path.expanduser(bundle)
        if not os.path.isdir(bundle):
            messagebox.showerror("Errore", f"Bundle non trovato:\n{bundle}")
            return None
        if not os.path.exists(db_path(bundle)):
            messagebox.showerror("Errore",
                                 f"master.db non trovato nel bundle:\n{bundle}")
            return None
        sel = self._clipset_selezionati()
        if not sel:
            messagebox.showwarning("Nessun Clipset",
                                   "Seleziona almeno un Clipset dalla lista.")
            return None
        return bundle, sel

    def _dry_run(self):
        val = self._valida()
        if not val:
            return
        bundle, sel = val
        self._log("=" * 55)
        self._log(f"DRY-RUN — {os.path.basename(bundle)}")
        self._log("=" * 55)
        try:
            res = importa_clipset(bundle, sel, dry_run=True, log_fn=self._log)
            self._log("-" * 55)
            self._log(f"Clipset nuovi         : {res['nuovi']}")
            self._log(f"Clipset da aggiornare : {len(res['conflitti'])}")
            self._log(f"Clip totali           : {res['tot_clip']}")
            if res["conflitti"]:
                self._log(f"⚠️  Già esistenti (in Apply sceglierai): {', '.join(res['conflitti'])}")
            self._log("✅ Dry-run completato — nessuna modifica eseguita")
        except Exception as e:
            self._log(f"❌ Errore: {e}")

    def _apply(self):
        val = self._valida()
        if not val:
            return
        bundle, sel = val

        try:
            stato = get_stato_db(bundle)
        except Exception as e:
            messagebox.showerror("Errore", str(e))
            return

        conflitti = [cs["name"] for cs in sel if cs["name"] in stato["set_names"]]
        nuovi_set  = [cs["name"] for cs in sel if cs["name"] not in stato["set_names"]]

        # Costruisce il messaggio di conferma
        msg = f"Stai per importare {len(sel)} Clipset in:\n{os.path.basename(bundle)}\n\n"
        if nuovi_set:
            msg += "Nuovi:\n"
            msg += "\n".join(f"  • {c}" for c in nuovi_set) + "\n\n"

        force_update = False
        if conflitti:
            msg += "⚠️  Questi Clipset esistono già nel database:\n"
            msg += "\n".join(f"  • {c}" for c in conflitti) + "\n\n"
            msg += "Vuoi sovrascriverli (i clip esistenti verranno cancellati e riscritti)?\n"
            msg += "Scegli SÌ per sovrascrivere, NO per saltarli."
            risposta = messagebox.askyesnocancel("Clipset esistenti", msg)
            if risposta is None:   # Annulla
                return
            force_update = risposta   # True=sovrascrivi, False=salta
            if not messagebox.askyesno("Conferma Apply",
                    f"Procedo con l'importazione su\n{os.path.basename(bundle)}.\n\n"
                    f"Una copia _pre_import verrà salvata prima.\n\nConfermi?"):
                return
        else:
            msg += "Una copia _pre_import verrà salvata prima di procedere.\n\nConfermi?"
            if not messagebox.askyesno("Conferma Apply", msg):
                return

        self._log("=" * 55)
        self._log(f"APPLY — {os.path.basename(bundle)}"
                  + (" [force-update]" if force_update else ""))
        self._log("=" * 55)
        log.info(f"APPLY su {bundle}"
                 + (" [force-update]" if force_update else ""))
        log.info(f"Clipset da importare: {[cs['name'] for cs in sel]}")
        try:
            res = importa_clipset(bundle, sel, dry_run=False,
                                  log_fn=self._log, force_update=force_update)
            self._log("-" * 55)
            self._log(f"Clipset importati/aggiornati : {res['nuovi']}")
            self._log(f"Clip importati               : {res['tot_clip']}")
            if res["conflitti"]:
                self._log(f"⚠️  Saltati (già esistenti): {', '.join(res['conflitti'])}")
            self._log("✅ Importazione completata")
            log.info(f"APPLY completato: {res['nuovi']} clipset, "
                     f"{res['tot_clip']} clip importati. "
                     f"Saltati: {res.get('conflitti', [])}")
            self._aggiorna_lista_backup()
            messagebox.showinfo("Completato",
                                f"Importati/aggiornati {res['nuovi']} Clipset ({res['tot_clip']} clip).\n"
                                + (f"Saltati: {', '.join(res['conflitti'])}"
                                   if res['conflitti'] else ""))
        except Exception as e:
            self._log(f"❌ Errore: {e}")
            log.error(f"APPLY fallito: {e}", exc_info=True)
            messagebox.showerror("Errore", str(e))

    def _pulisci_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _log(self, msg):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")
        self.update_idletasks()

    # ── Tab Esporta DB → JSON ─────────────────────────────────────────────────

    def _build_tab_esporta(self):
        f   = self.tab_esporta
        pad = {"padx": 8, "pady": 4}

        ttk.Label(f, text=(
            "Legge tutti i Clipset da un backup .iclipdb e salva un file JSON per ognuno.\n"
            "Utile per trasferire la configurazione su un altro Mac o per archivio."
        ), wraplength=720, justify="left").pack(**pad)

        # Selezione backup sorgente
        frm_db = ttk.LabelFrame(f, text="Database sorgente (backup)")
        frm_db.pack(fill="x", **pad)

        frm_combo = ttk.Frame(frm_db)
        frm_combo.pack(fill="x", padx=6, pady=6)
        self.var_exp_label = tk.StringVar(value="— seleziona un backup —")
        self.combo_exp = ttk.Combobox(frm_combo,
                                       textvariable=self.var_exp_label,
                                       state="readonly", width=70)
        self.combo_exp.pack(side="left", fill="x", expand=True)
        self.combo_exp.bind("<<ComboboxSelected>>", self._on_exp_backup_selezionato)
        ttk.Button(frm_combo, text="↻",
                   command=self._aggiorna_combo_exp).pack(side="left", padx=4)

        self.var_exp_db = PathVar() if _HAS_PW else tk.StringVar()
        # PathEntry sostituisce Entry
        _w_exp = (PathEntry(frm_db, self.var_exp_db) if _HAS_PW else
                  ttk.Entry(frm_db, textvariable=self.var_exp_db, state="readonly", width=80))
        _w_exp.pack(
            padx=6, pady=(0, 6), fill="x")

        # Cartella output
        frm_out = ttk.LabelFrame(f, text="Cartella output JSON")
        frm_out.pack(fill="x", **pad)
        frm_out_row = ttk.Frame(frm_out)
        frm_out_row.pack(fill="x", padx=6, pady=6)
        # Cartella output: default da config (export_dir), persistito quando
        # l'utente cambia via "Sfoglia...". Default iniziale (alla prima
        # esecuzione): ~/Documents/output/AggiornaiClip/ via app_output_dir.
        self.var_exp_out = tk.StringVar(value=self.cfg.get("export_dir", ""))
        ttk.Entry(frm_out_row, textvariable=self.var_exp_out, width=60).pack(
            side="left", fill="x", expand=True)
        ttk.Button(frm_out_row, text="Sfoglia…",
                   command=self._scegli_out_exp).pack(side="left", padx=4)

        # Opzione skip Recorder
        self.var_exp_skip = tk.BooleanVar(value=True)
        ttk.Checkbutton(f, text="Salta il Clipset Recorder",
                        variable=self.var_exp_skip).pack(anchor="w", **pad)

        # Azioni
        frm_azioni = ttk.Frame(f)
        frm_azioni.pack(fill="x", **pad)
        ttk.Button(frm_azioni, text="📤  Esporta",
                   command=self._esporta).pack(side="left", padx=4)
        ttk.Button(frm_azioni, text="🗑  Pulisci log",
                   command=self._pulisci_log_exp).pack(side="right", padx=4)

        # Log
        frm_log = ttk.LabelFrame(f, text="Log")
        frm_log.pack(fill="both", expand=True, **pad)
        self.log_exp = scrolledtext.ScrolledText(frm_log, font=("Menlo", 11),
                                                  state="disabled", height=12)
        self.log_exp.pack(fill="both", expand=True, padx=4, pady=4)

        self._aggiorna_combo_exp()

    def _aggiorna_combo_exp(self):
        files  = lista_backup(self.cfg["backup_dir"])
        self._exp_backup_files = files
        labels = [os.path.basename(f) for f in files]
        self.combo_exp["values"] = labels
        if labels:
            self.combo_exp.current(0)
            self.var_exp_db.set(files[0])
        else:
            self.var_exp_label.set("— nessun backup trovato —")
            self.var_exp_db.set("")

    def _on_exp_backup_selezionato(self, event=None):
        idx = self.combo_exp.current()
        if 0 <= idx < len(self._exp_backup_files):
            self.var_exp_db.set(self._exp_backup_files[idx])

    def _scegli_out_exp(self):
        path = filedialog.askdirectory(
            title="Cartella output JSON",
            initialdir=self.var_exp_out.get())
        if path:
            self.var_exp_out.set(path)
            # Persistenza: ricorda la scelta per la prossima sessione.
            # Aggiorna cfg in memoria e salva su file.
            self.cfg["export_dir"] = path
            save_config(self.cfg)

    def _esporta(self):
        bundle = self.var_exp_db.get().strip()
        if not bundle or not os.path.isdir(bundle):
            messagebox.showwarning("Database mancante",
                                   "Seleziona un backup dalla lista.")
            return
        if not os.path.exists(db_path(bundle)):
            messagebox.showerror("Errore", f"master.db non trovato:\n{bundle}")
            return

        out_dir = self.var_exp_out.get().strip()
        if not out_dir:
            messagebox.showwarning("Output mancante",
                                   "Seleziona la cartella di output.")
            return

        skip = ["Recorder"] if self.var_exp_skip.get() else []

        self._log_exp("=" * 55)
        self._log_exp(f"ESPORTA — {os.path.basename(bundle)}")
        self._log_exp(f"Output  → {out_dir}")
        self._log_exp("=" * 55)
        log.info(f"ESPORTA da {bundle} -> {out_dir} (skip={skip})")
        try:
            risultati = esporta_db_json(bundle, out_dir, skip_names=skip,
                                         log_fn=self._log_exp)
            self._log_exp("-" * 55)
            self._log_exp(f"Clipset esportati: {len(risultati)}")
            self._log_exp(f"Clip totali      : {sum(r['clips'] for r in risultati)}")
            self._log_exp("✅ Esportazione completata")
            log.info(f"ESPORTA completato: {len(risultati)} clipset, "
                     f"{sum(r['clips'] for r in risultati)} clip totali")
            messagebox.showinfo("Completato",
                                f"Esportati {len(risultati)} Clipset in:\n{out_dir}")
            # Se l'output coincide con json_dir, aggiorna la lista nel tab Importa
            if os.path.normpath(out_dir) == os.path.normpath(self.cfg["json_dir"]):
                self._aggiorna_lista_json()
        except Exception as e:
            self._log_exp(f"❌ Errore: {e}")
            log.error(f"ESPORTA fallito: {e}", exc_info=True)
            messagebox.showerror("Errore", str(e))

    def _pulisci_log_exp(self):
        self.log_exp.configure(state="normal")
        self.log_exp.delete("1.0", "end")
        self.log_exp.configure(state="disabled")

    def _log_exp(self, msg):
        self.log_exp.configure(state="normal")
        self.log_exp.insert("end", msg + "\n")
        self.log_exp.see("end")
        self.log_exp.configure(state="disabled")
        self.update_idletasks()

# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = App()
    app.mainloop()
