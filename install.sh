#!/usr/bin/env bash
#
# Lensfy — instalador para Linux (usuário atual, sem root).
#
# Instala o backend (que serve a UI + API) num venv isolado, cria o comando
# `lensfy`, um atalho no menu de aplicativos e, opcionalmente, um serviço
# systemd --user. Re-executar atualiza a instalação no lugar.
#
#   ./install.sh              instala / atualiza
#   ./install.sh --service    + serviço systemd --user (inicia no login)
#   ./install.sh --help
#
# Layout instalado:
#   ~/.local/share/lensfy/app     código + templates + static
#   ~/.local/share/lensfy/venv    venv com as dependências
#   ~/.local/bin/lensfy           launcher (start/stop/open…)
#   ~/.local/share/applications/lensfy.desktop
#   ~/.local/state/lensfy/        pid + log (em runtime)
#   ~/.lensfy/                     dados (SQLite, token do dispositivo) — preservado
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$ROOT/backend"
PREFIX="${LENSFY_PREFIX:-${XDG_DATA_HOME:-$HOME/.local/share}/lensfy}"
APP="$PREFIX/app"
VENV="$PREFIX/venv"
BIN="$HOME/.local/bin"
DESKTOP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
SERVICE=0

for a in "$@"; do
  case "$a" in
    --service) SERVICE=1 ;;
    -h|--help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "argumento desconhecido: $a" >&2; exit 2 ;;
  esac
done

[[ -d "$SRC" ]] || { echo "erro: $SRC não encontrado (rode a partir da raiz do repo)" >&2; exit 1; }
PY="$(command -v python3 || true)"
[[ -n "$PY" ]] || { echo "erro: python3 não encontrado no PATH" >&2; exit 1; }
echo "→ Python: $("$PY" --version 2>&1)"

# 1) Copia o app (sem venv/run/caches) para o destino.
echo "→ Instalando app em $APP"
rm -rf "$APP"; mkdir -p "$APP"
( cd "$SRC" && tar --exclude='.venv' --exclude='.run' --exclude='__pycache__' \
    --exclude='.pytest_cache' --exclude='*.pyc' -cf - . ) | ( cd "$APP" && tar -xf - )

# 2) Cria o venv e instala as dependências (com fallback p/ wheels-only).
echo "→ Criando venv em $VENV"
"$PY" -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip >/dev/null
echo "→ Instalando dependências (pode demorar)…"
if ! "$VENV/bin/pip" install -r "$APP/requirements.txt"; then
  echo "  primeira tentativa falhou; tentando com --only-binary=:all: (Python novo costuma exigir wheels)…"
  "$VENV/bin/pip" install --only-binary=:all: -r "$APP/requirements.txt"
fi

# 3) Instala o launcher.
echo "→ Instalando comando 'lensfy' em $BIN"
mkdir -p "$BIN"
install -m 0755 "$ROOT/packaging/lensfy" "$BIN/lensfy"

# 4) Atalho no menu de aplicativos + ícone.
echo "→ Criando atalho no menu de aplicativos"
mkdir -p "$DESKTOP_DIR"
ICON="$APP/static/icons/icon-512.png"
[[ -f "$ICON" ]] || ICON="$APP/static/icons/icon.svg"
cat > "$DESKTOP_DIR/lensfy.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Lensfy
GenericName=Kubernetes Manager
Comment=Gerenciador local de clusters Kubernetes
Exec=$BIN/lensfy open
Icon=$ICON
Terminal=false
Categories=Development;
Keywords=kubernetes;k8s;cluster;devops;lens;
StartupNotify=true
EOF
update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true

# 5) Serviço systemd --user (opcional).
if [[ $SERVICE -eq 1 ]]; then
  echo "→ Configurando serviço systemd --user"
  UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
  mkdir -p "$UNIT_DIR"
  cat > "$UNIT_DIR/lensfy.service" <<EOF
[Unit]
Description=Lensfy — gerenciador local de clusters Kubernetes
After=network.target

[Service]
Type=simple
WorkingDirectory=$APP
ExecStart=$VENV/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=on-failure

[Install]
WantedBy=default.target
EOF
  if command -v systemctl >/dev/null 2>&1; then
    systemctl --user daemon-reload || true
    systemctl --user enable --now lensfy.service || true
    echo "  serviço habilitado (inicia no login). Gerencie com: systemctl --user {status|stop|restart} lensfy"
  else
    echo "  systemctl indisponível; unit criada em $UNIT_DIR/lensfy.service"
  fi
fi

echo
echo "✓ Lensfy instalado."
case ":$PATH:" in
  *":$BIN:"*) echo "  Rode:  lensfy        (ou abra 'Lensfy' no menu de aplicativos)" ;;
  *) echo "  Aviso: $BIN não está no PATH. Use o caminho completo ($BIN/lensfy) ou adicione ao PATH:";
     echo "         echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.bashrc && source ~/.bashrc" ;;
esac
[[ $SERVICE -eq 0 ]] && echo "  Dica: ./install.sh --service  para iniciar automaticamente no login."
echo "  Desinstalar: ./uninstall.sh"
