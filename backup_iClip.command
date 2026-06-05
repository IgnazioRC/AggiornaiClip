#!/bin/zsh

set -e

MAC_NAME="$(scutil --get ComputerName | tr ' ' '_')"
DATA="$(date +"%Y-%m-%d_%H%M")"

BACKUP_DIR="$HOME/Library/CloudStorage/Dropbox/iClip_backup"
ICLIP_DB="$HOME/Library/Application Support/iClip/iClip Clippings.iclipdb"

WAS_RUNNING=0

# Se iClip è aperto, chiudilo in modo ordinato
if pgrep -x "iClip" >/dev/null; then
  WAS_RUNNING=1
  echo "iClip è aperto: lo chiudo..."
  osascript -e 'quit app "iClip"'

  # attesa chiusura
  for i in {1..20}; do
    if ! pgrep -x "iClip" >/dev/null; then
      break
    fi
    sleep 0.5
  done

  # se non si chiude, esci senza copiare
  if pgrep -x "iClip" >/dev/null; then
    echo "ERRORE: iClip non si è chiuso. Backup annullato."
    exit 1
  fi
fi

# Verifica database
if [ ! -e "$ICLIP_DB" ]; then
  echo "ERRORE: database iClip non trovato:"
  echo "$ICLIP_DB"
  exit 1
fi

mkdir -p "$BACKUP_DIR"

DEST="$BACKUP_DIR/iClip_${MAC_NAME}_${DATA}.iclipdb"

cp -R "$ICLIP_DB" "$DEST"

echo "Backup completato:"
echo "$DEST"

# Riapri iClip solo se era aperto prima
if [ "$WAS_RUNNING" -eq 1 ]; then
  echo "Riapro iClip..."
  open -a "iClip"
fi