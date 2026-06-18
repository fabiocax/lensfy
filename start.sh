#!/usr/bin/env bash
# Atalho: inicia o Lensfy. Veja ./lensfy.sh para todas as opções e variáveis.
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lensfy.sh" start "$@"
