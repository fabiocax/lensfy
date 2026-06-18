# Kubernetes Desktop Manager (OpenLens Clone)

## Visão Geral

Desenvolver uma aplicação desktop para gerenciamento de clusters Kubernetes inspirada no Lens/OpenLens.

O sistema deverá ser executado localmente na máquina do usuário, sem dependência de serviços externos obrigatórios.

A solução será composta por:

* Backend: FastAPI (Python 3.12+)
* Frontend: React + TypeScript
* Desktop: Tauri
* Banco Local: SQLite
* ORM: SQLAlchemy
* Comunicação em tempo real: WebSocket
* Kubernetes SDK: kubernetes-python
* Containers locais opcionais: Docker
* Empacotamento:

  * Linux (.AppImage e .deb)
  * Windows (.exe e .msi)
  * MacOS (.dmg)

---

# Objetivos

Criar uma ferramenta moderna para administração Kubernetes contendo:

* Gerenciamento de múltiplos clusters
* Visualização de workloads
* Logs em tempo real
* Terminal integrado
* Dashboard de métricas
* Gerenciamento de namespaces
* Deploy de manifests YAML
* Gerenciamento de Helm Charts
* Port Forward
* Exec em Pods
* Visualização de eventos
* Monitoramento de recursos
* RBAC Viewer
* Secrets Viewer
* ConfigMaps Viewer

---

# Arquitetura

## Backend

FastAPI

Estrutura:

```text
backend/
├── app/
│   ├── api/
│   ├── services/
│   ├── models/
│   ├── repositories/
│   ├── websocket/
│   ├── kubernetes/
│   ├── auth/
│   ├── database/
│   └── core/
│
├── tests/
├── alembic/
└── requirements.txt
```

---

## Frontend

React + TypeScript + Vite

Estrutura:

```text
frontend/
├── src/
│   ├── pages/
│   ├── components/
│   ├── hooks/
│   ├── services/
│   ├── layouts/
│   ├── contexts/
│   ├── store/
│   ├── routes/
│   └── types/
│
└── package.json
```

---

## Desktop

Tauri

Estrutura:

```text
desktop/
├── src-tauri/
└── build/
```

---

# Funcionalidades

## 1. Gerenciamento de Clusters

Permitir:

* Importar kubeconfig
* Detectar contextos automaticamente
* Alternar entre clusters
* Remover cluster
* Atualizar informações

Exibir:

* Nome
* Contexto
* Versão Kubernetes
* Provider
* Status

---

## 2. Dashboard

Mostrar:

* CPU
* Memória
* Pods
* Nodes
* Deployments
* Services
* Ingresses

Atualização em tempo real.

---

## 3. Explorer Kubernetes

Árvore lateral semelhante ao Lens.

Itens:

```text
Cluster
 ├── Nodes
 ├── Namespaces
 ├── Pods
 ├── Deployments
 ├── StatefulSets
 ├── DaemonSets
 ├── Services
 ├── Ingress
 ├── Jobs
 ├── CronJobs
 ├── Secrets
 ├── ConfigMaps
 ├── PVC
 ├── StorageClasses
 └── Events
```

---

## 4. Logs em Tempo Real

Visualização semelhante ao Lens.

Recursos:

* Auto Scroll
* Busca
* Filtro
* Download
* Copiar conteúdo
* Multiline

Backend usando:

```python
watch.Watch().stream()
```

WebSocket para frontend.

---

## 5. Terminal Integrado

Exec dentro de Pods.

Comando:

```bash
kubectl exec -it
```

Recursos:

* Múltiplas abas
* Histórico
* Resize automático

Frontend:

xterm.js

---

## 6. YAML Viewer

Exibir recurso Kubernetes completo.

Opções:

* Visualizar
* Editar
* Aplicar alterações

Utilizar editor Monaco.

---

## 7. Deploy de Manifestos

Tela drag-and-drop.

Aceitar:

* YAML único
* Múltiplos YAML
* Diretórios

Aplicar utilizando:

```python
kubernetes.utils.create_from_yaml()
```

---

## 8. Helm Manager

Gerenciar releases Helm.

Funcionalidades:

* Listar releases
* Instalar chart
* Atualizar chart
* Rollback
* Remover release

Executar comandos Helm localmente.

---

## 9. Port Forward

Permitir criar túneis locais.

Exemplo:

```text
Pod: nginx
Porta Remota: 80
Porta Local: 8080
```

Gerenciar múltiplos forwards simultaneamente.

---

## 10. Métricas

Integrar:

* Metrics Server
* Prometheus (opcional)

Exibir:

* CPU
* Memória
* Requests
* Limits

Gráficos em tempo real.

Biblioteca:

```text
Recharts
```

---

## 11. Viewer de Secrets

Exibir:

* Nome
* Tipo
* Namespace

Opção:

* Mostrar valor decodificado
* Copiar

Com confirmação do usuário.

---

## 12. Viewer de ConfigMaps

Exibir conteúdo formatado.

Editor integrado.

---

## 13. RBAC Viewer

Mostrar:

* Roles
* ClusterRoles
* RoleBindings
* ClusterRoleBindings

Permitir navegar entre permissões.

---

## 14. Event Viewer

Atualização em tempo real.

Mostrar:

* Warning
* Normal
* Error

Filtros por namespace.

---

## 15. Nodes

Exibir:

* CPU
* Memória
* Versão
* Sistema Operacional
* Labels
* Taints

Visualização detalhada.

---

## 16. Workloads

Suporte completo para:

* Deployments
* StatefulSets
* DaemonSets
* Jobs
* CronJobs

Operações:

* Escalar
* Reiniciar
* Remover
* Editar

---

# Banco de Dados

SQLite local.

Tabelas:

## clusters

```sql
id
name
context
provider
version
created_at
```

## favorites

```sql
id
resource_type
resource_name
namespace
cluster_id
```

## settings

```sql
id
theme
language
refresh_interval
```

---

# Segurança

* Armazenar kubeconfigs criptografados
* AES-256
* Tokens protegidos
* Nenhuma informação enviada para servidores externos
* Operação 100% local

---

# UI/UX

Tema semelhante ao Lens.

Tecnologias:

* Material UI
* React Query
* Zustand
* Monaco Editor
* xterm.js

Modo:

* Claro
* Escuro

Responsivo.

---

# API REST

## Clusters

```http
GET /api/clusters
POST /api/clusters
DELETE /api/clusters/{id}
```

## Pods

```http
GET /api/pods
GET /api/pods/{name}
DELETE /api/pods/{name}
```

## Deployments

```http
GET /api/deployments
PATCH /api/deployments/{name}/scale
```

## Logs

```http
GET /api/logs
```

## Terminal

```http
WS /ws/terminal
```

## Metrics

```http
GET /api/metrics
```

---

# WebSockets

Criar canais:

```text
/ws/logs
/ws/terminal
/ws/events
/ws/metrics
```

---

# Testes

Backend:

* Pytest
* Coverage > 85%

Frontend:

* Vitest
* React Testing Library

E2E:

* Playwright

---

# CI/CD

GitHub Actions

Pipelines:

* Lint
* Testes
* Build
* Release

---

# Entregáveis

1. Backend FastAPI completo
2. Frontend React completo
3. Integração Kubernetes
4. Desktop via Tauri
5. Docker Compose para desenvolvimento
6. Testes automatizados
7. Documentação completa
8. Instaladores Linux, Windows e MacOS

---

# Diferenciais

Implementar recursos que o Lens Community não possui:

* Favoritos
* Histórico de comandos executados
* Comparação entre clusters
* Exportação de inventário
* Auditoria local
* Dashboard customizável
* Multi-cluster simultâneo
* Busca global em todos os clusters
* AI Assistant opcional para Kubernetes troubleshooting

Objetivo final: criar uma alternativa open-source moderna ao Lens/OpenLens com foco em performance, simplicidade, extensibilidade e operação 100% local.

