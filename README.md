# Lensfy

**Português** · [English](README.en.md)

**Gerenciador local de clusters Kubernetes** — uma alternativa open-source ao Lens/OpenLens que roda inteiramente na sua máquina, sem serviços externos obrigatórios.

Multi-cluster, logs e métricas em tempo real, terminal/exec integrado, shell `kubectl`, port-forward, deploy de manifestos/Helm, editor YAML (Monaco) com histórico de versões, e um **assistente de IA** (Claude API) que diagnostica problemas e automatiza operações no cluster — com aprovação.

> A interface é um **PWA instalável**, servida pelo próprio backend (FastAPI + Jinja2 + JS/CSS vanilla — sem build step, sem npm). O acesso é restrito à máquina local e protegido por um **token de dispositivo** (sem login/senha).

---

## Sumário

- [Recursos](#recursos)
- [Requisitos](#requisitos)
- [Instalação](#instalação)
  - [Instalador desktop (Linux)](#1-instalador-desktop-linux--recomendado)
  - [Pacote .rpm (Fedora/RHEL)](#2-pacote-rpm-fedorarhel)
  - [A partir do código (desenvolvimento)](#3-a-partir-do-código-desenvolvimento)
- [Como rodar](#como-rodar)
- [Segurança](#segurança)
- [Configuração](#configuração-variáveis-de-ambiente)
- [Primeiros passos na UI](#primeiros-passos-na-ui)
- [Testes](#testes)
- [Estrutura](#estrutura)
- [Roadmap](#roadmap)

---

## Recursos

### Multi-cluster
- **Importar kubeconfig** por caminho, upload de arquivo ou colando o conteúdo — detecção de contextos com checklist (importe vários de uma vez).
- **Importar do Google Cloud (GKE):** aba **gcloud** lista projetos e clusters e roda `get-credentials` por você (requer `gcloud` + `gke-gcloud-auth-plugin`).
- **Seletor de clusters** com busca, status/versão por item, troca em um clique, **reordenação por arrastar** e remoção. Importar nunca trava a UI (clusters sobem em segundo plano).
- **Sessão por cluster:** ao voltar a um cluster, o Lensfy restaura onde você parou — a view, o filtro de namespace e as abas abertas no dock (logs/console/YAML/IA).

### Explorer de recursos
- Árvore com **Pods, Deployments, StatefulSets, DaemonSets, Jobs, CronJobs, Services, Ingress, ConfigMaps, Secrets, PVC, StorageClasses, Namespaces, Nodes, Events, RBAC** (roles/bindings), **LimitRanges** e **ResourceQuotas**.
- **Tabelas ao vivo** (`/ws/watch`): pods criados/removidos, status e restarts atualizam sozinhos — reconciliação incremental **sem flicker** (seleção e scroll preservados).
- **Filtro global de namespace** multi-seleção (estilo Lens) e **busca global / command palette** (foco com `/`).
- **Painel de detalhes (drawer)** por recurso: resumo, metadados, status, containers (estado/restarts/imagens), condições, **métricas ao vivo** (CPU/mem) e eventos.

### Observabilidade
- **Dashboard:** saúde do cluster (nós, versões), fases dos pods, restarts, deployments disponíveis vs desejados, uso de CPU/memória e eventos de alerta.
- **Métricas:** nós/pods via `metrics.k8s.io`, cards de resumo, colunas ordenáveis e barras coloridas por limiar.
- **Problemas:** varredura do cluster que lista issues por categoria e severidade (CrashLoop, ImagePull, OOMKilled, pendentes, PVC não vinculado, nós com pressão/cordon, etc.).
- **Recursos & Cotas:** soma de requests/limits por namespace, `ResourceQuota` usado/limite e containers **sem requests/limits** (risco de OOM/SLA).
- **Mapa de tráfego:** topologia **Ingress → Service → Workload → Pods** em SVG, com zoom/pan.

### Tempo real (terminal, logs, console)
- **Logs ao vivo:** filtro, auto-scroll, copiar, baixar e seletor de container.
- **Terminal/console (xterm.js):** `exec` em pod (PTY), **shell de nó** (estilo Lens, via pod privilegiado + `nsenter`) e **shell `kubectl`** com o contexto do cluster.
- **Dock inferior estilo Lens:** logs, console, YAML e IA em **abas**, várias ao mesmo tempo, painel redimensionável que empurra a view (não sobrepõe).

### Editor YAML & deploy
- **Editor YAML (Monaco)** para ver/editar/aplicar qualquer recurso, com **autocomplete de Kubernetes** e **diff**.
- **Histórico de versões (até 5)** por recurso, gravado a cada *Aplicar*: carregar uma versão, **diff contra o editor** ou **diff entre duas versões**.
- **Apply robusto:** realinha o `resourceVersion` ao estado atual e repete em conflito (sem falhas intermitentes de save).
- **Deploy de manifestos:** editor Monaco com templates, **Construtor** (formulário → YAML), **validação dry-run**, e arrastar-e-soltar de arquivos/pastas YAML (multi-documento).

### Operações
- **Workloads:** escalar, *restart* (rollout), excluir.
- **Rollout:** histórico de revisões, *undo* (rollback) e *pause/resume*.
- **Nós:** *cordon/uncordon* e *drain* (respeitando PodDisruptionBudgets via Eviction API).
- **CronJobs:** *trigger* (executar agora) e *suspend/resume*.
- **Recursos:** editar requests/limits por container; editar **Secrets/ConfigMaps** in-place.
- **Port-forward:** túneis para pods, gerenciados na UI.
- **Helm:** releases, install/upgrade/rollback e uninstall.

### Assistente de IA (opcional)
- Agente SRE sobre a **Claude API**: ferramentas **read-only** (visão geral, listar/ver recursos, logs, top) rodam automaticamente; **ações que alteram o cluster** (escalar/restart/excluir/cordon/drain/rollback/cronjob) exigem **Aprovar/Negar** na UI.
- Pode ser desligado por completo (`LENSFY_AI_ALLOW_MUTATIONS=false`) e os diagnósticos podem ser **salvos como relatórios**.

### Plataforma
- **Segurança local sem login:** acesso só de *loopback*, *allowlist* de Host (anti DNS-rebinding) e **token de dispositivo**; tela de **onboarding** gera o token na primeira execução.
- **PWA instalável** com app shell offline.

---

## Requisitos

- **Python 3.12+** (testado em 3.14).
- Um **kubeconfig** com acesso aos seus clusters (`~/.kube/config` ou importado pela UI).
- Opcionais (cada recurso degrada com aviso quando ausente):
  - `kubectl` — para o shell kubectl do header.
  - `helm` — para a aba Helm.
  - `gcloud` (+ `gke-gcloud-auth-plugin`) — para importar clusters GKE.
  - **metrics-server** no cluster — para gráficos de CPU/memória.
  - Uma **chave da Claude API** (`LENSFY_ANTHROPIC_API_KEY`) — para o assistente de IA.

---

## Instalação

### 1. Instalador desktop (Linux) — recomendado

Instala num venv isolado, cria o comando **`lensfy`** e um **atalho no menu de aplicativos** (sem root):

```bash
git clone https://gitlab.com/fabiocax/lensfy.git
cd lensfy
./install.sh                 # instala/atualiza (re-rodar atualiza no lugar)
./install.sh --service       # + serviço systemd --user (inicia no login)
```

Depois:

```bash
lensfy            # inicia (se preciso) e abre no navegador
lensfy status     # estado + health
lensfy stop       # para
```

Ou abra **“Lensfy”** no menu de aplicativos. Layout instalado:

| Caminho | Conteúdo |
|---|---|
| `~/.local/share/lensfy/app` | código + UI |
| `~/.local/share/lensfy/venv` | dependências |
| `~/.local/bin/lensfy` | launcher |
| `~/.local/share/applications/lensfy.desktop` | atalho de menu |
| `~/.local/state/lensfy/` | pid + log |
| `~/.lensfy/` | dados (SQLite, token) — **preservado** |

Desinstalar: `./uninstall.sh` (use `--purge` para apagar também `~/.lensfy`).

### 2. Pacote .rpm (Fedora/RHEL)

Gera um `.rpm` distribuível, com as dependências embutidas (instalação **offline**):

```bash
sudo dnf install -y rpm-build rpmdevtools python3-pip
./packaging/rpm/build-rpm.sh          # → packaging/rpm/dist/lensfy-<versão>.rpm

sudo dnf install packaging/rpm/dist/lensfy-*.rpm
lensfy                                  # ou pelo menu de aplicativos
sudo dnf remove lensfy
```

> Os *wheels* embutidos são específicos da plataforma e da versão do Python do host de build (ex.: x86_64 / Python 3.14). Gere o pacote num ambiente compatível com o destino. Ajuste a licença em `packaging/rpm/lensfy.spec` (atualmente um placeholder).

### 3. A partir do código (desenvolvimento)

```bash
git clone https://gitlab.com/fabiocax/lensfy.git
cd lensfy/backend

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

> **Python 3.14:** se algum pacote tentar compilar do código-fonte, force *wheels* prontas:
> `pip install --only-binary=:all: -r requirements.txt`

---

## Como rodar

### Scripts de controle (a partir do código)

Na **raiz** do projeto:

```bash
./start.sh            # inicia em background → http://127.0.0.1:8000
./lensfy.sh status    # estado + health
./lensfy.sh logs      # acompanha o log
./lensfy.sh restart   # reinicia
./stop.sh             # para (encerra o grupo de processos)
```

### Forma manual (desenvolvimento)

```bash
cd backend
source .venv/bin/activate
uvicorn app.main:app --reload --port 8000
```

- App: <http://localhost:8000>
- Docs da API (Swagger): <http://localhost:8000/docs>
- Health: <http://localhost:8000/health>

Editar arquivos em `backend/templates/` ou `backend/static/` só exige **atualizar o navegador** (sem rebuild).

---

## Segurança

Lensfy é um app local de **um usuário** e foi pensado para não ser acessível de outras máquinas — **sem login nem senha**. Três camadas, aplicadas a toda requisição HTTP **e** WebSocket:

1. **Somente loopback** — conexões fora de `127.0.0.0/8`/`::1` são recusadas (mesmo que o servidor seja exposto por engano).
2. **Allowlist de `Host`** — bloqueia ataques de *DNS-rebinding* (um site remoto resolvendo seu domínio para `127.0.0.1`).
3. **Token de dispositivo** — gerado uma vez no equipamento (`~/.lensfy/device_token`, permissão `0600`) e exigido em `/api` e `/ws`. A SPA o obtém em runtime; uma página de outra origem não consegue lê-lo nem forjá-lo (também derrota CSRF). Na **primeira execução**, uma tela de **onboarding** gera o token.

| Variável | Default | Efeito |
|---|---|---|
| `LENSFY_SECURITY_ENABLED` | `true` | `false` desliga todas as camadas (ambiente confiável/testes). |
| `LENSFY_ALLOW_REMOTE` | `false` | `true` permite acesso fora do loopback (LAN) — **o token continua valendo**. Use com cuidado. |
| `LENSFY_ALLOWED_HOSTS` | `[]` | Valores extras aceitos no header `Host` (ex.: o hostname da máquina). |

Outras notas:
- O assistente de IA só executa ações que **alteram** o cluster após **aprovação explícita**; desligue com `LENSFY_AI_ALLOW_MUTATIONS=false`.
- Trate kubeconfigs importados como **conteúdo confiável** (podem conter credenciais e comandos `exec`).
- Se você **regenerar/rotacionar** o token, **recarregue as abas abertas** (a UI mostra um aviso "Sessão de dispositivo inválida → Recarregar" quando isso acontece).

---

## Configuração (variáveis de ambiente)

Todas com prefixo `LENSFY_`. Veja `backend/.env.example` (copie para `backend/.env`).

| Variável | Default | Descrição |
|---|---|---|
| `LENSFY_HOST` | `127.0.0.1` | Interface de bind. **`0.0.0.0` requer `LENSFY_ALLOW_REMOTE=true` — veja Segurança.** |
| `LENSFY_PORT` | `8000` | Porta. |
| `LENSFY_RELOAD` | `0` | `1` para auto-reload (dev). |
| `LENSFY_DEBUG` | `false` | Cria as tabelas do banco no startup (dispensa migrations no dev). |
| `LENSFY_DATABASE_URL` | sqlite em `~/.lensfy/lensfy.db` | Override do banco. |
| `LENSFY_SECURITY_ENABLED` | `true` | Liga/desliga o controle de acesso local. |
| `LENSFY_ALLOW_REMOTE` | `false` | Permite acesso não-loopback (token continua exigido). |
| `LENSFY_ALLOWED_HOSTS` | `[]` | Hosts extras aceitos no header `Host`. |
| `LENSFY_ANTHROPIC_API_KEY` | — | Habilita o assistente de IA (Claude API). |
| `LENSFY_ANTHROPIC_MODEL` | `claude-sonnet-4-6` | Modelo do assistente. |
| `LENSFY_AI_ALLOW_MUTATIONS` | `true` | `false` deixa a IA só diagnosticar. |

Exemplos:

```bash
LENSFY_PORT=9000 ./start.sh
LENSFY_ANTHROPIC_API_KEY=sk-ant-... ./start.sh   # liga o assistente IA
```

---

## Primeiros passos na UI

1. **(Primeira execução)** uma tela de **onboarding** gera o token deste equipamento — clique em **“Gerar token e começar”** e depois **“Entrar”**.
2. **Importar cluster** — no seletor de clusters (topo da sidebar) → **“+ Importar cluster”**:
   - **Caminho / Arquivo / Colar** um kubeconfig, ou
   - **gcloud** → escolher projeto → listar e importar clusters **GKE**.
3. Navegue pela árvore de recursos (Pods, Deployments, Services, Secrets, etc.).
4. O **Dashboard** mostra a saúde do cluster; **Problemas**, **Recursos** e **Mapa** ficam no topo.
5. **Assistente IA** (botão 🤖 no header) — peça um diagnóstico ou uma ação; ações que alteram o cluster pedem **Aprovar/Negar**.

### Instalar como app (PWA)

No Chrome/Edge, clique no ícone de instalar na barra de endereço (ou no botão **Instalar** do header). Abre em janela própria, com ícone no sistema.
> Service workers exigem **contexto seguro**: `localhost` (ok) ou **HTTPS**.

---

## Testes

```bash
cd backend
source .venv/bin/activate
pytest                    # suíte completa
pytest --cov              # com cobertura
pytest tests/test_ai.py   # um arquivo específico
```

---

## Estrutura

```
lensfy/
├── lensfy.sh, start.sh, stop.sh   # controle da aplicação (modo dev)
├── install.sh, uninstall.sh       # instalador desktop (Linux, por usuário)
├── packaging/
│   ├── lensfy                     # launcher instalado
│   └── rpm/                       # spec + build-rpm.sh (pacote .rpm)
├── PROJECT.md                     # especificação (pt-BR)
├── CLAUDE.md                      # guia de arquitetura p/ contribuir
└── backend/
    ├── app/
    │   ├── api/          # rotas REST (/api)
    │   ├── websocket/    # canais em tempo real (/ws): logs, terminal, watch, events, metrics, ai, kubectl
    │   ├── services/     # regras de negócio
    │   ├── repositories/ # acesso a dados
    │   ├── models/       # SQLAlchemy
    │   ├── kubernetes/   # integração com o SDK do Kubernetes, helm, gcloud
    │   ├── ai/           # assistente de IA (Claude API)
    │   ├── core/         # config + segurança (token de dispositivo)
    │   └── web/          # serve a UI (Jinja2) + PWA
    ├── templates/        # index.html (app shell)
    ├── static/           # css/, js/, icons/, manifest.webmanifest, sw.js
    ├── tests/
    └── requirements.txt
```

Arquitetura em camadas: `api/` → `services/` → `repositories/` → `models/`. Persistência local em SQLite (migrations via Alembic). Detalhes em [`CLAUDE.md`](CLAUDE.md) e a especificação em [`PROJECT.md`](PROJECT.md).

---

## Roadmap

- Empacotamento **`.deb` / AppImage** e wrapper **desktop nativo** (Tauri) para Linux/Windows/macOS.
- Rotação de token pela UI.
- Testes E2E (Playwright) e CI.

---

## Licença

Defina a licença do projeto (não há arquivo `LICENSE` no repositório ainda). Ajuste também o campo `License` em `packaging/rpm/lensfy.spec`.
