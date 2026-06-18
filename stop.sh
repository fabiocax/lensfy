#!/usr/bin/env bash
# Atalho: para o Lensfy. Veja ./lensfy.sh para todas as opções.
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lensfy.sh" stop "$@"
