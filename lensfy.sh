#!/usr/bin/env bash
#
# Lensfy — controle da aplicação (backend FastAPI/uvicorn que serve a UI + API).
#
#   ./lensfy.sh start      inicia em background
#   ./lensfy.sh stop       para (encerra o grupo de processos)
#   ./lensfy.sh restart    para e inicia de novo
#   ./lensfy.sh status     mostra estado + health
#   ./lensfy.sh logs       acompanha o log (tail -f)
#
# Variáveis de ambiente:
#   LENSFY_HOST    (default 127.0.0.1)  — use 0.0.0.0 para expor na rede (CUIDADO: sem auth)
#   LENSFY_PORT    (default 8000)
#   LENSFY_RELOAD  (1 = auto-reload p/ dev)
#   demais LENSFY_* (ex.: LENSFY_ANTHROPIC_API_KEY) são repassadas ao processo.
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$ROOT/backend"
VENV="$BACKEND/.venv"
RUN_DIR="$BACKEND/.run"
PID_FILE="$RUN_DIR/lensfy.pid"
LOG_FILE="$RUN_DIR/lensfy.log"
HOST="${LENSFY_HOST:-127.0.0.1}"
PORT="${LENSFY_PORT:-8000}"
URL="http://$HOST:$PORT"

is_running() {
  [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE" 2>/dev/null)" 2>/dev/null
}

wait_health() {
  command -v curl >/dev/null 2>&1 || { sleep 1; return 0; }
  local pid="$1"
  for _ in $(seq 1 40); do
    if curl -fs "$URL/health" >/dev/null 2>&1; then return 0; fi
    kill -0 "$pid" 2>/dev/null || return 1   # processo morreu
    sleep 0.5
  done
  return 2   # subiu mas /health não respondeu a tempo
}

start() {
  if is_running; then
    echo "Lensfy já está rodando (PID $(cat "$PID_FILE")) — $URL"
    return 0
  fi
  if [[ ! -x "$VENV/bin/uvicorn" ]]; then
    echo "erro: venv não encontrado em $VENV" >&2
    echo "  crie com: cd '$BACKEND' && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
    exit 1
  fi
  mkdir -p "$RUN_DIR"

  local extra=()
  [[ "${LENSFY_RELOAD:-0}" == "1" ]] && extra+=(--reload)

  echo "Iniciando Lensfy em $URL …"
  # setsid -> processo vira líder de sessão/grupo (PGID == PID), permitindo
  # encerrar uvicorn + todos os filhos (reloader, PTYs do kubectl, helm, gcloud)
  # de uma vez no stop. cwd = backend para resolver o pacote app.main.
  ( cd "$BACKEND" && exec setsid "$VENV/bin/uvicorn" app.main:app \
      --host "$HOST" --port "$PORT" "${extra[@]}" >>"$LOG_FILE" 2>&1 <&- ) &
  local pid=$!
  echo "$pid" > "$PID_FILE"

  case "$(wait_health "$pid"; echo $?)" in
    0) echo "✓ Lensfy no ar: $URL   (PID $pid · log: $LOG_FILE)" ;;
    1) echo "✗ falha ao iniciar — últimas linhas do log:" >&2
       tail -n 25 "$LOG_FILE" >&2; rm -f "$PID_FILE"; exit 1 ;;
    *) echo "⚠ iniciou (PID $pid) mas /health não respondeu a tempo — veja $LOG_FILE" >&2 ;;
  esac
}

stop() {
  if ! is_running; then
    echo "Lensfy não está rodando."
    if pgrep -f "uvicorn app.main:app" >/dev/null 2>&1; then
      echo "  aviso: há uvicorn app.main:app fora deste script (pgrep -f 'uvicorn app.main:app')."
    fi
    rm -f "$PID_FILE"
    return 0
  fi
  local pid; pid="$(cat "$PID_FILE")"
  echo "Parando Lensfy (PID $pid) …"
  # sinaliza o grupo inteiro (-pid); cai pro pid simples se não for líder de grupo
  kill -TERM "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
  for _ in $(seq 1 20); do
    kill -0 "$pid" 2>/dev/null || { rm -f "$PID_FILE"; echo "✓ parado."; return 0; }
    sleep 0.5
  done
  echo "  não parou com SIGTERM; enviando SIGKILL."
  kill -KILL "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
  rm -f "$PID_FILE"
  echo "✓ parado (forçado)."
}

status() {
  if is_running; then
    local pid; pid="$(cat "$PID_FILE")"
    echo "● rodando — PID $pid · $URL"
    if command -v curl >/dev/null 2>&1; then
      if curl -fs "$URL/health" >/dev/null 2>&1; then echo "  health: ok"; else echo "  health: sem resposta"; fi
    fi
  else
    echo "○ parado."
  fi
}

case "${1:-}" in
  start)   start ;;
  stop)    stop ;;
  restart) stop; start ;;
  status)  status ;;
  logs)    exec tail -n 100 -f "$LOG_FILE" ;;
  *) echo "uso: $0 {start|stop|restart|status|logs}" >&2; exit 2 ;;
esac
