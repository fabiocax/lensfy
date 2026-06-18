"""Tools the AI agent can call against a cluster.

Each entry is an Anthropic tool schema. ``execute_tool`` dispatches a tool call
to the existing ``KubernetesClient``. Read-only tools run automatically; tools
in ``MUTATING`` change the cluster and must be approved in the UI first.
"""

from __future__ import annotations

import json

from app.kubernetes.client import KubernetesClient

# Tools that change cluster state — gated behind explicit user approval.
MUTATING: set[str] = {
    "scale_workload",
    "restart_workload",
    "delete_pod",
    "delete_resource",
    "trigger_cronjob",
    "set_cronjob_suspend",
    "cordon_node",
    "drain_node",
    "rollout_undo",
}

# Cluster-scoped kinds have no namespace; everything else requires one for a
# targeted delete (so the model can't accidentally hit the wrong namespace).
_CLUSTER_SCOPED: set[str] = {
    "node", "nodes",
    "namespace", "namespaces", "ns",
    "persistentvolume", "persistentvolumes", "pv",
    "storageclass", "storageclasses", "sc",
    "clusterrole", "clusterroles",
    "clusterrolebinding", "clusterrolebindings",
}


def _is_cluster_scoped(kind: str) -> bool:
    return kind.strip().lower() in _CLUSTER_SCOPED


_KINDS_HINT = (
    "pods, deployments, statefulsets, daemonsets, replicasets, jobs, cronjobs, "
    "services, ingress, configmaps, secrets, pvc, nodes, namespaces, events, "
    "storageclasses, roles, clusterroles, rolebindings, clusterrolebindings"
)

TOOLS: list[dict] = [
    {
        "name": "cluster_overview",
        "description": "Visão geral de saúde do cluster: contagens, nós prontos, fases dos pods, restarts, deployments indisponíveis, eventos de alerta recentes, uso de CPU/memória e versão. Use isto primeiro para diagnosticar.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_resources",
        "description": f"Lista recursos de um tipo como tabela (colunas + linhas). Tipos válidos: {_KINDS_HINT}.",
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "description": "tipo do recurso (ex.: pods)"},
                "namespace": {"type": "string", "description": "namespace; omita para todos"},
            },
            "required": ["kind"],
        },
    },
    {
        "name": "get_resource",
        "description": "Retorna o manifesto/objeto completo de um recurso (como kubectl get -o yaml/describe). Útil para inspecionar spec, status, conditions, imagens, probes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string"},
                "name": {"type": "string"},
                "namespace": {"type": "string"},
            },
            "required": ["kind", "name"],
        },
    },
    {
        "name": "get_pod_logs",
        "description": "Retorna as últimas linhas de log de um pod (snapshot, não streaming). Use para investigar crashes, erros de aplicação, stack traces.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "namespace": {"type": "string"},
                "container": {"type": "string", "description": "opcional; default = primeiro container"},
                "tail": {"type": "integer", "description": "nº de linhas (default 200, máx 500)"},
            },
            "required": ["name", "namespace"],
        },
    },
    {
        "name": "top",
        "description": "Uso de CPU/memória por nó ou por pod (metrics-server). kind = 'nodes' ou 'pods'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["nodes", "pods"]},
                "namespace": {"type": "string"},
            },
            "required": ["kind"],
        },
    },
    {
        "name": "cluster_issues",
        "description": "Lista TODOS os problemas detectados no cluster (pods em CrashLoop/OOMKilled/pending/failed, workloads indisponíveis, jobs falhos, PVCs não vinculados, nodes not-ready/cordonados). Use para um diagnóstico rápido do que está quebrado.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "rollout_history",
        "description": "Histórico de revisões de um deployment (imagens por revisão), para decidir um rollback.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "namespace": {"type": "string"}},
            "required": ["name", "namespace"],
        },
    },
    # --- mutating ---
    {
        "name": "cordon_node",
        "description": "Marca um node como não-agendável (cordon) ou volta a permitir (uncordon, unschedulable=false). AÇÃO QUE ALTERA O CLUSTER.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "unschedulable": {"type": "boolean", "description": "true = cordon (default), false = uncordon"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "drain_node",
        "description": "Cordona um node e despeja seus pods (mantém DaemonSets/pods estáticos, respeita PDBs). Use antes de manutenção do node. AÇÃO QUE ALTERA O CLUSTER.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
    {
        "name": "rollout_undo",
        "description": "Reverte um deployment para uma revisão anterior (kubectl rollout undo). Consulte rollout_history antes. AÇÃO QUE ALTERA O CLUSTER.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "namespace": {"type": "string"},
                "revision": {"type": "integer"},
            },
            "required": ["name", "namespace", "revision"],
        },
    },
    {
        "name": "scale_workload",
        "description": "Escala um deployment/statefulset para N réplicas. AÇÃO QUE ALTERA O CLUSTER.",
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["deployments", "statefulsets"]},
                "name": {"type": "string"},
                "namespace": {"type": "string"},
                "replicas": {"type": "integer", "minimum": 0},
            },
            "required": ["kind", "name", "namespace", "replicas"],
        },
    },
    {
        "name": "restart_workload",
        "description": "Faz rollout restart (recria os pods) de um deployment/statefulset/daemonset. AÇÃO QUE ALTERA O CLUSTER.",
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["deployments", "statefulsets", "daemonsets"]},
                "name": {"type": "string"},
                "namespace": {"type": "string"},
            },
            "required": ["kind", "name", "namespace"],
        },
    },
    {
        "name": "delete_pod",
        "description": "Deleta um pod (será recriado se gerenciado por um controller). AÇÃO QUE ALTERA O CLUSTER.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "namespace": {"type": "string"},
            },
            "required": ["name", "namespace"],
        },
    },
    {
        "name": "delete_resource",
        "description": f"Remove um recurso de qualquer tipo do cluster (como kubectl delete). Tipos válidos: {_KINDS_HINT}. AÇÃO QUE ALTERA O CLUSTER.",
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string"},
                "name": {"type": "string"},
                "namespace": {"type": "string", "description": "obrigatório para recursos com namespace"},
            },
            "required": ["kind", "name"],
        },
    },
    {
        "name": "trigger_cronjob",
        "description": "Cria um Job manual a partir de um CronJob (executa agora). AÇÃO QUE ALTERA O CLUSTER.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "namespace": {"type": "string"},
            },
            "required": ["name", "namespace"],
        },
    },
    {
        "name": "set_cronjob_suspend",
        "description": "Suspende (true) ou reativa (false) um CronJob. AÇÃO QUE ALTERA O CLUSTER.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "namespace": {"type": "string"},
                "suspend": {"type": "boolean"},
            },
            "required": ["name", "namespace", "suspend"],
        },
    },
]


def tool_summary(name: str, inp: dict) -> str:
    """Short human-readable description of a tool call (for the UI / approval)."""
    ns = inp.get("namespace")
    loc = f" em {ns}" if ns else ""
    n = inp.get("name", "")
    if name == "scale_workload":
        return f"Escalar {inp.get('kind')}/{n}{loc} para {inp.get('replicas')} réplica(s)"
    if name == "restart_workload":
        return f"Reiniciar {inp.get('kind')}/{n}{loc}"
    if name == "delete_pod":
        return f"Deletar pod {n}{loc}"
    if name == "delete_resource":
        return f"Remover {inp.get('kind')}/{n}{loc}"
    if name == "trigger_cronjob":
        return f"Executar agora o CronJob {n}{loc}"
    if name == "set_cronjob_suspend":
        verb = "Suspender" if inp.get("suspend") else "Reativar"
        return f"{verb} o CronJob {n}{loc}"
    if name == "cordon_node":
        return ("Cordon" if inp.get("unschedulable", True) else "Uncordon") + f" o node {n}"
    if name == "drain_node":
        return f"Drenar (drain) o node {n}"
    if name == "rollout_undo":
        return f"Rollback do deployment {n}{loc} para a revisão {inp.get('revision')}"
    if name == "cluster_issues":
        return "Diagnosticar problemas do cluster"
    if name == "rollout_history":
        return f"Histórico de rollout de {n}{loc}"
    if name == "get_pod_logs":
        return f"Ler logs de {n}{loc}"
    if name == "list_resources":
        return f"Listar {inp.get('kind')}{loc}"
    if name == "get_resource":
        return f"Inspecionar {inp.get('kind')}/{n}{loc}"
    if name == "top":
        return f"Métricas de {inp.get('kind')}"
    if name == "cluster_overview":
        return "Visão geral do cluster"
    return name


_MAX_RESULT_CHARS = 12000


def _clip(obj) -> str:
    """Serialize a tool result to JSON, bounded so we don't blow the context."""
    text = json.dumps(obj, ensure_ascii=False, default=str)
    if len(text) > _MAX_RESULT_CHARS:
        text = text[:_MAX_RESULT_CHARS] + "\n…(resultado truncado)"
    return text


def execute_tool(client: KubernetesClient, name: str, inp: dict) -> str:
    """Run a tool against the cluster and return a JSON string for the model."""
    if name == "cluster_overview":
        return _clip(client.cluster_overview())
    if name == "list_resources":
        kind = inp["kind"]
        # pods/deployments have richer dedicated summaries
        if kind == "pods":
            rows = [p.model_dump() for p in client.list_pods(inp.get("namespace"))]
            return _clip({"kind": "pods", "count": len(rows), "rows": rows[:200]})
        if kind == "deployments":
            rows = [d.model_dump() for d in client.list_deployments(inp.get("namespace"))]
            return _clip({"kind": "deployments", "count": len(rows), "rows": rows[:200]})
        data = client.list_resource(kind, inp.get("namespace"))
        data["count"] = len(data.get("rows", []))
        data["rows"] = data.get("rows", [])[:200]
        return _clip(data)
    if name == "get_resource":
        return _clip(client.get_object(inp["kind"], inp["name"], inp.get("namespace")))
    if name == "get_pod_logs":
        tail = min(int(inp.get("tail", 200) or 200), 500)
        logs = client.pod_logs(inp["name"], inp["namespace"], inp.get("container"), tail)
        return _clip({"pod": inp["name"], "logs": logs or "(sem logs)"})
    if name == "top":
        return _clip(client.cluster_top(inp["kind"], inp.get("namespace")))
    if name == "cluster_issues":
        return _clip(client.cluster_issues())
    if name == "rollout_history":
        return _clip({"revisions": client.rollout_history("deployments", inp["name"], inp["namespace"])})
    # --- mutating ---
    if name == "cordon_node":
        client.cordon_node(inp["name"], bool(inp.get("unschedulable", True)))
        return _clip({"ok": True, "node": inp["name"]})
    if name == "drain_node":
        return _clip({"ok": True, **client.drain_node(inp["name"])})
    if name == "rollout_undo":
        client.rollout_undo("deployments", inp["name"], inp["namespace"], int(inp["revision"]))
        return _clip({"ok": True, "rolledback_to": inp["revision"]})
    if name == "scale_workload":
        client.scale_workload(inp["kind"], inp["name"], inp["namespace"], int(inp["replicas"]))
        return _clip({"ok": True, "scaled_to": inp["replicas"]})
    if name == "restart_workload":
        client.restart_workload(inp["kind"], inp["name"], inp["namespace"])
        return _clip({"ok": True, "restarted": inp["name"]})
    if name == "delete_pod":
        client.delete_pod(inp["name"], inp["namespace"])
        return _clip({"ok": True, "deleted": inp["name"]})
    if name == "delete_resource":
        kind, ns = inp["kind"], inp.get("namespace")
        if not _is_cluster_scoped(kind) and not ns:
            raise ValueError(
                f"namespace é obrigatório para excluir um recurso '{kind}'"
            )
        client.delete_resource(kind, inp["name"], ns)
        return _clip({"ok": True, "deleted": inp["name"]})
    if name == "trigger_cronjob":
        job = client.trigger_cronjob(inp["name"], inp["namespace"])
        return _clip({"ok": True, "job": job})
    if name == "set_cronjob_suspend":
        client.set_cronjob_suspend(inp["name"], inp["namespace"], bool(inp["suspend"]))
        return _clip({"ok": True, "suspended": inp["suspend"]})
    raise ValueError(f"ferramenta desconhecida: {name}")
