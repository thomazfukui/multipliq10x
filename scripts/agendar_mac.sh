#!/bin/bash
# Agenda a verificação de hora em hora (launchd). Uso: ./scripts/agendar_mac.sh [desligar]
set -euo pipefail
cd "$(dirname "$0")/.."
ROTULO="br.com.ldt.nfse"
PLIST="$HOME/Library/LaunchAgents/$ROTULO.plist"

if [ "${1:-}" = "desligar" ]; then
  launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  echo "Agendamento removido."
  exit 0
fi

mkdir -p "$HOME/Library/LaunchAgents" dados
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$ROTULO</string>
  <key>ProgramArguments</key>
  <array>
    <string>$(pwd)/.venv/bin/python</string>
    <string>-m</string><string>nfse_auto</string><string>verificar</string>
  </array>
  <key>WorkingDirectory</key><string>$(pwd)</string>
  <key>StartInterval</key><integer>3600</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$(pwd)/dados/log.txt</string>
  <key>StandardErrorPath</key><string>$(pwd)/dados/log.txt</string>
</dict>
</plist>
PL
launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Agendado: roda agora e depois de hora em hora (enquanto o Mac estiver ligado)."
echo "Log: $(pwd)/dados/log.txt     Para desligar: ./scripts/agendar_mac.sh desligar"
