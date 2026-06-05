#!/bin/zsh

BACKUP_DIR="$HOME/Library/CloudStorage/Dropbox/iClip_backup"
ICLIP_DB="$HOME/Library/Application Support/iClip/iClip Clippings.iclipdb"

# Raccoglie i backup escludendo i _pre_import, ordinati per nome file (= per data/ora)
BACKUPS=("${(@f)$(find "$BACKUP_DIR" -maxdepth 1 -name "*.iclipdb" \
  | grep -v "_pre_import" \
  | sort -r)}")

if [ ${#BACKUPS[@]} -eq 0 ]; then
  echo "ERRORE: nessun backup trovato in:"
  echo "$BACKUP_DIR"
  exit 1
fi

# Mostra elenco numerato con data/ora di modifica
echo ""
echo "Backup disponibili:"
echo "--------------------"
echo ""
echo "Database attuale: $(stat -f "%Sm" -t "%Y-%m-%d %H:%M" "$ICLIP_DB")"

# Calcola la larghezza del nome più lungo per allineamento dinamico
MAX_LEN=0
for f in "${BACKUPS[@]}"; do
  LEN=${#$(basename $f)}
  (( LEN > MAX_LEN )) && MAX_LEN=$LEN
done
(( MAX_LEN += 3 ))

i=1
for f in "${BACKUPS[@]}"; do
  MDATE=$(stat -f "%Sm" -t "%Y-%m-%d %H:%M" "$f")
  printf "  %2d)  %-${MAX_LEN}s  %s\n" $i "$(basename $f)" "$MDATE"
  (( i++ ))
done
echo ""

# Chiede la scelta
echo -n "Quale vuoi ripristinare? (1-${#BACKUPS[@]}): "
read SCELTA

# Valida la scelta
if ! [[ "$SCELTA" =~ ^[0-9]+$ ]] || [ "$SCELTA" -lt 1 ] || [ "$SCELTA" -gt ${#BACKUPS[@]} ]; then
  echo "Scelta non valida. Uscita."
  exit 1
fi

SELECTED="${BACKUPS[$SCELTA]}"
echo ""
echo "Ripristino: $(basename $SELECTED)"
echo -n "Confermi? (s/n): "
read CONFERMA

if [[ "$CONFERMA" != "s" && "$CONFERMA" != "S" ]]; then
  echo "Annullato."
  exit 0
fi

# Chiude iClip se aperto
WAS_RUNNING=0
if pgrep -x "iClip" >/dev/null; then
  WAS_RUNNING=1
  echo "iClip è aperto: lo chiudo..."
  osascript -e 'quit app "iClip"'

  for i in {1..20}; do
    if ! pgrep -x "iClip" >/dev/null; then
      break
    fi
    sleep 0.5
  done

  if pgrep -x "iClip" >/dev/null; then
    echo "ERRORE: iClip non si è chiuso. Ripristino annullato."
    exit 1
  fi
fi

# Salva il database attuale come safety backup (nome fisso, viene sovrascritto)
SAFETY="$HOME/Library/Application Support/iClip/iClip Clippings _prima_restore.iclipdb"
if [ -e "$ICLIP_DB" ]; then
  echo "Salvo il database attuale come sicurezza..."
  cp -R "$ICLIP_DB" "$SAFETY"
fi

# Ripristina
rm -rf "$ICLIP_DB"
cp -R "$SELECTED" "$ICLIP_DB"

echo "Ripristino completato."

# Riapre iClip se era aperto
if [ "$WAS_RUNNING" -eq 1 ]; then
  echo "Riapro iClip..."
  open -a "iClip"
fi