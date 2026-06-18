# Lensfy RPM — empacota o backend (que serve a UI + API) + um wheelhouse offline.
# O venv é construído no %post a partir dos wheels embutidos (sem rede), garantindo
# que os caminhos do venv fiquem corretos no destino. Veja packaging/rpm/build-rpm.sh.

%global appname   lensfy
%global appdir    %{_prefix}/lib/%{appname}
# Nada para compilar/strip/bytecompile: o payload é app .py + wheels; o venv vem no %post.
%global __os_install_post %{nil}
%global debug_package %{nil}

Name:           lensfy
Version:        %{?lensfy_version}%{!?lensfy_version:0.1.0}
Release:        1%{?dist}
Summary:        Gerenciador local de clusters Kubernetes (alternativa ao Lens/OpenLens)

# TODO: defina a licença real do projeto (não há arquivo LICENSE no repo).
License:        MIT
URL:            https://github.com/fabiocax/lensfy

Source0:        %{appname}-app-%{version}.tar.gz
Source1:        wheelhouse-%{version}.tar.gz
Source2:        lensfy.launcher
Source3:        lensfy.desktop
Source4:        lensfy.png

# Wheels são específicos da plataforma+ABI do host de build.
ExclusiveArch:  x86_64
# Auto-deps geraria requires errados a partir dos .py/.whl embutidos.
AutoReqProv:    no
Requires:       python3
Requires:       python3-pip
# Os wheels embutidos são da ABI usada no build (ex.: cp314) — exija o python certo.
%{?lensfy_pyabi:Requires: python(abi) = %{lensfy_pyabi}}
Recommends:     kubectl

%description
Lensfy é um aplicativo local para gerenciar clusters Kubernetes — alternativa
open-source ao Lens/OpenLens. Roda inteiramente na máquina do usuário: o backend
FastAPI serve a interface web e a API em http://127.0.0.1:8000, com acesso restrito
a loopback + token de dispositivo (sem login/senha). Recursos: multi-cluster, logs
e métricas em tempo real, terminal integrado, deploy de manifestos/Helm,
port-forward, exec em pods e assistente de IA opcional.

Após instalar, rode "lensfy" ou abra "Lensfy" no menu de aplicativos.

%prep
# Source0 contém o diretório "app/"; Source1 contém "wheelhouse/".
%setup -q -c -n %{appname}-%{version}
tar -xzf %{SOURCE1}

%build
# nada a compilar

%install
rm -rf %{buildroot}

# App (código + templates + static) e wheelhouse offline.
install -d %{buildroot}%{appdir}
cp -a app       %{buildroot}%{appdir}/app
cp -a wheelhouse %{buildroot}%{appdir}/wheelhouse

# Launcher compartilhado + wrapper em /usr/bin que fixa o prefixo do sistema.
install -Dm0755 %{SOURCE2} %{buildroot}%{appdir}/launcher
install -d %{buildroot}%{_bindir}
cat > %{buildroot}%{_bindir}/lensfy <<EOF
#!/usr/bin/env bash
export LENSFY_PREFIX=%{appdir}
exec %{appdir}/launcher "\$@"
EOF
chmod 0755 %{buildroot}%{_bindir}/lensfy

# Atalho de menu + ícone.
install -Dm0644 %{SOURCE3} %{buildroot}%{_datadir}/applications/lensfy.desktop
install -Dm0644 %{SOURCE4} %{buildroot}%{_datadir}/icons/hicolor/512x512/apps/lensfy.png

%files
%dir %{appdir}
%{appdir}/app
%{appdir}/wheelhouse
%{appdir}/launcher
%{_bindir}/lensfy
%{_datadir}/applications/lensfy.desktop
%{_datadir}/icons/hicolor/512x512/apps/lensfy.png

%post
# Constrói o venv a partir do wheelhouse embutido (offline). Idempotente.
if [ ! -x %{appdir}/venv/bin/python ]; then
  python3 -m venv %{appdir}/venv || exit 0
fi
%{appdir}/venv/bin/pip install --no-index --find-links=%{appdir}/wheelhouse \
    -r %{appdir}/app/requirements.txt >/dev/null 2>&1 || \
  echo "lensfy: falha ao montar o venv a partir do wheelhouse (veja %{appdir}/wheelhouse)" >&2
/usr/bin/update-desktop-database &>/dev/null || :
/usr/bin/gtk-update-icon-cache -qtf %{_datadir}/icons/hicolor &>/dev/null || :

%postun
# Em remoção total ($1==0): apaga o venv gerado no %post e o diretório, se vazio.
if [ "$1" -eq 0 ]; then
  rm -rf %{appdir}/venv
  rmdir --ignore-fail-on-non-empty %{appdir} 2>/dev/null || :
fi
/usr/bin/update-desktop-database &>/dev/null || :

%changelog
* Sun Jun 15 2026 Lensfy <noreply@lensfy.local> - 0.1.0-1
- Pacote inicial: backend + UI, venv offline via wheelhouse, launcher e atalho de menu.
