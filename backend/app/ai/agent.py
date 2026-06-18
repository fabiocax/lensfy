"""The Lensfy AI agent — a Kubernetes SRE assistant powered by the Claude API.

Runs an agentic tool-use loop: Claude inspects the cluster with read-only tools
(logs, events, manifests, metrics) to diagnose problems, and can propose
mutating actions (scale/restart/delete/…) which the UI must approve before they
run. Talks to the Anthropic Messages API directly over httpx (no extra SDK).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import anyio
import httpx

from app.ai.tools import MUTATING, TOOLS, execute_tool, tool_summary
from app.core.config import get_settings
from app.core.logging import get_logger
from app.kubernetes.client import KubernetesClient

logger = get_logger(__name__)

_ANTHROPIC_VERSION = "2023-06-01"
_MAX_STEPS = 12  # safety cap on tool-use rounds per question
_MAX_RETRIES = 4  # retries on transient Claude API errors (429/529/5xx/network)

SYSTEM_PROMPT = """\
Você é o assistente de SRE do Lensfy, especialista em Kubernetes. Ajuda o usuário \
a diagnosticar problemas em aplicações e a automatizar operações no cluster.

Princípios:
- Investigue com evidências antes de concluir. Use as ferramentas de leitura \
(cluster_overview, list_resources, get_resource, get_pod_logs, top) para coletar \
fatos — não invente. Comece por cluster_overview quando o pedido for amplo.
- Ao diagnosticar, aponte a causa raiz provável, cite o que viu (fase do pod, \
restarts, mensagens de evento, trechos de log, probes) e dê próximos passos.
- Para CORRIGIR/automatizar, use as ferramentas que alteram o cluster \
(scale_workload, restart_workload, delete_pod, trigger_cronjob, …). Cada uma \
exige aprovação do usuário — então explique o que vai fazer e por quê antes.
- Seja conciso e prático. Responda em português. Use markdown (listas, `código`).
- Nunca exponha valores de secrets em texto claro.
"""


class AIError(Exception):
    pass


def is_configured() -> bool:
    return bool(get_settings().anthropic_api_key)


def status() -> dict:
    s = get_settings()
    if not s.anthropic_api_key:
        return {
            "available": False,
            "model": s.anthropic_model,
            "message": "Assistente de IA desativado — defina LENSFY_ANTHROPIC_API_KEY.",
        }
    return {"available": True, "model": s.anthropic_model, "mutations": s.ai_allow_mutations}


# emit(event) streams a step to the client; approve(req) -> bool gates mutations.
Emit = Callable[[dict], Awaitable[None]]
Approve = Callable[[dict], Awaitable[bool]]


class AIAgent:
    def __init__(self) -> None:
        self.settings = get_settings()
        if not self.settings.anthropic_api_key:
            raise AIError("LENSFY_ANTHROPIC_API_KEY não configurada")

    async def _call(self, http: httpx.AsyncClient, messages: list[dict]) -> dict:
        payload = {
            "model": self.settings.anthropic_model,
            "max_tokens": 4096,
            "system": SYSTEM_PROMPT,
            "tools": TOOLS,
            "messages": messages,
        }
        last_err: AIError | None = None
        for attempt in range(_MAX_RETRIES):
            delay: float | None = None
            try:
                resp = await http.post("/v1/messages", json=payload)
            except httpx.HTTPError as exc:  # connect/read timeout, network blip
                last_err = AIError(f"Falha de rede ao chamar a Claude API: {exc}")
            else:
                if resp.status_code == 200:
                    return resp.json()
                last_err = AIError(f"Claude API {resp.status_code}: {self._err_detail(resp)}")
                # 429 (rate limit), 529 (overloaded) and 5xx are transient; the
                # rest (400/401/403…) are caller errors — fail fast.
                if not (resp.status_code in (429, 529) or resp.status_code >= 500):
                    raise last_err
                delay = self._retry_after(resp)
            if attempt < _MAX_RETRIES - 1:
                await anyio.sleep(delay if delay is not None else min(2 ** attempt, 8))
        raise last_err or AIError("Claude API: falha após múltiplas tentativas")

    @staticmethod
    def _err_detail(resp: httpx.Response) -> str:
        try:
            return resp.json().get("error", {}).get("message", resp.text)
        except Exception:  # noqa: BLE001
            return resp.text

    @staticmethod
    def _retry_after(resp: httpx.Response) -> float | None:
        raw = resp.headers.get("retry-after")
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    async def run(
        self,
        client: KubernetesClient,
        messages: list[dict],
        emit: Emit,
        approve: Approve,
    ) -> None:
        """Drive the tool-use loop until the model gives a final answer.

        ``messages`` is the running conversation (mutated in place so multi-turn
        chats keep context). Each step streams events via ``emit``; mutating
        tools are gated by ``approve``.
        """
        headers = {
            "x-api-key": self.settings.anthropic_api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        async with httpx.AsyncClient(
            base_url=self.settings.anthropic_base_url, headers=headers, timeout=120
        ) as http:
            for _ in range(_MAX_STEPS):
                data = await self._call(http, messages)
                blocks = data.get("content", [])

                for b in blocks:
                    if b.get("type") == "text" and b.get("text", "").strip():
                        await emit({"type": "text", "text": b["text"]})

                tool_uses = [b for b in blocks if b.get("type") == "tool_use"]
                if not tool_uses:
                    messages.append({"role": "assistant", "content": blocks})
                    await emit({"type": "done"})
                    return

                results = []
                for tu in tool_uses:
                    name, inp, tid = tu["name"], tu.get("input", {}) or {}, tu["id"]
                    summary = tool_summary(name, inp)
                    mutating = name in MUTATING
                    await emit({
                        "type": "tool", "id": tid, "name": name,
                        "summary": summary, "input": inp, "mutating": mutating,
                    })

                    if mutating and not self.settings.ai_allow_mutations:
                        results.append(_tool_result(tid, "Mutações desativadas na configuração.", True))
                        await emit({"type": "tool_result", "id": tid, "ok": False, "summary": "mutações desativadas"})
                        continue
                    if mutating:
                        approved = await approve({"id": tid, "name": name, "summary": summary, "input": inp})
                        if not approved:
                            results.append(_tool_result(tid, "Ação negada pelo usuário.", False))
                            await emit({"type": "tool_result", "id": tid, "ok": False, "summary": "negado pelo usuário"})
                            continue

                    try:
                        out = await anyio.to_thread.run_sync(execute_tool, client, name, inp)
                        results.append(_tool_result(tid, out, False))
                        await emit({"type": "tool_result", "id": tid, "ok": True, "summary": _short(out)})
                    except Exception as exc:  # noqa: BLE001 - surface tool error to the model
                        logger.warning("ai tool %s failed: %s", name, exc)
                        results.append(_tool_result(tid, f"erro ao executar: {exc}", True))
                        await emit({"type": "tool_result", "id": tid, "ok": False, "summary": str(exc)})

                # Commit the assistant turn and its tool results together, only
                # after every tool_use has a matching result. If emit/approve
                # raised mid-loop (e.g. client disconnect), nothing was appended,
                # so the conversation never ends with an orphaned tool_use turn
                # that would 400 the next request.
                messages.append({"role": "assistant", "content": blocks})
                messages.append({"role": "user", "content": results})

            await emit({"type": "text", "text": "_(limite de passos atingido)_"})
            await emit({"type": "done"})


def _tool_result(tool_use_id: str, content: str, is_error: bool) -> dict:
    block = {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
    if is_error:
        block["is_error"] = True
    return block


def _short(text: str, n: int = 160) -> str:
    text = " ".join(text.split())
    return text[:n] + ("…" if len(text) > n else "")
