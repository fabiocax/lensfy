"""Local TCP tunnels to pod ports (kubectl port-forward equivalent).

A module-level ``manager`` holds the active forwards so they survive across HTTP
requests. Each forward runs a threaded TCP server on 127.0.0.1:<local_port>; for
every accepted connection it opens a fresh kubernetes port-forward to the pod and
pumps bytes both ways until either side closes.
"""

from __future__ import annotations

import select
import socketserver
import threading

from kubernetes.stream import portforward

from app.core.logging import get_logger

logger = get_logger(__name__)


def _pump(a, b) -> None:
    """Bidirectionally copy between two sockets until one closes."""
    socks = [a, b]
    try:
        while True:
            readable, _, _ = select.select(socks, [], [], 1.0)
            for s in readable:
                data = s.recv(4096)
                if not data:
                    return
                (b if s is a else a).sendall(data)
    except OSError:
        pass
    finally:
        for s in socks:
            try:
                s.close()
            except OSError:
                pass


class _Forward:
    def __init__(self, fid, cluster_id, core_api, namespace, pod, remote_port):
        self.id = fid
        self.cluster_id = cluster_id
        self.namespace = namespace
        self.pod = pod
        self.remote_port = remote_port
        self._core = core_api
        self.local_port = 0
        self._server: socketserver.TCPServer | None = None

    def start(self, local_port: int) -> None:
        core, namespace, pod, remote = self._core, self.namespace, self.pod, self.remote_port

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                try:
                    pf = portforward(
                        core.connect_get_namespaced_pod_portforward,
                        pod,
                        namespace,
                        ports=str(remote),
                    )
                    remote_sock = pf.socket(remote)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("port-forward dial failed: %s", exc)
                    return
                _pump(self.request, remote_sock)

        server = socketserver.ThreadingTCPServer(("127.0.0.1", local_port), Handler)
        server.daemon_threads = True
        self._server = server
        self.local_port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True, name=f"pf-{self.id}").start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def info(self) -> dict:
        return {
            "id": self.id,
            "cluster_id": self.cluster_id,
            "namespace": self.namespace,
            "pod": self.pod,
            "remote_port": self.remote_port,
            "local_port": self.local_port,
            "status": "active" if self._server is not None else "stopped",
        }


class PortForwardManager:
    def __init__(self) -> None:
        self._forwards: dict[int, _Forward] = {}
        self._counter = 0
        self._lock = threading.Lock()

    def start(self, cluster_id, core_api, namespace, pod, remote_port, local_port=0) -> dict:
        with self._lock:
            self._counter += 1
            fid = self._counter
        fwd = _Forward(fid, cluster_id, core_api, namespace, pod, remote_port)
        try:
            fwd.start(local_port)
        except OSError as exc:
            raise RuntimeError(f"não foi possível abrir a porta local: {exc}") from exc
        with self._lock:
            self._forwards[fid] = fwd
        return fwd.info()

    def list(self) -> list[dict]:
        with self._lock:
            forwards = list(self._forwards.values())
        return [f.info() for f in forwards]

    def stop(self, fid: int) -> bool:
        with self._lock:
            fwd = self._forwards.pop(fid, None)
        if fwd is None:
            return False
        fwd.stop()
        return True

    def stop_for_cluster(self, cluster_id) -> int:
        """Tear down every forward bound to a cluster (call on remove/rotate)."""
        with self._lock:
            doomed = [f for f in self._forwards.values() if f.cluster_id == cluster_id]
            for f in doomed:
                self._forwards.pop(f.id, None)
        for f in doomed:
            f.stop()
        return len(doomed)


manager = PortForwardManager()
