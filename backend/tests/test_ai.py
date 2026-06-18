"""AI agent loop tests — the Claude HTTP call is mocked, so no key/network."""

import asyncio
from types import SimpleNamespace

from app.ai import agent as agent_mod


def _fake_settings(allow_mutations=True):
    return SimpleNamespace(
        anthropic_api_key="test-key",
        anthropic_model="claude-sonnet-4-6",
        anthropic_base_url="https://example.invalid",
        ai_allow_mutations=allow_mutations,
    )


class _FakeClient:
    def __init__(self):
        self.scaled = None

    def cluster_overview(self):
        return {"counts": {"pods": 3}, "pods": {"phases": {"Running": 3}}}

    def scale_workload(self, kind, name, namespace, replicas):
        self.scaled = (kind, name, namespace, replicas)


def _run(agent, client, responses, approve_value=True):
    """Drive agent.run with canned API responses; return (events, approvals)."""

    async def fake_call(http, messages):
        return responses.pop(0)

    agent._call = fake_call  # bypass the real Claude API
    events, approvals = [], []

    async def emit(ev):
        events.append(ev)

    async def approve(req):
        approvals.append(req)
        return approve_value

    asyncio.run(agent.run(client, [{"role": "user", "content": "hi"}], emit, approve))
    return events, approvals


def test_agent_runs_read_tool_then_answers(monkeypatch):
    monkeypatch.setattr(agent_mod, "get_settings", lambda: _fake_settings())
    agent = agent_mod.AIAgent()
    responses = [
        {"content": [
            {"type": "text", "text": "Vou verificar o cluster."},
            {"type": "tool_use", "id": "t1", "name": "cluster_overview", "input": {}},
        ]},
        {"content": [{"type": "text", "text": "Tudo saudável: 3 pods Running."}]},
    ]
    events, approvals = _run(agent, _FakeClient(), responses)
    types = [e["type"] for e in events]
    assert "tool" in types and "tool_result" in types and types[-1] == "done"
    assert not approvals  # read-only tool: no approval needed
    assert any(e["type"] == "tool_result" and e["ok"] for e in events)
    assert any(e["type"] == "text" and "saudável" in e["text"] for e in events)


def test_mutation_requires_approval_and_denial_blocks_it(monkeypatch):
    monkeypatch.setattr(agent_mod, "get_settings", lambda: _fake_settings())
    agent = agent_mod.AIAgent()
    client = _FakeClient()
    responses = [
        {"content": [
            {"type": "tool_use", "id": "m1", "name": "scale_workload",
             "input": {"kind": "deployments", "name": "api", "namespace": "prod", "replicas": 5}},
        ]},
        {"content": [{"type": "text", "text": "Ok, não escalei."}]},
    ]
    events, approvals = _run(agent, client, responses, approve_value=False)
    assert len(approvals) == 1 and approvals[0]["name"] == "scale_workload"
    assert client.scaled is None  # denied -> never executed
    assert any(e["type"] == "tool_result" and not e["ok"] for e in events)


def test_mutation_executes_when_approved(monkeypatch):
    monkeypatch.setattr(agent_mod, "get_settings", lambda: _fake_settings())
    agent = agent_mod.AIAgent()
    client = _FakeClient()
    responses = [
        {"content": [
            {"type": "tool_use", "id": "m1", "name": "scale_workload",
             "input": {"kind": "deployments", "name": "api", "namespace": "prod", "replicas": 5}},
        ]},
        {"content": [{"type": "text", "text": "Escalado para 5."}]},
    ]
    events, approvals = _run(agent, client, responses, approve_value=True)
    assert client.scaled == ("deployments", "api", "prod", 5)
    assert any(e["type"] == "tool_result" and e["ok"] for e in events)


def test_report_crud(client):
    # create
    r = client.post("/api/ai/reports", json={
        "title": "Diagnóstico do dev",
        "content": "# Resumo\n3 pods Running.",
        "cluster_id": 1, "cluster_name": "Dev",
    })
    assert r.status_code == 201
    rid = r.json()["id"]
    # list (summary, no content)
    lst = client.get("/api/ai/reports").json()
    assert any(x["id"] == rid and x["title"] == "Diagnóstico do dev" for x in lst)
    assert "content" not in lst[0]
    # get full
    full = client.get(f"/api/ai/reports/{rid}").json()
    assert full["content"].startswith("# Resumo")
    assert full["cluster_name"] == "Dev"
    # delete
    assert client.delete(f"/api/ai/reports/{rid}").status_code == 204
    assert client.get(f"/api/ai/reports/{rid}").status_code == 404


def test_status_disabled_without_key(monkeypatch):
    monkeypatch.setattr(agent_mod, "get_settings", lambda: _fake_settings())
    monkeypatch.setattr(
        agent_mod, "get_settings",
        lambda: SimpleNamespace(anthropic_api_key="", anthropic_model="m"),
    )
    assert agent_mod.status()["available"] is False
