# AggiornaiClip — Documentazione
**Versione app:** 1.3.0
**Aggiornato:** 2026-05-27

---

## 1. Struttura file

```
Python/stable/AggiornaiClip/
├── aggiorna_iClip.py          ← app principale (GUI tkinter)
├── backup_iClip.command       ← script backup database iClip
├── restore_iClip.command      ← script restore con menu interattivo
├── AggiornaiClip_CLD.md       ← questo documento
└── iClip_backup_restore_CLD.md← documentazione script .command

Python/shared/
├── irc_paths.py               ← path canonici dell'ambiente IRC
├── irc_logging.py             ← setup logger standard
└── path_widgets.py            ← widget Tk per percorsi

Python/_Config/AggiornaiClip/
├── build.json                 ← per AppBuilder
├── config.json                ← percorsi, skip_clipsets, export_dir
├── bper.json
├── carte_irc.json
├── carte_sc.json
├── codici_fiscali.json
├── documenti.json
├── fineco.json
└── git_comandi.json

Dropbox/iClip_backup/
└── *.iclipdb                  ← backup database iClip (esclusi _pre_import dalla lista)

~/Documents/log/AggiornaiClip/
└── <YYYYMMDD_HHMMSS>.log      ← log di sessione (uno per esecuzione)

~/Documents/output/AggiornaiClip/
└── *.json                     ← export JSON Clipset (default v1.3.0)
```

---

## 2. Struttura database iClip

Il database è un bundle: `iClip Clippings.iclipdb/master.db`

### Tabelle chiave

**`clippingSetTable`** — i Clipset (bin group)
- `ID`, `the_position`, `name`, `binCount`, `sortby`, `noPreview`

**`clippingTable`** — i singoli clip
- `ID`, `clippingSetTablePointer` (→ Set), `the_position`, `name`, `persistentID`
- `previewStyle` → `2` = mostra nome bin (Show), `1` = mostra preview testo
- `binTintColor` → colore sfondo bin (es. `&h000433FF` = blu)
- `textColor` → colore testo (es. `&h00000000` = nero)

**`clippingFlavorTable`** — le rappresentazioni di ogni clip (UTF-8, UTF-16, ASCII)
- `ID`, `clippingTablePointer` (→ Clip)
- `uti` → **bytes** (es. `b"public.utf8-plain-text"`)
- `the_data_encoding` → **bytes** (es. `b"UTF-8"`)
- `the_data_b64` → sempre **NULL** nei clip reali
- `data_clippingBlobTable_ID` → punta al blob

**`clippingBlobTable`** — dati binari effettivi
- `ID`, `key_md5_b64` (MD5 Base64 come bytes), `mode` (sempre 0), `data` (blob), `size`
- `INSERT OR IGNORE` sulla chiave UNIQUE `key_md5_b64` — gestisce duplicati automaticamente
- In caso di IGNORE, `inserisci_blob` recupera l'ID reale del blob esistente tramite la chiave MD5

**`clippingsSettingsTable`** — contatore globale
- `lastPersistentID` → va aggiornato dopo ogni insert

### Pattern insert per ogni clip di testo

```python
# 1. Insert in clippingTable (con previewStyle=2, binTintColor, textColor)
# 2. Per ogni flavor (UTF-8, UTF-16, ASCII):
#    a. Calcola bytes del testo
#    b. Insert in clippingBlobTable (key = base64(md5(data)))
#       → se esiste già, recupera l'ID reale con SELECT per chiave MD5
#    c. Insert in clippingFlavorTable con l'ID reale del blob
# 3. Update lastPersistentID in clippingsSettingsTable
```

---

## 3. Formato JSON Clipset

```json
{
  "name": "Terminale 1 – Cerca e lista",
  "position": 1,
  "skip": false,
  "defaultBinTintColor": "&h000433FF",
  "defaultTextColor": "&h00000000",
  "clips": [
    {
      "title": "📄 Lista file – tutti i file sotto una cartella",
      "text": "## Elenca tutti i file sotto una cartella e sottocartelle\nfind \"/percorso/cartella\" -type f | sort"
    },
    {
      "title": "BPER Silvia personale",
      "text": "IT68T0538701618000049078168",
      "binTintColor": "&h00FF2600",
      "textColor": "&h00000000",
      "previewStyle": 1
    }
  ]
}
```

### Campi clip

| Campo | Default | Note |
|---|---|---|
| `title` | obbligatorio | Nome del bin visibile in iClip |
| `text` | obbligatorio | Testo incollato (senza ripetere il title) |
| `previewStyle` | `2` | `2` = mostra nome, `1` = mostra preview testo |
| `binTintColor` | null | Sovrascrive il default del set |
| `textColor` | null | Sovrascrive il default del set |

### Campi set (opzionali)

| Campo | Note |
|---|---|
| `defaultBinTintColor` | Se valorizzato, sovrascrive il colore di **tutti** i clip del set, anche quelli con colore proprio. Utile per ridipingere un set in un colpo solo. La prossima esportazione azzera i default e li trasferisce nei singoli clip. |
| `defaultTextColor` | Stesso comportamento di `defaultBinTintColor` per il colore testo. |
| `skip` | `true` = ignorato dall'import |

### Gerarchia colori (dal più specifico al più generico)

```
cs["defaultBinTintColor"]  valorizzato → sovrascrive TUTTI i clip del set
  → altrimenti: clip["binTintColor"]   → colore del singolo clip
    → altrimenti: null                 → default iClip
```

Stesso schema per `defaultTextColor` / `textColor`.

### Retrocompatibilità

- Se `text` inizia con il `title` (formato v1.0), la prima riga viene rimossa automaticamente.
- Se `title == text` e il testo risulterebbe vuoto dopo la rimozione (es. Bridge: `♠️`), il testo viene mantenuto intatto.

### Sanificazione automatica

Lo script corregge automaticamente i file JSON editati con editor macOS:
- Virgolette tipografiche `"` `"` → `"`
- Apostrofi tipografici `'` `'` → `'`
- Tab letterali → spazi

**Attenzione:** per editare i JSON a mano disabilitare le Smart Quotes:
TextEdit → Modifica → Sostituzioni → deseleziona **Virgolette tipografiche**

---

## 4. Workflow operativo

### Importare Clipset su un Mac

```
1. backup_iClip.command
2. aggiorna_iClip.py → tab "Importa Clipset"
   - Seleziona il backup dal menu a tendina
   - Seleziona i Clipset da importare
   - Dry-run → verifica log (mostra nuovi e già esistenti)
   - Apply → se ci sono set già esistenti, chiede se sovrascrivere o saltare
             salva copia _pre_import e procede
3. restore_iClip.command → scegli il backup modificato
4. Apri iClip e verifica
```

### Trasferire configurazione da un Mac all'altro

```
1. Su Mac sorgente: backup_iClip.command
2. Su Mac sorgente: aggiorna_iClip.py → tab "Esporta DB → JSON"
   - Seleziona il backup
   - Output: ~/Documents/output/AggiornaiClip/ (default v1.3.0)
     → da qui sposta i .json in _Config/AggiornaiClip/ se sono modifiche
       da trasferire
3. Aspetta sync Dropbox
4. Su Mac destinazione: backup_iClip.command
5. Su Mac destinazione: aggiorna_iClip.py → tab "Importa Clipset"
   - Dry-run → Apply
6. restore_iClip.command → scegli il backup modificato
```

### Aggiornare i colori di un set esistente

```
1. Esporta il set con "Esporta DB → JSON" → il JSON ha defaultBinTintColor: "" pronto
2. Apri il JSON, inserisci il colore desiderato in defaultBinTintColor e/o defaultTextColor
3. Importa con Apply → scegli "Sovrascrivere" quando chiesto
4. restore_iClip.command → verifica in iClip
5. La prossima esportazione produrrà i colori nei singoli clip e azzererà i default
```

---

## 5. Note tecniche

- **Python con venv `stable`** — lanciare SEMPRE da `pystable` (Python 3.14 in `~/Python_venv/stable/`). Il `python3` di sistema/Homebrew ha un Tk diverso che, in combinazione con i moduli `shared/`, può rendere vuote le finestre Tk.
- Lo script **non tocca mai il database live** — lavora sempre su backup
- I backup `_pre_import_HHMMSS.iclipdb` vengono salvati automaticamente prima di ogni Apply
- `restore_iClip.command` esclude i `_pre_import` dalla lista e ordina per timestamp nel nome file
- Il colore globale dei bin è in `com.irradiatedsoftware.iClip.plist` → `Bin color mask` — non gestibile via DB
- Il campo `uti` in `clippingFlavorTable` è di tipo **blob** — le query devono usare bytes, non stringhe

### Integrazione ambiente IRC (v1.3.0)

L'app è allineata alle convenzioni dell'ambiente IRC tramite i moduli in `Python/shared/`:

- **`irc_paths`** — fornisce i path canonici dell'ambiente:
  - `app_config_dir(APP_NAME)` → `_Config/AggiornaiClip/`
  - `app_output_dir(APP_NAME)` → `~/Documents/output/AggiornaiClip/` (default per export JSON)
  - `app_log_dir(APP_NAME)` → `~/Documents/log/AggiornaiClip/`
- **`irc_logging`** — produce un file di log dedicato per ogni sessione in `~/Documents/log/AggiornaiClip/<timestamp>.log`, con intestazione dei path effettivi della sessione e tracciamento delle operazioni APPLY/ESPORTA
- **`config.json`** ora include il campo `export_dir` persistito: la scelta dell'utente nel tab Esporta viene ricordata tra una sessione e l'altra

Il bootstrap del `sys.path` per `shared/` è gestito in cima allo script: gli import funzionano sia in modalità `python3 aggiorna_iClip.py` sia da bundle PyInstaller (`sys.frozen=True` salta il bootstrap, i moduli sono già nel bundle).

---

## 6. Prossimi sviluppi

- [ ] Tab "Gestione Clipset" — lista clip esistenti nel database, possibilità di eliminare un Clipset
- [ ] Aggiungere al Python Launcher
- [x] AppBuilder — completato
- [x] Refactoring v1.3.0: allineamento ai moduli shared (`irc_paths`, `irc_logging`), default output in `~/Documents/output/AggiornaiClip/`, persistenza `export_dir` in config, log di sessione su file

---

## 7. Colori di riferimento

Formato: `&hAARRGGBB` — AA = opacità (sempre `00` per i bin), RR GG BB = colore hex.

### Colori sfondo bin (`binTintColor`)

| Colore | Valore | Uso suggerito |
|---|---|---|
| Blu | `&h000433FF` | Dati finanziari (IBAN, conti) |
| Rosso | `&h00FF2600` | Email, contatti |
| Verde | `&h0034C759` | Comandi sicuri, conferme |
| Arancio | `&h00FF9500` | Attenzione, da verificare |
| Viola | `&h005856D6` | Dati personali, documenti |
| Rosa | `&h00FF2D55` | Priorità alta |
| Grigio scuro | `&h003A3A3C` | Comandi Terminale, sistema |
| Grigio chiaro | `&h00AEAEB2` | Neutro, uso generico |
| Verde chiaro | `&h008EFA00` | Dati bancari (IBAN) |
| Grigio chiaro 2 | `&h00CBCBCB` | Terminale, leggibile |
| Nero | `&h00000000` | Massimo contrasto |
| Bianco/default | `&hFFFFFFFF` | Sfondo chiaro (come default iClip) |

### Colori testo (`textColor`)

| Colore | Valore | Uso |
|---|---|---|
| Nero | `&h00000000` | Su sfondi chiari |
| Nero 2 | `&h01000000` | Variante nero (usata con grigio chiaro) |
| Bianco | `&hFFFFFFFF` | Su sfondi scuri |
| Omesso | — | iClip sceglie automaticamente il contrasto |

### Combinazioni collaudate

| Sfondo | Testo | Usato in |
|---|---|---|
| `&h000433FF` | `&h00000000` | IBAN |
| `&h000433FF` | omesso | IBAN variante |
| `&h00FF2600` | omesso | Mail |
| `&h00CBCBCB` | `&h01000000` | Terminale (grigio chiaro, testo nero) |
| `&h008EFA00` | `&h01000000` | IBAN (verde chiaro, testo nero) |
| `&h000433FF` | `&h00000000` | Mail (blu, testo nero) |
| `&h003A3A3C` | omesso | Comandi sistema |
| omesso | omesso | Default iClip |

### Note
- `textColor` omesso è la scelta più sicura: iClip sceglie automaticamente il contrasto
- Il canale AA è sempre `00` — valori diversi non producono effetti visibili
- I valori sono stati verificati direttamente sul database iClip su macOS

---

## 8. Storico modifiche

- **v1.3.0 (27/05/2026)** — Refactoring allineamento ambiente IRC:
  - Migrazione a `irc_paths` per i path canonici (rimossi `HOME`, `DROPBOX` hardcoded)
  - Aggiunto `irc_logging` per log di sessione su file in `~/Documents/log/AggiornaiClip/`
  - Default export cambiato da `~/Desktop` a `~/Documents/output/AggiornaiClip/`
  - Nuovo campo `export_dir` in `config.json` con persistenza della scelta utente
  - Logging strutturato delle operazioni APPLY ed ESPORTA con dettaglio file e esiti
- **v1.2.1 (23/05/2026)** — Ultima versione pre-refactoring
