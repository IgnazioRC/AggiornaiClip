# iClip — Backup e Restore manuale

## Contesto

iClip con database condiviso su Dropbox causa instabilità (loop di reload, errori di accesso al database). La soluzione adottata è tenere il database **locale** su ogni Mac e sincronizzare manualmente i Clip Sets quando necessario.

I due script si trovano in:
`~/Library/CloudStorage/Dropbox/iClip_backup/`

---

## Backup — `backup_iClip.command`

Esegui dal Terminale oppure con doppio clic dal Finder.

Lo script:
- chiude iClip se aperto
- copia il database in `iClip_backup` con nome che include il Mac e la data/ora
- riapre iClip

Esempio di file generato:
```
iClip_iMac_Gignese_2026-04-25_0725.iclipdb
iClip_iMac_BdS_2026-04-25_0930.iclipdb
```

**Quando farlo:** ogni volta che modifichi i Clip Sets in modo significativo (nuovo bin, nuova struttura), prima di passare a lavorare sull'altro Mac.

---

## Restore — `restore_iClip.command`

Esegui dal Terminale (non con doppio clic — richiede input interattivo).

```bash
~/Library/CloudStorage/Dropbox/iClip_backup/restore_iClip.command
```

Lo script:
- mostra la **data del database corrente** per aiutare la scelta
- mostra l'elenco numerato dei backup disponibili
- chiede quale ripristinare
- chiede conferma
- salva il database attuale come `iClip Clippings _prima_restore.iclipdb` (piano B)
- chiude iClip, ripristina il backup scelto, riapre iClip

Esempio di output:
```
Backup disponibili:
-------------------
Database attuale: 2026-04-25 07:31
  1) iClip_iMac_Gignese_2026-04-25_0725.iclipdb

Quale vuoi ripristinare? (1-1):
```

---

## Workflow tipico

> Hai aggiornato i Clip Sets su **iMac Gignese** e vuoi portarli su **iMac BdS**:

1. Su **Gignese**: esegui `backup_iClip.command`
2. Aspetta che Dropbox sincronizzi
3. Su **BdS**: esegui `restore_iClip.command` e scegli il backup appena creato da Gignese

---

## Note

- Il database iClip è una cartella bundle (`.iclipdb`), non un file singolo — gli script usano `cp -R` di conseguenza.
- Il backup di sicurezza `_prima_restore` viene sovrascritto ad ogni restore — se serve conservarlo, rinominalo manualmente.
- Ricordare di fare `chmod +x` sugli script dopo averli copiati su un nuovo Mac (i permessi di esecuzione non vengono sincronizzati da Dropbox).
