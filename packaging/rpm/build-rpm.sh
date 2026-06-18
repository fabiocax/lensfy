#!/usr/bin/env bash
#
# Lensfy — gera o pacote .rpm (Fedora/RHEL) para distribuição.
#
# Empacota o backend + um wheelhouse OFFLINE (todas as dependências como wheels),
# de modo que a instalação não precise de internet — o venv é montado no %post a
# partir dos wheels embutidos. Os wheels são específicos da plataforma e da ABI do
# Python do host de build (ex.: x86_64 / cp314), então o destino precisa do mesmo
# python3 (o pacote declara Requires: python(abi) = <versão>).
#
#   ./packaging/rpm/build-rpm.sh            gera dist/lensfy-<ver>.rpm
#   LENSFY_VERSION=0.2.0 ./packaging/rpm/build-rpm.sh
#
# Requisitos do host de build:  rpm-build rpmdevtools python3-pip  (+ internet)
#   sudo dnf install -y rpm-build rpmdevtools python3-pip
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
VERSION="${LENSFY_VERSION:-0.1.0}"
PYABI="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"

command -v rpmbuild >/dev/null 2>&1 || {
  echo "erro: rpmbuild não encontrado. Instale com:" >&2
  echo "  sudo dnf install -y rpm-build rpmdevtools python3-pip" >&2
  exit 1
}

TOP="$HERE/_build"
echo "→ Preparando árvore de build em $TOP"
rm -rf "$TOP"
mkdir -p "$TOP"/{SOURCES,SPECS,BUILD,BUILDROOT,RPMS,SRPMS}

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

# 1) Tarball do app (sem venv/run/caches/tests) com prefixo "app/".
echo "→ Empacotando o app"
mkdir -p "$STAGE/app"
( cd "$ROOT/backend" && tar \
    --exclude='.venv' --exclude='.run' --exclude='__pycache__' \
    --exclude='.pytest_cache' --exclude='*.pyc' --exclude='tests' \
    -cf - . ) | ( cd "$STAGE/app" && tar -xf - )
( cd "$STAGE" && tar -czf "$TOP/SOURCES/lensfy-app-$VERSION.tar.gz" app )

# 2) Wheelhouse offline (todas as dependências como wheels para esta plataforma).
echo "→ Baixando wheels (wheelhouse offline) — pode demorar"
mkdir -p "$STAGE/wheelhouse"
python3 -m pip download -r "$ROOT/backend/requirements.txt" -d "$STAGE/wheelhouse"
( cd "$STAGE" && tar -czf "$TOP/SOURCES/wheelhouse-$VERSION.tar.gz" wheelhouse )

# 3) Demais fontes (launcher compartilhado, atalho, ícone).
cp "$ROOT/packaging/lensfy"              "$TOP/SOURCES/lensfy.launcher"
cp "$HERE/lensfy.desktop"                "$TOP/SOURCES/lensfy.desktop"
cp "$ROOT/backend/static/icons/icon-512.png" "$TOP/SOURCES/lensfy.png"
cp "$HERE/lensfy.spec"                   "$TOP/SPECS/lensfy.spec"

# 4) Build do binário .rpm.
echo "→ rpmbuild (versão $VERSION, python abi $PYABI)"
rpmbuild -bb \
  --define "_topdir $TOP" \
  --define "lensfy_version $VERSION" \
  --define "lensfy_pyabi $PYABI" \
  "$TOP/SPECS/lensfy.spec"

# 5) Coleta o artefato.
OUT="$HERE/dist"
mkdir -p "$OUT"
cp "$TOP"/RPMS/*/lensfy-*.rpm "$OUT"/
echo
echo "✓ RPM gerado:"
ls -1 "$OUT"/lensfy-*.rpm
echo
echo "Instalar:    sudo dnf install $OUT/lensfy-$VERSION-1.*.rpm"
echo "Rodar:       lensfy        (ou abra 'Lensfy' no menu de aplicativos)"
echo "Remover:     sudo dnf remove lensfy"
