#!/usr/bin/env bash
#
# Lensfy — desinstalador (usuário atual).
#
#   ./uninstall.sh            remove o app, comando, atalho e serviço
#   ./uninstall.sh --purge    + apaga os dados em ~/.lensfy (clusters, token, histórico)
set -euo pipefail

PREFIX="${LENSFY_PREFIX:-${XDG_DATA_HOME:-$HOME/.local/share}/lensfy}"
BIN="$HOME/.local/bin/lensfy"
DESKTOP="${XDG_DATA_HOME:-$HOME/.local/share}/applications/lensfy.desktop"
UNIT="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/lensfy.service"
STATE="${XDG_STATE_HOME:-$HOME/.local/state}/lensfy"
PURGE=0
[[ "${1:-}" == "--purge" ]] && PURGE=1

# Para o servidor / serviço, se estiver rodando.
if command -v systemctl >/dev/null 2>&1 && systemctl --user list-unit-files 2>/dev/null | grep -q '^lensfy\.service'; then
  systemctl --user disable --now lensfy.service >/dev/null 2>&1 || true
fi
[[ -x "$BIN" ]] && "$BIN" stop >/dev/null 2>&1 || true

echo "→ Removendo arquivos instalados"
rm -rf "$PREFIX" "$STATE"
rm -f "$BIN" "$DESKTOP" "$UNIT"
command -v systemctl >/dev/null 2>&1 && systemctl --user daemon-reload >/dev/null 2>&1 || true
update-desktop-database "$(dirname "$DESKTOP")" >/dev/null 2>&1 || true

if [[ $PURGE -eq 1 ]]; then
  echo "→ Apagando dados em ~/.lensfy"
  rm -rf "$HOME/.lensfy"
else
  echo "  (dados preservados em ~/.lensfy — use --purge para apagar)"
fi
echo "✓ Lensfy desinstalado."
