"""WebSocket channels from the spec: /ws/logs, /ws/terminal, /ws/events, /ws/metrics.

/ws/logs streams pod logs in real time (implemented). The remaining producers
(exec PTY, event/metric watch loops) are stubbed with a working connection
lifecycle; each handler documents what it should pump once implemented.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import queue
import threading

import anyio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.ai.agent import AIAgent, AIError
from app.core.logging import get_logger
from app.database.session import SessionLocal
from app.kubernetes.client import KubernetesError
from app.kubernetes.resources import RESOURCES
from app.repositories.cluster import ClusterRepository
from app.services.workloads import WorkloadService, WorkloadServiceError

logger = get_logger(__name__)
ws_router = APIRouter()


_STREAM_END = object()


def _build_client(cluster_id: int):
    """Build a KubernetesClient using a short-lived DB session.

    WebSocket streams are long-lived; if we held the SQLAlchemy session for the
    socket's lifetime it would pin a pooled connection the whole time and the
    QueuePool (5 + 10 overflow) would exhaust under a few open views. The client
    is self-contained (its own kube API client), so we release the connection at
    once. Raises WorkloadServiceError if the cluster is unknown/unloadable.
    """
    db = SessionLocal()
    try:
        return WorkloadService(db)._client(cluster_id)
    finally:
        db.close()


def _load_cluster(cluster_id: int):
    """Fetch a Cluster row (detached) via a short-lived session; None if absent."""
    db = SessionLocal()
    try:
        cluster = ClusterRepository(db).get(cluster_id)
        if cluster is not None:
            db.expunge(cluster)  # detach so we can read its (loaded) columns later
        return cluster
    finally:
        db.close()


@ws_router.websocket("/ws/logs")
async def ws_logs(websocket: WebSocket, cluster_id: int, name: str, namespace: str):
    """Tail a pod's logs in real time.

    Query params: cluster_id, name, namespace, [container], [tail].
    Each blocking ``next()`` on the watch stream runs in a worker thread so the
    event loop stays free; the Watch is stopped on disconnect.
    """
    container = websocket.query_params.get("container")
    try:
        tail = int(websocket.query_params.get("tail", "200"))
    except ValueError:
        tail = 200

    await websocket.accept()
    resp = None
    try:
        client = await anyio.to_thread.run_sync(_build_client, cluster_id)
        resp, stream = await anyio.to_thread.run_sync(
            client.stream_logs, name, namespace, container, tail
        )

        def _next():
            return next(stream, _STREAM_END)

        def _teardown():
            try:
                resp.close()
            except Exception:  # noqa: BLE001
                pass

        await _pump_until_disconnect(
            websocket, _next, lambda line: websocket.send_text(line), _teardown
        )
    except WorkloadServiceError as exc:
        await _safe_send_json(websocket, {"error": str(exc)})
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass  # client closed the viewer / server shutting down
    except Exception as exc:  # noqa: BLE001 - API/connection errors during stream
        logger.warning("ws/logs error: %s", exc)
        await _safe_send_json(websocket, {"error": str(exc)})
    finally:
        if resp is not None:
            try:
                resp.close()
            except Exception:  # noqa: BLE001
                pass


async def _safe_send_json(websocket: WebSocket, payload: dict) -> None:
    try:
        await websocket.send_json(payload)
    except Exception:  # noqa: BLE001 - socket already gone
        pass


async def _await_disconnect(websocket: WebSocket) -> None:
    """Resolve when the client disconnects.

    Starlette only surfaces a disconnect from ``receive()``, so a *send-only*
    stream (logs/events/watch/metrics) would otherwise not notice a closed peer
    until its next send — and while parked in a blocking ``next()`` between
    events, that send may never come, leaking the worker thread + upstream kube
    connection. Watching ``receive()`` concurrently gives us a prompt signal.
    """
    try:
        while True:
            ev = await websocket.receive()
            if ev.get("type") == "websocket.disconnect":
                return
    except (WebSocketDisconnect, asyncio.CancelledError):
        return


async def _pump_until_disconnect(websocket, next_fn, on_item, teardown) -> None:
    """Pump ``next_fn() -> on_item(item)`` until the stream ends or the client
    disconnects, whichever comes first.

    On disconnect we call ``teardown()`` immediately (``resp.close()`` /
    ``watcher.stop()``) which tears the upstream socket and unblocks the
    abandoned blocking ``next()`` worker thread, instead of leaving it parked
    until the next event arrives.
    """

    async def producer():
        while True:
            item = await anyio.to_thread.run_sync(next_fn, abandon_on_cancel=True)
            if item is _STREAM_END:
                return
            await on_item(item)

    prod = asyncio.ensure_future(producer())
    disc = asyncio.ensure_future(_await_disconnect(websocket))
    try:
        done, _pending = await asyncio.wait(
            {prod, disc}, return_when=asyncio.FIRST_COMPLETED
        )
        if disc in done:
            teardown()  # unblock the parked next() thread so it doesn't leak
        if prod in done and prod.exception() is not None:
            raise prod.exception()
    finally:
        prod.cancel()
        disc.cancel()
        # Retrieve results so a finished-with-exception task doesn't warn.
        for task in (prod, disc):
            with contextlib.suppress(BaseException):
                await task


@ws_router.websocket("/ws/terminal")
async def ws_terminal(websocket: WebSocket, cluster_id: int):
    """Interactive shell into a pod, or into a node's host (Lens-style).

    Query params: cluster_id + either ``node`` (host shell via a privileged
    node-shell pod) or ``name``/``namespace``/[``container``] (pod exec). Browser
    frames are tagged: ``0``+data = stdin, ``1``+JSON{cols,rows} = resize.

    The kubernetes exec WSClient is **not** safe to read and write from two
    threads at once — concurrent ``update()``/``write_*`` corrupts the
    websocket-client frame buffer (surfaces as "'NoneType' has no attribute
    'decode'"). So a single worker thread owns the socket: it drains a stdin
    queue then polls for output, and hands output back to the event loop via
    ``run_coroutine_threadsafe``. The async side only touches the queue.
    """
    qp = websocket.query_params
    node = qp.get("node")
    name = qp.get("name")
    namespace = qp.get("namespace")
    container = qp.get("container")
    await websocket.accept()
    node_pod = None  # (client, pod, ns) to clean up when a node shell ends

    try:
        ksclient = await anyio.to_thread.run_sync(_build_client, cluster_id)
        if node:
            # The node-shell pod can take a few seconds to pull/start — tell the
            # user so the terminal isn't blank while node_shell_exec blocks.
            await websocket.send_text(
                f"\x1b[90m• criando node-shell (pod privilegiado em kube-system) no node {node} "
                "e aguardando ficar Running…\x1b[0m\r\n"
            )
            exec_ws, pod, ns = await anyio.to_thread.run_sync(ksclient.node_shell_exec, node)
            node_pod = (ksclient, pod, ns)
        else:
            exec_ws = await anyio.to_thread.run_sync(
                ksclient.exec_shell, name, namespace, container
            )
    except WorkloadServiceError as exc:
        await _safe_send_json(websocket, {"error": str(exc)})
        return
    except Exception as exc:  # noqa: BLE001 - exec build / API errors
        logger.warning("ws/terminal exec failed: %s", exc)
        await _safe_send_json(websocket, {"error": str(exc)})
        return

    loop = asyncio.get_running_loop()
    inbox: queue.Queue = queue.Queue()
    done = threading.Event()

    def _on_loop(coro):
        # Bounded wait: a slow/half-open client must not block this worker thread
        # (and the event loop hand-off) forever. On timeout, bail the session.
        try:
            return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=15)
        except Exception:  # noqa: BLE001 - loop closing / socket gone / timeout
            done.set()

    def _worker():
        had_output = False
        err_raw = ""
        try:
            while exec_ws.is_open() and not done.is_set():
                while True:
                    try:
                        kind, payload = inbox.get_nowait()
                    except queue.Empty:
                        break
                    if kind == "resize":
                        exec_ws.write_channel(4, payload)  # channel 4 = resize
                    else:
                        exec_ws.write_stdin(payload)
                exec_ws.update(timeout=0.1)
                out = ""
                if exec_ws.peek_stdout():
                    out += exec_ws.read_stdout()
                if exec_ws.peek_stderr():
                    out += exec_ws.read_stderr()
                if exec_ws.peek_channel(_ERROR_CHANNEL):
                    err_raw += exec_ws.read_channel(_ERROR_CHANNEL)
                if out:
                    had_output = True
                    _on_loop(websocket.send_text(out))
            # The exec failed to even start a shell (e.g. distroless image with no
            # /bin/sh) -> surface the error-channel status instead of a blank close.
            if not had_output:
                msg = _exec_status_message(err_raw)
                if msg:
                    _on_loop(_safe_send_json(websocket, {"error": msg}))
        except Exception as exc:  # noqa: BLE001 - stream error
            logger.warning("ws/terminal stream error: %s", exc)
            _on_loop(_safe_send_json(websocket, {"error": str(exc)}))
        finally:
            done.set()
            try:
                exec_ws.close()
            except Exception:  # noqa: BLE001
                pass
            owner = getattr(exec_ws, "_lensfy_api_client", None)
            if owner is not None:  # release the dedicated exec ApiClient's pool
                try:
                    owner.close()
                except Exception:  # noqa: BLE001
                    pass
            _on_loop(_safe_close(websocket))  # unblock the receive loop

    worker = threading.Thread(target=_worker, name="lensfy-exec", daemon=True)
    worker.start()

    try:
        while not done.is_set():
            msg = await websocket.receive_text()
            if not msg:
                continue
            if msg[0] == "1":  # resize: {"cols":N,"rows":N}
                try:
                    dims = json.loads(msg[1:])
                    inbox.put((
                        "resize",
                        json.dumps({
                            "Width": int(dims.get("cols", 80)),
                            "Height": int(dims.get("rows", 24)),
                        }),
                    ))
                except Exception:  # noqa: BLE001
                    pass
            else:  # stdin
                inbox.put(("data", msg[1:]))
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        done.set()
        if node_pod is not None:
            ks, pod, ns = node_pod
            await anyio.to_thread.run_sync(ks.delete_pod_quiet, pod, ns)


_ERROR_CHANNEL = 3  # kubernetes exec error/status channel


def _exec_status_message(raw: str) -> str | None:
    """Turn the exec error-channel v1.Status into a user-facing message.

    Returns None on success. Detects the common "no shell in the container"
    case (exit 127 / executable not found) and explains it.
    """
    if not raw:
        return None
    try:
        status = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if status.get("status") == "Success":
        return None
    message = status.get("message", "") or ""
    lowered = message.lower()
    if "127" in lowered or "executable file not found" in lowered or "no such file" in lowered:
        return (
            "Não foi possível abrir um shell neste container "
            "(sh/bash não encontrado — imagem provavelmente distroless). "
            "Selecione outro container, se houver."
        )
    return message or "Falha ao iniciar o terminal."


async def _safe_close(websocket: WebSocket) -> None:
    try:
        await websocket.close()
    except Exception:  # noqa: BLE001
        pass


def _spawn_kubectl_shell(cluster):
    """Spawn an interactive shell in a PTY, scoped to the cluster.

    Sets KUBECONFIG to the cluster's kubeconfig and prepends a tiny ``kubectl``
    wrapper that injects ``--context <ctx>`` so commands target this cluster.
    Returns (master_fd, popen, tmpdir, kubectl_present).
    """
    import pty
    import shlex
    import shutil
    import subprocess
    import tempfile

    shell = shutil.which("bash") or "/bin/sh"
    env = os.environ.copy()
    env["TERM"] = "xterm-256color"
    if cluster.kubeconfig_path:
        env["KUBECONFIG"] = os.path.expanduser(cluster.kubeconfig_path)

    kubectl = shutil.which("kubectl")
    tmpdir = None
    if kubectl:
        tmpdir = tempfile.mkdtemp(prefix="lensfy-kubectl-")
        wrapper = os.path.join(tmpdir, "kubectl")
        with open(wrapper, "w", encoding="utf-8") as fh:
            fh.write(
                f'#!/bin/sh\nexec {shlex.quote(kubectl)} '
                f'--context {shlex.quote(cluster.context)} "$@"\n'
            )
        os.chmod(wrapper, 0o755)
        env["PATH"] = tmpdir + os.pathsep + env.get("PATH", "")

    master, slave = pty.openpty()
    proc = subprocess.Popen(
        [shell, "-i"],
        stdin=slave, stdout=slave, stderr=slave,
        env=env, cwd=os.path.expanduser("~"),
        preexec_fn=os.setsid, close_fds=True,
    )
    os.close(slave)
    return master, proc, tmpdir, bool(kubectl)


@ws_router.websocket("/ws/kubectl")
async def ws_kubectl(websocket: WebSocket, cluster_id: int):
    """Local shell scoped to the cluster (KUBECONFIG + a context-injecting
    kubectl wrapper on PATH), bridged over a PTY — Lens-style terminal."""
    import signal

    await websocket.accept()
    cluster = _load_cluster(cluster_id)
    if cluster is None:
        await _safe_send_json(websocket, {"error": "Cluster não encontrado"})
        return

    try:
        master, proc, tmpdir, has_kubectl = await anyio.to_thread.run_sync(
            _spawn_kubectl_shell, cluster
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("ws/kubectl spawn failed: %s", exc)
        await _safe_send_json(websocket, {"error": str(exc)})
        return

    await websocket.send_text(f"\x1b[36mkubectl shell · contexto {cluster.context}\x1b[0m\r\n")
    if not has_kubectl:
        await websocket.send_text("\x1b[33maviso: kubectl não está instalado no host\x1b[0m\r\n")

    loop = asyncio.get_running_loop()
    done = threading.Event()

    def _reader():
        try:
            while not done.is_set():
                try:
                    data = os.read(master, 4096)
                except OSError:
                    break
                if not data:
                    break
                text = data.decode("utf-8", "replace")
                try:
                    asyncio.run_coroutine_threadsafe(websocket.send_text(text), loop).result()
                except Exception:  # noqa: BLE001
                    break
        finally:
            done.set()

    threading.Thread(target=_reader, name="lensfy-kubectl", daemon=True).start()

    try:
        while not done.is_set():
            msg = await websocket.receive_text()
            if not msg:
                continue
            if msg[0] == "1":  # resize
                try:
                    import fcntl
                    import struct
                    import termios

                    dims = json.loads(msg[1:])
                    winsz = struct.pack("HHHH", int(dims.get("rows", 24)), int(dims.get("cols", 80)), 0, 0)
                    fcntl.ioctl(master, termios.TIOCSWINSZ, winsz)
                except Exception:  # noqa: BLE001
                    pass
            else:
                os.write(master, msg[1:].encode("utf-8"))
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        logger.warning("ws/kubectl error: %s", exc)
    finally:
        done.set()
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception:  # noqa: BLE001
            pass
        try:
            os.close(master)
        except Exception:  # noqa: BLE001
            pass
        # Reap the shell so it doesn't linger as a zombie until GC.
        try:
            proc.wait(timeout=3)
        except Exception:  # noqa: BLE001 - already gone / still dying
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                proc.wait(timeout=2)
            except Exception:  # noqa: BLE001
                pass
        if tmpdir:
            import shutil

            shutil.rmtree(tmpdir, ignore_errors=True)
        await _safe_close(websocket)


@ws_router.websocket("/ws/events")
async def ws_events(websocket: WebSocket, cluster_id: int):
    """Stream cluster events live. Query params: cluster_id, [namespace].

    Each frame is a JSON event row (type/reason/object/message/age + event_type
    ADDED|MODIFIED|DELETED). Watch iteration runs in a worker thread.
    """
    namespace = websocket.query_params.get("namespace") or None
    await websocket.accept()
    watcher = None
    try:
        client = await anyio.to_thread.run_sync(_build_client, cluster_id)
        watcher, stream = await anyio.to_thread.run_sync(client.stream_events, namespace)
        row_fn = RESOURCES["events"].row_fn

        def _next():
            return next(stream, _STREAM_END)

        def _teardown():
            try:
                watcher.stop()
            except Exception:  # noqa: BLE001
                pass

        async def _on_item(item):
            row = row_fn(item["object"])
            row["event_type"] = item.get("type")
            await websocket.send_json(row)

        await _pump_until_disconnect(websocket, _next, _on_item, _teardown)
    except WorkloadServiceError as exc:
        await _safe_send_json(websocket, {"error": str(exc)})
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass  # client navigated away / disconnected / server shutting down
    except Exception as exc:  # noqa: BLE001 - API/connection errors during watch
        logger.warning("ws/events error: %s", exc)
        await _safe_send_json(websocket, {"error": str(exc)})
    finally:
        if watcher is not None:
            try:
                watcher.stop()
            except Exception:  # noqa: BLE001
                pass


@ws_router.websocket("/ws/watch")
async def ws_watch(websocket: WebSocket, cluster_id: int, kind: str):
    """Stream live changes for a resource list (pods, deployments, generic kinds).

    Query params: cluster_id, kind, [namespace]. Each frame is
    ``{"type": ADDED|MODIFIED|DELETED, "row": <row dict>}`` where ``row`` matches
    the shape the REST list endpoint returns, so the UI can upsert/remove rows
    in place. Watch iteration runs in a worker thread.
    """
    namespace = websocket.query_params.get("namespace") or None
    await websocket.accept()
    watcher = None
    try:
        client = await anyio.to_thread.run_sync(_build_client, cluster_id)
        watcher, stream, fmt = await anyio.to_thread.run_sync(
            client.watch_resource, kind, namespace
        )

        def _next():
            return next(stream, _STREAM_END)

        def _teardown():
            try:
                watcher.stop()
            except Exception:  # noqa: BLE001
                pass

        async def _on_item(item):
            # When the server-side watch expires (410 Gone) or times out, the
            # stream ends and the socket closes; the client reconnects (see
            # watchInto) and the fresh watch re-sends an ADDED burst, recovering
            # current state. Deletes missed during the gap reconcile on the next
            # manual refresh — a deliberate tradeoff documented client-side.
            try:
                row = fmt(item["object"])
            except Exception as exc:  # noqa: BLE001 - skip rows we can't format
                logger.debug("ws/watch row format failed (%s): %s", kind, exc)
                return
            await websocket.send_json({"type": item.get("type"), "row": row})

        await _pump_until_disconnect(websocket, _next, _on_item, _teardown)
    except (WorkloadServiceError, KubernetesError) as exc:
        await _safe_send_json(websocket, {"error": str(exc)})
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass  # client navigated away / disconnected / server shutting down
    except Exception as exc:  # noqa: BLE001 - API/connection errors during watch
        logger.warning("ws/watch error (%s): %s", kind, exc)
        await _safe_send_json(websocket, {"error": str(exc)})
    finally:
        if watcher is not None:
            try:
                watcher.stop()
            except Exception:  # noqa: BLE001
                pass


@ws_router.websocket("/ws/ai")
async def ws_ai(websocket: WebSocket, cluster_id: int):
    """AI assistant: an agentic chat over a cluster (diagnose + automate).

    Client -> server: {"type":"message","text":...} and
    {"type":"approval","id":...,"approved":bool}.
    Server -> client: text / tool / tool_result / approval_request / done / error
    events streamed as the agent works. Mutating tools wait for an approval.
    """
    await websocket.accept()
    try:
        try:
            agent = AIAgent()
        except AIError as exc:
            await _safe_send_json(websocket, {"type": "error", "message": str(exc)})
            return
        try:
            client = await anyio.to_thread.run_sync(_build_client, cluster_id)
        except WorkloadServiceError as exc:
            await _safe_send_json(websocket, {"type": "error", "message": str(exc)})
            return

        messages: list[dict] = []

        async def emit(event: dict) -> None:
            await websocket.send_json(event)

        async def approve(req: dict) -> bool:
            await websocket.send_json({"type": "approval_request", **req})
            while True:
                raw = await websocket.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue  # ignore stray/malformed frames instead of killing the socket
                if msg.get("type") == "approval" and msg.get("id") == req["id"]:
                    return bool(msg.get("approved"))

        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if msg.get("type") != "message":
                continue
            text = (msg.get("text") or "").strip()
            if not text:
                continue
            messages.append({"role": "user", "content": text})
            try:
                await agent.run(client, messages, emit, approve)
            except AIError as exc:
                await _safe_send_json(websocket, {"type": "error", "message": str(exc)})
                await _safe_send_json(websocket, {"type": "done"})
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except Exception as exc:  # noqa: BLE001
        logger.warning("ws/ai error: %s", exc)
        await _safe_send_json(websocket, {"type": "error", "message": str(exc)})
        await _safe_send_json(websocket, {"type": "done"})


@ws_router.websocket("/ws/metrics")
async def ws_metrics(websocket: WebSocket, cluster_id: int):
    """Push dashboard metrics on a timer. Query params: cluster_id, [interval]."""
    try:
        interval = max(2, int(websocket.query_params.get("interval", "5")))
    except ValueError:
        interval = 5

    await websocket.accept()
    try:
        client = await anyio.to_thread.run_sync(_build_client, cluster_id)

        async def _poll():
            while True:
                try:
                    metrics = await anyio.to_thread.run_sync(
                        client.cluster_metrics, abandon_on_cancel=True
                    )
                except KubernetesError as exc:
                    await _safe_send_json(websocket, {"error": str(exc)})
                    return
                await websocket.send_json(metrics.model_dump())
                await anyio.sleep(interval)

        # Race the timer loop against client disconnect so a closed viewer stops
        # the cluster polling promptly instead of up to `interval` seconds late.
        poll = asyncio.ensure_future(_poll())
        disc = asyncio.ensure_future(_await_disconnect(websocket))
        try:
            done, _pending = await asyncio.wait(
                {poll, disc}, return_when=asyncio.FIRST_COMPLETED
            )
            if poll in done and poll.exception() is not None:
                raise poll.exception()
        finally:
            poll.cancel()
            disc.cancel()
            for task in (poll, disc):
                with contextlib.suppress(BaseException):
                    await task
    except WorkloadServiceError as exc:
        await _safe_send_json(websocket, {"error": str(exc)})
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass  # client navigated away / disconnected / server shutting down
    except Exception as exc:  # noqa: BLE001
        logger.warning("ws/metrics error: %s", exc)
