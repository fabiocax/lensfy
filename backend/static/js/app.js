/**
 * Lensfy frontend — vanilla JS no modelo Fluxy.
 * Consome a API REST do backend FastAPI em /api.
 */
(function () {
  'use strict';

  const API = '/api';
  const state = {
    clusters: [],
    currentId: null,
    view: 'dashboard',
    selectedNs: [], // namespaces filtrados (vazio = todos), multi-seleção
    namespaces: [], // namespaces do cluster atual (para o seletor)
    discovered: [], // grupos de tipos descobertos dinamicamente (CRDs etc.)
    discoveredMap: {}, // id 'dyn:apiVersion:Kind' -> descritor do recurso
    viewSocket: null, // WebSocket vivo da view atual (métricas/eventos)
    viewTimer: null, // timer de polling da view atual (métricas top)
    viewWatchTimer: null, // debounce de re-render do watch ao vivo
    // Painel inferior (dock estilo Lens): múltiplas abas, inclusive do mesmo tipo.
    dock: { tabs: [], activeId: null }, // tabs: [{ id, type, title, key }]
    dockHeight: 320, // altura do dock (px), redimensionável e persistida
    dockMax: false, // dock maximizado
  };

  // true se o namespace passa pelo filtro global (vazio = todos os namespaces).
  const nsMatch = (ns) => !state.selectedNs.length || state.selectedNs.includes(ns);

  // Árvore de recursos (espelha o Explorer do PROJECT.md).
  // impl: false => exibe estado "em breve" (endpoint ainda não existe).
  const TREE = [
    {
      group: 'Cluster',
      items: [
        { id: 'dashboard', label: 'Dashboard', icon: 'fa-gauge-high', impl: true },
        { id: 'metrics', label: 'Métricas', icon: 'fa-chart-line' },
        { id: 'nodes', label: 'Nodes', icon: 'fa-server' },
        { id: 'namespaces', label: 'Namespaces', icon: 'fa-layer-group' },
      ],
    },
    {
      group: 'Workloads',
      items: [
        { id: 'pods', label: 'Pods', icon: 'fa-cube', impl: true },
        { id: 'deployments', label: 'Deployments', icon: 'fa-rocket', impl: true },
        { id: 'statefulsets', label: 'StatefulSets', icon: 'fa-database' },
        { id: 'daemonsets', label: 'DaemonSets', icon: 'fa-diagram-project' },
        { id: 'jobs', label: 'Jobs', icon: 'fa-list-check' },
        { id: 'cronjobs', label: 'CronJobs', icon: 'fa-clock' },
      ],
    },
    {
      group: 'Network',
      items: [
        { id: 'services', label: 'Services', icon: 'fa-network-wired' },
        { id: 'ingress', label: 'Ingress', icon: 'fa-globe' },
        { id: 'networkpolicies', label: 'Network Policies', icon: 'fa-shield' },
      ],
    },
    {
      group: 'Config',
      items: [
        { id: 'configmaps', label: 'ConfigMaps', icon: 'fa-sliders' },
        { id: 'secrets', label: 'Secrets', icon: 'fa-key' },
      ],
    },
    {
      group: 'Storage',
      items: [
        { id: 'pvc', label: 'Persistent Volume Claims', icon: 'fa-hard-drive' },
        { id: 'storageclasses', label: 'Storage Classes', icon: 'fa-box-archive' },
      ],
    },
    {
      group: 'Recursos & Cotas',
      items: [
        { id: 'limitranges', label: 'LimitRanges', icon: 'fa-ruler-combined' },
        { id: 'resourcequotas', label: 'ResourceQuotas', icon: 'fa-gauge-high' },
      ],
    },
    {
      group: 'Acesso (RBAC)',
      items: [
        { id: 'roles', label: 'Roles', icon: 'fa-user-shield' },
        { id: 'clusterroles', label: 'ClusterRoles', icon: 'fa-users-gear' },
        { id: 'rolebindings', label: 'Role Bindings', icon: 'fa-link' },
        { id: 'clusterrolebindings', label: 'ClusterRole Bindings', icon: 'fa-link' },
      ],
    },
    {
      group: 'Custom Resources',
      items: [{ id: 'crds', label: 'CRDs', icon: 'fa-cubes' }],
    },
    {
      group: 'Helm',
      items: [{ id: 'helm', label: 'Releases', icon: 'fa-ship' }],
    },
    {
      group: 'Events',
      items: [{ id: 'events', label: 'Events', icon: 'fa-bell' }],
    },
  ];

  const VIEW_LABELS = {};
  TREE.forEach((g) => g.items.forEach((i) => (VIEW_LABELS[i.id] = i.label)));
  // Views promovidas para o menu superior (não ficam na árvore).
  Object.assign(VIEW_LABELS, {
    issues: 'Problemas', budget: 'Recursos', map: 'Mapa',
    security: 'Segurança', rbac: 'RBAC', capacity: 'Capacidade', search: 'Busca global',
    impact: 'Impacto',
  });

  // ---------- helpers ----------
  const $ = (sel) => document.querySelector(sel);
  const el = (html) => {
    const t = document.createElement('template');
    t.innerHTML = html.trim();
    return t.content.firstElementChild;
  };
  const esc = (s) =>
    String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  // Token de dispositivo: buscado em runtime (não embutido no HTML, para um
  // shell cacheado pelo PWA nunca servir um token vazio/antigo). Autentica as
  // chamadas a /api (header) e /ws (query param) — sem login/senha. Começa vazio
  // e é preenchido por loadDeviceToken() (ou pela tela de onboarding).
  let DEVICE_TOKEN = '';

  async function api(path, options) {
    const opts = options || {};
    const res = await fetch(API + path, {
      cache: 'no-store', // dados ao vivo: nunca usar o cache HTTP do navegador
      ...opts,
      headers: {
        'Content-Type': 'application/json',
        'X-Lensfy-Token': DEVICE_TOKEN,
        ...(opts.headers || {}),
      },
    });
    if (!res.ok) {
      // 401 no gate de segurança => token de dispositivo desatualizado (ex.: foi
      // regenerado). Recarregar a página rebusca o token embutido no HTML.
      if (res.status === 401) authError();
      let detail = res.statusText;
      try {
        detail = (await res.json()).detail || detail;
      } catch (_) {}
      throw new Error(detail);
    }
    return res.status === 204 ? null : res.json();
  }

  // Aviso único de token inválido/desatualizado, com ação de recarregar.
  let authErrorShown = false;
  function authError() {
    if (authErrorShown) return;
    authErrorShown = true;
    uiModal({
      title: 'Sessão de dispositivo inválida',
      icon: 'fa-triangle-exclamation',
      body:
        '<p>O token deste dispositivo está desatualizado ou foi regenerado, então o acesso à API foi recusado.</p>' +
        '<p style="margin-top:8px;color:var(--text-tertiary);font-size:var(--font-size-sm)">Recarregue a página para obter o token atual.</p>',
      actions: [
        { label: 'Recarregar agora', cls: 'btn-primary', primary: true, value: 'reload' },
      ],
    }).then((v) => {
      if (v === 'reload') location.reload();
      else authErrorShown = false; // permite reexibir se fechar sem recarregar
    });
  }

  // Indicador de reconexão (stream ao vivo /ws/watch) no header.
  function setConnState(reconnecting) {
    const el = document.getElementById('conn-indicator');
    if (el) el.hidden = !reconnecting;
  }

  const current = () => state.clusters.find((c) => c.id === state.currentId) || null;

  function wsUrl(path, params) {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    // Browsers can't set headers on a WebSocket handshake, so the device token
    // travels as a query param (validated by the security middleware).
    const qs = new URLSearchParams({ ...params, token: DEVICE_TOKEN }).toString();
    return `${proto}://${location.host}${path}?${qs}`;
  }

  // Fecha o WebSocket e o timer da view anterior (ao trocar de view/cluster).
  function closeViewSocket() {
    if (state.viewSocket) {
      const s = state.viewSocket;
      state.viewSocket = null;
      // Desanexa handlers para um frame em trânsito não re-renderizar a view antiga.
      s.onmessage = null;
      s.onopen = null;
      s.onclose = null;
      s.onerror = null;
      try {
        s.close();
      } catch (_) {}
    }
    setConnState(false); // troca de view/cluster: estado de reconexão não se aplica
    if (state.viewTimer) {
      clearInterval(state.viewTimer);
      state.viewTimer = null;
    }
    if (state.viewWatchTimer) {
      clearTimeout(state.viewWatchTimer);
      state.viewWatchTimer = null;
    }
  }

  // Ordena linhas por (namespace, nome) para a lista ficar estável quando o
  // watch insere/atualiza itens (senão a linha nova vai parar no fim).
  const byNsName = (a, b) =>
    (a.namespace || '').localeCompare(b.namespace || '') ||
    (a.name || '').localeCompare(b.name || '');

  // Live updates for a list view via /ws/watch. Mutates `rows` in place (upsert
  // by key on ADDED/MODIFIED, remove on DELETED) and re-renders, debounced.
  // Re-render is an incremental patch (patchTable), so selection/scroll survive.
  function watchInto(cur, kind, rows, keyOf, render, ns) {
    const params = { cluster_id: cur.id, kind };
    if (ns) params.namespace = ns;
    let backoff = 1000; // reconexão com backoff exponencial (até 15s)

    const connect = () => {
      const sock = new WebSocket(wsUrl('/ws/watch', params));
      state.viewSocket = sock;
      const schedule = () => {
        if (state.viewWatchTimer) return;
        state.viewWatchTimer = setTimeout(() => {
          state.viewWatchTimer = null;
          if (state.viewSocket !== sock) return; // view trocou
          render();
        }, 400);
      };
      sock.onopen = () => {
        backoff = 1000; // conexão ok: zera o backoff
        setConnState(false); // some o indicador "Reconectando…"
      };
      sock.onmessage = (ev) => {
        let msg;
        try {
          msg = JSON.parse(ev.data);
        } catch (_) {
          return;
        }
        if (msg.error || !msg.row) return;
        const k = keyOf(msg.row);
        const i = rows.findIndex((r) => keyOf(r) === k);
        if (msg.type === 'DELETED') {
          if (i >= 0) rows.splice(i, 1);
        } else if (i >= 0) {
          rows[i] = msg.row;
        } else {
          rows.push(msg.row);
        }
        schedule();
      };
      // Reconecta em queda inesperada. closeViewSocket() zera onclose + viewSocket
      // na navegação, então uma desconexão intencional não dispara reconexão.
      // (O burst inicial de ADDED re-insere as linhas; deleções perdidas durante a
      // queda são reconciliadas no próximo refresh manual.)
      sock.onclose = () => {
        if (state.viewSocket !== sock) return;
        setConnState(true); // queda inesperada: mostra "Reconectando…"
        const wait = backoff;
        backoff = Math.min(backoff * 2, 15000);
        setTimeout(() => {
          if (state.viewSocket === sock) connect();
        }, wait);
      };
    };

    connect();
  }

  // ---------- clusters ----------
  async function loadClusters() {
    try {
      state.clusters = await api('/clusters');
    } catch (e) {
      window.toast('Falha ao carregar clusters: ' + e.message, 'error');
      state.clusters = [];
    }
    if (!current() && state.clusters.length) state.currentId = state.clusters[0].id;
    const dock = state.currentId ? restoreSession(state.currentId) : null;
    renderClusters();
    renderTree();
    loadNamespaces();
    loadDiscovery();
    renderView();
    restoreDock(current(), dock); // restaura o ponto onde parei (entre recargas)
  }

  // Carrega os namespaces do cluster para o seletor global (estilo Lens).
  async function loadNamespaces() {
    const cur = current();
    state.namespaces = [];
    if (cur) {
      try {
        const data = await api(`/resources?cluster_id=${cur.id}&kind=namespaces`);
        // Guard against a rapid cluster switch: a slow response for the previous
        // cluster must not clobber the picker for the one now selected.
        if (current() !== cur) return;
        state.namespaces = data.rows.map((r) => r.name).sort();
      } catch (_) {
        if (current() !== cur) return;
        state.namespaces = [];
      }
    }
    state.selectedNs = state.selectedNs.filter((n) => state.namespaces.includes(n));
    renderNamespacePicker();
  }

  function nsLabel() {
    if (!state.selectedNs.length) return 'Todos os namespaces';
    if (state.selectedNs.length === 1) return state.selectedNs[0];
    return `${state.selectedNs.length} namespaces`;
  }

  function renderNamespacePicker() {
    $('#ns-btn').disabled = !current();
    $('#ns-label').textContent = nsLabel();
    const q = ($('#ns-search').value || '').toLowerCase();
    const opts = $('#ns-options');
    const checked = (on) => (on ? 'fa-square-check' : 'fa-square');
    const rows = [
      `<div class="ns-option" data-ns="" role="button">` +
        `<i class="far ${checked(!state.selectedNs.length)}"></i><span>Todos os namespaces</span></div>`,
    ];
    state.namespaces
      .filter((n) => !q || n.toLowerCase().includes(q))
      .forEach((n) => {
        rows.push(
          `<div class="ns-option" data-ns="${esc(n)}" role="button">` +
            `<i class="far ${checked(state.selectedNs.includes(n))}"></i><span>${esc(n)}</span></div>`
        );
      });
    opts.innerHTML = rows.join('');
    opts.querySelectorAll('.ns-option').forEach((el) =>
      el.addEventListener('click', () => toggleNamespace(el.dataset.ns))
    );
  }

  function toggleNamespace(ns) {
    if (!ns) {
      state.selectedNs = []; // "Todos os namespaces"
    } else if (state.selectedNs.includes(ns)) {
      state.selectedNs = state.selectedNs.filter((n) => n !== ns);
    } else {
      state.selectedNs = [...state.selectedNs, ns];
    }
    renderNamespacePicker();
    renderView();
    persistSession();
  }

  // ---------- busca global (command palette: pular para qualquer recurso) ----------
  let searchMatches = [];
  let searchIdx = -1;
  const searchItems = () => {
    const out = [];
    TREE.forEach((g) => g.items.forEach((it) => out.push({ id: it.id, label: it.label, group: g.group, icon: it.icon })));
    return out;
  };
  function runSearch() {
    const q = $('#global-search').value.trim().toLowerCase();
    searchMatches = !q
      ? []
      : searchItems()
          .filter((it) => it.label.toLowerCase().includes(q) || it.id.includes(q) || it.group.toLowerCase().includes(q))
          .slice(0, 8);
    searchIdx = searchMatches.length ? 0 : -1;
    renderSearch();
  }
  function renderSearch() {
    const res = $('#search-results');
    if (!searchMatches.length) {
      res.hidden = true;
      res.innerHTML = '';
      return;
    }
    res.innerHTML = searchMatches
      .map(
        (m, i) =>
          `<div class="search-item ${i === searchIdx ? 'active' : ''}" data-i="${i}">` +
          `<i class="fas ${m.icon}"></i><span>${esc(m.label)}</span><span class="sr-group">${esc(m.group)}</span></div>`
      )
      .join('');
    res.hidden = false;
    res.querySelectorAll('.search-item').forEach((el) =>
      el.addEventListener('mousedown', (e) => {
        e.preventDefault();
        chooseSearch(+el.dataset.i);
      })
    );
  }
  function chooseSearch(i) {
    const m = searchMatches[i];
    if (!m) return;
    if (!current()) return window.toast('Selecione um cluster primeiro', 'warning');
    state.view = m.id;
    renderTree();
    renderView();
    $('#global-search').value = '';
    $('#search-results').hidden = true;
    $('#global-search').blur();
  }

  const CLUSTER_PALETTE = ['#3498db', '#2ecc71', '#9b59b6', '#e67e22', '#e74c3c', '#1abc9c', '#e84393', '#f39c12'];
  const clusterColor = (c) => c.color || CLUSTER_PALETTE[(c.id - 1 + CLUSTER_PALETTE.length * 99) % CLUSTER_PALETTE.length];
  const clusterInitial = (c) => ((c.name || '?').trim().charAt(0) || '?').toUpperCase();
  const avatarHTML = (c) =>
    `<span class="cluster-avatar" style="background:${clusterColor(c)}">${esc(clusterInitial(c))}` +
    `<span class="av-dot status-dot ${esc(c.status)}"></span></span>`;

  function renderClusters() {
    const cur = current();
    const av = $('#cluster-btn-avatar');
    if (cur) {
      av.style.background = clusterColor(cur);
      av.innerHTML = `${esc(clusterInitial(cur))}<span class="av-dot status-dot ${esc(cur.status)}"></span>`;
    } else {
      av.style.background = 'var(--bg-lighter)';
      av.innerHTML = '<span class="av-dot status-dot unknown"></span>';
    }
    $('#cluster-btn-name').textContent = cur ? cur.name : 'Nenhum cluster';
    $('#cluster-btn-meta').textContent = cur
      ? `${cur.version || cur.status} · ${cur.provider || 'cluster'}`
      : 'Importe um kubeconfig';
    renderClusterMenu();
  }

  function renderClusterMenu() {
    const q = ($('#cluster-search').value || '').toLowerCase();
    const list = state.clusters.filter(
      (c) => !q || c.name.toLowerCase().includes(q) || (c.context || '').toLowerCase().includes(q)
    );
    const opts = $('#cluster-options');
    if (!list.length) {
      opts.innerHTML = '<div class="cluster-empty">Nenhum cluster importado</div>';
      return;
    }
    // Arraste só é oferecido quando não há filtro (a ordem do DOM = ordem real).
    const draggable = !q;
    opts.innerHTML = list
      .map(
        (c) =>
          `<div class="cluster-option ${c.id === state.currentId ? 'active' : ''}" data-cid="${c.id}"${draggable ? ' draggable="true"' : ''}>` +
          (draggable ? '<span class="drag-handle" title="Arraste para reordenar"><i class="fas fa-grip-vertical"></i></span>' : '') +
          avatarHTML(c) +
          `<div class="info"><div class="name" title="${esc(c.name)}">${esc(c.name)}</div>` +
          `<div class="meta" title="${esc(c.context)}">${esc(c.context)}</div></div>` +
          `<label class="color-swatch" title="Cor" style="background:${clusterColor(c)}"><input type="color" data-ccolor="${c.id}" value="${clusterColor(c)}"></label>` +
          `<button class="btn btn-sm btn-icon" data-crename="${c.id}" title="Renomear"><i class="fas fa-pen"></i></button>` +
          `<button class="btn btn-sm btn-icon btn-danger" data-cdel="${c.id}" title="Remover"><i class="fas fa-trash"></i></button>` +
          `</div>`
      )
      .join('');
    const find = (id) => state.clusters.find((c) => c.id === +id);
    opts.querySelectorAll('.cluster-option').forEach((node) => {
      node.addEventListener('click', (ev) => {
        if (ev.target.closest('button, label, input, .drag-handle')) return;
        selectCluster(+node.dataset.cid);
      });
      if (draggable) {
        node.addEventListener('dragstart', (e) => {
          node.classList.add('dragging');
          e.dataTransfer.effectAllowed = 'move';
        });
        node.addEventListener('dragend', () => {
          node.classList.remove('dragging');
          persistClusterOrder();
        });
      }
    });
    opts.querySelectorAll('[data-ccolor]').forEach((inp) =>
      inp.addEventListener('change', () => setClusterColor(find(inp.dataset.ccolor), inp.value))
    );
    opts.querySelectorAll('[data-crename]').forEach((b) =>
      b.addEventListener('click', () => renameCluster(find(b.dataset.crename)))
    );
    opts.querySelectorAll('[data-cdel]').forEach((b) =>
      b.addEventListener('click', () => deleteCluster(find(b.dataset.cdel)))
    );
  }

  // O menu é `position: fixed` (escapa do overflow da sidebar) e é ancorado
  // ao botão por JS — assim pode ser mais largo que a sidebar e mostrar nomes
  // longos (ex.: contextos GKE) sem cortar.
  function positionClusterMenu() {
    const m = $('#cluster-menu');
    const r = $('#cluster-btn').getBoundingClientRect();
    const gap = 6;
    m.style.top = `${r.bottom + gap}px`;
    // alinhado à esquerda do botão, mas sem vazar pela direita da viewport
    const w = m.offsetWidth;
    const left = Math.min(r.left, window.innerWidth - w - 12);
    m.style.left = `${Math.max(12, left)}px`;
    // limita a altura ao espaço disponível abaixo do botão
    m.style.maxHeight = `${window.innerHeight - r.bottom - 24}px`;
  }

  // Elemento depois do qual o item arrastado deve ser inserido (pela posição do mouse).
  function dragAfterElement(container, y) {
    const els = [...container.querySelectorAll('.cluster-option:not(.dragging)')];
    let closest = { offset: -Infinity, el: null };
    for (const child of els) {
      const box = child.getBoundingClientRect();
      const offset = y - box.top - box.height / 2;
      if (offset < 0 && offset > closest.offset) closest = { offset, el: child };
    }
    return closest.el;
  }

  // Persiste a nova ordem (lida do DOM) e atualiza o estado local + sidebar.
  async function persistClusterOrder() {
    const ids = [...$('#cluster-options').querySelectorAll('.cluster-option')].map((n) => +n.dataset.cid);
    if (!ids.length) return;
    state.clusters.sort((a, b) => ids.indexOf(a.id) - ids.indexOf(b.id));
    renderClusters(); // reflete no botão/sidebar sem fechar o menu
    try {
      await api('/clusters/reorder', { method: 'POST', body: JSON.stringify({ order: ids }) });
    } catch (e) {
      window.toast('Falha ao salvar a ordem: ' + e.message, 'error');
    }
  }

  async function setClusterColor(c, color) {
    if (!c) return;
    try {
      await api(`/clusters/${c.id}`, { method: 'PATCH', body: JSON.stringify({ color }) });
      c.color = color; // atualiza local sem recarregar tudo
      renderClusters();
    } catch (e) {
      window.toast('Falha ao mudar a cor: ' + e.message, 'error');
    }
  }

  async function renameCluster(c) {
    if (!c) return;
    const name = prompt(`Novo nome para o cluster (contexto: ${c.context}):`, c.name);
    if (name === null) return;
    const trimmed = name.trim();
    if (!trimmed || trimmed === c.name) return;
    try {
      await api(`/clusters/${c.id}`, { method: 'PATCH', body: JSON.stringify({ name: trimmed }) });
      window.toast('Cluster renomeado', 'success');
      await loadClusters();
    } catch (e) {
      window.toast('Falha ao renomear: ' + e.message, 'error');
    }
  }

  // ---------- sessão por cluster (lembrar onde parei) ----------
  // Persiste, por cluster, a view atual + filtro de namespace + abas do dock
  // (logs/console/yaml/IA), para restaurar o mesmo ponto ao voltar ao cluster.
  const SESS_KEY = 'lensfy.sessions';

  function loadSessions() {
    try {
      return JSON.parse(localStorage.getItem(SESS_KEY) || '{}') || {};
    } catch (_) {
      return {};
    }
  }

  function persistSession() {
    if (!state.currentId) return;
    try {
      const all = loadSessions();
      const active = dockTab(state.dock.activeId);
      all[state.currentId] = {
        view: state.view,
        selectedNs: state.selectedNs,
        dock: {
          activeKey: active ? active.key : null,
          tabs: state.dock.tabs.map((t) => ({ type: t.type, title: t.title, key: t.key, meta: t.meta || {} })),
        },
      };
      localStorage.setItem(SESS_KEY, JSON.stringify(all));
    } catch (_) {}
  }

  // Restaura view + filtro de namespace do cluster e devolve o dock salvo
  // (reaberto depois, em restoreDock, pois precisa do objeto `cur` e do render).
  function restoreSession(id) {
    const sess = loadSessions()[id] || {};
    state.view = sess.view || 'dashboard';
    state.selectedNs = Array.isArray(sess.selectedNs) ? sess.selectedNs : [];
    return sess.dock || null;
  }

  // Reabre as abas do dock salvas (reestabelece os streams/sockets).
  function restoreDock(cur, dock) {
    if (!cur || !dock || !Array.isArray(dock.tabs) || !dock.tabs.length) return;
    dock.tabs.forEach((t) => reopenTab(cur, t));
    if (dock.activeKey) {
      const found = findDockTab(dock.activeKey);
      if (found) activateDockTab(found.id);
    }
  }

  function reopenTab(cur, t) {
    const m = t.meta || {};
    try {
      if (t.type === 'logs') showLogs(cur, m.name, m.ns, m.containers);
      else if (t.type === 'yaml') openYaml(cur, m.kind, m.name, m.ns);
      else if (t.type === 'ai') openAI(cur);
      else if (t.type === 'term') {
        if (m.mode === 'kubectl') openKubectlTerminal(cur);
        else if (m.mode === 'node') openNodeTerminal(cur, m.node);
        else openTerminal(cur, m.name, m.ns, m.containers);
      }
    } catch (_) {}
  }

  function selectCluster(id) {
    if (id === state.currentId) {
      $('#cluster-menu').hidden = true;
      return;
    }
    persistSession(); // salva onde parei no cluster que estou deixando
    closeDock(false); // encerra logs/console/yaml do cluster anterior (sem persistir)
    state.currentId = id;
    const dock = restoreSession(id); // restaura view + filtro de namespace
    $('#cluster-menu').hidden = true;
    renderClusters();
    renderTree();
    loadNamespaces();
    loadDiscovery();
    renderView();
    restoreDock(current(), dock); // reabre as abas onde parei neste cluster
  }

  // ---------- importar cluster (fonte -> detectar contextos -> selecionar) ----------
  const importState = { source: 'path', fileContent: '' };

  function openImport() {
    importState.source = 'path';
    importState.fileContent = '';
    $('#import-path').value = '';
    $('#import-paste').value = '';
    $('#import-file').value = '';
    $('#import-insecure').checked = false;
    $('#import-contexts').innerHTML = '';
    $('#import-do').disabled = true;
    selectImportSource('path');
    openModal('#import-modal');
  }

  function selectImportSource(src) {
    importState.source = src;
    $('#import-source')
      .querySelectorAll('[data-src]')
      .forEach((b) => b.classList.toggle('active', b.dataset.src === src));
    document.querySelectorAll('[data-srcpane]').forEach((p) => {
      p.hidden = p.dataset.srcpane !== src;
    });
    $('#import-contexts').innerHTML = '';
    $('#import-do').disabled = true;
    // gcloud tem seu próprio fluxo (projeto → listar); o "Detectar contextos" some.
    $('#import-detect').hidden = src === 'gcloud';
    if (src === 'gcloud') initGcloud();
  }

  // Carrega status do gcloud + lista de projetos no seletor.
  async function initGcloud() {
    const hint = $('#gcloud-hint');
    const sel = $('#gcloud-project');
    sel.innerHTML = '<option>Carregando…</option>';
    hint.textContent = '';
    try {
      const st = await api('/clusters/gcloud/status');
      if (!st.available) {
        sel.innerHTML = '';
        hint.innerHTML = `<span style="color:var(--color-danger)">${esc(st.message)}</span>`;
        return;
      }
      if (st.message) hint.innerHTML = `<span style="color:var(--color-warning)"><i class="fas fa-triangle-exclamation"></i> ${esc(st.message)}</span>`;
      const projs = await api('/clusters/gcloud/projects');
      sel.innerHTML = projs.map((p) => `<option value="${esc(p.project)}">${esc(p.name)} — ${esc(p.project)}</option>`).join('');
      if (!projs.length) hint.innerHTML = '<span style="color:var(--text-tertiary)">Nenhum projeto disponível para esta conta gcloud.</span>';
    } catch (e) {
      sel.innerHTML = '';
      hint.innerHTML = `<span style="color:var(--color-danger)">${esc(e.message)}</span>`;
    }
  }

  // Lista os clusters GKE do projeto selecionado como checklist.
  async function gcloudListClusters() {
    const project = $('#gcloud-project').value;
    if (!project) return window.toast('Selecione um projeto', 'warning');
    $('#import-contexts').innerHTML = '<div class="empty-state" style="height:auto;padding:var(--space-4)"><div class="spinner"></div></div>';
    $('#import-do').disabled = true;
    try {
      const cs = await api(`/clusters/gcloud/clusters?project=${encodeURIComponent(project)}`);
      if (!cs.length) {
        $('#import-contexts').innerHTML = '<p style="color:var(--text-tertiary)">Nenhum cluster GKE neste projeto.</p>';
        return;
      }
      $('#import-contexts').innerHTML =
        '<div class="form-hint">Selecione os clusters para importar:</div><div class="ctx-list">' +
        cs
          .map(
            (c) =>
              `<label class="ctx-item"><input type="checkbox" class="gke-cb" data-name="${esc(c.name)}" data-location="${esc(c.location || '')}" data-project="${esc(c.project)}" checked>` +
              `<div><div class="ctx-name">${esc(c.name)} <span class="badge">${esc(c.location || '')}</span></div>` +
              `<div class="ctx-meta">${esc(c.status || '')}${c.version ? ` · v${esc(c.version)}` : ''}${c.nodes != null ? ` · ${c.nodes} nós` : ''}</div></div></label>`
          )
          .join('') +
        '</div>';
      $('#import-do').disabled = false;
    } catch (e) {
      $('#import-contexts').innerHTML = `<p style="color:var(--color-danger)">${esc(e.message)}</p>`;
    }
  }

  function importSourcePayload() {
    if (importState.source === 'path') return { kubeconfig_path: $('#import-path').value.trim() || null };
    if (importState.source === 'paste') return { kubeconfig_content: $('#import-paste').value.trim() || null };
    return { kubeconfig_content: importState.fileContent || null };
  }

  async function detectContexts() {
    $('#import-contexts').innerHTML = '<div class="empty-state" style="height:auto;padding:var(--space-4)"><div class="spinner"></div></div>';
    $('#import-do').disabled = true;
    try {
      const ctxs = await api('/clusters/contexts', {
        method: 'POST',
        body: JSON.stringify(importSourcePayload()),
      });
      if (!ctxs.length) {
        $('#import-contexts').innerHTML = '<p style="color:var(--text-tertiary)">Nenhum contexto encontrado.</p>';
        return;
      }
      $('#import-contexts').innerHTML =
        '<div class="form-hint">Selecione os contextos para importar:</div><div class="ctx-list">' +
        ctxs
          .map(
            (c) =>
              `<label class="ctx-item"><input type="checkbox" class="ctx-cb" value="${esc(c.name)}" checked>` +
              `<div><div class="ctx-name">${esc(c.name)}</div>` +
              (c.cluster ? `<div class="ctx-meta">${esc(c.cluster)}</div>` : '') +
              `</div></label>`
          )
          .join('') +
        '</div>';
      $('#import-do').disabled = false;
    } catch (e) {
      $('#import-contexts').innerHTML = `<p style="color:var(--color-danger)">${esc(e.message)}</p>`;
    }
  }

  async function doImport() {
    if (importState.source === 'gcloud') return doImportGcloud();
    const checked = [...$('#import-contexts').querySelectorAll('.ctx-cb:checked')].map((c) => c.value);
    if (!checked.length) return window.toast('Selecione ao menos um contexto', 'warning');
    try {
      const created = await api('/clusters', {
        method: 'POST',
        body: JSON.stringify({
          ...importSourcePayload(),
          contexts: checked,
          insecure: $('#import-insecure').checked,
        }),
      });
      window.toast(`${created.length} cluster(s) importado(s)`, 'success');
      closeModal('#import-modal');
      state.currentId = created[0].id;
      state.selectedNs = [];
      await loadClusters(); // mostra imediatamente (status "unknown")
      // Atualiza status/versão em background (não trava se o cluster estiver lento/offline).
      Promise.allSettled(
        created.map((c) => api(`/clusters/${c.id}/refresh`, { method: 'POST' }))
      ).then(() => loadClusters());
    } catch (e) {
      window.toast('Importação falhou: ' + e.message, 'error');
    }
  }

  async function doImportGcloud() {
    const refs = [...$('#import-contexts').querySelectorAll('.gke-cb:checked')].map((c) => ({
      name: c.dataset.name,
      location: c.dataset.location,
      project: c.dataset.project,
    }));
    if (!refs.length) return window.toast('Selecione ao menos um cluster', 'warning');
    const btn = $('#import-do');
    btn.disabled = true;
    window.toast('Obtendo credenciais no gcloud…', 'info');
    try {
      const created = await api('/clusters/gcloud', {
        method: 'POST',
        body: JSON.stringify({ clusters: refs, insecure: $('#import-insecure').checked }),
      });
      window.toast(`${created.length} cluster(s) importado(s)`, 'success');
      closeModal('#import-modal');
      state.currentId = created[0].id;
      state.selectedNs = [];
      await loadClusters();
      Promise.allSettled(
        created.map((c) => api(`/clusters/${c.id}/refresh`, { method: 'POST' }))
      ).then(() => loadClusters());
    } catch (e) {
      window.toast('Importação falhou: ' + e.message, 'error');
      btn.disabled = false;
    }
  }

  async function refreshCluster() {
    const cur = current();
    if (!cur) return window.toast('Selecione um cluster primeiro', 'warning');
    try {
      await api(`/clusters/${cur.id}/refresh`, { method: 'POST' });
      window.toast('Cluster atualizado', 'success');
      await loadClusters();
    } catch (e) {
      window.toast('Falha ao atualizar: ' + e.message, 'error');
    }
  }

  async function deleteCluster(c) {
    if (!confirm(`Remover o cluster "${c.name}"?`)) return;
    try {
      await api(`/clusters/${c.id}`, { method: 'DELETE' });
      try {
        const all = loadSessions();
        delete all[c.id];
        localStorage.setItem(SESS_KEY, JSON.stringify(all));
      } catch (_) {}
      if (state.currentId === c.id) state.currentId = null;
      window.toast('Cluster removido', 'success');
      await loadClusters();
    } catch (e) {
      window.toast('Falha ao remover: ' + e.message, 'error');
    }
  }

  // ---------- tree ----------
  // Grupos da árvore são recolhíveis e começam FECHADOS; os que o usuário abre
  // ficam memorizados (localStorage), persistindo entre recargas.
  function expandedGroups() {
    if (!state._treeExpanded) {
      try {
        state._treeExpanded = new Set(JSON.parse(localStorage.getItem('lensfy.tree.expanded') || '[]'));
      } catch (_) {
        state._treeExpanded = new Set();
      }
    }
    return state._treeExpanded;
  }
  function toggleTreeGroup(key) {
    const exp = expandedGroups();
    if (exp.has(key)) exp.delete(key);
    else exp.add(key);
    try {
      localStorage.setItem('lensfy.tree.expanded', JSON.stringify([...exp]));
    } catch (_) {}
  }

  function renderTree() {
    const tree = $('#resource-tree');
    tree.innerHTML = '';
    const hasCluster = !!current();
    const exp = expandedGroups();
    const addGroup = (key, title, items, extraTitle) => {
      const open = exp.has(key);
      const head = el(
        `<div class="tree-group-title ${open ? '' : 'collapsed'}">` +
          `<i class="fas fa-chevron-down tree-caret"></i>` +
          `<span class="tree-grp-label">${esc(title)}</span>` +
          `<span class="tree-grp-count">${items.length}</span></div>`
      );
      if (extraTitle) head.title = extraTitle;
      head.addEventListener('click', () => {
        toggleTreeGroup(key);
        renderTree();
      });
      tree.appendChild(head);
      if (!open) return;
      items.forEach((it) => {
        const disabled = !hasCluster;
        const node = el(
          `<div class="tree-item ${it.id === state.view ? 'active' : ''} ${disabled ? 'disabled' : ''}">` +
            `<i class="fas ${it.icon}"></i><span>${esc(it.label)}</span></div>`
        );
        if (!disabled) {
          node.addEventListener('click', () => {
            state.view = it.id;
            renderTree();
            renderView();
            persistSession();
          });
        }
        tree.appendChild(node);
      });
    };
    TREE.forEach((grp) => addGroup(grp.group, grp.group, grp.items));
    // Grupos descobertos dinamicamente (CRDs e tipos não-curados do cluster).
    (state.discovered || []).forEach((grp) => addGroup('dyn:' + grp.group, grp.label, grp.items, grp.group));
  }

  // Kinds já cobertos pela árvore curada (com views dedicadas) — não duplicar.
  const CURATED_KINDS = new Set([
    'Pod', 'Deployment', 'StatefulSet', 'DaemonSet', 'Job', 'CronJob', 'Service',
    'Ingress', 'NetworkPolicy', 'ConfigMap', 'Secret', 'PersistentVolumeClaim',
    'StorageClass', 'Namespace', 'Node', 'Event', 'LimitRange', 'ResourceQuota',
    'Role', 'ClusterRole', 'RoleBinding', 'ClusterRoleBinding',
  ]);
  // Rótulo amigável para um API group.
  function groupLabel(group) {
    if (!group) return 'Core (extras)';
    const KNOWN = {
      'networking.istio.io': 'Istio · Networking',
      'security.istio.io': 'Istio · Security',
      'gateway.networking.k8s.io': 'Gateway API',
      'cert-manager.io': 'cert-manager',
      'argoproj.io': 'Argo',
      'monitoring.coreos.com': 'Prometheus Operator',
      'apiextensions.k8s.io': 'CRDs',
    };
    return KNOWN[group] || group;
  }
  const GROUP_ICON = {
    'gateway.networking.k8s.io': 'fa-door-open', 'networking.istio.io': 'fa-diagram-project',
    'cert-manager.io': 'fa-certificate', 'argoproj.io': 'fa-code-branch',
    'monitoring.coreos.com': 'fa-chart-line',
  };

  // Descobre dinamicamente os tipos de recurso do cluster e monta os grupos
  // extras da árvore (Gateway, VirtualService, etc.) — exclui os já curados.
  async function loadDiscovery() {
    const cur = current();
    state.discovered = [];
    state.discoveredMap = {};
    if (!cur) return renderTree();
    let data;
    try {
      data = await api(`/discovery?cluster_id=${cur.id}`);
    } catch (_) {
      return; // best-effort: a árvore curada continua funcionando
    }
    if (current() !== cur) return; // troca rápida de cluster
    const groups = [];
    (data.groups || []).forEach((g) => {
      const items = g.resources
        .filter((r) => !CURATED_KINDS.has(r.kind))
        .map((r) => {
          const id = `dyn:${r.apiVersion}:${r.kind}`;
          state.discoveredMap[id] = {
            apiVersion: r.apiVersion, kind: r.kind, namespaced: r.namespaced,
            name: r.name, group: r.group,
          };
          return { id, label: r.kind, icon: GROUP_ICON[r.group] || 'fa-cube' };
        });
      if (items.length) groups.push({ group: g.group, label: groupLabel(g.group), items });
    });
    state.discovered = groups;
    renderTree();
    // Sessão restaurada num recurso dinâmico: agora que o mapa existe, renderiza.
    if (state.view.startsWith('dyn:') && state.discoveredMap[state.view]) renderView();
  }

  // ---------- views ----------
  function setActions(html) {
    $('#view-actions').innerHTML = html || '';
  }
  // Ações padrão da view atual (ex.: botão "Criar"), restauradas quando a barra
  // de seleção em massa esvazia. {html, wire} — wire religa os listeners.
  let viewActions = null;
  function restoreViewActions() {
    setActions(viewActions ? viewActions.html : '');
    if (viewActions && viewActions.wire) viewActions.wire();
  }
  function loading() {
    $('#view-body').innerHTML = '<div class="empty-state"><div class="spinner"></div></div>';
  }
  function emptyState(icon, title, sub) {
    $('#view-body').innerHTML =
      `<div class="empty-state"><i class="fas ${icon}"></i><h3>${esc(title)}</h3>` +
      (sub ? `<p>${esc(sub)}</p>` : '') + '</div>';
  }

  async function renderView() {
    closeViewSocket(); // encerra stream da view anterior
    stopDetailMetrics(); // para o polling do drawer (5s) ao trocar de view
    $('#view-title').textContent = VIEW_LABELS[state.view] || 'Dashboard';
    viewActions = null; // cada view define as suas (ex.: "Criar")
    // Destaca o item do menu superior correspondente à view atual.
    [['nav-issues', 'issues'], ['nav-budget', 'budget'], ['nav-map', 'map'],
     ['nav-security', 'security'], ['nav-rbac', 'rbac'], ['nav-capacity', 'capacity'],
     ['nav-impact', 'impact'], ['nav-search', 'search']].forEach(([bid, v]) => {
      const b = $('#' + bid);
      if (b) b.classList.toggle('active', state.view === v);
    });
    setActions('');
    if (state.view !== 'crds') state.crdSel = null; // sai do drill-down de CRD
    // Busca global é entre clusters — não exige um cluster atual.
    if (state.view === 'search') return await viewSearch();
    const cur = current();
    if (!cur) {
      return emptyState('fa-binoculars', 'Bem-vindo ao Lensfy', 'Importe um kubeconfig para começar.');
    }
    try {
      if (state.view === 'dashboard') return await viewDashboard(cur);
      if (state.view === 'issues') return await viewIssues(cur);
      if (state.view === 'budget') return await viewBudget(cur);
      if (state.view === 'map') return await viewMap(cur);
      if (state.view === 'security') return await viewSecurity(cur);
      if (state.view === 'rbac') return await viewRBAC(cur);
      if (state.view === 'capacity') return await viewCapacity(cur);
      if (state.view === 'impact') return await viewImpact(cur);
      if (state.view === 'crds') return await viewCRDs(cur);
      if (state.view.startsWith('dyn:')) return await viewDiscovered(cur);
      if (state.view === 'metrics') return await viewMetrics(cur);
      if (state.view === 'pods') return await viewPods(cur);
      if (state.view === 'deployments') return await viewDeployments(cur);
      if (state.view === 'events') return viewEvents(cur);
      if (state.view === 'helm') return await viewHelm(cur);
      return await viewResource(cur, state.view);
    } catch (e) {
      emptyState('fa-circle-exclamation', 'Erro ao carregar', e.message);
    }
  }

  function liveBadge() {
    return '<span class="log-status"><span class="status-dot connected"></span> <span>ao vivo</span></span>';
  }

  // ---------- Assistente IA (chat agentico) ----------
  // Markdown minimalista e seguro: escapa tudo, depois aplica negrito/código/listas.
  function mdLite(t) {
    let h = esc(t || '');
    h = h.replace(/```([\s\S]*?)```/g, (m, c) => `<pre>${c.trim()}</pre>`);
    h = h.replace(/`([^`]+)`/g, '<code>$1</code>');
    h = h.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    h = h.replace(/^\s*[-*] (.*)$/gm, '• $1');
    return h.replace(/\n/g, '<br>');
  }

  // ---- Relatórios salvos (histórico) ----
  async function openReports() {
    openModal('#reports-modal');
    await renderReportList();
  }
  async function renderReportList() {
    const body = $('#reports-body');
    body.innerHTML = '<div class="empty-state" style="height:120px"><div class="spinner"></div></div>';
    let list;
    try {
      list = await api('/ai/reports');
    } catch (e) {
      body.innerHTML = `<p style="color:var(--color-danger)">${esc(e.message)}</p>`;
      return;
    }
    if (!list.length) {
      body.innerHTML = '<p style="color:var(--text-tertiary)">Nenhum relatório salvo ainda. Gere um diagnóstico e clique em “Salvar”.</p>';
      return;
    }
    body.innerHTML =
      '<div class="report-list">' +
      list
        .map(
          (r) =>
            `<div class="report-item" data-open="${r.id}"><div class="info">` +
            `<div class="t">${esc(r.title)}</div>` +
            `<div class="m">${r.cluster_name ? esc(r.cluster_name) + ' · ' : ''}${new Date(r.created_at).toLocaleString()}</div>` +
            `</div><button class="btn btn-sm btn-icon btn-danger" data-del="${r.id}" title="Excluir"><i class="fas fa-trash"></i></button></div>`
        )
        .join('') +
      '</div>';
    body.querySelectorAll('[data-open]').forEach((n) =>
      n.addEventListener('click', (e) => {
        if (e.target.closest('[data-del]')) return;
        openReport(+n.dataset.open);
      })
    );
    body.querySelectorAll('[data-del]').forEach((b) =>
      b.addEventListener('click', async () => {
        const ok = await uiConfirm({ title: 'Excluir relatório', danger: true, message: 'Remover este relatório salvo?', confirmLabel: 'Excluir' });
        if (!ok) return;
        try {
          await api(`/ai/reports/${b.dataset.del}`, { method: 'DELETE' });
          renderReportList();
        } catch (e) {
          window.toast('Falha ao excluir: ' + e.message, 'error');
        }
      })
    );
  }
  async function openReport(id) {
    const body = $('#reports-body');
    body.innerHTML = '<div class="empty-state" style="height:120px"><div class="spinner"></div></div>';
    let r;
    try {
      r = await api(`/ai/reports/${id}`);
    } catch (e) {
      body.innerHTML = `<p style="color:var(--color-danger)">${esc(e.message)}</p>`;
      return;
    }
    body.innerHTML =
      `<div class="report-detail-head"><button class="btn btn-sm" id="rep-back"><i class="fas fa-arrow-left"></i> Voltar</button>` +
      `<div class="report-meta">${r.cluster_name ? esc(r.cluster_name) + ' · ' : ''}${new Date(r.created_at).toLocaleString()}</div></div>` +
      `<h3 class="report-title">${esc(r.title)}</h3>` +
      `<div class="report-content ai-msg ai-bot">${mdLite(r.content)}</div>`;
    $('#rep-back').addEventListener('click', renderReportList);
  }

  const AI_SUGGESTIONS = [
    'Diagnostique a saúde do cluster e aponte os problemas.',
    'Quais pods estão com problema (CrashLoopBackOff, muitos restarts, não prontos)? Investigue a causa.',
    'Analise os eventos de alerta recentes e explique a causa provável.',
  ];

  const AI_TOOL_ICON = {
    cluster_overview: 'fa-gauge-high', list_resources: 'fa-table-list',
    get_resource: 'fa-file-code', get_pod_logs: 'fa-file-lines', top: 'fa-microchip',
    scale_workload: 'fa-up-down', restart_workload: 'fa-arrows-rotate',
    delete_pod: 'fa-trash', delete_resource: 'fa-trash',
    trigger_cronjob: 'fa-play', set_cronjob_suspend: 'fa-pause',
  };

  // Assistente de IA hospedado no dock inferior (aba 'ai', singleton por cluster).
  const aiInst = {};
  async function openAI(cur) {
    if (!cur) return window.toast('Selecione um cluster primeiro', 'warning');
    const { id, pane, isNew } = openOrFocusTab('ai', `ai|${cur.id}`, 'IA');
    if (!isNew) return; // já aberta: foca
    const els = {
      model: pane.querySelector('.ai-model'),
      save: pane.querySelector('.ai-save'),
      reports: pane.querySelector('.ai-reports'),
      msgs: pane.querySelector('.ai-messages'),
      input: pane.querySelector('.ai-input'),
      send: pane.querySelector('.ai-send'),
    };
    const inst = (aiInst[id] = { id, pane, els, socket: null, cur });

    let st;
    try {
      st = await api('/ai/status');
    } catch (e) {
      st = { available: false, message: e.message };
    }
    if (!aiInst[id]) return; // aba fechada durante o await
    if (!st.available) {
      els.msgs.innerHTML =
        `<div class="ai-hello"><div class="ai-hello-icon"><i class="fas fa-robot"></i></div>` +
        `<h3>Assistente IA indisponível</h3><p>${esc(st.message || 'Configure LENSFY_ANTHROPIC_API_KEY.')}</p></div>`;
      els.input.disabled = true;
      return;
    }
    els.model.textContent = st.model ? 'modelo: ' + st.model : '';
    els.msgs.innerHTML =
      `<div class="ai-hello"><div class="ai-hello-icon"><i class="fas fa-robot"></i></div>` +
      `<h3>Assistente de SRE</h3><p>Pergunte sobre o cluster <b>${esc(cur.name)}</b>. Eu investigo pods, logs, eventos e métricas — e posso executar ações (com sua aprovação).</p>` +
      '<div class="ai-suggest">' +
      AI_SUGGESTIONS.map((s) => `<button class="ai-chip" data-q="${esc(s)}">${esc(s)}</button>`).join('') +
      '</div></div>';

    const msgs = els.msgs;
    const input = els.input;
    const sendBtn = els.send;
    const steps = {}; // tool id -> elemento do passo
    const convo = []; // {role:'user'|'assistant'|'tool', text} para salvar como relatório
    let assistantBubble = null;
    let busy = false;
    const scrollDown = () => (msgs.scrollTop = msgs.scrollHeight);

    // ---- salvar / histórico ----
    els.reports.addEventListener('click', openReports);
    const saveBtn = els.save;
    function recordAssistant(text) {
      const last = convo[convo.length - 1];
      if (last && last.role === 'assistant') last.text += '\n\n' + text;
      else convo.push({ role: 'assistant', text });
      saveBtn.disabled = false;
    }
    saveBtn.addEventListener('click', async () => {
      if (!convo.some((c) => c.role === 'assistant')) return window.toast('Nada para salvar ainda', 'warning');
      const firstQ = (convo.find((c) => c.role === 'user') || {}).text || 'Relatório da IA';
      const md = convo
        .map((c) =>
          c.role === 'user' ? `\n\n**🧑 Pergunta:** ${c.text}\n` : c.role === 'tool' ? `\n> 🔧 ${c.text}\n` : `\n${c.text}\n`
        )
        .join('')
        .trim();
      try {
        await api('/ai/reports', {
          method: 'POST',
          body: JSON.stringify({ title: firstQ.slice(0, 80), content: md, cluster_id: cur.id, cluster_name: cur.name }),
        });
        window.toast('Relatório salvo', 'success');
      } catch (e) {
        window.toast('Falha ao salvar: ' + e.message, 'error');
      }
    });

    const sock = new WebSocket(wsUrl('/ws/ai', { cluster_id: cur.id }));
    inst.socket = sock;
    sock.onopen = () => {
      sendBtn.disabled = false;
    };
    sock.onclose = () => setBusy(false);

    function setBusy(b) {
      busy = b;
      sendBtn.disabled = b || sock.readyState !== 1;
      input.disabled = b;
      if (b) {
        assistantBubble = null;
        addThinking();
      } else {
        removeThinking();
      }
    }
    function addThinking() {
      removeThinking();
      msgs.insertAdjacentHTML('beforeend', '<div class="ai-msg ai-bot ai-thinking"><span class="dot"></span><span class="dot"></span><span class="dot"></span></div>');
      scrollDown();
    }
    function removeThinking() {
      const t = msgs.querySelector('.ai-thinking');
      if (t) t.remove();
    }
    function addUser(text) {
      const hello = msgs.querySelector('.ai-hello');
      if (hello) hello.remove();
      msgs.insertAdjacentHTML('beforeend', `<div class="ai-msg ai-user">${esc(text)}</div>`);
      convo.push({ role: 'user', text });
      scrollDown();
    }
    function botBubble() {
      removeThinking();
      if (!assistantBubble) {
        assistantBubble = el('<div class="ai-msg ai-bot"></div>');
        msgs.appendChild(assistantBubble);
      }
      return assistantBubble;
    }

    function handle(ev) {
      if (ev.type === 'text') {
        const b = botBubble();
        b.insertAdjacentHTML('beforeend', `<div class="ai-text">${mdLite(ev.text)}</div>`);
        recordAssistant(ev.text);
        if (busy) addThinking(); // mantém o indicador enquanto continua trabalhando
        scrollDown();
      } else if (ev.type === 'tool') {
        removeThinking();
        convo.push({ role: 'tool', text: ev.summary });
        const icon = AI_TOOL_ICON[ev.name] || 'fa-wrench';
        const node = el(
          `<div class="ai-step ${ev.mutating ? 'mut' : ''}"><i class="fas ${icon}"></i> <span class="s-sum">${esc(ev.summary)}</span> <span class="s-state"><span class="spinner-xs"></span></span></div>`
        );
        steps[ev.id] = node;
        msgs.appendChild(node);
        if (busy) addThinking();
        scrollDown();
      } else if (ev.type === 'tool_result') {
        const node = steps[ev.id];
        if (node) {
          const st2 = node.querySelector('.s-state');
          st2.innerHTML = ev.ok
            ? '<i class="fas fa-check" style="color:var(--color-success)"></i>'
            : `<i class="fas fa-xmark" style="color:var(--color-danger)"></i>`;
          if (ev.summary) node.title = ev.summary;
        }
        scrollDown();
      } else if (ev.type === 'approval_request') {
        removeThinking();
        const card = el(
          `<div class="ai-approval"><div class="ai-approval-h"><i class="fas fa-shield-halved"></i> A IA quer executar uma ação no cluster:</div>` +
          `<div class="ai-approval-act">${esc(ev.summary)}</div>` +
          `<div class="ai-approval-btns"><button class="btn btn-sm" data-deny>Negar</button>` +
          `<button class="btn btn-sm btn-primary" data-approve><i class="fas fa-check"></i> Aprovar</button></div></div>`
        );
        const decide = (approved) => {
          sock.send(JSON.stringify({ type: 'approval', id: ev.id, approved }));
          card.querySelector('.ai-approval-btns').innerHTML = `<span class="muted">${approved ? 'Aprovado ✓' : 'Negado ✗'}</span>`;
          if (busy) addThinking();
        };
        card.querySelector('[data-approve]').addEventListener('click', () => decide(true));
        card.querySelector('[data-deny]').addEventListener('click', () => decide(false));
        msgs.appendChild(card);
        scrollDown();
      } else if (ev.type === 'error') {
        botBubble().insertAdjacentHTML('beforeend', `<div class="ai-text" style="color:var(--color-danger)"><i class="fas fa-circle-exclamation"></i> ${esc(ev.message)}</div>`);
        scrollDown();
      } else if (ev.type === 'done') {
        setBusy(false);
      }
    }
    sock.onmessage = (e) => {
      try {
        handle(JSON.parse(e.data));
      } catch (_) {}
    };

    function send() {
      const text = input.value.trim();
      if (!text || busy || sock.readyState !== 1) return;
      addUser(text);
      input.value = '';
      input.style.height = 'auto';
      sock.send(JSON.stringify({ type: 'message', text }));
      setBusy(true);
    }
    sendBtn.addEventListener('click', send);
    // Permite que outros painéis (ex.: análise de logs) façam uma pergunta à IA.
    // Enfileira até o socket abrir, se necessário.
    inst.ask = (text) => {
      const run = () => { input.value = text; send(); };
      if (sock.readyState === 1) run();
      else sock.addEventListener('open', run, { once: true });
    };
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        send();
      }
    });
    input.addEventListener('input', () => {
      input.style.height = 'auto';
      input.style.height = Math.min(input.scrollHeight, 160) + 'px';
    });
    msgs.querySelectorAll('.ai-chip').forEach((c) =>
      c.addEventListener('click', () => {
        input.value = c.dataset.q;
        send();
      })
    );
    setTimeout(() => input.focus(), 50);
  }

  let podHistory = [];

  function sparkline(values, w, h) {
    if (values.length < 2) return '';
    const max = Math.max(...values, 1);
    const min = Math.min(...values, 0);
    const span = max - min || 1;
    const pts = values
      .map((v, i) => {
        const x = (i / (values.length - 1)) * w;
        const y = h - ((v - min) / span) * (h - 4) - 2;
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(' ');
    return (
      `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">` +
      `<polyline fill="none" stroke="var(--color-primary)" stroke-width="2" points="${pts}"/></svg>`
    );
  }

  function goView(id) {
    state.view = id;
    renderTree();
    renderView();
    persistSession();
  }

  const PHASE_COLORS = {
    Running: 'var(--color-success)', Succeeded: '#3498db',
    Pending: 'var(--color-warning)', Failed: 'var(--color-danger)',
    Unknown: 'var(--text-tertiary)',
  };
  const PHASE_ORDER = ['Running', 'Pending', 'Failed', 'Succeeded', 'Unknown'];

  // Barra (gauge) de uso com cor por severidade.
  function usageGauge(label, pct, sub) {
    const color = pct == null ? 'var(--text-tertiary)' : pct >= 90 ? 'var(--color-danger)' : pct >= 75 ? 'var(--color-warning)' : 'var(--color-success)';
    return (
      `<div class="gauge"><div class="gauge-top"><span>${esc(label)}</span><b>${pct == null ? '–' : pct + '%'}</b></div>` +
      `<div class="usage-bar"><div class="usage-fill" style="width:${Math.min(pct || 0, 100)}%;background:${color}"></div></div>` +
      `<div class="gauge-sub">${esc(sub || '')}</div></div>`
    );
  }

  function renderDashboard(cur, ov) {
    const c = ov.counts;
    const cards = [
      ['fa-server', c.nodes, 'Nodes', 'nodes'],
      ['fa-layer-group', c.namespaces, 'Namespaces', 'namespaces'],
      ['fa-cube', c.pods, 'Pods', 'pods'],
      ['fa-rocket', c.deployments, 'Deployments', 'deployments'],
      ['fa-network-wired', c.services, 'Services', 'services'],
      ['fa-globe', c.ingresses, 'Ingresses', 'ingress'],
    ];
    const statGrid =
      '<div class="metric-grid">' +
      cards
        .map(
          ([icon, val, label, view]) =>
            `<div class="metric-card clickable" data-go="${view}"><div class="icon"><i class="fas ${icon}"></i></div>` +
            `<div><div class="value">${esc(val)}</div><div class="label">${esc(label)}</div></div></div>`
        )
        .join('') +
      '</div>';

    // Saúde dos nós
    const nodesOk = ov.nodes.ready === ov.nodes.total;
    const nodeCard =
      `<div class="dash-card"><div class="dash-card-h"><i class="fas fa-server"></i> Nós</div>` +
      `<div class="big-stat ${nodesOk ? 'ok' : 'bad'}">${ov.nodes.ready}<span>/${ov.nodes.total}</span> <small>prontos</small></div>` +
      `<div class="usage-bar"><div class="usage-fill" style="width:${ov.nodes.total ? (ov.nodes.ready / ov.nodes.total) * 100 : 0}%;background:${nodesOk ? 'var(--color-success)' : 'var(--color-danger)'}"></div></div>` +
      `<div class="gauge-sub">${esc((ov.nodes.versions || []).join(', ') || '—')}</div></div>`;

    // Fases dos pods (barra empilhada + legenda)
    const ph = ov.pods.phases || {};
    const total = ov.pods.total || 1;
    const segs = PHASE_ORDER.filter((p) => ph[p])
      .map((p) => `<span class="seg" style="width:${(ph[p] / total) * 100}%;background:${PHASE_COLORS[p]}" title="${p}: ${ph[p]}"></span>`)
      .join('');
    const legend = PHASE_ORDER.filter((p) => ph[p])
      .map((p) => `<span class="legend-item"><span class="dot" style="background:${PHASE_COLORS[p]}"></span>${p} <b>${ph[p]}</b></span>`)
      .join('');
    const podBadges =
      (ov.pods.not_ready ? `<span class="pill warn">${ov.pods.not_ready} não prontos</span>` : '') +
      (ov.pods.restarts ? `<span class="pill">${ov.pods.restarts} restarts</span>` : '');
    const podCard =
      `<div class="dash-card"><div class="dash-card-h"><i class="fas fa-cube"></i> Pods <span class="muted">${ov.pods.total}</span></div>` +
      `<div class="phase-bar">${segs}</div>` +
      `<div class="legend">${legend}</div>` +
      (podBadges ? `<div class="pill-row">${podBadges}</div>` : '') +
      `</div>`;

    // Deployments
    const depOk = ov.deployments.unhealthy === 0;
    const depCard =
      `<div class="dash-card"><div class="dash-card-h"><i class="fas fa-rocket"></i> Deployments</div>` +
      `<div class="big-stat ${depOk ? 'ok' : 'bad'}">${ov.deployments.total - ov.deployments.unhealthy}<span>/${ov.deployments.total}</span> <small>saudáveis</small></div>` +
      (depOk
        ? '<div class="gauge-sub">Todos disponíveis</div>'
        : `<div class="gauge-sub" style="color:var(--color-warning)">${ov.deployments.unhealthy} com réplicas indisponíveis</div>`) +
      `</div>`;

    // Uso de CPU/Memória (metrics-server) ou aviso
    let usageCard;
    if (ov.usage && ov.usage.available) {
      const u = ov.usage;
      usageCard =
        `<div class="dash-card"><div class="dash-card-h"><i class="fas fa-microchip"></i> Uso do cluster</div>` +
        usageGauge('CPU', u.cpu_pct, `${(u.cpu_used / 1000).toFixed(1)} / ${(u.cpu_cap / 1000).toFixed(0)} cores`) +
        usageGauge('Memória', u.mem_pct, `${(u.mem_used / 1024).toFixed(1)} / ${(u.mem_cap / 1024).toFixed(0)} GiB`) +
        `</div>`;
    } else {
      usageCard =
        `<div class="dash-card"><div class="dash-card-h"><i class="fas fa-microchip"></i> Uso do cluster</div>` +
        `<div class="gauge-sub" style="padding:var(--space-3) 0">Metrics Server indisponível — instale-o para ver CPU/memória.</div></div>`;
    }

    const healthRow = `<div class="dash-row">${nodeCard}${podCard}${depCard}${usageCard}</div>`;

    // Gráfico de pods ao longo do tempo
    const chart =
      podHistory.length >= 2
        ? `<div class="dash-card"><div class="dash-card-h"><i class="fas fa-chart-line"></i> Pods ao longo do tempo</div>${sparkline(podHistory, 900, 90)}</div>`
        : '';

    // Eventos de alerta recentes
    const warns = ov.warnings || [];
    const warnList = warns.length
      ? warns
          .map(
            (w) =>
              `<div class="warn-item"><div class="warn-head">` +
              `<span class="warn-reason">${esc(w.reason || '')}</span>` +
              `<span class="warn-obj">${esc(w.object || '')}${w.namespace ? ` <span class="muted">· ${esc(w.namespace)}</span>` : ''}</span>` +
              (w.count > 1 ? `<span class="warn-count">×${w.count}</span>` : '') +
              `<span class="warn-age">${esc(ageOf(w.time))}</span></div>` +
              `<div class="warn-msg" title="${esc(w.message || '')}">${esc(w.message || '')}</div></div>`
          )
          .join('')
      : '<div class="gauge-sub" style="padding:var(--space-3)">Nenhum alerta recente. 🎉</div>';
    const warnCard =
      `<div class="dash-card"><div class="dash-card-h"><i class="fas fa-triangle-exclamation"></i> Eventos de alerta` +
      (ov.warnings_total ? ` <span class="muted">${ov.warnings_total}</span>` : '') +
      `</div><div class="warn-list">${warnList}</div></div>`;

    $('#view-body').innerHTML =
      `<div class="dash">${statGrid}${healthRow}${chart}${warnCard}</div>`;
    $('#view-body')
      .querySelectorAll('[data-go]')
      .forEach((n) => n.addEventListener('click', () => goView(n.dataset.go)));
  }

  async function viewDashboard(cur) {
    loading();
    podHistory = [];
    const load = async () => {
      let ov;
      try {
        ov = await api(`/metrics/overview?cluster_id=${cur.id}`);
      } catch (e) {
        if (!podHistory.length) emptyState('fa-circle-exclamation', 'Erro ao carregar o dashboard', e.message);
        return;
      }
      // A resposta pode chegar depois de navegar/trocar de cluster: não escreva
      // na DOM de outra view.
      if (state.view !== 'dashboard' || state.currentId !== cur.id) return;
      podHistory.push(ov.counts.pods);
      if (podHistory.length > 60) podHistory.shift();
      renderDashboard(cur, ov);
    };
    await load();
    setActions(liveBadge());
    state.viewTimer = setInterval(load, 10000); // atualiza a cada 10s
  }

  // ---------- Problemas (diagnóstico do cluster) ----------
  const ISSUE_ICON = {
    Nodes: 'fa-server', Pods: 'fa-cube', Workloads: 'fa-rocket',
    Jobs: 'fa-list-check', Storage: 'fa-hard-drive',
  };
  async function viewIssues(cur) {
    loading();
    const load = async () => {
      let data;
      try {
        data = await api(`/metrics/issues?cluster_id=${cur.id}`);
      } catch (e) {
        return emptyState('fa-circle-exclamation', 'Erro ao analisar o cluster', e.message);
      }
      if (state.view !== 'issues' || state.currentId !== cur.id) return; // resposta tardia
      // Respeita o filtro global de namespace (itens cluster-scoped sempre passam).
      const issues = (data.issues || []).filter((i) => !i.namespace || nsMatch(i.namespace));
      const crit = issues.filter((i) => i.severity === 'critical').length;
      const warn = issues.length - crit;
      setActions(
        `<span class="bulk-count" style="color:var(--color-danger)">${crit} crítico(s)</span> ` +
        `<span class="bulk-count" style="color:var(--color-warning,#d6a700)">${warn} alerta(s)</span> ` +
        liveBadge()
      );
      if (!issues.length) {
        return emptyState('fa-circle-check', 'Nenhum problema detectado', 'O cluster parece saudável.');
      }
      // Agrupa por categoria.
      const groups = {};
      issues.forEach((i) => (groups[i.category] = groups[i.category] || []).push(i));
      const sevBadge = (s) =>
        s === 'critical'
          ? '<span class="badge danger">crítico</span>'
          : '<span class="badge warning">alerta</span>';
      const sections = Object.keys(groups)
        .map((cat) => {
          const rows = groups[cat]
            .map(
              (i) =>
                `<tr data-kind="${esc(i.kind)}" data-name="${esc(i.name)}" data-ns="${esc(i.namespace || '')}" style="cursor:pointer">` +
                `<td>${sevBadge(i.severity)}</td>` +
                `<td>${i.namespace ? `<span class="badge">${esc(i.namespace)}</span> ` : ''}${esc(i.name)}</td>` +
                `<td><b>${esc(i.reason)}</b></td>` +
                `<td style="color:var(--text-secondary)">${esc(i.detail || '')}</td></tr>`
            )
            .join('');
          return (
            `<div class="dash-card" style="margin-bottom:var(--space-4)"><h3 style="margin:0 0 var(--space-2)">` +
            `<i class="fas ${ISSUE_ICON[cat] || 'fa-circle-dot'}"></i> ${esc(cat)} ` +
            `<span class="log-count">(${groups[cat].length})</span></h3>` +
            `<table class="data-table"><thead><tr><th>Severidade</th><th>Objeto</th><th>Motivo</th><th>Detalhe</th></tr></thead>` +
            `<tbody>${rows}</tbody></table></div>`
          );
        })
        .join('');
      $('#view-body').innerHTML = `<div class="issues">${sections}</div>`;
      // Clicar numa linha abre o drawer de detalhes do objeto.
      $('#view-body')
        .querySelectorAll('tr[data-kind]')
        .forEach((tr) =>
          tr.addEventListener('click', () =>
            openDetail(cur, tr.dataset.kind, tr.dataset.name, tr.dataset.ns || '', null, null)
          )
        );
    };
    await load();
    state.viewTimer = setInterval(load, 15000); // re-analisa a cada 15s
  }

  // ---------- Recursos (orçamento de requests/limits + risco de SLA) ----------
  const fmtCpu = (m) => (m >= 1000 ? (m / 1000).toFixed(m % 1000 ? 2 : 0) + ' cores' : m + ' m');
  const fmtMem = (mi) => (mi >= 1024 ? (mi / 1024).toFixed(1) + ' GiB' : Math.round(mi) + ' MiB');
  async function viewBudget(cur) {
    loading();
    const load = async () => {
      let data;
      try {
        data = await api(`/metrics/budget?cluster_id=${cur.id}`);
      } catch (e) {
        return emptyState('fa-circle-exclamation', 'Erro ao calcular o orçamento', e.message);
      }
      if (state.view !== 'budget' || state.currentId !== cur.id) return; // resposta tardia
      const rows = (data.rows || []).filter((r) => nsMatch(r.namespace));
      const risks = (data.risks || []).filter((r) => nsMatch(r.namespace));
      setActions(
        `<span class="bulk-count" style="color:var(--color-warning,#d6a700)">${risks.length} container(s) em risco</span> ` +
        liveBadge()
      );
      if (!rows.length) return emptyState('fa-scale-balanced', 'Sem dados de recursos');
      const head = '<th>Namespace</th><th>Pods</th><th>CPU req/lim</th><th>Memória req/lim</th><th>Sem requests</th><th>Sem limits</th>';
      const body = rows
        .map((r) => {
          const warn = (n) => (n ? `<span class="badge warning">${n}</span>` : '0');
          const q = r.quota ? ' <i class="fas fa-lock" title="ResourceQuota ativa"></i>' : '';
          return (
            `<tr data-ns="${esc(r.namespace)}" style="cursor:pointer">` +
            `<td><span class="badge">${esc(r.namespace)}</span>${q}</td>` +
            `<td>${r.pods}</td>` +
            `<td>${fmtCpu(r.cpu_req)} / ${r.cpu_lim ? fmtCpu(r.cpu_lim) : '—'}</td>` +
            `<td>${fmtMem(r.mem_req)} / ${r.mem_lim ? fmtMem(r.mem_lim) : '—'}</td>` +
            `<td>${warn(r.no_requests)}</td><td>${warn(r.no_limits)}</td></tr>`
          );
        })
        .join('');
      let html =
        '<div class="dash-card" style="margin-bottom:var(--space-4)"><h3 style="margin:0 0 var(--space-2)">' +
        '<i class="fas fa-scale-balanced"></i> Orçamento por namespace</h3>' +
        `<table class="data-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
      if (risks.length) {
        const rb = risks
          .slice(0, 200)
          .map(
            (r) =>
              `<tr data-kind="pods" data-name="${esc(r.pod)}" data-ns="${esc(r.namespace)}" style="cursor:pointer">` +
              `<td><span class="badge">${esc(r.namespace)}</span> ${esc(r.pod)}</td>` +
              `<td>${esc(r.container)}</td><td><span class="badge warning">${esc(r.reason)}</span></td></tr>`
          )
          .join('');
        html +=
          '<div class="dash-card"><h3 style="margin:0 0 var(--space-2)">' +
          '<i class="fas fa-triangle-exclamation"></i> Risco de OOM/SLA ' +
          `<span class="log-count">(${risks.length})</span></h3>` +
          '<p style="color:var(--text-tertiary);margin:0 0 var(--space-2)">Containers sem requests/limits — defina recursos para garantir escalonamento e evitar OOM.</p>' +
          `<table class="data-table"><thead><tr><th>Pod</th><th>Container</th><th>Risco</th></tr></thead><tbody>${rb}</tbody></table></div>`;
      }
      $('#view-body').innerHTML = `<div class="budget">${html}</div>`;
      $('#view-body')
        .querySelectorAll('tr[data-kind]')
        .forEach((tr) =>
          tr.addEventListener('click', () =>
            openDetail(cur, 'pods', tr.dataset.name, tr.dataset.ns, null, null)
          )
        );
    };
    await load();
    state.viewTimer = setInterval(load, 15000);
  }

  // ---------- Mapa de topologia de tráfego (SVG, Ingress→Service→Workload→Pods) ----------
  const MAP_COL = { ingress: 0, service: 1, workload: 2, pod: 3 };
  const MAP_HDR = ['Ingress', 'Services', 'Workloads', 'Pods'];
  function mapPodClass(s) {
    s = (s || '').toLowerCase();
    if (s === 'running' || s === 'succeeded') return 'ok';
    if (s === 'pending') return 'warn';
    if (s === 'failed' || s === 'unknown') return 'bad';
    return '';
  }
  async function viewMap(cur) {
    loading();
    const nsParam = state.selectedNs.length === 1 ? `&namespace=${encodeURIComponent(state.selectedNs[0])}` : '';
    let data;
    try {
      data = await api(`/metrics/topology?cluster_id=${cur.id}${nsParam}`);
    } catch (e) {
      return emptyState('fa-circle-exclamation', 'Erro ao montar o mapa', e.message);
    }
    if (state.view !== 'map' || state.currentId !== cur.id) return;
    let nodes = data.nodes || [];
    const edges = data.edges || [];
    // Em "todos os namespaces" o filtro global ainda recorta os nós namespaced.
    if (state.selectedNs.length) nodes = nodes.filter((n) => nsMatch(n.namespace));
    if (!nodes.length)
      return emptyState('fa-diagram-project', 'Sem topologia', 'Nenhum ingress/service/workload/pod neste escopo.');
    const ids = new Set(nodes.map((n) => n.id));
    const links = edges.filter((e) => ids.has(e.from) && ids.has(e.to));
    setActions(
      `<button class="btn btn-sm" id="map-refresh"><i class="fas fa-rotate"></i> Atualizar</button> ` +
      `<button class="btn btn-sm" id="map-fit" title="Centralizar"><i class="fas fa-compress"></i></button> ` +
      `<span class="log-status muted">${nodes.length} nós · ${links.length} ligações</span>`
    );

    // Layout em colunas.
    const COLW = 250, NW = 188, NH = 44, VGAP = 12, PADX = 24, PADY = 48;
    const byCol = [[], [], [], []];
    nodes.forEach((n) => byCol[MAP_COL[n.kind]].push(n));
    byCol.forEach((arr) => arr.sort((a, b) => (a.name || '').localeCompare(b.name || '')));
    const pos = {};
    let maxRows = 0;
    byCol.forEach((arr, ci) => {
      maxRows = Math.max(maxRows, arr.length);
      arr.forEach((n, ri) => (pos[n.id] = { x: PADX + ci * COLW, y: PADY + ri * (NH + VGAP) }));
    });
    const W = PADX * 2 + 3 * COLW + NW;
    const H = Math.max(PADY * 2 + maxRows * (NH + VGAP), 200);

    const trunc = (s, n) => (s && s.length > n ? s.slice(0, n - 1) + '…' : s || '');
    const edgeHtml = links
      .map((e) => {
        const a = pos[e.from], b = pos[e.to];
        const x1 = a.x + NW, y1 = a.y + NH / 2, x2 = b.x, y2 = b.y + NH / 2, mx = (x1 + x2) / 2;
        return `<path class="cmap-edge" d="M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}"/>`;
      })
      .join('');
    const WL_KIND = { Deployment: 'deployments', StatefulSet: 'statefulsets', DaemonSet: 'daemonsets', Job: 'jobs' };
    const detailKind = (n) =>
      n.kind === 'pod' ? 'pods' : n.kind === 'service' ? 'services' : n.kind === 'ingress' ? 'ingress' : (WL_KIND[n.subkind] || '');
    const nodeHtml = nodes
      .map((n) => {
        const p = pos[n.id];
        const sub = n.kind === 'workload' ? (n.subkind || '') : n.kind === 'service' ? (n.svc_type || '') : n.kind === 'pod' ? (n.status || '') : 'ingress';
        const cls = `cmap-node ${n.kind}` + (n.kind === 'pod' ? ' p-' + mapPodClass(n.status) : '');
        return (
          `<g class="${cls}" data-dkind="${detailKind(n)}" data-name="${esc(n.name)}" data-ns="${esc(n.namespace)}" transform="translate(${p.x},${p.y})">` +
          `<rect width="${NW}" height="${NH}" rx="7"/>` +
          `<text class="cmap-lbl" x="12" y="19">${esc(trunc(n.name, 24))}</text>` +
          `<text class="cmap-sub" x="12" y="34">${esc(sub)}</text></g>`
        );
      })
      .join('');
    const headers = MAP_HDR.map((h, i) =>
      `<text class="cmap-hdr" x="${PADX + i * COLW}" y="22">${h.toUpperCase()}</text>`
    ).join('');

    $('#view-body').innerHTML =
      '<div class="cmap-wrap" id="cmap-wrap">' +
      `<svg class="cmap-svg" id="cmap-svg"><g id="cmap-vp">${headers}<g>${edgeHtml}</g>${nodeHtml}</g></svg>` +
      '</div>';

    // Zoom/pan no <g id="cmap-vp">.
    const wrap = $('#cmap-wrap'), svg = $('#cmap-svg'), vp = $('#cmap-vp');
    const tf = { k: 1, tx: 0, ty: 0 };
    const apply = () => vp.setAttribute('transform', `translate(${tf.tx},${tf.ty}) scale(${tf.k})`);
    const fit = () => {
      const bw = wrap.clientWidth || W, bh = wrap.clientHeight || H;
      tf.k = Math.min(1, Math.min(bw / W, bh / H) * 0.95) || 1;
      tf.tx = Math.max(0, (bw - W * tf.k) / 2);
      tf.ty = 0;
      apply();
    };
    fit();
    svg.addEventListener('wheel', (e) => {
      e.preventDefault();
      const r = svg.getBoundingClientRect();
      const mx = e.clientX - r.left, my = e.clientY - r.top;
      const f = e.deltaY < 0 ? 1.1 : 1 / 1.1;
      const nk = Math.min(2.5, Math.max(0.2, tf.k * f));
      tf.tx = mx - (mx - tf.tx) * (nk / tf.k);
      tf.ty = my - (my - tf.ty) * (nk / tf.k);
      tf.k = nk;
      apply();
    }, { passive: false });
    let drag = null;
    // Pan listeners live only for the duration of a drag (added on mousedown,
    // removed on mouseup) so re-rendering the map doesn't leak a new pair of
    // permanent window listeners on every navigation/refresh.
    const onMove = (e) => {
      if (!drag) return;
      tf.tx = drag.tx + (e.clientX - drag.x);
      tf.ty = drag.ty + (e.clientY - drag.y);
      apply();
    };
    const onUp = () => {
      drag = null;
      wrap.classList.remove('grabbing');
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
    svg.addEventListener('mousedown', (e) => {
      if (e.target.closest('.cmap-node')) return; // clique no nó abre detalhe
      drag = { x: e.clientX, y: e.clientY, tx: tf.tx, ty: tf.ty };
      wrap.classList.add('grabbing');
      window.addEventListener('mousemove', onMove);
      window.addEventListener('mouseup', onUp);
    });
    svg.querySelectorAll('.cmap-node').forEach((g) =>
      g.addEventListener('click', () => {
        const dk = g.dataset.dkind;
        if (dk) openDetail(cur, dk, g.dataset.name, g.dataset.ns, null, null);
      })
    );
    $('#map-refresh').addEventListener('click', () => viewMap(cur));
    $('#map-fit').addEventListener('click', fit);
  }

  // ---------- Segurança (varredura de postura PSS-style) ----------
  function secSevBadge(s) {
    if (s === 'critical') return '<span class="badge danger">crítico</span>';
    if (s === 'warning') return '<span class="badge warning">alerta</span>';
    return '<span class="badge">info</span>';
  }
  async function viewSecurity(cur) {
    loading();
    const nsParam = state.selectedNs.length === 1 ? `&namespace=${encodeURIComponent(state.selectedNs[0])}` : '';
    const load = async () => {
      let data;
      try {
        data = await api(`/security/scan?cluster_id=${cur.id}${nsParam}`);
      } catch (e) {
        return emptyState('fa-circle-exclamation', 'Erro na varredura de segurança', e.message);
      }
      if (state.view !== 'security' || state.currentId !== cur.id) return;
      const findings = (data.findings || []).filter((f) => !f.namespace || nsMatch(f.namespace));
      const c = data.counts || {};
      const scoreColor = data.score >= 80 ? 'var(--color-success)' : data.score >= 50 ? 'var(--color-warning,#d6a700)' : 'var(--color-danger)';
      setActions(
        `<span class="bulk-count" style="color:${scoreColor}">score ${data.score}/100</span> ` +
        `<span class="bulk-count" style="color:var(--color-danger)">${c.critical || 0} crítico(s)</span> ` +
        `<span class="bulk-count" style="color:var(--color-warning,#d6a700)">${c.warning || 0} alerta(s)</span> ` +
        liveBadge()
      );
      if (!findings.length) {
        return emptyState('fa-shield-halved', 'Nenhum risco detectado', `${data.scanned} pod(s) analisado(s).`);
      }
      const groups = {};
      findings.forEach((f) => (groups[f.rule] = groups[f.rule] || []).push(f));
      const sections = Object.keys(groups).map((rule) => {
        const rows = groups[rule].map((f) =>
          `<tr data-name="${esc(f.pod)}" data-ns="${esc(f.namespace || '')}" style="cursor:pointer">` +
          `<td>${secSevBadge(f.severity)}</td>` +
          `<td>${f.namespace ? `<span class="badge">${esc(f.namespace)}</span> ` : ''}${esc(f.pod)}</td>` +
          `<td>${f.container ? esc(f.container) : '<span style="color:var(--text-tertiary)">—</span>'}</td>` +
          `<td style="color:var(--text-secondary)">${esc(f.detail || '')}</td></tr>`
        ).join('');
        return `<div class="dash-card" style="margin-bottom:var(--space-4)"><h3 style="margin:0 0 var(--space-2)">` +
          `<i class="fas fa-shield-halved"></i> ${esc(rule)} <span class="log-count">(${groups[rule].length})</span></h3>` +
          `<table class="data-table"><thead><tr><th>Severidade</th><th>Pod</th><th>Container</th><th>Detalhe</th></tr></thead>` +
          `<tbody>${rows}</tbody></table></div>`;
      }).join('');
      $('#view-body').innerHTML = `<div class="security">${sections}</div>`;
      $('#view-body').querySelectorAll('tr[data-name]').forEach((tr) =>
        tr.addEventListener('click', () =>
          openDetail(cur, 'pods', tr.dataset.name, tr.dataset.ns || '', null, null)));
    };
    await load();
    state.viewTimer = setInterval(load, 20000);
  }

  // ---------- RBAC (sujeitos + simulador can-i) ----------
  async function viewRBAC(cur) {
    loading();
    let data;
    try {
      data = await api(`/security/rbac/subjects?cluster_id=${cur.id}`);
    } catch (e) {
      return emptyState('fa-circle-exclamation', 'Erro ao carregar RBAC', e.message);
    }
    if (state.view !== 'rbac' || state.currentId !== cur.id) return;
    setActions(
      `<span class="bulk-count" style="color:var(--color-danger)">${data.cluster_admins} cluster-admin(s)</span> ` +
      `<span class="bulk-count">${data.total} sujeito(s)</span>`
    );
    const rows = (data.subjects || []).map((s, i) =>
      `<tr data-i="${i}" style="cursor:pointer">` +
      `<td><span class="badge">${esc(s.kind)}</span></td>` +
      `<td>${s.namespace ? `<span class="badge">${esc(s.namespace)}</span> ` : ''}<b>${esc(s.name)}</b>` +
      `${s.cluster_admin ? ' <span class="badge danger">cluster-admin</span>' : ''}</td>` +
      `<td>${s.binding_count}</td>` +
      `<td style="color:var(--text-secondary)">${esc((s.verbs || []).slice(0, 8).join(', '))}${(s.verbs || []).length > 8 ? '…' : ''}</td>` +
      `<td style="color:var(--text-secondary)">${esc((s.resources || []).slice(0, 8).join(', '))}${(s.resources || []).length > 8 ? '…' : ''}</td></tr>`
    ).join('');
    const simulator =
      `<div class="dash-card" style="margin-bottom:var(--space-4)"><h3 style="margin:0 0 var(--space-2)">` +
      `<i class="fas fa-vial"></i> Simulador “can-i”</h3>` +
      `<div style="display:flex;gap:var(--space-2);flex-wrap:wrap;align-items:center">` +
      `<input class="input input-inline" id="ci-verb" placeholder="verbo (get, create, *)" style="flex:0 1 160px">` +
      `<input class="input input-inline" id="ci-res" placeholder="recurso (pods, secrets)" style="flex:0 1 180px">` +
      `<input class="input input-inline" id="ci-ns" placeholder="namespace (opcional)" style="flex:0 1 170px">` +
      `<input class="input input-inline" id="ci-sa" placeholder="serviceaccount (opcional)" style="flex:0 1 210px">` +
      `<button class="btn btn-primary btn-sm" id="ci-run">Verificar</button>` +
      `<span id="ci-out"></span></div></div>`;
    const table =
      `<div class="dash-card"><h3 style="margin:0 0 var(--space-2)"><i class="fas fa-user-shield"></i> Sujeitos RBAC</h3>` +
      `<table class="data-table"><thead><tr><th>Tipo</th><th>Sujeito</th><th>Bindings</th><th>Verbos</th><th>Recursos</th></tr></thead>` +
      `<tbody>${rows}</tbody></table></div>`;
    $('#view-body').innerHTML = `<div class="rbac">${simulator}${table}</div>`;
    $('#ci-run').addEventListener('click', async () => {
      const verb = $('#ci-verb').value.trim();
      const resource = $('#ci-res').value.trim();
      const out = $('#ci-out');
      if (!verb || !resource) {
        out.innerHTML = '<span class="badge warning">informe verbo e recurso</span>';
        return;
      }
      out.innerHTML = '<span style="color:var(--text-tertiary)">verificando…</span>';
      try {
        const r = await api(`/security/rbac/can-i?cluster_id=${cur.id}`, {
          method: 'POST',
          body: JSON.stringify({
            verb, resource,
            namespace: $('#ci-ns').value.trim() || null,
            serviceaccount: $('#ci-sa').value.trim() || null,
          }),
        });
        out.innerHTML = r.allowed
          ? '<span class="badge success">permitido</span>'
          : '<span class="badge danger">negado</span>';
        if (r.reason) out.innerHTML += ` <span style="color:var(--text-tertiary)">${esc(r.reason)}</span>`;
      } catch (e) {
        out.innerHTML = `<span class="badge danger">${esc(e.message)}</span>`;
      }
    });
    $('#view-body').querySelectorAll('tr[data-i]').forEach((tr) =>
      tr.addEventListener('click', () => {
        const s = data.subjects[+tr.dataset.i];
        const binds = (s.bindings || []).map((b) =>
          `<tr><td>${esc(b.role)}</td><td>${esc(b.scope)}</td></tr>`).join('') ||
          '<tr><td colspan="2" style="color:var(--text-tertiary)">—</td></tr>';
        uiModal({
          title: `${s.kind}: ${s.name}`, icon: 'fa-user-shield', width: 'min(560px,94vw)',
          body:
            `<p style="margin:0 0 8px"><b>Verbos:</b> ${esc((s.verbs || []).join(', ') || '—')}</p>` +
            `<p style="margin:0 0 8px"><b>Recursos:</b> ${esc((s.resources || []).join(', ') || '—')}</p>` +
            `<table class="data-table"><thead><tr><th>Role</th><th>Escopo</th></tr></thead><tbody>${binds}</tbody></table>`,
          actions: [{ label: 'Fechar', cls: 'btn-primary', primary: true, value: 'ok' }],
        });
      }));
  }

  // ---------- Capacidade & Rightsizing ----------
  let capacityTab = 'capacity';
  async function viewCapacity(cur) {
    const chip = (v, l) => `<button class="btn btn-sm chip ${capacityTab === v ? 'active' : ''}" data-ctab="${v}">${l}</button>`;
    loading();
    setActions(chip('capacity', 'Capacidade') + chip('rightsizing', 'Rightsizing') + liveBadge());
    $('#view-actions').querySelectorAll('[data-ctab]').forEach((b) =>
      b.addEventListener('click', () => {
        if (capacityTab !== b.dataset.ctab) {
          capacityTab = b.dataset.ctab;
          viewCapacity(cur);
        }
      }));
    const load = capacityTab === 'rightsizing' ? loadRightsizing : loadCapacity;
    await load(cur);
    state.viewTimer = setInterval(() => {
      if (state.view === 'capacity' && state.currentId === cur.id) load(cur);
    }, 15000);
  }
  async function loadCapacity(cur) {
    let data;
    try {
      data = await api(`/capacity?cluster_id=${cur.id}`);
    } catch (e) {
      return emptyState('fa-circle-exclamation', 'Erro ao calcular capacidade', e.message);
    }
    if (state.view !== 'capacity' || state.currentId !== cur.id || capacityTab !== 'capacity') return;
    const t = data.totals || {};
    const dash = (pct, txt) => `${usageBar(pct)}<small style="color:var(--text-tertiary)">${txt}</small>`;
    const noMetrics = '<span style="color:var(--text-tertiary)">—</span>';
    const rows = (data.nodes || []).map((n) =>
      `<tr><td><b>${esc(n.name)}</b>${n.schedulable ? '' : ' <span class="badge warning">cordonado</span>'}</td>` +
      `<td>${dash(n.cpu_req_pct, `${fmtCpu(n.cpu_req)} / ${fmtCpu(n.cpu_alloc)}`)}</td>` +
      `<td>${t.metrics_available ? usageBar(n.cpu_use_pct) : noMetrics}</td>` +
      `<td>${dash(n.mem_req_pct, `${fmtMem(n.mem_req)} / ${fmtMem(n.mem_alloc)}`)}</td>` +
      `<td>${t.metrics_available ? usageBar(n.mem_use_pct) : noMetrics}</td>` +
      `<td>${n.pods}/${n.pod_cap || '—'}</td></tr>`
    ).join('');
    const stat = (label, value) =>
      `<div><div style="color:var(--text-tertiary);font-size:var(--font-size-sm)">${label}</div><b>${value}</b></div>`;
    const totCard =
      `<div class="dash-card" style="margin-bottom:var(--space-4)"><h3 style="margin:0 0 var(--space-2)">` +
      `<i class="fas fa-gauge"></i> Totais do cluster</h3>` +
      `<div style="display:flex;gap:var(--space-4);flex-wrap:wrap;align-items:flex-start">` +
      stat('CPU solicitada', `${fmtCpu(t.cpu_req || 0)} / ${fmtCpu(t.cpu_alloc || 0)}`) +
      stat('Memória solicitada', `${fmtMem(t.mem_req || 0)} / ${fmtMem(t.mem_alloc || 0)}`) +
      stat('Pods', `${t.pods || 0} / ${t.pod_cap || 0}`) +
      (t.metrics_available ? '' : `<div style="color:var(--text-tertiary)"><i class="fas fa-circle-info"></i> uso indisponível (sem metrics-server)</div>`) +
      `</div></div>`;
    $('#view-body').innerHTML = totCard +
      `<div class="dash-card"><h3 style="margin:0 0 var(--space-2)"><i class="fas fa-server"></i> Nós</h3>` +
      `<table class="data-table"><thead><tr><th>Nó</th><th>CPU solicitada</th><th>CPU uso</th><th>Mem solicitada</th><th>Mem uso</th><th>Pods</th></tr></thead>` +
      `<tbody>${rows}</tbody></table></div>`;
  }
  async function loadRightsizing(cur) {
    const nsParam = state.selectedNs.length === 1 ? `&namespace=${encodeURIComponent(state.selectedNs[0])}` : '';
    let data;
    try {
      data = await api(`/capacity/rightsizing?cluster_id=${cur.id}${nsParam}`);
    } catch (e) {
      return emptyState('fa-circle-exclamation', 'Erro no rightsizing', e.message);
    }
    if (state.view !== 'capacity' || state.currentId !== cur.id || capacityTab !== 'rightsizing') return;
    if (!data.available) {
      return emptyState('fa-gauge', 'Rightsizing indisponível', data.message || 'Instale o metrics-server para obter recomendações.');
    }
    const rows = (data.rows || []).filter((r) => nsMatch(r.namespace));
    if (!rows.length) return emptyState('fa-circle-check', 'Sem recomendações', 'Os recursos parecem adequados.');
    const body = rows.map((r) => {
      const verd = (r.verdict || []).map((v) => `<span class="badge warning">${esc(v)}</span>`).join(' ') || '<span class="badge success">ok</span>';
      return `<tr data-name="${esc(r.pod)}" data-ns="${esc(r.namespace)}" style="cursor:pointer">` +
        `<td><span class="badge">${esc(r.namespace)}</span> ${esc(r.pod)}</td>` +
        `<td>${fmtCpu(r.cpu_use || 0)}</td><td>${r.cpu_req ? fmtCpu(r.cpu_req) : '—'}</td><td>${r.cpu_rec ? fmtCpu(r.cpu_rec) : '—'}</td>` +
        `<td>${fmtMem(r.mem_use || 0)}</td><td>${r.mem_req ? fmtMem(r.mem_req) : '—'}</td><td>${r.mem_rec ? fmtMem(r.mem_rec) : '—'}</td>` +
        `<td>${verd}</td></tr>`;
    }).join('');
    $('#view-body').innerHTML =
      `<div class="dash-card"><h3 style="margin:0 0 var(--space-2)"><i class="fas fa-wand-magic-sparkles"></i> Recomendações de rightsizing</h3>` +
      `<p style="color:var(--text-tertiary);margin:0 0 var(--space-2)">Uso real (metrics-server) vs requests; recomendação ≈ 1,2× o uso atual.</p>` +
      `<table class="data-table"><thead><tr><th>Pod</th><th>CPU uso</th><th>CPU req</th><th>CPU rec</th><th>Mem uso</th><th>Mem req</th><th>Mem rec</th><th>Análise</th></tr></thead>` +
      `<tbody>${body}</tbody></table></div>`;
    $('#view-body').querySelectorAll('tr[data-name]').forEach((tr) =>
      tr.addEventListener('click', () => openDetail(cur, 'pods', tr.dataset.name, tr.dataset.ns, null, null)));
  }

  // ---------- CRDs / Custom Resources (descoberta dinâmica) ----------
  async function viewCRDs(cur) {
    if (state.crdSel) return loadCRDInstances(cur, state.crdSel);
    loading();
    let data;
    try {
      data = await api(`/crds?cluster_id=${cur.id}`);
    } catch (e) {
      return emptyState('fa-circle-exclamation', 'Erro ao listar CRDs', e.message);
    }
    if (state.view !== 'crds' || state.currentId !== cur.id) return;
    setActions(`<span class="bulk-count">${data.total} CRD(s)</span>`);
    if (!data.rows.length) {
      return emptyState('fa-cubes', 'Nenhuma CRD instalada', 'Operadores como ArgoCD/cert-manager registram CRDs aqui.');
    }
    const groups = {};
    data.rows.forEach((c) => (groups[c.group || '(core)'] = groups[c.group || '(core)'] || []).push(c));
    const sections = Object.keys(groups).sort().map((g) => {
      const rws = groups[g].map((c) =>
        `<tr data-crd='${esc(JSON.stringify({ group: c.group, version: c.version, plural: c.plural, kind: c.kind, scope: c.scope }))}' style="cursor:pointer">` +
        `<td><b>${esc(c.kind)}</b></td><td>${esc(c.plural)}</td><td>${esc(c.scope)}</td>` +
        `<td>${esc((c.versions || []).join(', '))}</td><td>${esc(c.age || '')}</td></tr>`
      ).join('');
      return `<div class="dash-card" style="margin-bottom:var(--space-4)"><h3 style="margin:0 0 var(--space-2)">` +
        `<i class="fas fa-cubes"></i> ${esc(g)} <span class="log-count">(${groups[g].length})</span></h3>` +
        `<table class="data-table"><thead><tr><th>Kind</th><th>Plural</th><th>Escopo</th><th>Versões</th><th>Idade</th></tr></thead>` +
        `<tbody>${rws}</tbody></table></div>`;
    }).join('');
    $('#view-body').innerHTML = `<div class="crds">${sections}</div>`;
    $('#view-body').querySelectorAll('tr[data-crd]').forEach((tr) =>
      tr.addEventListener('click', () => {
        state.crdSel = JSON.parse(tr.dataset.crd);
        loadCRDInstances(cur, state.crdSel);
      }));
  }
  async function loadCRDInstances(cur, crd) {
    loading();
    const namespaced = crd.scope === 'Namespaced';
    const nsParam = (namespaced && state.selectedNs.length === 1) ? `&namespace=${encodeURIComponent(state.selectedNs[0])}` : '';
    setActions(`<button class="btn btn-sm" id="crd-back"><i class="fas fa-arrow-left"></i> CRDs</button> <span class="bulk-count">${esc(crd.kind)}</span>`);
    $('#crd-back').addEventListener('click', () => { state.crdSel = null; viewCRDs(cur); });
    let data;
    try {
      data = await api(`/crds/instances?cluster_id=${cur.id}&group=${encodeURIComponent(crd.group)}&version=${encodeURIComponent(crd.version)}&plural=${encodeURIComponent(crd.plural)}${nsParam}`);
    } catch (e) {
      return emptyState('fa-circle-exclamation', 'Erro ao listar instâncias', e.message);
    }
    if (state.view !== 'crds' || state.currentId !== cur.id) return;
    const rows = (data.rows || []).filter((r) => !namespaced || !r.namespace || nsMatch(r.namespace));
    if (!rows.length) return emptyState('fa-cube', `Nenhum ${crd.kind}`, 'Nenhuma instância encontrada.');
    const body = rows.map((r) =>
      `<tr data-name="${esc(r.name)}" data-ns="${esc(r.namespace || '')}" style="cursor:pointer">` +
      `<td><b>${esc(r.name)}</b></td>${namespaced ? `<td><span class="badge">${esc(r.namespace || '')}</span></td>` : ''}<td>${esc(r.age || '')}</td></tr>`
    ).join('');
    $('#view-body').innerHTML =
      `<div class="dash-card"><h3 style="margin:0 0 var(--space-2)"><i class="fas fa-cube"></i> ${esc(crd.kind)} <span class="log-count">(${rows.length})</span></h3>` +
      `<table class="data-table"><thead><tr><th>Nome</th>${namespaced ? '<th>Namespace</th>' : ''}<th>Idade</th></tr></thead><tbody>${body}</tbody></table></div>`;
    // Reusa o editor unificado (ver/editar/salvar) por apiVersion+kind.
    $('#view-body').querySelectorAll('tr[data-name]').forEach((tr) =>
      tr.addEventListener('click', () => {
        const apiVersion = crd.group ? `${crd.group}/${crd.version}` : crd.version;
        openDynamicYaml(
          cur, { apiVersion, kind: crd.kind, namespaced: crd.scope === 'Namespaced' },
          tr.dataset.name, tr.dataset.ns || ''
        );
      }));
  }

  // ---------- recursos descobertos dinamicamente (Gateway, VirtualService…) ----------
  async function viewDiscovered(cur) {
    const desc = state.discoveredMap[state.view];
    if (!desc) return emptyState('fa-cube', 'Recurso não encontrado', 'Recarregue para atualizar a descoberta do cluster.');
    $('#view-title').textContent = desc.kind;
    loading();
    const namespaced = desc.namespaced;
    const nsParam = (namespaced && state.selectedNs.length === 1)
      ? `&namespace=${encodeURIComponent(state.selectedNs[0])}` : '';
    let data;
    try {
      data = await api(
        `/discovery/instances?cluster_id=${cur.id}` +
        `&apiVersion=${encodeURIComponent(desc.apiVersion)}&kind=${encodeURIComponent(desc.kind)}${nsParam}`
      );
    } catch (e) {
      return emptyState('fa-circle-exclamation', `Erro ao listar ${desc.kind}`, e.message);
    }
    if (state.view !== `dyn:${desc.apiVersion}:${desc.kind}` || state.currentId !== cur.id) return;
    setActions(`<span class="bulk-count">${esc(desc.apiVersion)}</span>`);
    const rows = (data.rows || []).filter((r) => !namespaced || !r.namespace || nsMatch(r.namespace));
    if (!rows.length) return emptyState('fa-cube', `Nenhum ${desc.kind}`, 'Nenhuma instância encontrada.');
    const body = rows.map((r) =>
      `<tr data-name="${esc(r.name)}" data-ns="${esc(r.namespace || '')}" style="cursor:pointer">` +
      `<td><b>${esc(r.name)}</b></td>${namespaced ? `<td><span class="badge">${esc(r.namespace || '')}</span></td>` : ''}` +
      `<td>${esc(r.age || '')}</td></tr>`).join('');
    $('#view-body').innerHTML =
      `<div class="dash-card"><h3 style="margin:0 0 var(--space-2)"><i class="fas fa-cube"></i> ` +
      `${esc(desc.kind)} <span class="log-count">(${rows.length})</span></h3>` +
      `<table class="data-table"><thead><tr><th>Nome</th>${namespaced ? '<th>Namespace</th>' : ''}<th>Idade</th></tr></thead>` +
      `<tbody>${body}</tbody></table></div>`;
    $('#view-body').querySelectorAll('tr[data-name]').forEach((tr) =>
      tr.addEventListener('click', () => openDynamicYaml(cur, desc, tr.dataset.name, tr.dataset.ns || '')));
  }
  // Visualiza E edita um recurso descoberto: editor Monaco + Salvar (server-side
  // apply, que funciona para qualquer apiVersion/kind, inclusive CRDs).
  function openDynamicYaml(cur, desc, name, ns) {
    let editor = null;
    const disposeEd = () => { try { editor && editor.dispose(); } catch (_) {} editor = null; };
    uiModal({
      title: `${desc.kind}: ${name}`, icon: 'fa-file-code', width: 'min(960px,96vw)',
      body:
        '<div id="dyn-yaml" style="height:60vh;border:1px solid var(--border-color);border-radius:var(--radius-md);overflow:hidden">' +
        '<div class="empty-state" style="height:100%"><div class="spinner"></div></div></div>' +
        '<div id="dyn-yaml-status" style="margin-top:8px;min-height:18px;font-size:var(--font-size-sm);color:var(--text-tertiary)"></div>',
      actions: [
        { label: 'Fechar', onClick: () => { disposeEd(); return 'close'; } },
        {
          label: '<i class="fas fa-rocket"></i> Salvar', cls: 'btn-primary', primary: true,
          onClick: (bd) => { if (bd._dynSave) bd._dynSave(); /* undefined: mantém aberto */ },
        },
      ],
      onOpen: async (bd, done) => {
        const box = bd.querySelector('#dyn-yaml');
        const status = bd.querySelector('#dyn-yaml-status');
        const q = `cluster_id=${cur.id}&apiVersion=${encodeURIComponent(desc.apiVersion)}` +
          `&kind=${encodeURIComponent(desc.kind)}&name=${encodeURIComponent(name)}` +
          (ns ? `&namespace=${encodeURIComponent(ns)}` : '');
        let yamlText;
        try {
          yamlText = (await api(`/discovery/manifest?${q}`)).yaml;
        } catch (e) {
          box.innerHTML = `<p style="color:var(--color-danger);padding:var(--space-3)">${esc(e.message)}</p>`;
          return;
        }
        let monaco;
        try {
          monaco = await ensureMonaco();
        } catch (e) {
          box.innerHTML = `<p style="color:var(--color-danger);padding:var(--space-3)">Editor indisponível: ${esc(e.message)}</p>`;
          return;
        }
        box.innerHTML = '';
        editor = monaco.editor.create(box, {
          value: yamlText, language: 'yaml', theme: 'vs-dark', automaticLayout: true,
          minimap: { enabled: false }, fontSize: 13, tabSize: 2, scrollBeyondLastLine: false,
        });
        bd._dynSave = async () => {
          if (!editor) return;
          const text = editor.getValue().trim();
          if (!text) return window.toast('Nada para salvar', 'warning');
          status.innerHTML = '<span class="status-dot unknown"></span> salvando…';
          try {
            const r = await api(`/resources/apply?cluster_id=${cur.id}`, {
              method: 'POST',
              body: JSON.stringify({ yaml: text, namespace: ns || 'default' }),
            });
            const bad = (r.results || []).find((x) => x.status === 'error');
            if (bad) {
              status.innerHTML = `<span style="color:var(--color-danger)"><i class="fas fa-circle-xmark"></i> ${esc(bad.message || 'erro ao aplicar')}</span>`;
              return;
            }
            window.toast(`${desc.kind} ${name} salvo`, 'success');
            disposeEd();
            done('saved');
            renderView(); // reflete a alteração na lista
          } catch (e) {
            status.innerHTML = `<span style="color:var(--color-danger)"><i class="fas fa-circle-xmark"></i> ${esc(e.message)}</span>`;
          }
        };
      },
    });
  }

  // ---------- Análise de impacto / blast radius (busca reversa) ----------
  let impactKind = 'configmaps';
  const IMPACT_KINDS = [
    { id: 'configmaps', label: 'ConfigMap' },
    { id: 'secrets', label: 'Secret' },
    { id: 'pvc', label: 'PVC' },
    { id: 'nodes', label: 'Node' },
  ];
  const WL_TREE = {
    Deployment: 'deployments', StatefulSet: 'statefulsets', DaemonSet: 'daemonsets',
    Job: 'jobs', CronJob: 'cronjobs', Pod: 'pods',
  };
  async function viewImpact(cur) {
    setActions('');
    const chips = IMPACT_KINDS.map((k) =>
      `<button class="btn btn-sm chip ${impactKind === k.id ? 'active' : ''}" data-ikind="${k.id}">${k.label}</button>`).join('');
    $('#view-body').innerHTML =
      `<div class="impact-view"><div class="dash-card" style="margin-bottom:var(--space-4)">` +
      `<h3 style="margin:0 0 var(--space-2)"><i class="fas fa-bullseye"></i> Análise de impacto (blast radius)</h3>` +
      `<p style="color:var(--text-tertiary);margin:0 0 var(--space-3)">Quem depende de um recurso e o que é afetado se ele cair. ` +
      `ConfigMap/Secret/PVC mostram os consumidores; Node mostra o raio de impacto e workloads SPOF.</p>` +
      `<div style="display:flex;gap:var(--space-2);flex-wrap:wrap;align-items:center">${chips}` +
      `<select class="input input-inline" id="imp-res" style="flex:0 1 320px"><option>carregando…</option></select>` +
      `<button class="btn btn-primary btn-sm" id="imp-run">Analisar</button></div></div>` +
      `<div id="imp-results"></div></div>`;
    $('#view-body').querySelectorAll('[data-ikind]').forEach((b) =>
      b.addEventListener('click', () => {
        if (impactKind !== b.dataset.ikind) { impactKind = b.dataset.ikind; viewImpact(cur); }
      }));
    await loadImpactOptions(cur);
    $('#imp-run').addEventListener('click', () => runImpact(cur));
  }
  async function loadImpactOptions(cur) {
    const sel = $('#imp-res');
    try {
      const data = await api(`/resources?cluster_id=${cur.id}&kind=${impactKind}`);
      let rows = data.rows || [];
      const namespaced = data.namespaced;
      if (namespaced) rows = rows.filter((r) => nsMatch(r.namespace));
      if (!rows.length) { sel.innerHTML = '<option value="">(nenhum recurso)</option>'; return; }
      sel.innerHTML = rows.map((r) => {
        const val = namespaced ? `${r.namespace}|${r.name}` : `|${r.name}`;
        const label = namespaced ? `${r.namespace}/${r.name}` : r.name;
        return `<option value="${esc(val)}">${esc(label)}</option>`;
      }).join('');
    } catch (e) {
      sel.innerHTML = `<option value="">erro: ${esc(e.message)}</option>`;
    }
  }
  async function runImpact(cur) {
    const val = $('#imp-res').value;
    const box = $('#imp-results');
    if (!val) { box.innerHTML = ''; return; }
    const [ns, name] = val.split('|');
    box.innerHTML = '<div class="empty-state" style="height:120px"><div class="spinner"></div></div>';
    let data;
    try {
      const nsq = ns ? `&namespace=${encodeURIComponent(ns)}` : '';
      data = await api(`/impact?cluster_id=${cur.id}&kind=${impactKind}&name=${encodeURIComponent(name)}${nsq}`);
    } catch (e) {
      box.innerHTML = `<p style="color:var(--color-danger)">${esc(e.message)}</p>`;
      return;
    }
    if (state.view !== 'impact' || state.currentId !== cur.id) return;
    renderImpact(cur, data);
  }
  function renderImpact(cur, data) {
    const box = $('#imp-results');
    const s = data.summary || {};
    const wls = data.workloads || [];
    const isNode = data.target.kind === 'nodes';
    let html =
      `<div class="dash-card" style="margin-bottom:var(--space-4)"><div style="display:flex;gap:var(--space-4);flex-wrap:wrap;align-items:center">` +
      `<span class="bulk-count">${s.pods || 0} pod(s) afetado(s)</span>` +
      `<span class="bulk-count">${s.workloads || 0} workload(s)</span>` +
      (isNode ? `<span class="bulk-count" style="color:var(--color-danger)">${s.spof || 0} SPOF</span>` : '') +
      `</div></div>`;
    if (isNode && (data.spof || []).length) {
      const rows = data.spof.map((w) =>
        `<tr data-kind="${esc(WL_TREE[w.kind] || '')}" data-name="${esc(w.name)}" data-ns="${esc(w.namespace || '')}" style="cursor:pointer">` +
        `<td><span class="badge danger">SPOF</span></td><td>${esc(w.kind)}</td>` +
        `<td>${w.namespace ? `<span class="badge">${esc(w.namespace)}</span> ` : ''}<b>${esc(w.name)}</b></td>` +
        `<td>${w.pods}/${w.replicas} réplica(s) neste nó</td></tr>`).join('');
      html += `<div class="dash-card" style="margin-bottom:var(--space-4)"><h3 style="margin:0 0 var(--space-2)">` +
        `<i class="fas fa-triangle-exclamation"></i> Single point of failure</h3>` +
        `<p style="color:var(--text-tertiary);margin:0 0 var(--space-2)">Todas as réplicas destes workloads estão neste nó — drená-lo ou perdê-lo derruba o serviço.</p>` +
        `<table class="data-table"><thead><tr><th></th><th>Tipo</th><th>Workload</th><th>Réplicas</th></tr></thead><tbody>${rows}</tbody></table></div>`;
    }
    if (!wls.length) {
      html += `<div class="dash-card"><div class="empty-state" style="height:140px"><i class="fas fa-circle-check"></i>` +
        `<h3>${isNode ? 'Nenhum pod neste nó' : 'Nenhum consumidor'}</h3>` +
        `<p>${isNode ? '' : 'O recurso não é referenciado por nenhum pod.'}</p></div></div>`;
    } else {
      const rows = wls.map((w) =>
        `<tr data-kind="${esc(WL_TREE[w.kind] || '')}" data-name="${esc(w.name)}" data-ns="${esc(w.namespace || '')}" style="cursor:pointer">` +
        `<td>${esc(w.kind)}</td><td>${w.namespace ? `<span class="badge">${esc(w.namespace)}</span> ` : ''}<b>${esc(w.name)}</b></td>` +
        `<td>${w.pods}</td><td>${(w.via || []).map((v) => `<span class="badge">${esc(v)}</span>`).join(' ')}</td></tr>`).join('');
      html += `<div class="dash-card"><h3 style="margin:0 0 var(--space-2)"><i class="fas fa-diagram-project"></i> ` +
        `${isNode ? 'Workloads no nó' : 'Consumidores'} <span class="log-count">(${wls.length})</span></h3>` +
        `<table class="data-table"><thead><tr><th>Tipo</th><th>Workload</th><th>Pods</th><th>${isNode ? 'Relação' : 'Como referencia'}</th></tr></thead>` +
        `<tbody>${rows}</tbody></table></div>`;
    }
    box.innerHTML = html;
    box.querySelectorAll('tr[data-name]').forEach((tr) =>
      tr.addEventListener('click', () => {
        if (tr.dataset.kind) openDetail(cur, tr.dataset.kind, tr.dataset.name, tr.dataset.ns || '', null, null);
      }));
  }

  // ---------- Busca global (entre clusters) ----------
  let searchGlobalQuery = '';
  async function viewSearch() {
    setActions('');
    $('#view-body').innerHTML =
      `<div class="global-search-view">` +
      `<div class="dash-card" style="margin-bottom:var(--space-4)"><h3 style="margin:0 0 var(--space-2)">` +
      `<i class="fas fa-magnifying-glass"></i> Busca global (todos os clusters)</h3>` +
      `<div style="display:flex;gap:var(--space-2);align-items:center">` +
      `<input class="input" id="gsv-input" placeholder="nome do recurso (pods, services, deployments…)" value="${esc(searchGlobalQuery)}" style="flex:1" autofocus>` +
      `<button class="btn btn-primary" id="gsv-run">Buscar</button></div>` +
      `<p style="color:var(--text-tertiary);margin:var(--space-2) 0 0">Procura por substring do nome em todos os clusters registrados.</p></div>` +
      `<div id="gsv-results"></div></div>`;
    const run = async () => {
      const term = $('#gsv-input').value.trim();
      searchGlobalQuery = term;
      const box = $('#gsv-results');
      if (!term) { box.innerHTML = ''; return; }
      box.innerHTML = '<div class="empty-state" style="height:120px"><div class="spinner"></div></div>';
      let data;
      try {
        data = await api(`/multicluster/search?q=${encodeURIComponent(term)}`);
      } catch (e) {
        box.innerHTML = `<p style="color:var(--color-danger)">${esc(e.message)}</p>`;
        return;
      }
      if (state.view !== 'search') return;
      const res = data.results || [];
      let html = `<div class="dash-card"><h3 style="margin:0 0 var(--space-2)">${data.total} resultado(s)${data.truncated ? ` (mostrando ${res.length})` : ''}</h3>`;
      if (!res.length) {
        html += '<p style="color:var(--text-tertiary)">Nada encontrado.</p>';
      } else {
        const rows = res.map((r) =>
          `<tr data-cid="${r.cluster_id}" data-kind="${esc(r.kind)}" data-name="${esc(r.name)}" data-ns="${esc(r.namespace || '')}" style="cursor:pointer">` +
          `<td><span class="badge">${esc(r.cluster_name)}</span></td><td>${esc(r.kind)}</td>` +
          `<td>${r.namespace ? `<span class="badge">${esc(r.namespace)}</span> ` : ''}<b>${esc(r.name)}</b></td></tr>`
        ).join('');
        html += `<table class="data-table"><thead><tr><th>Cluster</th><th>Tipo</th><th>Recurso</th></tr></thead><tbody>${rows}</tbody></table>`;
      }
      html += '</div>';
      if ((data.errors || []).length) {
        html += `<div class="dash-card" style="margin-top:var(--space-4)"><h3 style="margin:0 0 var(--space-2)">` +
          `<i class="fas fa-triangle-exclamation"></i> Clusters inacessíveis</h3>` +
          data.errors.map((e) => `<div style="color:var(--text-secondary)"><span class="badge warning">${esc(e.cluster_name)}</span> ${esc(e.message)}</div>`).join('') +
          `</div>`;
      }
      box.innerHTML = html;
      box.querySelectorAll('tr[data-cid]').forEach((tr) =>
        tr.addEventListener('click', () => {
          state.currentId = +tr.dataset.cid;
          const cur = current();
          renderClusters();
          if (cur) openDetail(cur, tr.dataset.kind, tr.dataset.name, tr.dataset.ns || '', null, null);
        }));
    };
    $('#gsv-run').addEventListener('click', run);
    $('#gsv-input').addEventListener('keydown', (e) => { if (e.key === 'Enter') run(); });
    if (searchGlobalQuery) run();
  }

  // Métricas de uso (CPU/Memória) via metrics-server.
  let metricsKind = 'nodes';
  let metricsSort = 'cpu';   // coluna de ordenação
  let metricsDesc = true;    // ordem decrescente
  let metricsQuery = '';     // filtro por nome/namespace
  let metricsData = null;    // últimas linhas buscadas (sem filtro de busca)

  const fmtCores = (m) => (m / 1000).toFixed(m >= 1000 ? 1 : 2) + ' cores';
  const fmtGiB = (mi) => (mi / 1024).toFixed(mi >= 1024 ? 1 : 2) + ' GiB';
  function usageBar(pct) {
    if (pct == null) return '<span style="color:var(--text-tertiary)">—</span>';
    const cls = pct >= 85 ? 'mc-bad' : pct >= 60 ? 'mc-warn' : 'mc-ok';
    return `<div class="usage-bar ${cls}"><div class="usage-fill" style="width:${Math.min(pct, 100)}%"></div><span>${pct}%</span></div>`;
  }
  function relBar(val, max, label) {
    const w = max ? Math.round((val / max) * 100) : 0;
    return `<div class="usage-bar mc-ok"><div class="usage-fill" style="width:${w}%"></div><span>${esc(label)}</span></div>`;
  }

  async function viewMetrics(cur) {
    const chip = (val, label) =>
      `<button class="btn btn-sm chip ${metricsKind === val ? 'active' : ''}" data-mkind="${val}">${label}</button>`;
    setActions(
      chip('nodes', 'Nodes') + chip('pods', 'Pods') +
      `<input class="input input-inline" id="metrics-search" placeholder="filtrar…" value="${esc(metricsQuery)}" style="flex:0 1 200px"> ` +
      liveBadge()
    );
    $('#view-actions')
      .querySelectorAll('[data-mkind]')
      .forEach((b) =>
        b.addEventListener('click', () => {
          if (metricsKind === b.dataset.mkind) return;
          metricsKind = b.dataset.mkind;
          metricsSort = 'cpu';
          metricsDesc = true;
          metricsQuery = '';
          metricsData = null;
          renderView();
        })
      );
    $('#metrics-search').addEventListener('input', (e) => {
      metricsQuery = e.target.value;
      renderMetrics();
    });

    const sortRows = (rows) => {
      const k = metricsSort, dir = metricsDesc ? -1 : 1;
      return rows.slice().sort((a, b) => {
        const av = a[k], bv = b[k];
        if (typeof av === 'number' || typeof bv === 'number') return ((av || 0) - (bv || 0)) * dir;
        return String(av || '').localeCompare(String(bv || '')) * dir;
      });
    };

    function renderMetrics() {
      if (!metricsData || state.view !== 'metrics' || state.currentId !== cur.id) return;
      const nodesView = metricsKind === 'nodes';
      const all = metricsData;
      const q = metricsQuery.trim().toLowerCase();
      let rows = q
        ? all.filter((r) => (r.name || '').toLowerCase().includes(q) || (r.namespace || '').toLowerCase().includes(q))
        : all;
      rows = sortRows(rows);
      if (!all.length) return emptyState('fa-chart-line', 'Sem métricas');

      // Cards-resumo (escopo = todos os itens, não filtrados pela busca).
      const sum = (f) => all.reduce((t, r) => t + (f(r) || 0), 0);
      const card = (label, value, sub, bar) =>
        `<div class="mc-card"><div class="mc-label">${label}</div><div class="mc-value">${value}</div>` +
        (sub ? `<div class="mc-sub">${sub}</div>` : '') + (bar || '') + '</div>';
      let cards;
      if (nodesView) {
        const cu = sum((r) => r.cpu), cc = sum((r) => r.cpu_cap);
        const mu = sum((r) => r.memory), mc = sum((r) => r.memory_cap);
        const cpct = cc ? Math.round((cu / cc) * 100) : null;
        const mpct = mc ? Math.round((mu / mc) * 100) : null;
        cards =
          card('CPU do cluster', fmtCores(cu), cc ? `de ${fmtCores(cc)}` : '', usageBar(cpct)) +
          card('Memória do cluster', fmtGiB(mu), mc ? `de ${fmtGiB(mc)}` : '', usageBar(mpct)) +
          card('Nodes', all.length, '');
      } else {
        cards =
          card('CPU total', fmtCores(sum((r) => r.cpu)), 'soma dos pods') +
          card('Memória total', fmtGiB(sum((r) => r.memory)), 'soma dos pods') +
          card('Pods', all.length, q ? `${rows.length} no filtro` : '');
      }

      // Cabeçalho ordenável.
      const caret = (k) => (metricsSort === k ? ` <i class="fas fa-caret-${metricsDesc ? 'down' : 'up'}"></i>` : '');
      const th = (k, label) => `<th class="sortable" data-sort="${k}">${label}${caret(k)}</th>`;
      const head = nodesView
        ? th('name', 'Node') + th('cpu', 'CPU') + th('cpu_pct', 'CPU %') + th('memory', 'Memória') + th('memory_pct', 'Mem %')
        : th('name', 'Pod') + th('namespace', 'Namespace') + th('cpu', 'CPU') + th('memory', 'Memória');
      const maxCpu = Math.max(1, ...rows.map((r) => r.cpu || 0));
      const maxMem = Math.max(1, ...rows.map((r) => r.memory || 0));
      const body = rows
        .map((r) =>
          nodesView
            ? `<tr data-name="${esc(r.name)}" data-ns="" style="cursor:pointer"><td>${esc(r.name)}</td>` +
              `<td>${fmtCores(r.cpu)}</td><td>${usageBar(r.cpu_pct)}</td>` +
              `<td>${fmtGiB(r.memory)}</td><td>${usageBar(r.memory_pct)}</td></tr>`
            : `<tr data-name="${esc(r.name)}" data-ns="${esc(r.namespace || '')}" style="cursor:pointer">` +
              `<td>${esc(r.name)}</td><td><span class="badge">${esc(r.namespace)}</span></td>` +
              `<td>${relBar(r.cpu, maxCpu, r.cpu + ' m')}</td><td>${relBar(r.memory, maxMem, r.memory + ' MiB')}</td></tr>`
        )
        .join('');
      $('#view-body').innerHTML =
        `<div class="mc-cards">${cards}</div>` +
        `<table class="data-table"><thead><tr>${head}</tr></thead><tbody>${body || ''}</tbody></table>` +
        (rows.length ? '' : '<p style="color:var(--text-tertiary);padding:var(--space-3)">Nada no filtro.</p>');

      $('#view-body').querySelectorAll('th.sortable').forEach((h) =>
        h.addEventListener('click', () => {
          const k = h.dataset.sort;
          if (metricsSort === k) metricsDesc = !metricsDesc;
          else { metricsSort = k; metricsDesc = true; }
          renderMetrics();
        })
      );
      $('#view-body').querySelectorAll('tbody tr[data-name]').forEach((tr) =>
        tr.addEventListener('click', () =>
          openDetail(cur, nodesView ? 'nodes' : 'pods', tr.dataset.name, tr.dataset.ns || '', null, null)
        )
      );
    }

    const load = async () => {
      const nsParam =
        metricsKind === 'pods' && state.selectedNs.length === 1
          ? `&namespace=${encodeURIComponent(state.selectedNs[0])}`
          : '';
      let data;
      try {
        data = await api(`/metrics/top?cluster_id=${cur.id}&kind=${metricsKind}${nsParam}`);
      } catch (e) {
        return emptyState('fa-circle-exclamation', 'Erro', e.message);
      }
      if (state.view !== 'metrics' || state.currentId !== cur.id) return;
      if (!data.available) {
        metricsData = null;
        return emptyState('fa-chart-line', 'Métricas indisponíveis', data.message);
      }
      metricsData = metricsKind === 'pods' ? data.rows.filter((r) => nsMatch(r.namespace)) : data.rows;
      renderMetrics();
    };
    loading();
    await load();
    state.viewTimer = setInterval(load, 5000); // atualização em tempo real
  }

  // Eventos do cluster em tempo real via /ws/events
  let eventTypeFilter = 'all';
  function viewEvents(cur) {
    const rows = [];
    const cols = [
      ['type', 'Tipo'],
      ['reason', 'Motivo'],
      ['object', 'Objeto'],
      ['message', 'Mensagem'],
      ['age', 'Idade'],
    ];
    const chip = (val, label) =>
      `<button class="btn btn-sm chip ${eventTypeFilter === val ? 'active' : ''}" data-evtype="${val}">${label}</button>`;
    const renderChips = () => {
      setActions(chip('all', 'Todos') + chip('Normal', 'Normal') + chip('Warning', 'Warning'));
      $('#view-actions')
        .querySelectorAll('[data-evtype]')
        .forEach((b) =>
          b.addEventListener('click', () => {
            eventTypeFilter = b.dataset.evtype;
            renderChips();
            render();
          })
        );
    };
    const visible = () =>
      eventTypeFilter === 'all' ? rows : rows.filter((r) => r.type === eventTypeFilter);
    const render = () => {
      const shown = visible();
      if (!shown.length) return emptyState('fa-bell', 'Aguardando eventos…');
      const head = cols.map((c) => `<th>${esc(c[1])}</th>`).join('');
      const body = shown
        .slice(-300)
        .reverse()
        .map((r) => '<tr>' + cols.map((c) => `<td>${cell(c[0], r[c[0]])}</td>`).join('') + '</tr>')
        .join('');
      $('#view-body').innerHTML =
        `<table class="data-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
    };
    // Em clusters movimentados os eventos chegam em rajada; reconstruir a tabela
    // inteira a cada frame trava a UI. Agrupa os re-renders num debounce (o slot
    // viewWatchTimer é limpo por closeViewSocket ao navegar).
    const scheduleRender = () => {
      if (state.viewWatchTimer) return;
      state.viewWatchTimer = setTimeout(() => {
        state.viewWatchTimer = null;
        if (state.view === 'events') render();
      }, 300);
    };
    renderChips();
    render();
    const sock = new WebSocket(wsUrl('/ws/events', { cluster_id: cur.id }));
    state.viewSocket = sock;
    sock.onmessage = (ev) => {
      try {
        const o = JSON.parse(ev.data);
        if (o.error) return emptyState('fa-circle-exclamation', 'Erro', o.error);
        if (!nsMatch(o.namespace)) return; // respeita o filtro global de namespace
        rows.push(o);
        if (rows.length > 600) rows.splice(0, rows.length - 600); // limita memória
        scheduleRender();
      } catch (_) {}
    };
  }

  async function viewPods(cur) {
    loading();
    let all;
    try {
      all = await api(`/pods?cluster_id=${cur.id}`);
    } catch (e) {
      return emptyState('fa-circle-exclamation', 'Erro ao carregar pods', e.message);
    }
    const keyOf = (p) => p.namespace + '/' + p.name;
    const podCols = [
      { key: 'namespace', label: 'Namespace' }, { key: 'phase', label: 'Status' },
      { key: 'ready', label: 'Ready' }, { key: 'up', label: 'Up' },
      { key: 'restarts', label: 'Restarts' }, { key: 'node', label: 'Node' },
    ];
    const head =
      `<tr>${cbHead()}<th>Nome</th><th>Namespace</th><th>Status</th><th>Ready</th><th>Up</th><th>Restarts</th><th>Node</th><th></th></tr>`;
    const rowInner = (p) =>
      `${cbCell(p.name, p.namespace)}<td>${esc(p.name)}</td><td><span class="badge">${esc(p.namespace)}</span></td>` +
      `<td><span class="status-dot ${esc((p.phase || '').toLowerCase())}"></span> ${esc(p.phase || '-')}</td>` +
      `<td>${esc(p.ready || '-')}</td><td>${esc(p.up || '-')}</td><td>${esc(p.restarts)}</td><td>${esc(p.node || '-')}</td>` +
      `<td class="actions">` +
      `<button class="btn btn-sm" data-logs data-name="${esc(p.name)}" data-ns="${esc(p.namespace)}" data-containers="${esc((p.containers || []).join(','))}" title="Logs"><i class="fas fa-file-lines"></i></button> ` +
      `<button class="btn btn-sm" data-term data-name="${esc(p.name)}" data-ns="${esc(p.namespace)}" data-containers="${esc((p.containers || []).join(','))}" title="Terminal"><i class="fas fa-terminal"></i></button> ` +
      `<button class="btn btn-sm" data-pf data-name="${esc(p.name)}" data-ns="${esc(p.namespace)}" title="Port forward"><i class="fas fa-plug"></i></button> ` +
      `<button class="btn btn-sm" data-yaml data-name="${esc(p.name)}" data-ns="${esc(p.namespace)}" title="YAML"><i class="fas fa-file-code"></i></button> ` +
      `<button class="btn btn-sm btn-danger" data-del data-name="${esc(p.name)}" data-ns="${esc(p.namespace)}" title="Deletar"><i class="fas fa-trash"></i></button>` +
      `</td>`;
    const render = () => {
      const pods = all.filter((p) => nsMatch(p.namespace)).sort(byNsName);
      if (!pods.length) return emptyState('fa-cube', 'Nenhum pod encontrado');
      if (patchTable(head, pods, keyOf, rowInner)) wireTable(cur, 'pods', podCols);
      updateSelection(cur, 'pods');
    };
    render();
    watchInto(cur, 'pods', all, keyOf, render); // atualização ao vivo (watch)
  }

  async function deletePod(cur, name, ns) {
    const ok = await uiConfirm({
      title: 'Deletar pod',
      danger: true,
      message: `Deletar o pod <b>${esc(name)}</b>?<br><span class="muted">Se for gerenciado por um controller, será recriado.</span>`,
      confirmLabel: 'Deletar',
    });
    if (!ok) return;
    try {
      await api(`/pods/${encodeURIComponent(name)}?cluster_id=${cur.id}&namespace=${encodeURIComponent(ns)}`, {
        method: 'DELETE',
      });
      window.toast('Pod deletado', 'success');
      renderView();
    } catch (e) {
      window.toast('Falha ao deletar: ' + e.message, 'error');
    }
  }

  async function viewDeployments(cur) {
    loading();
    let all;
    try {
      all = await api(`/deployments?cluster_id=${cur.id}`);
    } catch (e) {
      return emptyState('fa-circle-exclamation', 'Erro ao carregar deployments', e.message);
    }
    const keyOf = (d) => d.namespace + '/' + d.name;
    const depCols = [
      { key: 'namespace', label: 'Namespace' }, { key: 'ready_replicas', label: 'Ready' },
      { key: 'replicas', label: 'Réplicas' }, { key: 'available_replicas', label: 'Disponíveis' },
    ];
    const head =
      `<tr>${cbHead()}<th>Nome</th><th>Namespace</th><th>Ready</th><th>Disponíveis</th><th></th></tr>`;
    const rowInner = (d) =>
      `${cbCell(d.name, d.namespace)}<td>${esc(d.name)}</td><td><span class="badge">${esc(d.namespace)}</span></td>` +
      `<td>${esc(d.ready_replicas)}/${esc(d.replicas)}</td><td>${esc(d.available_replicas)}</td>` +
      `<td class="actions">` +
      `<button class="btn btn-sm" data-scale data-name="${esc(d.name)}" data-ns="${esc(d.namespace)}" data-rep="${esc(d.replicas)}" title="Escalar"><i class="fas fa-up-down"></i></button> ` +
      `<button class="btn btn-sm" data-restart data-name="${esc(d.name)}" data-ns="${esc(d.namespace)}" title="Reiniciar"><i class="fas fa-arrows-rotate"></i></button> ` +
      `<button class="btn btn-sm" data-yaml data-name="${esc(d.name)}" data-ns="${esc(d.namespace)}" title="YAML"><i class="fas fa-file-code"></i></button> ` +
      `<button class="btn btn-sm btn-danger" data-del data-name="${esc(d.name)}" data-ns="${esc(d.namespace)}" title="Remover"><i class="fas fa-trash"></i></button>` +
      `</td>`;
    setCreateAction('deployments'); // botão "Criar Deployment"
    const render = () => {
      const deps = all.filter((d) => nsMatch(d.namespace)).sort(byNsName);
      if (!deps.length) { emptyState('fa-rocket', 'Nenhum deployment encontrado'); return restoreViewActions(); }
      if (patchTable(head, deps, keyOf, rowInner)) wireTable(cur, 'deployments', depCols);
      updateSelection(cur, 'deployments');
    };
    restoreViewActions();
    render();
    watchInto(cur, 'deployments', all, keyOf, render); // atualização ao vivo (watch)
  }

  // ---------- multi-seleção + ações em massa ----------
  const cbHead = () => '<th class="cb-col"><input type="checkbox" id="sel-all"></th>';
  const cbCell = (name, ns) =>
    `<td class="cb-col"><input type="checkbox" class="row-cb" data-name="${esc(name)}" data-ns="${esc(ns || '')}"></td>`;

  // Sincroniza "selecionar tudo" + barra de ações em massa com os checkboxes.
  function updateSelection(cur, kind) {
    const body = $('#view-body');
    const cbs = [...body.querySelectorAll('.row-cb')];
    const sel = cbs.filter((c) => c.checked);
    const all = body.querySelector('#sel-all');
    if (all) all.checked = sel.length > 0 && sel.length === cbs.length;
    renderBulkBar(cur, kind, sel);
  }

  // Reconciliação keyed do <tbody>: só a linha que mudou é tocada (mesma chave +
  // mesmo HTML interno = intacta), evitando o "piscar" do innerHTML inteiro.
  // Preserva ordem, scroll e o checkbox marcado de linhas atualizadas.
  // Retorna true quando (re)criou a tabela do zero (hora de religar a delegação).
  function patchTable(headHtml, rows, keyOf, rowInner) {
    const host = $('#view-body');
    let table = host.querySelector('table.data-table');
    let created = false;
    if (!table) {
      host.innerHTML = `<table class="data-table"><thead>${headHtml}</thead><tbody></tbody></table>`;
      table = host.querySelector('table.data-table');
      created = true;
    }
    const tbody = table.querySelector('tbody');
    const existing = new Map();
    for (const tr of [...tbody.children]) existing.set(tr.dataset.key, tr);
    const seen = new Set();
    let prev = null;
    for (const row of rows) {
      const key = keyOf(row);
      seen.add(key);
      const inner = rowInner(row);
      let tr = existing.get(key);
      if (!tr) {
        tr = document.createElement('tr');
        tr.dataset.key = key;
        tr.className = 'clickable';
        tr.innerHTML = inner;
        tr._sig = inner;
      } else if (tr._sig !== inner) {
        const cb = tr.querySelector('.row-cb');
        const wasChecked = cb && cb.checked;
        tr.innerHTML = inner;
        tr._sig = inner;
        if (wasChecked) {
          const ncb = tr.querySelector('.row-cb');
          if (ncb) ncb.checked = true;
        }
      }
      tr._row = row;
      const ref = prev ? prev.nextSibling : tbody.firstChild;
      if (tr !== ref) tbody.insertBefore(tr, ref); // só move se a posição mudou
      prev = tr;
    }
    for (const tr of [...tbody.children]) if (!seen.has(tr.dataset.key)) tr.remove();
    return created;
  }

  // Delegação única de eventos da tabela (sobrevive aos patches de linha): ações
  // por botão (data-*), clique na linha → detalhe, e seleção (row-cb / sel-all).
  function wireTable(cur, kind, cols) {
    const table = $('#view-body table.data-table');
    if (!table) return;
    const tbody = table.querySelector('tbody');
    tbody.addEventListener('click', (e) => {
      const btn = e.target.closest('button');
      if (btn && btn.dataset) {
        const d = btn.dataset;
        const name = d.name;
        const ns = d.ns || '';
        const conts = d.containers ? d.containers.split(',').filter(Boolean) : [];
        if ('logs' in d) return showLogs(cur, name, ns, conts);
        if ('term' in d) return openTerminal(cur, name, ns, conts);
        if ('pf' in d) return portForwardPod(cur, name, ns);
        if ('nodeTerm' in d) return openNodeTerminal(cur, name);
        if ('cordon' in d) return cordonNode(cur, name, d.on === '1');
        if ('drain' in d) return drainNode(cur, name);
        if ('rollout' in d) return openRollout(cur, kind, name, ns);
        if ('trigger' in d) return triggerCronjob(cur, name, ns);
        if ('suspend' in d) return toggleCronjobSuspend(cur, name, ns, d.suspended === '1');
        if ('scale' in d) return scaleWorkload(cur, kind, name, ns, d.rep);
        if ('restart' in d) return restartWorkload(cur, kind, name, ns);
        if ('data' in d) return openData(cur, kind, name, ns);
        if ('yaml' in d) return openYaml(cur, kind, name, ns);
        if ('del' in d) return kind === 'pods' ? deletePod(cur, name, ns) : deleteResource(cur, kind, name, ns);
        return;
      }
      if (e.target.closest('input, a, label, .cb-col')) return;
      const tr = e.target.closest('tr');
      if (tr && tr._row && tr._row.name)
        openDetail(cur, kind, tr._row.name, tr._row.namespace || '', tr._row, cols);
    });
    tbody.addEventListener('change', (e) => {
      if (e.target.classList && e.target.classList.contains('row-cb')) updateSelection(cur, kind);
    });
    const selAll = table.querySelector('#sel-all');
    if (selAll)
      selAll.addEventListener('change', () => {
        $('#view-body')
          .querySelectorAll('.row-cb')
          .forEach((c) => (c.checked = selAll.checked));
        updateSelection(cur, kind);
      });
  }

  function renderBulkBar(cur, kind, sel) {
    if (!sel.length) return restoreViewActions();
    const items = sel.map((c) => ({ name: c.dataset.name, ns: c.dataset.ns || '' }));
    let buttons = '';
    if (SCALABLE.has(kind))
      buttons += '<button class="btn btn-sm" id="bulk-scale"><i class="fas fa-up-down"></i> Escalar</button> ';
    if (RESTARTABLE.has(kind))
      buttons += '<button class="btn btn-sm" id="bulk-restart"><i class="fas fa-arrows-rotate"></i> Reiniciar</button> ';
    buttons += '<button class="btn btn-sm btn-danger" id="bulk-del"><i class="fas fa-trash"></i> Remover</button>';
    setActions(
      `<span class="bulk-count">${sel.length} selecionado(s)</span> ${buttons} ` +
        '<button class="btn btn-sm" id="bulk-clear">Limpar</button>'
    );
    $('#bulk-del').addEventListener('click', () => bulkAction(cur, kind, items, 'delete'));
    if ($('#bulk-scale'))
      $('#bulk-scale').addEventListener('click', () => bulkAction(cur, kind, items, 'scale'));
    if ($('#bulk-restart'))
      $('#bulk-restart').addEventListener('click', () => bulkAction(cur, kind, items, 'restart'));
    $('#bulk-clear').addEventListener('click', () => {
      $('#view-body').querySelectorAll('.row-cb, #sel-all').forEach((c) => (c.checked = false));
      restoreViewActions();
    });
  }

  async function bulkAction(cur, kind, items, op) {
    let replicas = null;
    if (op === 'scale') {
      replicas = await uiScale({ title: `Escalar ${kind}`, current: 1, count: items.length });
      if (replicas === null) return;
    } else {
      const danger = op === 'delete';
      const verb = danger ? 'Remover' : 'Reiniciar';
      const ok = await uiConfirm({
        title: `${verb} em massa`,
        danger,
        icon: danger ? undefined : 'fa-arrows-rotate',
        message: `${verb} <b>${items.length}</b> item(ns) de <b>${esc(kind)}</b>?` + (danger ? '<br>Esta ação não pode ser desfeita.' : ''),
        confirmLabel: verb,
      });
      if (!ok) return;
    }
    let ok = 0;
    let fail = 0;
    await Promise.all(
      items.map(async (it) => {
        try {
          if (op === 'delete') {
            const q =
              `cluster_id=${cur.id}&kind=${kind}&name=${encodeURIComponent(it.name)}` +
              (it.ns ? `&namespace=${encodeURIComponent(it.ns)}` : '');
            await api(`/resources?${q}`, { method: 'DELETE' });
          } else if (op === 'restart') {
            await api(
              `/resources/restart?cluster_id=${cur.id}&kind=${kind}&name=${encodeURIComponent(it.name)}&namespace=${encodeURIComponent(it.ns)}`,
              { method: 'POST' }
            );
          } else {
            await api(
              `/resources/scale?cluster_id=${cur.id}&kind=${kind}&name=${encodeURIComponent(it.name)}&namespace=${encodeURIComponent(it.ns)}`,
              { method: 'POST', body: JSON.stringify({ replicas }) }
            );
          }
          ok++;
        } catch (_) {
          fail++;
        }
      })
    );
    const done =
      op === 'delete' ? 'removido(s)' : op === 'restart' ? 'reiniciado(s)' : `escalado(s) para ${replicas}`;
    window.toast(`${ok} ${done}` + (fail ? `, ${fail} com erro` : ''), fail ? 'warning' : 'success');
    renderView();
  }

  // ---------- operações de workloads (escalar / reiniciar / remover) ----------
  const SCALABLE = new Set(['deployments', 'statefulsets']);
  const RESTARTABLE = new Set(['deployments', 'statefulsets', 'daemonsets']);
  const DELETABLE = new Set(['deployments', 'statefulsets', 'daemonsets', 'jobs', 'cronjobs']);
  const DATA_KINDS = new Set(['secrets', 'configmaps']);

  async function scaleWorkload(cur, kind, name, ns, currentReplicas) {
    const replicas = await uiScale({ title: `Escalar “${name}”`, current: currentReplicas });
    if (replicas === null) return;
    try {
      await api(
        `/resources/scale?cluster_id=${cur.id}&kind=${kind}&name=${encodeURIComponent(name)}&namespace=${encodeURIComponent(ns)}`,
        { method: 'POST', body: JSON.stringify({ replicas }) }
      );
      window.toast(`Escalado para ${replicas} réplica(s)`, 'success');
      renderView();
    } catch (e) {
      window.toast('Falha ao escalar: ' + e.message, 'error');
    }
  }

  async function restartWorkload(cur, kind, name, ns) {
    const ok = await uiConfirm({
      title: 'Reiniciar workload',
      icon: 'fa-arrows-rotate',
      message: `Disparar <b>rollout restart</b> de <b>${esc(name)}</b>? Os pods serão recriados gradualmente.`,
      confirmLabel: 'Reiniciar',
    });
    if (!ok) return;
    try {
      await api(
        `/resources/restart?cluster_id=${cur.id}&kind=${kind}&name=${encodeURIComponent(name)}&namespace=${encodeURIComponent(ns)}`,
        { method: 'POST' }
      );
      window.toast('Restart disparado', 'success');
      renderView();
    } catch (e) {
      window.toast('Falha ao reiniciar: ' + e.message, 'error');
    }
  }

  async function deleteResource(cur, kind, name, ns) {
    const ok = await uiConfirm({
      title: 'Remover recurso',
      danger: true,
      message: `Remover <b>${esc(name)}</b> <span class="muted">(${esc(kind)})</span>?<br>Esta ação não pode ser desfeita.`,
      confirmLabel: 'Remover',
    });
    if (!ok) return;
    try {
      const q =
        `cluster_id=${cur.id}&kind=${kind}&name=${encodeURIComponent(name)}` +
        (ns ? `&namespace=${encodeURIComponent(ns)}` : '');
      await api(`/resources?${q}`, { method: 'DELETE' });
      window.toast('Removido', 'success');
      renderView();
    } catch (e) {
      window.toast('Falha ao remover: ' + e.message, 'error');
    }
  }

  // ---------- ações de CronJob (rodar agora / suspender) ----------
  async function triggerCronjob(cur, name, ns) {
    const ok = await uiConfirm({
      title: 'Executar CronJob agora',
      icon: 'fa-play',
      message: `Criar um Job manual a partir de <b>${esc(name)}</b> e executar imediatamente?`,
      confirmLabel: 'Executar',
    });
    if (!ok) return;
    try {
      const r = await api(
        `/resources/cronjob/trigger?cluster_id=${cur.id}&name=${encodeURIComponent(name)}&namespace=${encodeURIComponent(ns)}`,
        { method: 'POST' }
      );
      window.toast(`Job criado: ${r.job}`, 'success');
    } catch (e) {
      window.toast('Falha ao executar: ' + e.message, 'error');
    }
  }

  async function toggleCronjobSuspend(cur, name, ns, suspended) {
    const suspend = !suspended; // alterna o estado atual
    const ok = await uiConfirm({
      title: suspend ? 'Suspender CronJob' : 'Reativar CronJob',
      icon: suspend ? 'fa-pause' : 'fa-play',
      message: suspend
        ? `Suspender <b>${esc(name)}</b>? Novas execuções deixarão de ser agendadas até ser reativado.`
        : `Reativar <b>${esc(name)}</b>? Voltará a executar conforme o schedule.`,
      confirmLabel: suspend ? 'Suspender' : 'Reativar',
    });
    if (!ok) return;
    try {
      await api(
        `/resources/cronjob/suspend?cluster_id=${cur.id}&name=${encodeURIComponent(name)}&namespace=${encodeURIComponent(ns)}&suspend=${suspend}`,
        { method: 'POST' }
      );
      window.toast(suspend ? 'CronJob suspenso' : 'CronJob reativado', 'success');
      renderView();
    } catch (e) {
      window.toast('Falha: ' + e.message, 'error');
    }
  }

  // ---------- gerenciamento de nodes (cordon / uncordon / drain) ----------
  async function cordonNode(cur, name, cordon) {
    const ok = await uiConfirm({
      title: cordon ? 'Cordon node' : 'Uncordon node',
      icon: cordon ? 'fa-ban' : 'fa-circle-check',
      message: cordon
        ? `Marcar <b>${esc(name)}</b> como não-agendável? Novos pods deixam de ser alocados nele (os existentes permanecem).`
        : `Voltar a permitir agendamento em <b>${esc(name)}</b>?`,
      confirmLabel: cordon ? 'Cordon' : 'Uncordon',
    });
    if (!ok) return;
    try {
      await api(
        `/resources/node/cordon?cluster_id=${cur.id}&name=${encodeURIComponent(name)}&unschedulable=${cordon}`,
        { method: 'POST' }
      );
      window.toast(cordon ? 'Node cordonado' : 'Node liberado', 'success');
      closeDetail();
      renderView();
    } catch (e) {
      window.toast('Falha: ' + e.message, 'error');
    }
  }

  async function drainNode(cur, name) {
    const ok = await uiConfirm({
      title: 'Drain node',
      danger: true,
      icon: 'fa-truck-medical',
      message: `Drenar <b>${esc(name)}</b>?<br>O node é cordonado e seus pods são despejados (DaemonSets e pods estáticos são mantidos). PodDisruptionBudgets são respeitados.`,
      confirmLabel: 'Drenar',
    });
    if (!ok) return;
    window.toast('Drenando node…', 'info');
    try {
      const r = await api(
        `/resources/node/drain?cluster_id=${cur.id}&name=${encodeURIComponent(name)}`,
        { method: 'POST' }
      );
      const sk = (r.skipped || []).length;
      window.toast(
        `Node drenado: ${r.evicted} pod(s) despejado(s)` + (sk ? `, ${sk} mantido(s)` : ''),
        'success', 6000
      );
      closeDetail();
      renderView();
    } catch (e) {
      window.toast('Falha ao drenar: ' + e.message, 'error');
    }
  }

  // ---------- gerenciamento de rollout (deployments) ----------
  async function openRollout(cur, kind, name, ns, paused) {
    let data;
    try {
      data = await api(
        `/resources/rollout/history?cluster_id=${cur.id}&kind=${kind}&name=${encodeURIComponent(name)}&namespace=${encodeURIComponent(ns)}`
      );
    } catch (e) {
      return window.toast('Falha ao carregar histórico: ' + e.message, 'error');
    }
    const revs = data.revisions || [];
    const table = revs.length
      ? '<table class="data-table"><thead><tr><th>Revisão</th><th>Imagens</th><th>Réplicas</th><th></th></tr></thead><tbody>' +
        revs
          .map(
            (r) =>
              `<tr><td>${r.revision ?? '-'}${r.current ? ' <span class="badge success">atual</span>' : ''}</td>` +
              `<td style="font-family:var(--font-mono);font-size:var(--font-size-sm)">${esc((r.images || []).join(', '))}</td>` +
              `<td>${r.replicas}</td>` +
              `<td>${r.current || r.revision == null ? '' : `<button class="btn btn-sm" data-undo="${r.revision}"><i class="fas fa-clock-rotate-left"></i> Rollback</button>`}</td></tr>`
          )
          .join('') +
        '</tbody></table>'
      : '<p style="color:var(--text-tertiary)">Sem histórico de revisões.</p>';
    const pauseBtns =
      paused === true
        ? '<button class="btn btn-sm" data-resume><i class="fas fa-play"></i> Retomar rollout</button>'
        : paused === false
        ? '<button class="btn btn-sm" data-pause><i class="fas fa-pause"></i> Pausar rollout</button>'
        : '<button class="btn btn-sm" data-pause><i class="fas fa-pause"></i> Pausar</button> ' +
          '<button class="btn btn-sm" data-resume><i class="fas fa-play"></i> Retomar</button>';
    uiModal({
      title: `Rollout · ${name}`,
      width: 'min(720px,95vw)',
      body: `<div style="display:flex;gap:var(--space-2);margin-bottom:var(--space-3)">${pauseBtns}</div>${table}`,
      actions: [{ label: 'Fechar', value: null }],
      onOpen: (bd) => {
        const setPause = async (p) => {
          try {
            await api(
              `/resources/rollout/pause?cluster_id=${cur.id}&kind=${kind}&name=${encodeURIComponent(name)}&namespace=${encodeURIComponent(ns)}&paused=${p}`,
              { method: 'POST' }
            );
            window.toast(p ? 'Rollout pausado' : 'Rollout retomado', 'success');
            bd.remove();
          } catch (e) {
            window.toast('Falha: ' + e.message, 'error');
          }
        };
        const pb = bd.querySelector('[data-pause]');
        const rb = bd.querySelector('[data-resume]');
        if (pb) pb.addEventListener('click', () => setPause(true));
        if (rb) rb.addEventListener('click', () => setPause(false));
        bd.querySelectorAll('[data-undo]').forEach((b) =>
          b.addEventListener('click', async () => {
            const rev = parseInt(b.dataset.undo, 10);
            const ok = await uiConfirm({
              title: 'Rollback',
              icon: 'fa-clock-rotate-left',
              message: `Reverter <b>${esc(name)}</b> para a revisão ${rev}?`,
              confirmLabel: 'Rollback',
            });
            if (!ok) return;
            try {
              await api(
                `/resources/rollout/undo?cluster_id=${cur.id}&kind=${kind}&name=${encodeURIComponent(name)}&namespace=${encodeURIComponent(ns)}&revision=${rev}`,
                { method: 'POST' }
              );
              window.toast(`Revertido para a revisão ${rev}`, 'success');
              bd.remove();
              renderView();
            } catch (e) {
              window.toast('Falha no rollback: ' + e.message, 'error');
            }
          })
        );
      },
    });
  }

  // ---------- editor de recursos (requests/limits) por container ----------
  const QTY_RE = /^\d+(\.\d+)?(m|k|Ki|M|Mi|G|Gi|T|Ti|P|Pi|E|Ei|n|u)?$/;
  function editResources(cur, kind, name, ns, obj) {
    const tspec = (obj.spec && obj.spec.template && obj.spec.template.spec) || obj.spec || {};
    const conts = tspec.containers || [];
    if (!conts.length) return window.toast('Nenhum container para editar', 'warning');
    const valOf = (c, side, key) => ((c.resources && c.resources[side] && c.resources[side][key]) || '');
    const sel =
      conts.length > 1
        ? `<select class="input res-cont">${conts.map((c, i) => `<option value="${i}">${esc(c.name)}</option>`).join('')}</select>`
        : `<input class="input" value="${esc(conts[0].name)}" disabled>`;
    const field = (cls, label, ph) =>
      `<div class="form-group"><label>${label}</label><input class="input ${cls}" placeholder="${ph}"></div>`;
    const body =
      `<div class="form-group"><label>Container</label>${sel}</div>` +
      '<div style="display:grid;grid-template-columns:1fr 1fr;gap:var(--space-3)">' +
      field('res-cr', 'CPU request', 'ex.: 100m') + field('res-mr', 'Memória request', 'ex.: 128Mi') +
      field('res-cl', 'CPU limit', 'ex.: 500m') + field('res-ml', 'Memória limit', 'ex.: 256Mi') +
      '</div>' +
      '<div class="form-hint">Vazio = remove aquele valor. Quantidades k8s (m, Mi, Gi…).</div>' +
      '<div style="display:flex;justify-content:flex-end;gap:var(--space-2);margin-top:var(--space-3)">' +
      '<button class="btn btn-primary res-apply"><i class="fas fa-check"></i> Aplicar</button></div>';
    uiModal({
      title: `Recursos · ${name}`,
      width: 'min(560px,95vw)',
      body,
      actions: [{ label: 'Fechar', value: null }],
      onOpen: (bd) => {
        const fill = (i) => {
          const c = conts[i];
          bd.querySelector('.res-cr').value = valOf(c, 'requests', 'cpu');
          bd.querySelector('.res-mr').value = valOf(c, 'requests', 'memory');
          bd.querySelector('.res-cl').value = valOf(c, 'limits', 'cpu');
          bd.querySelector('.res-ml').value = valOf(c, 'limits', 'memory');
        };
        fill(0);
        const cs = bd.querySelector('.res-cont');
        if (cs) cs.addEventListener('change', () => fill(+cs.value));
        bd.querySelector('.res-apply').addEventListener('click', async () => {
          const idx = cs ? +cs.value : 0;
          const get = (q) => bd.querySelector(q).value.trim();
          const requests = {}, limits = {};
          const cr = get('.res-cr'), mr = get('.res-mr'), cl = get('.res-cl'), ml = get('.res-ml');
          for (const [v, lbl] of [[cr, 'CPU request'], [mr, 'Memória request'], [cl, 'CPU limit'], [ml, 'Memória limit']]) {
            if (v && !QTY_RE.test(v)) return window.toast(`Quantidade inválida em ${lbl}: ${v}`, 'warning');
          }
          if (cr) requests.cpu = cr;
          if (mr) requests.memory = mr;
          if (cl) limits.cpu = cl;
          if (ml) limits.memory = ml;
          try {
            await api(
              `/resources/container-resources?cluster_id=${cur.id}&kind=${kind}&name=${encodeURIComponent(name)}&namespace=${encodeURIComponent(ns)}`,
              { method: 'POST', body: JSON.stringify({ container: conts[idx].name, requests, limits }) }
            );
            window.toast('Recursos atualizados', 'success');
            bd.remove();
            renderView();
          } catch (e) {
            window.toast('Falha ao aplicar: ' + e.message, 'error');
          }
        });
      },
    });
  }

  // Renderiza qualquer recurso read-only via /api/resources (colunas + linhas).
  async function viewResource(cur, kind) {
    loading();
    let data;
    try {
      data = await api(`/resources?cluster_id=${cur.id}&kind=${encodeURIComponent(kind)}`);
    } catch (e) {
      return emptyState('fa-circle-exclamation', 'Erro ao carregar', e.message);
    }
    // Itens sem nome (raro nesses kinds) caem num key por conteúdo para não colidir.
    const keyOf = (r) => (r.name ? (r.namespace || '') + '/' + r.name : 'x:' + JSON.stringify(r));
    const hasName = data.rows.some((r) => r.name);
    const head =
      '<tr>' +
      (hasName ? cbHead() : '') +
      data.columns.map((c) => `<th>${esc(c.label)}</th>`).join('') +
      (hasName ? '<th></th>' : '') +
      '</tr>';
    const rowInner = (r) => {
      let tds = (hasName ? (r.name ? cbCell(r.name, r.namespace || '') : '<td></td>') : '') +
        data.columns.map((c) => `<td>${cell(c.key, r[c.key])}</td>`).join('');
      if (hasName) {
        if (r.name) {
          const n = esc(r.name);
          const nsv = esc(r.namespace || '');
          let acts = '';
          if (kind === 'nodes') {
            acts += `<button class="btn btn-sm" data-node-term data-name="${n}" title="Terminal do node"><i class="fas fa-terminal"></i></button> `;
            const cordoned = String(r.status || '').includes('SchedulingDisabled');
            acts += `<button class="btn btn-sm" data-cordon data-name="${n}" data-on="${cordoned ? 0 : 1}" title="${cordoned ? 'Uncordon' : 'Cordon'}"><i class="fas ${cordoned ? 'fa-circle-check' : 'fa-ban'}"></i></button> `;
            acts += `<button class="btn btn-sm" data-drain data-name="${n}" title="Drain"><i class="fas fa-truck-medical"></i></button> `;
          }
          if (kind === 'deployments')
            acts += `<button class="btn btn-sm" data-rollout data-name="${n}" data-ns="${nsv}" title="Rollout / histórico"><i class="fas fa-code-branch"></i></button> `;
          if (kind === 'cronjobs') {
            const susp = r.suspend === 'sim';
            acts += `<button class="btn btn-sm" data-trigger data-name="${n}" data-ns="${nsv}" title="Executar agora"><i class="fas fa-play"></i></button> `;
            acts += `<button class="btn btn-sm" data-suspend data-name="${n}" data-ns="${nsv}" data-suspended="${susp ? 1 : 0}" title="${susp ? 'Reativar' : 'Suspender'}"><i class="fas ${susp ? 'fa-circle-play' : 'fa-pause'}"></i></button> `;
          }
          if (SCALABLE.has(kind)) {
            const rep = esc(String(r.ready || '').split('/')[1] || '');
            acts += `<button class="btn btn-sm" data-scale data-name="${n}" data-ns="${nsv}" data-rep="${rep}" title="Escalar"><i class="fas fa-up-down"></i></button> `;
          }
          if (RESTARTABLE.has(kind))
            acts += `<button class="btn btn-sm" data-restart data-name="${n}" data-ns="${nsv}" title="Reiniciar"><i class="fas fa-arrows-rotate"></i></button> `;
          if (DATA_KINDS.has(kind))
            acts += `<button class="btn btn-sm" data-data data-name="${n}" data-ns="${nsv}" title="Ver dados"><i class="fas fa-eye"></i></button> `;
          acts += `<button class="btn btn-sm" data-yaml data-name="${n}" data-ns="${nsv}" title="YAML"><i class="fas fa-file-code"></i></button>`;
          if (DELETABLE.has(kind))
            acts += ` <button class="btn btn-sm btn-danger" data-del data-name="${n}" data-ns="${nsv}" title="Remover"><i class="fas fa-trash"></i></button>`;
          tds += `<td class="actions">${acts}</td>`;
        } else {
          tds += '<td></td>';
        }
      }
      return tds;
    };
    setCreateAction(kind); // botão "Criar" como ação padrão (kinds com builder)
    const render = () => {
      // Recursos namespaced respeitam o filtro global; cluster-scoped mostram tudo.
      const rowsData = (data.namespaced ? data.rows.filter((r) => nsMatch(r.namespace)) : data.rows.slice()).sort(byNsName);
      if (!rowsData.length) { emptyState('fa-inbox', 'Nada encontrado'); return restoreViewActions(); }
      if (patchTable(head, rowsData, keyOf, rowInner)) wireTable(cur, kind, data.columns);
      if (hasName) updateSelection(cur, kind);
      else restoreViewActions();
    };
    restoreViewActions(); // mostra "Criar" antes do primeiro paint
    render();
    watchInto(cur, kind, data.rows, keyOf, render); // atualização ao vivo (watch)
  }

  // ---------- viewer/editor de dados (Secrets / ConfigMaps) ----------
  let dataState = { cur: null, kind: '', name: '', ns: '', type: null, items: [], editing: false };

  async function openData(cur, kind, name, ns) {
    dataState = { cur, kind, name, ns, type: null, items: [], editing: false };
    $('#data-title').textContent =
      `${kind === 'secrets' ? 'Secret' : 'ConfigMap'} · ${ns}/${name}`;
    $('#data-body').innerHTML = '<div class="empty-state"><div class="spinner"></div></div>';
    openModal('#data-modal');
    try {
      const d = await api(
        `/resources/data?cluster_id=${cur.id}&kind=${kind}&name=${encodeURIComponent(name)}&namespace=${encodeURIComponent(ns)}`
      );
      dataState.items = d.items.map((it) => ({ ...it }));
      dataState.type = d.type;
      renderData();
    } catch (e) {
      $('#data-body').innerHTML = `<p style="color:var(--color-danger)">${esc(e.message)}</p>`;
    }
  }

  // Lê os inputs do modo edição de volta para dataState.items (antes de re-renderizar).
  function syncDataEdits() {
    const items = [];
    $('#data-body')
      .querySelectorAll('.data-row')
      .forEach((row) =>
        items.push({
          key: row.querySelector('.data-k').value,
          value: row.querySelector('.data-v').value,
        })
      );
    dataState.items = items;
  }

  function renderData() {
    const { kind, items, editing, type } = dataState;
    const secret = kind === 'secrets';
    const meta = secret && type ? `<span class="form-hint">type: ${esc(type)}</span>` : '';
    const toolbar = editing
      ? '<button class="btn btn-sm" id="data-add"><i class="fas fa-plus"></i> Chave</button>' +
        '<button class="btn btn-sm btn-primary" id="data-save"><i class="fas fa-check"></i> Salvar</button>' +
        '<button class="btn btn-sm" id="data-cancel">Cancelar</button>'
      : '<button class="btn btn-sm" id="data-edit"><i class="fas fa-pen"></i> Editar</button>';

    let bodyHtml;
    if (editing) {
      bodyHtml =
        items
          .map(
            (it) =>
              '<div class="data-row" style="display:grid;grid-template-columns:200px 1fr auto;gap:var(--space-2);align-items:start;margin-bottom:var(--space-2)">' +
              `<input class="input data-k" value="${esc(it.key)}" placeholder="chave">` +
              `<textarea class="input data-v" spellcheck="false" style="min-height:38px;font-family:var(--font-mono)">${esc(it.value)}</textarea>` +
              '<button class="btn btn-sm btn-danger data-rm" title="Remover"><i class="fas fa-trash"></i></button></div>'
          )
          .join('') || '<p style="color:var(--text-tertiary)">Sem chaves. Adicione uma.</p>';
    } else if (!items.length) {
      bodyHtml = '<p style="color:var(--text-tertiary)">Sem dados.</p>';
    } else {
      bodyHtml = items
        .map(
          (it, i) =>
            `<div class="data-entry"><div class="data-key">${esc(it.key)}</div>` +
            `<pre class="data-val${secret ? ' masked' : ''}" data-i="${i}">${secret ? '••••••••' : esc(it.value)}</pre>` +
            `<div class="data-actions">` +
            (secret ? `<button class="btn btn-sm" data-reveal="${i}"><i class="fas fa-eye"></i> Revelar</button>` : '') +
            `<button class="btn btn-sm" data-copy="${i}"><i class="fas fa-copy"></i></button></div></div>`
        )
        .join('');
    }
    $('#data-body').innerHTML =
      `<div style="display:flex;gap:var(--space-2);align-items:center;margin-bottom:var(--space-3)">${toolbar}<span style="flex:1"></span>${meta}</div>${bodyHtml}`;
    wireData();
  }

  function wireData() {
    const q = (s) => $('#data-body').querySelector(s);
    const qa = (s) => $('#data-body').querySelectorAll(s);
    if (q('#data-edit')) q('#data-edit').addEventListener('click', () => { dataState.editing = true; renderData(); });
    if (q('#data-cancel')) q('#data-cancel').addEventListener('click', () => openData(dataState.cur, dataState.kind, dataState.name, dataState.ns));
    if (q('#data-add')) q('#data-add').addEventListener('click', () => { syncDataEdits(); dataState.items.push({ key: '', value: '' }); renderData(); });
    qa('.data-rm').forEach((b, i) =>
      b.addEventListener('click', () => { syncDataEdits(); dataState.items.splice(i, 1); renderData(); })
    );
    if (q('#data-save')) q('#data-save').addEventListener('click', saveData);
    qa('[data-reveal]').forEach((b) =>
      b.addEventListener('click', async () => {
        const ok = await uiConfirm({ title: 'Revelar Secret', icon: 'fa-eye', message: 'Mostrar o valor decodificado deste Secret?', confirmLabel: 'Revelar' });
        if (!ok) return;
        const i = +b.dataset.reveal;
        const pre = q(`.data-val[data-i="${i}"]`);
        pre.textContent = dataState.items[i].value;
        pre.classList.remove('masked');
        b.remove();
      })
    );
    qa('[data-copy]').forEach((b) =>
      b.addEventListener('click', () =>
        navigator.clipboard.writeText(dataState.items[+b.dataset.copy].value).then(
          () => window.toast('Copiado', 'success'),
          () => window.toast('Falha ao copiar', 'error')
        )
      )
    );
  }

  async function saveData() {
    syncDataEdits();
    const map = {};
    for (const it of dataState.items) {
      const k = (it.key || '').trim();
      if (!k) return window.toast('Há chave(s) em branco', 'warning');
      if (k in map) return window.toast(`Chave duplicada: ${k}`, 'warning');
      if (!/^[A-Za-z0-9_.-]+$/.test(k)) return window.toast(`Chave inválida: ${k} (use letras, números, . _ -)`, 'warning');
      map[k] = it.value;
    }
    const secret = dataState.kind === 'secrets';
    const ok = await uiConfirm({
      title: secret ? 'Salvar Secret' : 'Salvar ConfigMap',
      danger: secret,
      icon: secret ? 'fa-key' : 'fa-check',
      message: `Aplicar ${Object.keys(map).length} chave(s) em <b>${esc(dataState.name)}</b>?` +
        (secret ? '<br>Isto sobrescreve o conteúdo do Secret.' : ''),
      confirmLabel: 'Salvar',
    });
    if (!ok) return;
    try {
      await api(
        `/resources/data?cluster_id=${dataState.cur.id}&kind=${dataState.kind}&name=${encodeURIComponent(dataState.name)}&namespace=${encodeURIComponent(dataState.ns)}`,
        { method: 'PUT', body: JSON.stringify({ data: map }) }
      );
      window.toast('Dados salvos', 'success');
      openData(dataState.cur, dataState.kind, dataState.name, dataState.ns); // recarrega
    } catch (e) {
      window.toast('Falha ao salvar: ' + e.message, 'error');
    }
  }

  function cell(key, val) {
    if (val === null || val === undefined || val === '') return '<span style="color:var(--text-tertiary)">-</span>';
    if (key === 'status') {
      const s = String(val).toLowerCase();
      const ok = ['ready', 'running', 'bound', 'active', 'complete', 'succeeded'].some((x) => s.includes(x));
      const bad = ['failed', 'error', 'lost', 'notready', 'pending', 'unknown'].some((x) => s.includes(x));
      const klass = ok ? 'connected' : bad ? 'unreachable' : 'unknown';
      return `<span class="status-dot ${klass}"></span> ${esc(val)}`;
    }
    if (key === 'type') {
      const s = String(val).toLowerCase();
      if (s === 'warning') return `<span class="badge warning">${esc(val)}</span>`;
      if (s === 'normal') return `<span class="badge success">${esc(val)}</span>`;
      return `<span class="badge">${esc(val)}</span>`;
    }
    if (key === 'namespace') return `<span class="badge">${esc(val)}</span>`;
    return esc(val);
  }

  // ---------- log viewer (streaming via /ws/logs) ----------
  const logsInst = {}; // id -> { socket, lines, cur, name, ns, container, containers, max, pane, els }

  // ---------- detecção inteligente de problemas em logs (ao vivo) ----------
  // Regras heurísticas: cada linha é classificada em tempo real conforme chega.
  // sev: error|warn; cat: rótulo da categoria do problema.
  const LOG_RULES = [
    { cat: 'panic/crash', sev: 'error', re: /\b(panic|fatal\s*error|segfault|sigsegv|sigabrt|core dumped|stack overflow|assertion failed)\b/i },
    { cat: 'OOM', sev: 'error', re: /\b(oomkilled|out of memory|cannot allocate memory|memory limit (?:exceeded|reached)|killed process)\b/i },
    { cat: 'exceção', sev: 'error', re: /(exception\b|traceback|unhandled|panic:|null ?pointer|undefined is not|cannot read propert|uncaught|stack ?trace)/i },
    { cat: 'rede', sev: 'error', re: /\b(connection refused|connection reset|no route to host|broken pipe|dial tcp|network is unreachable|i\/o timeout|tls handshake|certificate)\b/i },
    { cat: 'timeout', sev: 'error', re: /\b(timed?\s*out|context deadline exceeded|deadline exceeded|read timeout|request timeout)\b/i },
    { cat: 'auth', sev: 'error', re: /(\bunauthorized\b|\bforbidden\b|permission denied|access denied|authentication failed|invalid credentials|\b401\b|\b403\b)/i },
    { cat: 'HTTP 5xx', sev: 'error', re: /\b(internal server error|bad gateway|service unavailable|gateway timeout|http[\/ ]?\s*5\d\d|status[=:\s]+5\d\d)\b/i },
    { cat: 'banco', sev: 'error', re: /\b(deadlock|too many connections|duplicate key|could not connect|connection pool exhausted|query failed)\b/i },
    { cat: 'erro', sev: 'error', re: /(^|\s|\[|")(error|errors|fatal|critical|crit|severe|emerg|panic)(\s|\]|"|:|=|$)/i },
    { cat: 'falha', sev: 'error', re: /\b(failed to|failure|failed)\b/i },
    { cat: 'alerta', sev: 'warn', re: /(^|\s|\[|")(warn|warning|deprecated)(\s|\]|"|:|=|$)/i },
  ];

  function classifyLogLine(line) {
    let sev = null;
    const cats = [];
    for (const r of LOG_RULES) {
      if (r.re.test(line)) {
        cats.push(r.cat);
        if (r.sev === 'error') sev = 'error';
        else if (!sev) sev = 'warn';
      }
    }
    if (!sev) return null;
    // Prefere uma categoria específica ao rótulo genérico de nível (erro/alerta).
    const cat = cats.find((c) => c !== 'erro' && c !== 'alerta') || cats[0];
    return { sev, cat };
  }

  // Assinatura normalizada (timestamps/números/ids/IPs/aspas viram placeholders)
  // para agrupar ocorrências do MESMO problema.
  function logSignature(line) {
    let s = line;
    s = s.replace(/^\s*\[?\d{4}-\d{2}-\d{2}[ T][\d:.,]+Z?\]?\s*/, '');
    s = s.replace(/^\s*\[?\d{2}:\d{2}:\d{2}[.,\d]*\]?\s*/, '');
    s = s.replace(/0x[0-9a-fA-F]+/g, '0x#');
    s = s.replace(/\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b/g, '<uuid>');
    s = s.replace(/\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(:\d+)?\b/g, '<ip>');
    s = s.replace(/"[^"]*"/g, '"…"').replace(/'[^']*'/g, "'…'");
    s = s.replace(/\b\d+\b/g, '#');
    return s.replace(/\s+/g, ' ').trim().slice(0, 200);
  }

  // Status do assistente de IA (cacheado) — controla o botão "Analisar".
  let _aiStatusCache;
  async function aiAvailable() {
    if (_aiStatusCache === undefined) {
      try {
        _aiStatusCache = (await api('/ai/status')).available;
      } catch (_) {
        _aiStatusCache = false;
      }
    }
    return _aiStatusCache;
  }

  function showLogs(cur, name, ns, containers) {
    const key = `logs|${cur.id}|${ns}|${name}`;
    const { id, pane, isNew } = openOrFocusTab('logs', key, name, { name, ns, containers });
    if (!isNew) return; // já aberto: apenas foca a aba
    const conts = (containers || []).filter(Boolean);
    const inst = (logsInst[id] = {
      id, pane, socket: null, lines: [], cls: [], cur, name, ns,
      containers: conts, container: conts[0] || '', max: 5000,
      problems: new Map(), problemCount: 0, errorCount: 0, warnCount: 0,
      onlyProblems: false, activeSig: null, panelOpen: false,
      els: {
        title: pane.querySelector('.logs-title'),
        status: pane.querySelector('.logs-status'),
        container: pane.querySelector('.logs-container'),
        search: pane.querySelector('.logs-search'),
        autoscroll: pane.querySelector('.logs-autoscroll'),
        onlyprob: pane.querySelector('.logs-onlyprob'),
        count: pane.querySelector('.logs-count'),
        output: pane.querySelector('.logs-output'),
        problems: pane.querySelector('.logs-problems'),
        probCount: pane.querySelector('.logs-prob-count'),
        panel: pane.querySelector('.logs-problems-panel'),
        analyze: pane.querySelector('.logs-analyze'),
        copy: pane.querySelector('.logs-copy'),
        download: pane.querySelector('.logs-download'),
      },
    });
    const sel = inst.els.container;
    if (conts.length > 1) {
      sel.style.display = '';
      sel.innerHTML = conts.map((c) => `<option value="${esc(c)}">${esc(c)}</option>`).join('');
      sel.value = inst.container;
      sel.addEventListener('change', () => {
        inst.container = sel.value;
        connectLogs(inst);
      });
    }
    inst.els.search.addEventListener('input', () => renderLogs(inst));
    inst.els.autoscroll.addEventListener('change', () => renderLogs(inst));
    inst.els.onlyprob.addEventListener('change', () => {
      inst.onlyProblems = inst.els.onlyprob.checked;
      renderLogs(inst);
    });
    inst.els.problems.addEventListener('click', () => toggleProblemsPanel(inst));
    inst.els.analyze.addEventListener('click', () => analyzeLogs(inst));
    inst.els.copy.addEventListener('click', () => copyLogs(inst));
    inst.els.download.addEventListener('click', () => downloadLogs(inst));
    // O botão "Analisar (IA)" só aparece se o assistente estiver configurado.
    aiAvailable().then((ok) => {
      if (ok && inst.els.analyze) inst.els.analyze.hidden = false;
    });
    connectLogs(inst);
  }

  function connectLogs(inst) {
    closeLogSocket(inst);
    inst.lines = [];
    inst.cls = [];
    inst.problems = new Map();
    inst.problemCount = inst.errorCount = inst.warnCount = 0;
    inst.activeSig = null;
    inst.els.title.textContent =
      `Logs · ${inst.ns}/${inst.name}` + (inst.container ? ` · ${inst.container}` : '');
    inst.els.output.textContent = '';
    inst.els.count.textContent = '';
    setLogStatus(inst, 'connecting', 'conectando…');

    const params = { cluster_id: inst.cur.id, name: inst.name, namespace: inst.ns, tail: 500 };
    if (inst.container) params.container = inst.container;
    const sock = new WebSocket(wsUrl('/ws/logs', params));
    inst.socket = sock;
    sock.onopen = () => setLogStatus(inst, 'connected', 'ao vivo');
    sock.onmessage = (ev) => {
      const d = ev.data;
      if (d && d[0] === '{' && d.includes('"error"')) {
        try {
          const o = JSON.parse(d);
          if (o.error) {
            appendLog(inst, '⚠ ' + o.error);
            setLogStatus(inst, 'error', 'erro');
            return;
          }
        } catch (_) {}
      }
      appendLog(inst, String(d).replace(/\n+$/, ''));
    };
    sock.onclose = () => {
      if (inst.socket === sock) setLogStatus(inst, 'closed', 'encerrado');
    };
    sock.onerror = () => setLogStatus(inst, 'error', 'erro');
  }

  function appendLog(inst, line) {
    inst.lines.push(line);
    const c = classifyLogLine(line);
    if (c) c.sig = logSignature(line);
    inst.cls.push(c);
    if (c) {
      inst.problemCount++;
      if (c.sev === 'error') inst.errorCount++;
      else inst.warnCount++;
      const key = c.sev + '|' + c.sig;
      let g = inst.problems.get(key);
      if (!g) {
        g = { sev: c.sev, cat: c.cat, sig: c.sig, count: 0, sample: line };
        inst.problems.set(key, g);
      }
      g.count++;
    }
    if (inst.lines.length > inst.max) {
      const drop = inst.lines.length - inst.max;
      inst.lines.splice(0, drop);
      inst.cls.splice(0, drop);
    }
    scheduleLogRender(inst);
  }

  // Agrupa renders em um por frame: o tail inicial chega como centenas de frames
  // (1 por linha) quase juntas — re-renderizar a cada linha custa O(n²) e faz a
  // tela "varrer" de cima até embaixo. Com rAF, a rajada coalesce num render só.
  function scheduleLogRender(inst) {
    if (inst.renderRaf) return;
    inst.renderRaf = requestAnimationFrame(() => {
      inst.renderRaf = 0;
      renderLogs(inst);
    });
  }

  function renderLogs(inst) {
    const q = inst.els.search.value.trim().toLowerCase();
    const out = inst.els.output;
    const onlyProb = inst.onlyProblems;
    const sig = inst.activeSig;
    const parts = [];
    let shown = 0;
    for (let i = 0; i < inst.lines.length; i++) {
      const line = inst.lines[i];
      const c = inst.cls[i];
      if (onlyProb && !c) continue;
      if (sig && (!c || c.sig !== sig)) continue;
      if (q && !line.toLowerCase().includes(q)) continue;
      shown++;
      const cls = c ? (c.sev === 'error' ? 'logline log-err' : 'logline log-warn') : 'logline';
      parts.push(`<span class="${cls}">${esc(line) || ' '}</span>`);
    }
    out.innerHTML = parts.join('');
    const filtered = q || onlyProb || sig;
    const probTxt = inst.problemCount
      ? ` · ${inst.errorCount} erro(s), ${inst.warnCount} alerta(s)`
      : '';
    inst.els.count.textContent =
      (filtered ? `${shown}/${inst.lines.length} linhas` : `${inst.lines.length} linhas`) + probTxt;
    updateProblemsBadge(inst);
    if (inst.panelOpen) renderProblemsPanel(inst);
    if (inst.els.autoscroll.checked) out.scrollTop = out.scrollHeight;
  }

  function updateProblemsBadge(inst) {
    const b = inst.els.problems;
    inst.els.probCount.textContent = inst.problemCount;
    b.classList.toggle('has-problems', inst.errorCount > 0);
    b.classList.toggle('active', inst.panelOpen);
  }

  function toggleProblemsPanel(inst) {
    inst.panelOpen = !inst.panelOpen;
    inst.els.panel.hidden = !inst.panelOpen;
    if (inst.panelOpen) renderProblemsPanel(inst);
    updateProblemsBadge(inst);
  }

  function renderProblemsPanel(inst) {
    const panel = inst.els.panel;
    const groups = [...inst.problems.values()].sort(
      (a, b) => (a.sev === b.sev ? b.count - a.count : a.sev === 'error' ? -1 : 1)
    );
    if (!groups.length) {
      panel.innerHTML = '<div class="logs-prob-empty">Nenhum problema detectado até agora. 🎉</div>';
      return;
    }
    panel.innerHTML = groups
      .slice(0, 100)
      .map((g) => {
        const badge = g.sev === 'error'
          ? '<span class="badge danger">erro</span>'
          : '<span class="badge warning">alerta</span>';
        const active = inst.activeSig === g.sig ? ' active' : '';
        return (
          `<div class="logs-prob-group${active}" data-sig="${esc(g.sig)}" title="${esc(g.sample)}">` +
          `${badge}<span class="badge">${esc(g.cat)}</span>` +
          `<span class="sig">${esc(g.sample)}</span>` +
          `<span class="cnt">×${g.count}</span></div>`
        );
      })
      .join('');
    panel.querySelectorAll('[data-sig]').forEach((row) =>
      row.addEventListener('click', () => {
        inst.activeSig = inst.activeSig === row.dataset.sig ? null : row.dataset.sig;
        renderLogs(inst);
      })
    );
  }

  // Envia um resumo dos problemas detectados ao assistente de IA para análise.
  function analyzeLogs(inst) {
    if (!inst.problems.size) return window.toast('Nenhum problema detectado para analisar', 'warning');
    const groups = [...inst.problems.values()].sort(
      (a, b) => (a.sev === b.sev ? b.count - a.count : a.sev === 'error' ? -1 : 1)
    ).slice(0, 10);
    const lines = groups.map((g) => `- [${g.sev}/${g.cat}] ×${g.count}: ${g.sample.slice(0, 240)}`);
    const prompt =
      `Analise os problemas detectados nos logs ao vivo do pod ${inst.ns}/${inst.name}` +
      (inst.container ? ` (container ${inst.container})` : '') +
      `. Aponte a causa raiz provável e como corrigir. Você pode buscar mais logs/eventos se precisar.\n\n` +
      `Problemas (agrupados, com contagem):\n${lines.join('\n')}`;
    askAILogs(inst.cur, prompt);
  }

  async function askAILogs(cur, text) {
    await openAI(cur);
    const tab = state.dock.tabs.find((t) => t.type === 'ai' && t.key === `ai|${cur.id}`);
    const inst = tab && aiInst[tab.id];
    if (inst && inst.ask) inst.ask(text);
    else window.toast('Assistente IA indisponível', 'warning');
  }

  function setLogStatus(inst, kind, label) {
    const map = { connecting: 'unknown', connected: 'connected', closed: 'unknown', error: 'unreachable' };
    const box = inst.els.status;
    box.querySelector('.status-dot').className = `status-dot ${map[kind] || 'unknown'}`;
    box.querySelector('span:last-child').textContent = label;
  }

  function closeLogSocket(inst) {
    if (!inst) return;
    if (inst.renderRaf) {
      cancelAnimationFrame(inst.renderRaf);
      inst.renderRaf = 0;
    }
    if (inst.socket) {
      const s = inst.socket;
      inst.socket = null;
      try {
        s.close();
      } catch (_) {}
    }
  }

  function copyLogs(inst) {
    navigator.clipboard.writeText(inst.lines.join('\n')).then(
      () => window.toast('Logs copiados', 'success'),
      () => window.toast('Falha ao copiar', 'error')
    );
  }

  function downloadLogs(inst) {
    const blob = new Blob([inst.lines.join('\n')], { type: 'text/plain' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `${inst.ns}_${inst.name}.log`;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  // ---------- terminal (exec interativo via /ws/terminal) ----------
  const termInst = {}; // id -> { socket, xterm, fit, onResize, cur, mode, node, name, ns, container, containers, pane, els }

  function newTermInst(id, pane, base) {
    return Object.assign(
      {
        id, pane, socket: null, xterm: null, fit: null, onResize: null,
        node: '', name: '', ns: '', container: '', containers: [],
        els: {
          title: pane.querySelector('.term-title'),
          containerSel: pane.querySelector('.term-container-sel'),
          container: pane.querySelector('.term-container'),
        },
      },
      base
    );
  }

  // Shell kubectl do cluster (PTY local com KUBECONFIG + contexto).
  function openKubectlTerminal(cur) {
    if (!cur) return window.toast('Selecione um cluster primeiro', 'warning');
    const { id, pane, isNew } = openOrFocusTab('term', `term|kubectl|${cur.id}`, 'kubectl', { mode: 'kubectl' });
    if (!isNew) return;
    connectTerminal((termInst[id] = newTermInst(id, pane, { cur, mode: 'kubectl' })));
  }

  function openTerminal(cur, name, ns, containers) {
    const { id, pane, isNew } = openOrFocusTab('term', `term|pod|${cur.id}|${ns}|${name}`, name, { mode: 'pod', name, ns, containers });
    if (!isNew) return;
    const conts = (containers || []).filter(Boolean);
    const inst = (termInst[id] = newTermInst(id, pane, {
      cur, mode: 'pod', name, ns, containers: conts, container: conts[0] || '',
    }));
    const sel = inst.els.containerSel;
    if (conts.length > 1) {
      sel.style.display = '';
      sel.innerHTML = conts.map((c) => `<option value="${esc(c)}">${esc(c)}</option>`).join('');
      sel.value = inst.container;
      sel.addEventListener('change', () => {
        inst.container = sel.value;
        connectTerminal(inst);
      });
    }
    connectTerminal(inst);
  }

  // Shell de node (Lens-style): pod privilegiado + nsenter no host.
  function openNodeTerminal(cur, node) {
    const { id, pane, isNew } = openOrFocusTab('term', `term|node|${cur.id}|${node}`, 'node: ' + node, { mode: 'node', node });
    if (!isNew) return;
    connectTerminal((termInst[id] = newTermInst(id, pane, { cur, mode: 'node', node, name: node })));
  }

  function connectTerminal(inst) {
    closeTermSocket(inst);
    inst.els.title.textContent =
      inst.mode === 'kubectl'
        ? `kubectl · ${inst.cur.name}`
        : inst.mode === 'node'
        ? `Terminal · node/${inst.node}`
        : `Terminal · ${inst.ns}/${inst.name}` + (inst.container ? ` · ${inst.container}` : '');

    const xterm = new window.Terminal({
      cursorBlink: true,
      fontSize: 13,
      fontFamily: 'ui-monospace, Menlo, Consolas, "Liberation Mono", monospace',
      theme: { background: '#000000', foreground: '#d4d4d4' },
    });
    const fit = new window.FitAddon.FitAddon();
    xterm.loadAddon(fit);
    const cont = inst.els.container;
    cont.innerHTML = '';
    xterm.open(cont);
    inst.xterm = xterm;
    inst.fit = fit;
    setTimeout(() => safeFit(inst), 60);

    let sockUrl;
    if (inst.mode === 'kubectl') {
      sockUrl = wsUrl('/ws/kubectl', { cluster_id: inst.cur.id });
    } else {
      const params = inst.node
        ? { cluster_id: inst.cur.id, node: inst.node }
        : { cluster_id: inst.cur.id, name: inst.name, namespace: inst.ns };
      if (!inst.node && inst.container) params.container = inst.container;
      sockUrl = wsUrl('/ws/terminal', params);
    }
    const sock = new WebSocket(sockUrl);
    inst.socket = sock;

    sock.onopen = () => {
      safeFit(inst);
      sendTermResize(inst);
      xterm.focus();
    };
    sock.onmessage = (ev) => {
      const d = ev.data;
      if (d && d[0] === '{' && d.includes('"error"')) {
        try {
          const o = JSON.parse(d);
          if (o.error) {
            xterm.write(`\r\n\x1b[31m${o.error}\x1b[0m\r\n`);
            return;
          }
        } catch (_) {}
      }
      xterm.write(d);
    };
    sock.onclose = () => xterm.write('\r\n\x1b[90m[sessão encerrada]\x1b[0m\r\n');

    xterm.onData((d) => {
      if (sock.readyState === WebSocket.OPEN) sock.send('0' + d);
    });

    inst.onResize = () => {
      safeFit(inst);
      sendTermResize(inst);
    };
    window.addEventListener('resize', inst.onResize);
  }

  function safeFit(inst) {
    try {
      if (inst && inst.fit) inst.fit.fit();
    } catch (_) {}
  }

  function sendTermResize(inst) {
    if (inst && inst.socket && inst.socket.readyState === WebSocket.OPEN && inst.xterm) {
      inst.socket.send('1' + JSON.stringify({ cols: inst.xterm.cols, rows: inst.xterm.rows }));
    }
  }

  function closeTermSocket(inst) {
    if (!inst) return;
    if (inst.onResize) {
      window.removeEventListener('resize', inst.onResize);
      inst.onResize = null;
    }
    if (inst.socket) {
      const s = inst.socket;
      inst.socket = null;
      try {
        s.close();
      } catch (_) {}
    }
    if (inst.xterm) {
      try {
        inst.xterm.dispose();
      } catch (_) {}
      inst.xterm = null;
      inst.fit = null;
    }
  }

  // ---------- YAML viewer/editor (Monaco) ----------
  const yamlInst = {}; // id -> { editor, diffEditor, original, mode, cur, kind, name, ns, pane, els }
  let monacoReady = null;
  let k8sCompletionsDone = false;

  function ensureMonaco() {
    if (monacoReady) return monacoReady;
    monacoReady = new Promise((resolve, reject) => {
      const ready = () => {
        registerK8sCompletions(window.monaco);
        resolve(window.monaco);
      };
      if (window.monaco) return ready();
      if (!window.require) return reject(new Error('Monaco loader ausente'));
      window.require.config({
        paths: { vs: 'https://cdn.jsdelivr.net/npm/monaco-editor@0.45.0/min/vs' },
      });
      window.require(['vs/editor/editor.main'], ready, reject);
    });
    return monacoReady;
  }

  // Autocomplete Kubernetes para YAML, sensível ao contexto:
  //  - valores só onde fazem sentido (apiVersion:/kind:/enums e itens de lista);
  //  - chaves relevantes ao kind do documento atual;
  //  - snippets de esqueleto (k8s:deployment, …).
  const K8S_KINDS = [
    'Deployment', 'StatefulSet', 'DaemonSet', 'ReplicaSet', 'Pod', 'Service',
    'ConfigMap', 'Secret', 'Ingress', 'Job', 'CronJob', 'Namespace',
    'PersistentVolumeClaim', 'HorizontalPodAutoscaler', 'NetworkPolicy',
    'ServiceAccount', 'Role', 'RoleBinding', 'ClusterRole', 'ClusterRoleBinding',
    'LimitRange', 'ResourceQuota',
  ];
  const K8S_APIVERSIONS = [
    'v1', 'apps/v1', 'batch/v1', 'networking.k8s.io/v1', 'autoscaling/v2',
    'rbac.authorization.k8s.io/v1', 'storage.k8s.io/v1', 'policy/v1',
  ];
  // Valores sugeridos após "<chave>:".
  const VALUE_ENUMS = {
    apiVersion: K8S_APIVERSIONS, kind: K8S_KINDS,
    imagePullPolicy: ['Always', 'IfNotPresent', 'Never'],
    restartPolicy: ['Always', 'OnFailure', 'Never'],
    type: ['ClusterIP', 'NodePort', 'LoadBalancer', 'ExternalName'],
    protocol: ['TCP', 'UDP', 'SCTP'],
    pathType: ['Prefix', 'Exact', 'ImplementationSpecific'],
    concurrencyPolicy: ['Allow', 'Forbid', 'Replace'],
    volumeMode: ['Filesystem', 'Block'],
    dnsPolicy: ['ClusterFirst', 'Default', 'ClusterFirstWithHostNet', 'None'],
  };
  // Valores sugeridos em itens de lista ("- <valor>") por chave-pai.
  const LIST_ENUMS = {
    policyTypes: ['Ingress', 'Egress'],
    accessModes: ['ReadWriteOnce', 'ReadOnlyMany', 'ReadWriteMany', 'ReadWriteOncePod'],
  };
  // Snippets de chave (campo -> texto inserido).
  const FIELD_SNIPPETS = {
    apiVersion: 'apiVersion: ${1}', kind: 'kind: ${1}',
    metadata: 'metadata:\n  name: ${1:name}', name: 'name: ${1}', namespace: 'namespace: ${1:default}',
    labels: 'labels:\n  app: ${1:app}', annotations: 'annotations:\n  ${1:key}: ${2:value}',
    spec: 'spec:\n  ${1}',
    replicas: 'replicas: ${1:1}',
    selector: 'selector:\n  matchLabels:\n    app: ${1:app}',
    serviceName: 'serviceName: ${1:svc}',
    template: 'template:\n  metadata:\n    labels:\n      app: ${1:app}\n  spec:\n    containers:\n      - name: ${2:c}\n        image: ${3:nginx:alpine}',
    strategy: 'strategy:\n  type: ${1:RollingUpdate}',
    volumeClaimTemplates: 'volumeClaimTemplates:\n  - metadata:\n      name: ${1:dados}\n    spec:\n      accessModes: ["ReadWriteOnce"]\n      resources:\n        requests:\n          storage: ${2:1Gi}',
    containers: 'containers:\n  - name: ${1:c}\n    image: ${2:nginx:alpine}',
    image: 'image: ${1}', imagePullPolicy: 'imagePullPolicy: ${1:IfNotPresent}',
    ports: 'ports:\n  - containerPort: ${1:80}',
    env: 'env:\n  - name: ${1:KEY}\n    value: "${2:value}"',
    envFrom: 'envFrom:\n  - configMapRef:\n      name: ${1:cm}',
    resources: 'resources:\n  requests:\n    cpu: ${1:100m}\n    memory: ${2:128Mi}\n  limits:\n    cpu: ${3:500m}\n    memory: ${4:256Mi}',
    volumeMounts: 'volumeMounts:\n  - name: ${1:vol}\n    mountPath: ${2:/data}',
    volumes: 'volumes:\n  - name: ${1:vol}\n    emptyDir: {}',
    command: 'command: ["${1:sh}", "${2:-c}", "${3:echo hi}"]', args: 'args: ["${1}"]',
    livenessProbe: 'livenessProbe:\n  httpGet:\n    path: ${1:/}\n    port: ${2:80}\n  initialDelaySeconds: ${3:10}',
    readinessProbe: 'readinessProbe:\n  httpGet:\n    path: ${1:/}\n    port: ${2:80}',
    nodeSelector: 'nodeSelector:\n  ${1:disktype}: ${2:ssd}',
    serviceAccountName: 'serviceAccountName: ${1}', restartPolicy: 'restartPolicy: ${1:Never}',
    type: 'type: ${1}', port: 'port: ${1:80}', targetPort: 'targetPort: ${1:80}', protocol: 'protocol: ${1:TCP}',
    data: 'data:\n  ${1:key}: "${2:value}"', stringData: 'stringData:\n  ${1:key}: "${2:value}"',
    ingressClassName: 'ingressClassName: ${1:nginx}',
    rules: 'rules:\n  - host: ${1:host.example.com}\n    http:\n      paths:\n        - path: ${2:/}\n          pathType: ${3:Prefix}\n          backend:\n            service:\n              name: ${4:svc}\n              port:\n                number: ${5:80}',
    tls: 'tls:\n  - hosts:\n      - ${1:host.example.com}\n    secretName: ${2:tls-secret}',
    pathType: 'pathType: ${1:Prefix}',
    accessModes: 'accessModes: ["${1:ReadWriteOnce}"]', storageClassName: 'storageClassName: ${1}',
    volumeMode: 'volumeMode: ${1:Filesystem}',
    scaleTargetRef: 'scaleTargetRef:\n  apiVersion: apps/v1\n  kind: Deployment\n  name: ${1:app}',
    minReplicas: 'minReplicas: ${1:1}', maxReplicas: 'maxReplicas: ${1:5}',
    metrics: 'metrics:\n  - type: Resource\n    resource:\n      name: cpu\n      target:\n        type: Utilization\n        averageUtilization: ${1:70}',
    podSelector: 'podSelector: {}', policyTypes: 'policyTypes:\n  - ${1:Ingress}',
    schedule: 'schedule: "${1:*/5 * * * *}"',
    jobTemplate: 'jobTemplate:\n  spec:\n    template:\n      spec:\n        restartPolicy: OnFailure\n        containers:\n          - name: ${1:c}\n            image: ${2:busybox}',
    suspend: 'suspend: ${1:false}', concurrencyPolicy: 'concurrencyPolicy: ${1:Allow}',
    completions: 'completions: ${1:1}', parallelism: 'parallelism: ${1:1}', backoffLimit: 'backoffLimit: ${1:4}',
    automountServiceAccountToken: 'automountServiceAccountToken: ${1:true}',
    imagePullSecrets: 'imagePullSecrets:\n  - name: ${1:regcred}',
  };
  const K8S_COMMON_FIELDS = ['apiVersion', 'kind', 'metadata', 'name', 'namespace', 'labels', 'annotations'];
  const K8S_POD_KINDS = new Set(['Deployment', 'StatefulSet', 'DaemonSet', 'ReplicaSet', 'Job', 'CronJob', 'Pod']);
  const K8S_POD_CHILDREN = [
    'containers', 'image', 'imagePullPolicy', 'ports', 'env', 'envFrom', 'resources',
    'volumeMounts', 'volumes', 'command', 'args', 'livenessProbe', 'readinessProbe',
    'nodeSelector', 'serviceAccountName', 'restartPolicy', 'imagePullSecrets',
  ];
  const K8S_KIND_FIELDS = {
    Deployment: ['spec', 'replicas', 'selector', 'template', 'strategy'],
    StatefulSet: ['spec', 'replicas', 'selector', 'template', 'serviceName', 'volumeClaimTemplates'],
    DaemonSet: ['spec', 'selector', 'template'],
    ReplicaSet: ['spec', 'replicas', 'selector', 'template'],
    Pod: ['spec'],
    Service: ['spec', 'selector', 'type', 'ports', 'port', 'targetPort', 'protocol'],
    ConfigMap: ['data'],
    Secret: ['type', 'data', 'stringData'],
    Ingress: ['spec', 'ingressClassName', 'rules', 'tls'],
    Job: ['spec', 'template', 'completions', 'parallelism', 'backoffLimit'],
    CronJob: ['spec', 'schedule', 'jobTemplate', 'suspend', 'concurrencyPolicy'],
    PersistentVolumeClaim: ['spec', 'accessModes', 'resources', 'storageClassName', 'volumeMode'],
    HorizontalPodAutoscaler: ['spec', 'scaleTargetRef', 'minReplicas', 'maxReplicas', 'metrics'],
    NetworkPolicy: ['spec', 'podSelector', 'policyTypes'],
    ServiceAccount: ['automountServiceAccountToken', 'imagePullSecrets'],
  };

  function registerK8sCompletions(monaco) {
    if (k8sCompletionsDone) return;
    k8sCompletionsDone = true;
    const K = monaco.languages.CompletionItemKind;
    const SNIP = monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet;

    // kind do documento que contém `lineNumber` (entre marcadores `---`).
    const docKindAt = (model, lineNumber) => {
      const total = model.getLineCount();
      let start = 1, end = total;
      for (let l = lineNumber - 1; l >= 1; l--) {
        if (/^---/.test(model.getLineContent(l))) { start = l + 1; break; }
      }
      for (let l = lineNumber + 1; l <= total; l++) {
        if (/^---/.test(model.getLineContent(l))) { end = l - 1; break; }
      }
      for (let l = start; l <= end; l++) {
        const m = model.getLineContent(l).match(/^kind:\s*([A-Za-z0-9.]+)/);
        if (m) return m[1];
      }
      return null;
    };

    const valItems = (values, range) =>
      values.map((v) => ({ label: v, kind: K.Value, insertText: v, detail: 'valor k8s', range }));

    const keyItems = (kind, range) => {
      let names = K8S_COMMON_FIELDS.slice();
      if (kind && K8S_KIND_FIELDS[kind]) names = names.concat(K8S_KIND_FIELDS[kind]);
      else names = names.concat(['spec', 'data', 'type', 'selector', 'ports']);
      if (!kind || K8S_POD_KINDS.has(kind)) names = names.concat(K8S_POD_CHILDREN);
      const seen = new Set();
      const out = [];
      names.forEach((n) => {
        if (seen.has(n) || !FIELD_SNIPPETS[n]) return;
        seen.add(n);
        out.push({ label: n, kind: K.Field, insertText: FIELD_SNIPPETS[n], insertTextRules: SNIP, detail: 'campo k8s', range });
      });
      Object.entries(DEPLOY_TEMPLATES).forEach(([k, body]) =>
        out.push({ label: `k8s:${k}`, kind: K.Snippet, insertText: body, insertTextRules: SNIP, detail: 'esqueleto', range }));
      return out;
    };

    monaco.languages.registerCompletionItemProvider('yaml', {
      triggerCharacters: [':', ' ', '-'],
      provideCompletionItems(model, position) {
        const before = model.getLineContent(position.lineNumber).slice(0, position.column - 1);

        // 1) valor após "<chave>: " → enum específico, ou nada (não poluir valores).
        const vm = before.match(/^\s*(?:-\s+)?([A-Za-z0-9_.\-/]+):\s+(\S*)$/);
        if (vm) {
          const values = VALUE_ENUMS[vm[1]];
          if (!values) return { suggestions: [] };
          const typed = vm[2];
          const range = {
            startLineNumber: position.lineNumber, endLineNumber: position.lineNumber,
            startColumn: position.column - typed.length, endColumn: position.column,
          };
          return { suggestions: valItems(values, range) };
        }

        // 2) item de lista "- <valor>" → enum por chave-pai (senão cai p/ chaves).
        const lm = before.match(/^(\s*)-\s+(\S*)$/);
        if (lm) {
          const indent = lm[1].length;
          let parent = null;
          for (let l = position.lineNumber - 1; l >= 1; l--) {
            const t = model.getLineContent(l);
            if (/^\s*$/.test(t)) continue;
            const curIndent = (t.match(/^(\s*)/)[1] || '').length;
            const km = t.match(/^\s*([A-Za-z0-9_.\-/]+):\s*$/);
            if (km && curIndent < indent) { parent = km[1]; break; }
            if (curIndent < indent) break;
          }
          const values = LIST_ENUMS[parent];
          if (values) {
            const typed = lm[2];
            const range = {
              startLineNumber: position.lineNumber, endLineNumber: position.lineNumber,
              startColumn: position.column - typed.length, endColumn: position.column,
            };
            return { suggestions: valItems(values, range) };
          }
          // sem enum: segue para sugestões de chave (campos de objetos da lista)
        }

        // 3) contexto de chave → campos relevantes ao kind do documento.
        const w = model.getWordUntilPosition(position);
        const range = {
          startLineNumber: position.lineNumber, endLineNumber: position.lineNumber,
          startColumn: w.startColumn, endColumn: w.endColumn,
        };
        return { suggestions: keyItems(docKindAt(model, position.lineNumber), range) };
      },
    });
  }

  function yamlValue(inst) {
    return inst.mode === 'diff' && inst.diffEditor
      ? inst.diffEditor.getModel().modified.getValue()
      : inst.editor
      ? inst.editor.getValue()
      : '';
  }

  // Descarta o diff editor E os dois models que demos a ele. diffEditor.dispose()
  // não dispõe os models passados via setModel — sem isto eles vazam no registry
  // global do Monaco a cada alternância de diff.
  function disposeDiff(inst) {
    if (!inst.diffEditor) return;
    try {
      const m = inst.diffEditor.getModel();
      if (m) {
        m.original && m.original.dispose();
        m.modified && m.modified.dispose();
      }
    } catch (_) {}
    try { inst.diffEditor.dispose(); } catch (_) {}
    inst.diffEditor = null;
  }

  // Entra no modo diff: `leftText` no lado esquerdo (read-only) vs `rightText`
  // no direito (editável). `rightText` omitido = conteúdo atual do editor.
  // Reutilizado pelo botão Diff (esquerda = original carregado), pelo histórico
  // (esquerda = uma versão) e pela comparação entre duas versões (A vs B).
  function enterYamlDiff(inst, leftText, rightText) {
    const monaco = window.monaco;
    if (!monaco) return;
    const cont = inst.els.editor;
    const right = rightText === undefined ? yamlValue(inst) : rightText;
    if (inst.mode === 'diff') disposeDiff(inst);
    else if (inst.editor) { inst.editor.dispose(); inst.editor = null; }
    cont.innerHTML = '';
    inst.diffEditor = monaco.editor.createDiffEditor(cont, {
      theme: 'vs-dark', automaticLayout: true, fontSize: 13, renderSideBySide: true,
      scrollBeyondLastLine: false, originalEditable: false,
    });
    inst.diffEditor.setModel({
      original: monaco.editor.createModel(leftText, 'yaml'),
      modified: monaco.editor.createModel(right, 'yaml'),
    });
    inst.mode = 'diff';
    inst.els.diff.innerHTML = '<i class="fas fa-pen"></i> Editor';
  }

  function exitYamlDiff(inst) {
    const monaco = window.monaco;
    if (!monaco) return;
    const cont = inst.els.editor;
    const current = yamlValue(inst);
    disposeDiff(inst);
    cont.innerHTML = '';
    inst.editor = monaco.editor.create(cont, {
      value: current, language: 'yaml', theme: 'vs-dark', automaticLayout: true,
      minimap: { enabled: false }, fontSize: 13, tabSize: 2, scrollBeyondLastLine: false,
    });
    inst.mode = 'edit';
    inst.els.diff.innerHTML = '<i class="fas fa-code-compare"></i> Diff';
  }

  function toggleYamlDiff(inst) {
    if (inst.mode === 'edit') enterYamlDiff(inst, inst.original);
    else exitYamlDiff(inst);
  }

  function setYamlStatus(inst, kind, label) {
    const map = { loading: 'unknown', ok: 'connected', error: 'unreachable' };
    const box = inst.els.status;
    box.querySelector('.status-dot').className = `status-dot ${map[kind] || 'unknown'}`;
    box.querySelector('span:last-child').textContent = label;
  }

  function manifestParams(inst) {
    const p = { cluster_id: inst.cur.id, kind: inst.kind, name: inst.name };
    if (inst.ns) p.namespace = inst.ns;
    return new URLSearchParams(p).toString();
  }

  async function openYaml(cur, kind, name, ns) {
    const key = `yaml|${cur.id}|${kind}|${ns || ''}|${name}`;
    const { id, pane, isNew } = openOrFocusTab('yaml', key, name, { kind, name, ns: ns || '' });
    if (!isNew) return; // já aberto: apenas foca a aba
    const inst = (yamlInst[id] = {
      id, pane, editor: null, diffEditor: null, original: '', mode: 'edit',
      cur, kind, name, ns: ns || '',
      els: {
        title: pane.querySelector('.yaml-title'),
        status: pane.querySelector('.yaml-status'),
        editor: pane.querySelector('.yaml-editor'),
        diff: pane.querySelector('.yaml-diff'),
        apply: pane.querySelector('.yaml-apply'),
        history: pane.querySelector('.yaml-history'),
      },
    });
    inst.els.title.textContent = `YAML · ${ns ? ns + '/' : ''}${name}`;
    inst.els.diff.addEventListener('click', () => toggleYamlDiff(inst));
    inst.els.apply.addEventListener('click', () => applyYaml(inst));
    inst.els.history.addEventListener('click', () => openYamlHistory(inst));
    setYamlStatus(inst, 'loading', 'carregando…');

    let monaco;
    try {
      monaco = await ensureMonaco();
    } catch (e) {
      setYamlStatus(inst, 'error', 'erro');
      return window.toast('Falha ao carregar o editor: ' + e.message, 'error');
    }
    inst.els.editor.innerHTML = '';
    inst.editor = monaco.editor.create(inst.els.editor, {
      value: '',
      language: 'yaml',
      theme: 'vs-dark',
      automaticLayout: true,
      minimap: { enabled: false },
      fontSize: 13,
      tabSize: 2,
      scrollBeyondLastLine: false,
    });
    try {
      const data = await api('/resources/manifest?' + manifestParams(inst));
      inst.original = data.yaml;
      inst.editor.setValue(data.yaml);
      setYamlStatus(inst, 'ok', 'pronto para editar');
    } catch (e) {
      inst.original = '';
      inst.editor.setValue('# erro ao carregar: ' + e.message);
      setYamlStatus(inst, 'error', 'erro');
    }
  }

  async function applyYaml(inst) {
    setYamlStatus(inst, 'loading', 'aplicando…');
    try {
      const data = await api('/resources/manifest?' + manifestParams(inst), {
        method: 'PUT',
        body: JSON.stringify({ yaml: yamlValue(inst) }),
      });
      inst.original = data.yaml;
      if (inst.mode === 'diff' && inst.diffEditor) {
        inst.diffEditor.getModel().original.setValue(data.yaml);
        inst.diffEditor.getModel().modified.setValue(data.yaml);
      } else if (inst.editor) {
        inst.editor.setValue(data.yaml);
      }
      setYamlStatus(inst, 'ok', 'aplicado');
      window.toast('Alterações aplicadas', 'success');
    } catch (e) {
      setYamlStatus(inst, 'error', 'erro');
      window.toast('Falha ao aplicar: ' + e.message, 'error');
    }
  }

  // Define o conteúdo do editor respeitando o modo (edição/diff).
  function setYamlEditorValue(inst, text) {
    if (inst.mode === 'diff' && inst.diffEditor) {
      inst.diffEditor.getModel().modified.setValue(text);
    } else if (inst.editor) {
      inst.editor.setValue(text);
    }
  }

  // Histórico de versões do YAML (até 5, salvas a cada "Aplicar").
  async function openYamlHistory(inst) {
    let versions;
    try {
      versions = await api('/resources/manifest/versions?' + manifestParams(inst));
    } catch (e) {
      return window.toast('Falha ao carregar histórico: ' + e.message, 'error');
    }
    if (!versions.length) {
      return window.toast('Sem versões salvas ainda. Elas são gravadas a cada "Aplicar".', 'info');
    }
    const labelOf = (i) => (i === 0 ? 'Atual' : 'v' + (versions.length - i));
    const labelById = {};
    versions.forEach((v, i) => (labelById[v.id] = labelOf(i)));
    const rowsHtml = versions
      .map(
        (v, i) =>
          `<div class="yaml-ver-row" data-vid="${v.id}">` +
          `<input type="checkbox" class="yaml-ver-cb" data-sel="${v.id}" title="Selecionar para comparar">` +
          `<div class="yaml-ver-info"><strong>${labelOf(i)}</strong>` +
          `<span class="m">${new Date(v.created_at).toLocaleString()} · ${v.size} B</span></div>` +
          `<div class="yaml-ver-actions">` +
          `<button class="btn btn-sm" data-diff="${v.id}" title="Comparar esta versão com o editor atual"><i class="fas fa-code-compare"></i> Diff</button>` +
          `<button class="btn btn-sm" data-load="${v.id}"><i class="fas fa-rotate-left"></i> Carregar</button>` +
          `<button class="btn btn-sm btn-danger" data-del="${v.id}" title="Excluir versão"><i class="fas fa-trash"></i></button>` +
          `</div></div>`
      )
      .join('');
    await uiModal({
      title: 'Histórico de versões',
      icon: 'fa-clock-rotate-left',
      width: 'min(620px,94vw)',
      body:
        `<div class="yaml-ver-bar"><span class="hint">Marque 2 versões para comparar (A=mais antiga → B=mais nova).</span>` +
        `<button class="btn btn-sm" data-compare disabled><i class="fas fa-code-compare"></i> Comparar A↔B</button></div>` +
        `<div class="yaml-ver-list">${rowsHtml}</div>` +
        `<p style="margin-top:10px;font-size:var(--font-size-xs);color:var(--text-tertiary)">Carregar coloca a versão no editor — revise e clique em <strong>Aplicar</strong>. São mantidas até 5 versões.</p>`,
      actions: [{ label: 'Fechar', value: undefined }],
      onOpen: (bd, done) => {
        // Seleção (máx. 2) para comparar duas versões entre si.
        const selected = [];
        const compareBtn = bd.querySelector('[data-compare]');
        const sync = () => {
          compareBtn.disabled = selected.length !== 2;
        };
        bd.querySelectorAll('[data-sel]').forEach((cb) =>
          cb.addEventListener('change', () => {
            const id = parseInt(cb.dataset.sel, 10);
            if (cb.checked) {
              selected.push(id);
              if (selected.length > 2) {
                const drop = selected.shift(); // descarta a seleção mais antiga
                const old = bd.querySelector(`[data-sel="${drop}"]`);
                if (old) old.checked = false;
              }
            } else {
              const idx = selected.indexOf(id);
              if (idx >= 0) selected.splice(idx, 1);
            }
            sync();
          })
        );
        compareBtn.addEventListener('click', async () => {
          if (selected.length !== 2) return;
          // A = mais antiga (esquerda), B = mais nova (direita/editável).
          const pair = selected
            .map((id) => versions.find((v) => v.id === id))
            .sort((a, b) => new Date(a.created_at) - new Date(b.created_at));
          try {
            const [a, b] = await Promise.all(
              pair.map((v) => api('/resources/manifest/versions/' + v.id))
            );
            enterYamlDiff(inst, a.yaml, b.yaml);
            setYamlStatus(inst, 'ok', `diff ${labelById[a.id]} → ${labelById[b.id]}`);
            done(undefined);
          } catch (e) {
            window.toast('Falha ao comparar versões: ' + e.message, 'error');
          }
        });
        bd.querySelectorAll('[data-diff]').forEach((b) =>
          b.addEventListener('click', async () => {
            try {
              const v = await api('/resources/manifest/versions/' + b.dataset.diff);
              enterYamlDiff(inst, v.yaml); // versão à esquerda (read-only) vs editor atual
              setYamlStatus(inst, 'ok', 'diff vs ' + new Date(v.created_at).toLocaleString());
              done(undefined);
            } catch (e) {
              window.toast('Falha ao comparar: ' + e.message, 'error');
            }
          })
        );
        bd.querySelectorAll('[data-load]').forEach((b) =>
          b.addEventListener('click', async () => {
            try {
              const v = await api('/resources/manifest/versions/' + b.dataset.load);
              setYamlEditorValue(inst, v.yaml);
              setYamlStatus(inst, 'ok', 'versão carregada — revise e Aplicar');
              window.toast('Versão carregada no editor', 'success');
              done(undefined);
            } catch (e) {
              window.toast('Falha ao carregar versão: ' + e.message, 'error');
            }
          })
        );
        bd.querySelectorAll('[data-del]').forEach((b) =>
          b.addEventListener('click', async () => {
            try {
              await api('/resources/manifest/versions/' + b.dataset.del, { method: 'DELETE' });
              const delId = parseInt(b.dataset.del, 10);
              const sidx = selected.indexOf(delId);
              if (sidx >= 0) { selected.splice(sidx, 1); sync(); }
              const row = bd.querySelector(`.yaml-ver-row[data-vid="${b.dataset.del}"]`);
              if (row) row.remove();
              if (!bd.querySelector('.yaml-ver-row')) done(undefined);
            } catch (e) {
              window.toast('Falha ao excluir versão: ' + e.message, 'error');
            }
          })
        );
      },
    });
  }

  // ---------- port forward ----------
  async function portForwardPod(cur, name, ns) {
    const remote = prompt(`Porta remota do pod "${name}":`, '80');
    if (remote === null) return;
    const rp = parseInt(remote, 10);
    if (Number.isNaN(rp)) return window.toast('Porta remota inválida', 'warning');
    const local = prompt('Porta local (vazio = automática):', '');
    if (local === null) return;
    const lp = local.trim() ? parseInt(local, 10) : 0;
    try {
      const f = await api(`/portforward?cluster_id=${cur.id}`, {
        method: 'POST',
        body: JSON.stringify({ namespace: ns, pod: name, remote_port: rp, local_port: lp }),
      });
      window.toast(`Túnel ativo: 127.0.0.1:${f.local_port} → ${name}:${rp}`, 'success', 6000);
    } catch (e) {
      window.toast('Falha no port-forward: ' + e.message, 'error');
    }
  }

  async function openForwards() {
    $('#fwd-body').innerHTML = '<div class="empty-state"><div class="spinner"></div></div>';
    openModal('#fwd-modal');
    refreshForwards();
  }

  async function refreshForwards() {
    let list = [];
    try {
      list = await api('/portforward');
    } catch (_) {}
    if (!list.length) {
      $('#fwd-body').innerHTML = '<p style="color:var(--text-tertiary)">Nenhum túnel ativo.</p>';
      return;
    }
    $('#fwd-body').innerHTML =
      '<table class="data-table"><thead><tr><th>Local</th><th>Pod</th><th>Remota</th><th></th></tr></thead><tbody>' +
      list
        .map(
          (f) =>
            `<tr><td><a href="http://127.0.0.1:${f.local_port}" target="_blank" style="color:var(--color-primary)">127.0.0.1:${f.local_port}</a></td>` +
            `<td>${esc(f.namespace)}/${esc(f.pod)}</td><td>${esc(f.remote_port)}</td>` +
            `<td class="actions"><button class="btn btn-sm btn-danger" data-stop="${f.id}" title="Parar"><i class="fas fa-stop"></i></button></td></tr>`
        )
        .join('') +
      '</tbody></table>';
    $('#fwd-body')
      .querySelectorAll('[data-stop]')
      .forEach((b) =>
        b.addEventListener('click', async () => {
          try {
            await api(`/portforward/${b.dataset.stop}`, { method: 'DELETE' });
            window.toast('Túnel parado', 'success');
            refreshForwards();
          } catch (e) {
            window.toast('Falha ao parar: ' + e.message, 'error');
          }
        })
      );
  }

  // ---------- Helm ----------
  let helmCur = null;

  async function viewHelm(cur) {
    setActions('<button class="btn btn-sm btn-primary" id="helm-install-btn"><i class="fas fa-download"></i> Instalar</button>');
    $('#helm-install-btn').addEventListener('click', () => openHelmInstall(cur));
    loading();
    let data;
    try {
      data = await api(`/helm/releases?cluster_id=${cur.id}`);
    } catch (e) {
      return emptyState('fa-circle-exclamation', 'Erro', e.message);
    }
    if (!data.available) return emptyState('fa-ship', 'Helm indisponível', data.message);
    const rels = data.releases || [];
    if (!rels.length) return emptyState('fa-ship', 'Nenhuma release Helm');
    const body = rels
      .map((r) => {
        const n = esc(r.name);
        const ns = esc(r.namespace);
        return (
          `<tr><td>${n}</td><td><span class="badge">${ns}</span></td><td>${esc(r.chart || '')}</td>` +
          `<td>${esc(r.revision || '')}</td><td>${esc(r.status || '')}</td>` +
          `<td class="actions">` +
          `<button class="btn btn-sm" data-hroll data-name="${n}" data-ns="${ns}" title="Rollback"><i class="fas fa-clock-rotate-left"></i></button> ` +
          `<button class="btn btn-sm btn-danger" data-hdel data-name="${n}" data-ns="${ns}" title="Remover"><i class="fas fa-trash"></i></button>` +
          `</td></tr>`
        );
      })
      .join('');
    $('#view-body').innerHTML =
      `<table class="data-table"><thead><tr><th>Nome</th><th>Namespace</th><th>Chart</th><th>Rev</th><th>Status</th><th></th></tr></thead><tbody>${body}</tbody></table>`;
    const q = (s) => $('#view-body').querySelectorAll(s);
    q('[data-hroll]').forEach((b) => b.addEventListener('click', () => helmRollback(cur, b.dataset.name, b.dataset.ns)));
    q('[data-hdel]').forEach((b) => b.addEventListener('click', () => helmUninstall(cur, b.dataset.name, b.dataset.ns)));
  }

  function openHelmInstall(cur) {
    helmCur = cur;
    $('#helm-name').value = '';
    $('#helm-chart').value = '';
    $('#helm-repo').value = '';
    $('#helm-version').value = '';
    $('#helm-ns').value = state.selectedNs.length === 1 ? state.selectedNs[0] : 'default';
    openModal('#helm-modal');
  }

  async function helmInstall() {
    if (!helmCur) return;
    const body = {
      name: $('#helm-name').value.trim(),
      chart: $('#helm-chart').value.trim(),
      namespace: $('#helm-ns').value.trim() || 'default',
      repo: $('#helm-repo').value.trim() || null,
      version: $('#helm-version').value.trim() || null,
    };
    if (!body.name || !body.chart) return window.toast('Informe nome e chart', 'warning');
    try {
      await api(`/helm/install?cluster_id=${helmCur.id}`, { method: 'POST', body: JSON.stringify(body) });
      window.toast('Release instalada', 'success');
      closeModal('#helm-modal');
      renderView();
    } catch (e) {
      window.toast('Falha ao instalar: ' + e.message, 'error');
    }
  }

  async function helmRollback(cur, name, ns) {
    const rev = prompt(`Revisão para rollback de "${name}":`, '1');
    if (rev === null) return;
    const revision = parseInt(rev, 10);
    if (Number.isNaN(revision)) return window.toast('Revisão inválida', 'warning');
    try {
      await api(`/helm/rollback?cluster_id=${cur.id}`, {
        method: 'POST',
        body: JSON.stringify({ name, namespace: ns, revision }),
      });
      window.toast('Rollback aplicado', 'success');
      renderView();
    } catch (e) {
      window.toast('Falha no rollback: ' + e.message, 'error');
    }
  }

  async function helmUninstall(cur, name, ns) {
    if (!confirm(`Remover a release "${name}"?`)) return;
    try {
      await api(`/helm?cluster_id=${cur.id}&name=${encodeURIComponent(name)}&namespace=${encodeURIComponent(ns)}`, {
        method: 'DELETE',
      });
      window.toast('Release removida', 'success');
      renderView();
    } catch (e) {
      window.toast('Falha ao remover: ' + e.message, 'error');
    }
  }

  // ---------- deploy de manifestos (estilo Lens: Monaco + templates) ----------
  const DEPLOY_TEMPLATES = {
    namespace: `apiVersion: v1
kind: Namespace
metadata:
  name: meu-namespace
`,
    deployment: `apiVersion: apps/v1
kind: Deployment
metadata:
  name: meu-app
  labels:
    app: meu-app
spec:
  replicas: 1
  selector:
    matchLabels:
      app: meu-app
  template:
    metadata:
      labels:
        app: meu-app
    spec:
      containers:
        - name: meu-app
          image: nginx:alpine
          ports:
            - containerPort: 80
`,
    service: `apiVersion: v1
kind: Service
metadata:
  name: meu-app
spec:
  selector:
    app: meu-app
  ports:
    - port: 80
      targetPort: 80
  type: ClusterIP
`,
    configmap: `apiVersion: v1
kind: ConfigMap
metadata:
  name: meu-config
data:
  chave: valor
`,
    secret: `apiVersion: v1
kind: Secret
metadata:
  name: meu-secret
type: Opaque
stringData:
  senha: troque-me
`,
    pod: `apiVersion: v1
kind: Pod
metadata:
  name: meu-pod
spec:
  containers:
    - name: app
      image: nginx:alpine
`,
    ingress: `apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: meu-ingress
spec:
  rules:
    - host: meu-app.exemplo.com
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: meu-app
                port:
                  number: 80
`,
    job: `apiVersion: batch/v1
kind: Job
metadata:
  name: meu-job
spec:
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: job
          image: busybox
          command: ["sh", "-c", "echo ola && sleep 5"]
`,
    cronjob: `apiVersion: batch/v1
kind: CronJob
metadata:
  name: meu-cronjob
spec:
  schedule: "*/5 * * * *"
  jobTemplate:
    spec:
      template:
        spec:
          restartPolicy: OnFailure
          containers:
            - name: cron
              image: busybox
              command: ["sh", "-c", "date"]
`,
    statefulset: `apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: meu-sts
spec:
  serviceName: meu-sts
  replicas: 1
  selector:
    matchLabels:
      app: meu-sts
  template:
    metadata:
      labels:
        app: meu-sts
    spec:
      containers:
        - name: app
          image: nginx:alpine
          ports:
            - containerPort: 80
          volumeMounts:
            - name: dados
              mountPath: /data
  volumeClaimTemplates:
    - metadata:
        name: dados
      spec:
        accessModes: ["ReadWriteOnce"]
        resources:
          requests:
            storage: 1Gi
`,
    daemonset: `apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: meu-daemon
spec:
  selector:
    matchLabels:
      app: meu-daemon
  template:
    metadata:
      labels:
        app: meu-daemon
    spec:
      containers:
        - name: agent
          image: busybox
          command: ["sh", "-c", "sleep infinity"]
`,
    pvc: `apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: meu-pvc
spec:
  accessModes: ["ReadWriteOnce"]
  resources:
    requests:
      storage: 1Gi
`,
    hpa: `apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: meu-app
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: meu-app
  minReplicas: 1
  maxReplicas: 5
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
`,
    serviceaccount: `apiVersion: v1
kind: ServiceAccount
metadata:
  name: meu-sa
`,
    networkpolicy: `apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: nega-tudo-ingress
spec:
  podSelector: {}
  policyTypes:
    - Ingress
`,
  };

  let deployEditor = null;

  async function openDeploy() {
    if (!current()) return window.toast('Selecione um cluster primeiro', 'warning');
    $('#deploy-results').innerHTML = '';
    $('#deploy-ns').value = state.selectedNs.length === 1 ? state.selectedNs[0] : 'default';
    $('#deploy-template').value = '';
    // Datalist de namespaces do cluster (autocomplete no campo Namespace).
    const dl = $('#deploy-ns-list');
    if (dl) dl.innerHTML = (state.namespaces || []).map((n) => `<option value="${esc(n)}">`).join('');
    openModal('#deploy-modal');
    let monaco;
    try {
      monaco = await ensureMonaco();
    } catch (e) {
      return window.toast('Falha ao carregar o editor: ' + e.message, 'error');
    }
    if (!deployEditor) {
      deployEditor = monaco.editor.create($('#deploy-editor'), {
        value: '',
        language: 'yaml',
        theme: 'vs-dark',
        automaticLayout: true,
        minimap: { enabled: false },
        fontSize: 13,
        tabSize: 2,
        scrollBeyondLastLine: false,
      });
    } else {
      deployEditor.setValue('');
    }
  }

  // Abre o modal de Deploy já no Construtor, com o kind pré-selecionado.
  async function openBuilder(presetKind) {
    await openDeploy();
    if (!$('#deploy-modal').classList.contains('open')) return; // monaco falhou
    const panel = $('#deploy-builder');
    panel.hidden = false;
    initBuilderKinds();
    if (presetKind && MANIFEST_BUILDERS[presetKind]) $('#builder-kind').value = presetKind;
    renderBuilderFields();
  }

  // kind da view (plural) -> chave do builder (singular).
  const CREATE_KINDS = {
    deployments: 'deployment', services: 'service', configmaps: 'configmap',
    secrets: 'secret', ingress: 'ingress', limitranges: 'limitrange',
    resourcequotas: 'resourcequota', jobs: 'job', cronjobs: 'cronjob',
    pvc: 'pvc', namespaces: 'namespace',
  };
  // Define o botão "Criar" como ação padrão da view (se o kind tiver builder).
  function setCreateAction(kind) {
    const bk = CREATE_KINDS[kind];
    if (!bk) { viewActions = null; return; }
    const label = MANIFEST_BUILDERS[bk].label;
    viewActions = {
      html: `<button class="btn btn-sm btn-primary" id="view-create"><i class="fas fa-plus"></i> Criar ${esc(label)}</button>`,
      wire: () => {
        const b = $('#view-create');
        if (b) b.addEventListener('click', () => openBuilder(bk));
      },
    };
  }

  function appendDeployYaml(texts) {
    const blob = texts.map((t) => t.trim()).filter(Boolean).join('\n---\n');
    if (!blob || !deployEditor) return;
    const cur = deployEditor.getValue().trim();
    deployEditor.setValue(cur ? cur + '\n---\n' + blob : blob);
  }

  function insertTemplate(key) {
    const tpl = DEPLOY_TEMPLATES[key];
    if (!tpl || !deployEditor) return;
    const cur = deployEditor.getValue().trim();
    deployEditor.setValue(cur ? cur + '\n---\n' + tpl : tpl);
  }

  const isYaml = (n) => /\.ya?ml$/i.test(n);

  // Recursão em diretórios via FileSystem API (drag de pastas).
  function readEntry(entry, out) {
    return new Promise((resolve) => {
      if (entry.isFile) {
        if (!isYaml(entry.name)) return resolve();
        entry.file((f) => f.text().then((t) => (out.push(t), resolve()), resolve), resolve);
      } else if (entry.isDirectory) {
        const reader = entry.createReader();
        const pending = [];
        const readBatch = () =>
          reader.readEntries((ents) => {
            if (!ents.length) return Promise.all(pending).then(() => resolve());
            ents.forEach((e) => pending.push(readEntry(e, out)));
            readBatch();
          }, resolve);
        readBatch();
      } else resolve();
    });
  }

  async function handleDrop(e) {
    e.preventDefault();
    $('#drop-zone').classList.remove('dragover');
    const out = [];
    const entries = [...(e.dataTransfer.items || [])]
      .map((i) => (i.webkitGetAsEntry ? i.webkitGetAsEntry() : null))
      .filter(Boolean);
    if (entries.length) {
      await Promise.all(entries.map((en) => readEntry(en, out)));
    } else {
      for (const f of [...(e.dataTransfer.files || [])]) if (isYaml(f.name)) out.push(await f.text());
    }
    reportLoaded(out);
  }

  async function filesToYaml(fileList) {
    const out = [];
    for (const f of [...fileList]) if (isYaml(f.name)) out.push(await f.text());
    reportLoaded(out);
  }

  function reportLoaded(texts) {
    if (texts.length) {
      appendDeployYaml(texts);
      window.toast(`${texts.length} arquivo(s) YAML carregado(s)`, 'success');
    } else {
      window.toast('Nenhum arquivo .yaml/.yml encontrado', 'warning');
    }
  }

  async function applyDeploy() {
    const cur = current();
    if (!cur) return window.toast('Selecione um cluster', 'warning');
    const yamlText = (deployEditor ? deployEditor.getValue() : '').trim();
    if (!yamlText) return window.toast('Nada para aplicar', 'warning');
    $('#deploy-apply').disabled = true;
    try {
      const data = await api(`/resources/apply?cluster_id=${cur.id}`, {
        method: 'POST',
        body: JSON.stringify({ yaml: yamlText, namespace: $('#deploy-ns').value.trim() || 'default' }),
      });
      renderDeployResults(data.results);
      const created = data.results.filter((r) => r.status === 'created').length;
      const configured = data.results.filter((r) => r.status === 'configured').length;
      const err = data.results.length - created - configured;
      const parts = [];
      if (created) parts.push(`${created} criado(s)`);
      if (configured) parts.push(`${configured} atualizado(s)`);
      if (err) parts.push(`${err} com erro`);
      window.toast(parts.join(', ') || 'nada aplicado', err ? 'warning' : 'success');
      renderView();
    } catch (e) {
      window.toast('Falha no deploy: ' + e.message, 'error');
    } finally {
      $('#deploy-apply').disabled = false;
    }
  }

  const DEPLOY_STATUS_TAG = { valid: 'válido', created: 'criado', configured: 'atualizado' };
  function renderDeployResults(results) {
    $('#deploy-results').innerHTML =
      '<h4 style="margin:var(--space-4) 0 var(--space-2)">Resultado</h4>' +
      results
        .map((r) => {
          const ok = r.status !== 'error';
          const icon = ok ? 'fa-circle-check' : 'fa-circle-xmark';
          const color = ok ? 'var(--color-success)' : 'var(--color-danger)';
          const tag = DEPLOY_STATUS_TAG[r.status] ? ` (${DEPLOY_STATUS_TAG[r.status]})` : '';
          return (
            `<div class="deploy-result"><i class="fas ${icon}" style="color:${color}"></i>` +
            `<span>${esc(r.kind)}/${esc(r.namespace || '')}/${esc(r.name)}${tag}</span>` +
            (r.message ? `<span class="msg">${esc(r.message)}</span>` : '') +
            '</div>'
          );
        })
        .join('');
  }

  // Prévia de deploy: dry-run server-side + diff contra o estado vivo (kubectl diff).
  async function diffDeploy() {
    const cur = current();
    if (!cur) return window.toast('Selecione um cluster', 'warning');
    const yamlText = (deployEditor ? deployEditor.getValue() : '').trim();
    if (!yamlText) return window.toast('Nada para comparar', 'warning');
    $('#deploy-diff').disabled = true;
    $('#deploy-results').innerHTML =
      '<div class="empty-state" style="height:100px"><div class="spinner"></div></div>';
    try {
      const data = await api(`/resources/diff?cluster_id=${cur.id}`, {
        method: 'POST',
        body: JSON.stringify({ yaml: yamlText, namespace: $('#deploy-ns').value.trim() || 'default' }),
      });
      renderDiffResults(data.results);
    } catch (e) {
      $('#deploy-results').innerHTML = '';
      window.toast('Falha na prévia: ' + e.message, 'error');
    } finally {
      $('#deploy-diff').disabled = false;
    }
  }

  const DIFF_ACTION = {
    create: { label: 'novo', icon: 'fa-circle-plus', color: 'var(--color-success)' },
    update: { label: 'altera', icon: 'fa-pen', color: 'var(--color-warning,#d6a700)' },
    unchanged: { label: 'sem mudança', icon: 'fa-equals', color: 'var(--text-tertiary)' },
    error: { label: 'erro', icon: 'fa-circle-xmark', color: 'var(--color-danger)' },
  };
  function renderDiffResults(results) {
    const sections = results.map((r) => {
      const a = DIFF_ACTION[r.action] || DIFF_ACTION.error;
      const head =
        `<div class="deploy-result"><i class="fas ${a.icon}" style="color:${a.color}"></i>` +
        `<span>${esc(r.kind)}/${esc(r.namespace || '')}/${esc(r.name)} ` +
        `<span class="badge">${a.label}</span></span>` +
        (r.message ? `<span class="msg">${esc(r.message)}</span>` : '') + '</div>';
      if (!r.changes || !r.changes.length) return head;
      const rows = r.changes.map((c) =>
        `<tr><td style="color:var(--text-secondary)">${esc(c.path)}</td>` +
        `<td style="color:var(--color-danger)">${c.old == null ? '<i>—</i>' : esc(String(c.old))}</td>` +
        `<td style="color:var(--color-success)">${c.new == null ? '<i>—</i>' : esc(String(c.new))}</td></tr>`
      ).join('');
      return head +
        `<table class="data-table" style="margin:var(--space-1) 0 var(--space-3)">` +
        `<thead><tr><th>Campo</th><th>Atual</th><th>Novo</th></tr></thead><tbody>${rows}</tbody></table>`;
    }).join('');
    const changed = results.filter((r) => r.action === 'update' || r.action === 'create').length;
    $('#deploy-results').innerHTML =
      `<h4 style="margin:var(--space-4) 0 var(--space-2)">Prévia · ${changed} com mudança(s)</h4>${sections}`;
  }

  function clearDeploy() {
    if (deployEditor) deployEditor.setValue('');
    $('#deploy-results').innerHTML = '';
  }
  async function copyDeploy() {
    const t = deployEditor ? deployEditor.getValue() : '';
    if (!t.trim()) return window.toast('Editor vazio', 'warning');
    try {
      await navigator.clipboard.writeText(t);
      window.toast('YAML copiado', 'success');
    } catch (_) {
      window.toast('Não foi possível copiar', 'error');
    }
  }
  function downloadDeploy() {
    const t = deployEditor ? deployEditor.getValue() : '';
    if (!t.trim()) return window.toast('Editor vazio', 'warning');
    const blob = new Blob([t], { type: 'application/yaml' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'manifests.yaml';
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  async function validateDeploy() {
    const cur = current();
    if (!cur) return window.toast('Selecione um cluster', 'warning');
    const yamlText = (deployEditor ? deployEditor.getValue() : '').trim();
    if (!yamlText) return window.toast('Nada para validar', 'warning');
    $('#deploy-validate').disabled = true;
    try {
      const data = await api(`/resources/validate?cluster_id=${cur.id}`, {
        method: 'POST',
        body: JSON.stringify({ yaml: yamlText, namespace: $('#deploy-ns').value.trim() || 'default' }),
      });
      renderDeployResults(data.results);
      const ok = data.results.filter((r) => r.status === 'valid').length;
      const err = data.results.length - ok;
      window.toast(`${ok} válido(s)` + (err ? `, ${err} inválido(s)` : ''), err ? 'warning' : 'success');
    } catch (e) {
      window.toast('Falha na validação: ' + e.message, 'error');
    } finally {
      $('#deploy-validate').disabled = false;
    }
  }

  // ---------- construtor de manifestos (formulário -> YAML) ----------
  const parseKV = (text) =>
    (text || '')
      .split('\n')
      .map((l) => l.trim())
      .filter(Boolean)
      .map((l) => {
        const i = l.indexOf('=');
        return i < 0 ? [l, ''] : [l.slice(0, i).trim(), l.slice(i + 1).trim()];
      });

  const F = {
    text: (key, label, def, ph) => ({ key, label, type: 'text', def: def || '', ph: ph || '' }),
    num: (key, label, def) => ({ key, label, type: 'number', def: def ?? '' }),
    area: (key, label, ph) => ({ key, label, type: 'area', def: '', ph: ph || '' }),
    sel: (key, label, options) => ({ key, label, type: 'select', def: options[0], options }),
  };

  const MANIFEST_BUILDERS = {
    deployment: {
      label: 'Deployment',
      fields: [
        F.text('name', 'Nome', 'meu-app'), F.text('namespace', 'Namespace', 'default'),
        F.text('image', 'Imagem', 'nginx:alpine'), F.num('replicas', 'Réplicas', 1),
        F.num('port', 'Porta do container (opcional)'),
        F.area('env', 'Variáveis (KEY=VALUE por linha)', 'ENV=prod'),
      ],
      build: (v) => {
        const app = v.name || 'app';
        const lines = ['apiVersion: apps/v1', 'kind: Deployment', 'metadata:',
          `  name: ${v.name}`, `  namespace: ${v.namespace || 'default'}`,
          '  labels:', `    app: ${app}`, 'spec:', `  replicas: ${v.replicas || 1}`,
          '  selector:', '    matchLabels:', `      app: ${app}`,
          '  template:', '    metadata:', '      labels:', `        app: ${app}`,
          '    spec:', '      containers:', `        - name: ${v.name}`,
          `          image: ${v.image || 'nginx:alpine'}`];
        if (v.port) lines.push('          ports:', `            - containerPort: ${v.port}`);
        const env = parseKV(v.env);
        if (env.length) {
          lines.push('          env:');
          env.forEach(([k, val]) => lines.push(`            - name: ${k}`, `              value: "${val}"`));
        }
        return lines.join('\n') + '\n';
      },
    },
    service: {
      label: 'Service',
      fields: [
        F.text('name', 'Nome', 'meu-svc'), F.text('namespace', 'Namespace', 'default'),
        F.text('selector', 'Selector (app=)', 'meu-app'),
        F.sel('type', 'Tipo', ['ClusterIP', 'NodePort', 'LoadBalancer']),
        F.num('port', 'Porta', 80), F.num('targetPort', 'Target port', 80),
      ],
      build: (v) =>
        ['apiVersion: v1', 'kind: Service', 'metadata:', `  name: ${v.name}`,
          `  namespace: ${v.namespace || 'default'}`, 'spec:', `  type: ${v.type}`,
          '  selector:', `    app: ${v.selector}`, '  ports:',
          `    - port: ${v.port || 80}`, `      targetPort: ${v.targetPort || 80}`].join('\n') + '\n',
    },
    configmap: {
      label: 'ConfigMap',
      fields: [F.text('name', 'Nome', 'meu-config'), F.text('namespace', 'Namespace', 'default'),
        F.area('data', 'Dados (KEY=VALUE por linha)', 'chave=valor')],
      build: (v) => {
        const lines = ['apiVersion: v1', 'kind: ConfigMap', 'metadata:', `  name: ${v.name}`,
          `  namespace: ${v.namespace || 'default'}`, 'data:'];
        parseKV(v.data).forEach(([k, val]) => lines.push(`  ${k}: "${val}"`));
        return lines.join('\n') + '\n';
      },
    },
    secret: {
      label: 'Secret',
      fields: [F.text('name', 'Nome', 'meu-secret'), F.text('namespace', 'Namespace', 'default'),
        F.area('data', 'stringData (KEY=VALUE por linha)', 'senha=troque-me')],
      build: (v) => {
        const lines = ['apiVersion: v1', 'kind: Secret', 'metadata:', `  name: ${v.name}`,
          `  namespace: ${v.namespace || 'default'}`, 'type: Opaque', 'stringData:'];
        parseKV(v.data).forEach(([k, val]) => lines.push(`  ${k}: "${val}"`));
        return lines.join('\n') + '\n';
      },
    },
    ingress: {
      label: 'Ingress',
      fields: [F.text('name', 'Nome', 'meu-ingress'), F.text('namespace', 'Namespace', 'default'),
        F.text('ingressClassName', 'IngressClass (opcional)', 'nginx'),
        F.area('rules', 'Regras: host,path,service,porta (uma por linha)', 'app.exemplo.com,/,meu-svc,80'),
        F.text('tlsSecret', 'TLS secret (opcional)', ''),
        F.text('tlsHosts', 'TLS hosts (vírgula, opcional)', '')],
      build: (v) => {
        const lines = ['apiVersion: networking.k8s.io/v1', 'kind: Ingress', 'metadata:',
          `  name: ${v.name}`, `  namespace: ${v.namespace || 'default'}`, 'spec:'];
        if (v.ingressClassName) lines.push(`  ingressClassName: ${v.ingressClassName}`);
        const byHost = {};
        (v.rules || '').split('\n').map((l) => l.trim()).filter(Boolean).forEach((l) => {
          const [host, path, svc, port] = l.split(',').map((s) => (s || '').trim());
          (byHost[host] = byHost[host] || []).push({ path: path || '/', svc, port: port || '80' });
        });
        const hosts = Object.keys(byHost);
        if (hosts.length) {
          lines.push('  rules:');
          hosts.forEach((h) => {
            lines.push(`    - host: ${h}`, '      http:', '        paths:');
            byHost[h].forEach((p) =>
              lines.push(`          - path: ${p.path}`, '            pathType: Prefix',
                '            backend:', '              service:', `                name: ${p.svc}`,
                '                port:', `                  number: ${p.port}`));
          });
        }
        if (v.tlsSecret || v.tlsHosts) {
          lines.push('  tls:', `    - secretName: ${v.tlsSecret}`);
          const th = (v.tlsHosts || '').split(',').map((s) => s.trim()).filter(Boolean);
          if (th.length) { lines.push('      hosts:'); th.forEach((h) => lines.push(`        - ${h}`)); }
        }
        return lines.join('\n') + '\n';
      },
    },
    limitrange: {
      label: 'LimitRange',
      fields: [F.text('name', 'Nome', 'limites'), F.text('namespace', 'Namespace', 'default'),
        F.text('defCpu', 'CPU default (limit)', '500m'), F.text('defMem', 'Memória default (limit)', '512Mi'),
        F.text('reqCpu', 'CPU defaultRequest', '100m'), F.text('reqMem', 'Memória defaultRequest', '128Mi')],
      build: (v) =>
        ['apiVersion: v1', 'kind: LimitRange', 'metadata:', `  name: ${v.name}`,
          `  namespace: ${v.namespace || 'default'}`, 'spec:', '  limits:', '    - type: Container',
          '      default:', `        cpu: ${v.defCpu || '500m'}`, `        memory: ${v.defMem || '512Mi'}`,
          '      defaultRequest:', `        cpu: ${v.reqCpu || '100m'}`, `        memory: ${v.reqMem || '128Mi'}`].join('\n') + '\n',
    },
    resourcequota: {
      label: 'ResourceQuota',
      fields: [F.text('name', 'Nome', 'cota'), F.text('namespace', 'Namespace', 'default'),
        F.text('reqCpu', 'requests.cpu', '2'), F.text('reqMem', 'requests.memory', '4Gi'),
        F.text('limCpu', 'limits.cpu', '4'), F.text('limMem', 'limits.memory', '8Gi'),
        F.num('pods', 'pods (máx)')],
      build: (v) => {
        const lines = ['apiVersion: v1', 'kind: ResourceQuota', 'metadata:', `  name: ${v.name}`,
          `  namespace: ${v.namespace || 'default'}`, 'spec:', '  hard:'];
        if (v.reqCpu) lines.push(`    requests.cpu: "${v.reqCpu}"`);
        if (v.reqMem) lines.push(`    requests.memory: ${v.reqMem}`);
        if (v.limCpu) lines.push(`    limits.cpu: "${v.limCpu}"`);
        if (v.limMem) lines.push(`    limits.memory: ${v.limMem}`);
        if (v.pods) lines.push(`    pods: "${v.pods}"`);
        return lines.join('\n') + '\n';
      },
    },
    job: {
      label: 'Job',
      fields: [F.text('name', 'Nome', 'meu-job'), F.text('namespace', 'Namespace', 'default'),
        F.text('image', 'Imagem', 'busybox'), F.text('command', 'Comando', 'echo ola')],
      build: (v) =>
        ['apiVersion: batch/v1', 'kind: Job', 'metadata:', `  name: ${v.name}`,
          `  namespace: ${v.namespace || 'default'}`, 'spec:', '  template:', '    spec:',
          '      restartPolicy: Never', '      containers:', `        - name: ${v.name}`,
          `          image: ${v.image || 'busybox'}`,
          `          command: ["sh", "-c", "${(v.command || 'echo ola').replace(/"/g, '\\"')}"]`].join('\n') + '\n',
    },
    cronjob: {
      label: 'CronJob',
      fields: [F.text('name', 'Nome', 'meu-cron'), F.text('namespace', 'Namespace', 'default'),
        F.text('schedule', 'Schedule (cron)', '*/5 * * * *'),
        F.text('image', 'Imagem', 'busybox'), F.text('command', 'Comando', 'date')],
      build: (v) =>
        ['apiVersion: batch/v1', 'kind: CronJob', 'metadata:', `  name: ${v.name}`,
          `  namespace: ${v.namespace || 'default'}`, 'spec:', `  schedule: "${v.schedule || '*/5 * * * *'}"`,
          '  jobTemplate:', '    spec:', '      template:', '        spec:',
          '          restartPolicy: OnFailure', '          containers:', `            - name: ${v.name}`,
          `              image: ${v.image || 'busybox'}`,
          `              command: ["sh", "-c", "${(v.command || 'date').replace(/"/g, '\\"')}"]`].join('\n') + '\n',
    },
    pvc: {
      label: 'PersistentVolumeClaim',
      fields: [F.text('name', 'Nome', 'meu-pvc'), F.text('namespace', 'Namespace', 'default'),
        F.text('size', 'Tamanho', '1Gi'), F.sel('access', 'Acesso', ['ReadWriteOnce', 'ReadWriteMany', 'ReadOnlyMany']),
        F.text('storageClass', 'StorageClass (opcional)', '')],
      build: (v) => {
        const lines = ['apiVersion: v1', 'kind: PersistentVolumeClaim', 'metadata:',
          `  name: ${v.name}`, `  namespace: ${v.namespace || 'default'}`, 'spec:',
          '  accessModes:', `    - ${v.access}`, '  resources:', '    requests:',
          `      storage: ${v.size || '1Gi'}`];
        if (v.storageClass) lines.push(`  storageClassName: ${v.storageClass}`);
        return lines.join('\n') + '\n';
      },
    },
    namespace: {
      label: 'Namespace',
      fields: [F.text('name', 'Nome', 'meu-namespace')],
      build: (v) => ['apiVersion: v1', 'kind: Namespace', 'metadata:', `  name: ${v.name}`].join('\n') + '\n',
    },
  };

  function initBuilderKinds() {
    const sel = $('#builder-kind');
    if (sel.dataset.ready) return;
    sel.innerHTML = Object.entries(MANIFEST_BUILDERS).map(([k, b]) => `<option value="${k}">${esc(b.label)}</option>`).join('');
    sel.dataset.ready = '1';
    sel.addEventListener('change', renderBuilderFields);
  }

  function renderBuilderFields() {
    const kind = $('#builder-kind').value;
    const b = MANIFEST_BUILDERS[kind];
    $('#builder-fields').innerHTML = b.fields
      .map((f) => {
        const id = `bf-${f.key}`;
        let input;
        if (f.type === 'area') input = `<textarea class="input" id="${id}" rows="3" placeholder="${esc(f.ph)}"></textarea>`;
        else if (f.type === 'select')
          input = `<select class="input" id="${id}">${f.options.map((o) => `<option>${esc(o)}</option>`).join('')}</select>`;
        else input = `<input class="input" id="${id}" type="${f.type}" value="${esc(f.def)}" placeholder="${esc(f.ph || '')}">`;
        return `<div class="builder-field"><label>${esc(f.label)}</label>${input}</div>`;
      })
      .join('');
  }

  function generateManifest() {
    const kind = $('#builder-kind').value;
    const b = MANIFEST_BUILDERS[kind];
    const v = {};
    b.fields.forEach((f) => (v[f.key] = $(`#bf-${f.key}`).value.trim()));
    if (!v.name) return window.toast('Informe um nome', 'warning');
    appendDeployYaml([b.build(v)]);
    $('#deploy-builder').hidden = true;
    window.toast(`${b.label} gerado no editor`, 'success');
  }

  // ---------- painel de detalhes (drawer, estilo Lens) ----------
  function closeDetail() {
    stopDetailMetrics();
    $('#detail-drawer').classList.remove('open');
    $('#detail-backdrop').classList.remove('open');
  }

  async function openDetail(cur, kind, name, ns, row, columns) {
    $('#detail-kind').textContent = kind;
    $('#detail-name').textContent = name;
    $('#detail-body').innerHTML = '<div class="empty-state" style="height:140px"><div class="spinner"></div></div>';
    renderDetailActions(cur, kind, name, ns, [], null);
    $('#detail-drawer').classList.add('open');
    $('#detail-backdrop').classList.add('open');

    let obj;
    try {
      const nsq = ns ? `&namespace=${encodeURIComponent(ns)}` : '';
      const d = await api(`/resources/detail?cluster_id=${cur.id}&kind=${kind}&name=${encodeURIComponent(name)}${nsq}`);
      obj = d.object || {};
    } catch (e) {
      $('#detail-body').innerHTML = `<p style="color:var(--color-danger)">${esc(e.message)}</p>`;
      return;
    }
    const containers = ((obj.spec && obj.spec.containers) || []).map((c) => c.name);
    renderDetailActions(cur, kind, name, ns, containers, obj);
    renderDetailBody(cur, kind, name, ns, row, columns, obj);
  }

  function renderDetailActions(cur, kind, name, ns, containers, obj) {
    const acts = [];
    if (kind === 'pods') {
      acts.push(['fa-file-lines', 'Logs', () => showLogs(cur, name, ns, containers)]);
      acts.push(['fa-terminal', 'Terminal', () => openTerminal(cur, name, ns, containers)]);
      acts.push(['fa-plug', 'Forward', () => portForwardPod(cur, name, ns)]);
    }
    if (kind === 'nodes') {
      acts.push(['fa-terminal', 'Terminal', () => openNodeTerminal(cur, name)]);
      const cordoned = !!(obj && obj.spec && obj.spec.unschedulable);
      acts.push([
        cordoned ? 'fa-circle-check' : 'fa-ban',
        cordoned ? 'Uncordon' : 'Cordon',
        () => cordonNode(cur, name, !cordoned),
      ]);
      acts.push(['fa-truck-medical', 'Drain', () => drainNode(cur, name)]);
    }
    if (kind === 'deployments')
      acts.push([
        'fa-code-branch', 'Rollout',
        () => openRollout(cur, kind, name, ns, !!(obj && obj.spec && obj.spec.paused)),
      ]);
    if (kind === 'cronjobs') {
      const suspended = !!(obj && obj.spec && obj.spec.suspend);
      acts.push(['fa-play', 'Executar agora', () => triggerCronjob(cur, name, ns)]);
      acts.push([
        suspended ? 'fa-circle-play' : 'fa-pause',
        suspended ? 'Reativar' : 'Suspender',
        () => toggleCronjobSuspend(cur, name, ns, suspended),
      ]);
    }
    if (SCALABLE.has(kind)) acts.push(['fa-up-down', 'Escalar', () => scaleWorkload(cur, kind, name, ns)]);
    if (RESTARTABLE.has(kind)) acts.push(['fa-arrows-rotate', 'Reiniciar', () => restartWorkload(cur, kind, name, ns)]);
    if (RESTARTABLE.has(kind) && obj) acts.push(['fa-microchip', 'Recursos', () => editResources(cur, kind, name, ns, obj)]);
    if (DATA_KINDS.has(kind)) acts.push(['fa-eye', 'Dados', () => openData(cur, kind, name, ns)]);
    acts.push(['fa-file-code', 'YAML', () => openYaml(cur, kind, name, ns)]);
    acts.push([
      'fa-trash', 'Remover',
      async () => {
        const ok = await uiConfirm({
          title: 'Remover recurso',
          danger: true,
          message: `Remover <b>${esc(name)}</b>?<br>Esta ação não pode ser desfeita.`,
          confirmLabel: 'Remover',
        });
        if (!ok) return;
        try {
          const q = `cluster_id=${cur.id}&kind=${kind}&name=${encodeURIComponent(name)}` + (ns ? `&namespace=${encodeURIComponent(ns)}` : '');
          await api(`/resources?${q}`, { method: 'DELETE' });
          window.toast('Removido', 'success');
          closeDetail();
          renderView();
        } catch (e) {
          window.toast('Falha ao remover: ' + e.message, 'error');
        }
      },
      'btn-danger',
    ]);

    const box = $('#detail-actions');
    box.innerHTML = '';
    acts.forEach(([icon, label, fn, cls]) => {
      const b = el(`<button class="btn btn-sm ${cls || ''}"><i class="fas ${icon}"></i> ${label}</button>`);
      b.addEventListener('click', fn);
      box.appendChild(b);
    });
  }

  const detailSection = (title, html) => `<div class="detail-section"><h4>${esc(title)}</h4>${html}</div>`;
  const kvHTML = (rows) =>
    '<dl class="kv">' +
    rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v === null || v === undefined || v === '' ? '-' : esc(v)}</dd>`).join('') +
    '</dl>';
  // Igual a kvHTML, mas os valores são HTML já montado (badges) — não escapa o valor.
  const kvRaw = (rows) =>
    '<dl class="kv">' +
    rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v === null || v === undefined || v === '' ? '-' : v}</dd>`).join('') +
    '</dl>';
  function phaseBadge(phase) {
    const p = (phase || '').toLowerCase();
    const cls = p === 'running' || p === 'succeeded' ? 'success' : p === 'pending' ? 'warning' : p === 'failed' || p === 'unknown' ? 'danger' : '';
    return `<span class="badge ${cls}">${esc(phase || '—')}</span>`;
  }
  // Badge de uma condição (status.conditions[].status): True/False/Unknown.
  function condBadge(status) {
    const s = (status || '').toLowerCase();
    const cls = s === 'true' ? 'success' : s === 'false' ? 'warning' : 'danger';
    return `<span class="badge ${cls}">${esc(status || '—')}</span>`;
  }
  // Badge do estado de um container (status.containerStatuses[].state).
  function containerStateBadge(cs) {
    if (!cs || !cs.state) return '<span class="badge">—</span>';
    if (cs.state.running) return `<span class="badge success">Running</span>`;
    if (cs.state.waiting) return `<span class="badge warning">Waiting${cs.state.waiting.reason ? ' · ' + esc(cs.state.waiting.reason) : ''}</span>`;
    if (cs.state.terminated) {
      const t = cs.state.terminated;
      const cls = t.exitCode === 0 ? 'success' : 'danger';
      return `<span class="badge ${cls}">Terminated · ${esc(t.reason || '')} (exit ${t.exitCode})</span>`;
    }
    return '<span class="badge">—</span>';
  }
  const chipsHTML = (map, truncate) =>
    '<div class="chips">' +
    Object.entries(map)
      .map(([k, v]) => {
        let s = `${k}=${v}`;
        if (truncate && s.length > 64) s = s.slice(0, 64) + '…';
        return `<span class="chip-tag">${esc(s)}</span>`;
      })
      .join('') +
    '</div>';
  function ageOf(ts) {
    try {
      const s = Math.max(0, (Date.now() - new Date(ts).getTime()) / 1000);
      const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
      return d ? `${d}d` : h ? `${h}h` : m ? `${m}m` : `${Math.floor(s)}s`;
    } catch (_) {
      return '';
    }
  }

  function renderDetailBody(cur, kind, name, ns, row, columns, obj) {
    const meta = obj.metadata || {};
    const out = [];

    if (row && columns && columns.length) {
      out.push(detailSection('Resumo', kvHTML(columns.filter((c) => c.key !== 'name').map((c) => [c.label, row[c.key]]))));
    }

    // Pod: seção de Status (fase, uptime, prontidão, restarts, QoS, IPs), estilo Lens.
    const st = obj.status || {};
    const csList = st.containerStatuses || [];
    if (kind === 'pods') {
      const total = (obj.spec && obj.spec.containers ? obj.spec.containers.length : csList.length) || 0;
      const ready = csList.filter((c) => c.ready).length;
      const restarts = csList.reduce((t, c) => t + (c.restartCount || 0), 0);
      const start = st.startTime;
      const srows = [
        ['Fase', phaseBadge(st.phase) + (st.reason ? ' · ' + esc(st.reason) : '')],
        ['Pronto', `${ready}/${total} containers`],
        ['Reinícios', String(restarts)],
        ['Início', start ? `${new Date(start).toLocaleString()} · up ${ageOf(start)}` : '—'],
        ['QoS', st.qosClass || '—'],
        ['Node', (obj.spec && obj.spec.nodeName) || '—'],
        ['Pod IP', st.podIP || '—'],
        ['Host IP', st.hostIP || '—'],
      ];
      out.push(detailSection('Status', kvRaw(srows)));
    }

    const md = [];
    if (ns) md.push(['Namespace', ns]);
    const created = meta.creationTimestamp;
    if (created) md.push(['Criado', `${new Date(created).toLocaleString()} (${ageOf(created)})`]);
    if (meta.uid) md.push(['UID', meta.uid]);
    let metaHTML = kvHTML(md);
    if (meta.labels && Object.keys(meta.labels).length)
      metaHTML += `<h4 style="margin:var(--space-3) 0 var(--space-2);color:var(--text-tertiary);font-size:var(--font-size-xs)">LABELS</h4>${chipsHTML(meta.labels)}`;
    if (meta.annotations && Object.keys(meta.annotations).length)
      metaHTML += `<h4 style="margin:var(--space-3) 0 var(--space-2);color:var(--text-tertiary);font-size:var(--font-size-xs)">ANNOTATIONS</h4>${chipsHTML(meta.annotations, true)}`;
    out.push(detailSection('Metadados', metaHTML));

    const spec = obj.spec || {};
    const conts = spec.containers || (spec.template && spec.template.spec && spec.template.spec.containers) || [];
    // Para pods, junta o status ao vivo de cada container (status.containerStatuses).
    const csByName = {};
    csList.forEach((c) => (csByName[c.name] = c));

    // Monta o card de um container, com status quando for um pod ao vivo.
    const containerCard = (c, csMap) => {
      const cs = csMap[c.name];
      const ports = (c.ports || []).map((p) => `${p.containerPort}/${p.protocol || 'TCP'}`).join(', ');
      const res = c.resources || {};
      const head =
        `<div class="name">${esc(c.name)}` +
        (cs ? ` ${containerStateBadge(cs)} <span class="badge ${cs.ready ? 'success' : 'warning'}">${cs.ready ? 'ready' : 'not ready'}</span>` : '') +
        '</div>';
      const lines = [`<div class="sub">${esc(c.image || '')}</div>`];
      if (cs) {
        let r = `reinícios: ${cs.restartCount || 0}`;
        const last = cs.lastState && cs.lastState.terminated;
        if (last) r += ` · último fim: ${esc(last.reason || '')} (exit ${last.exitCode})`;
        lines.push(`<div class="sub">${r}</div>`);
      }
      if (ports) lines.push(`<div class="sub">portas: ${esc(ports)}</div>`);
      if ((res.requests && Object.keys(res.requests).length) || (res.limits && Object.keys(res.limits).length))
        lines.push(`<div class="sub">req ${esc(JSON.stringify(res.requests || {}))} · lim ${esc(JSON.stringify(res.limits || {}))}</div>`);
      return `<div class="detail-card">${head}${lines.join('')}</div>`;
    };

    if (conts.length) {
      out.push(detailSection(`Containers (${conts.length})`, conts.map((c) => containerCard(c, csByName)).join('')));
    }
    // Init containers (pods) — com seu próprio status.
    const initConts = spec.initContainers || [];
    if (initConts.length) {
      const initCs = {};
      (st.initContainerStatuses || []).forEach((c) => (initCs[c.name] = c));
      out.push(detailSection(`Init Containers (${initConts.length})`, initConts.map((c) => containerCard(c, initCs)).join('')));
    }

    // Ingress: regras (host → path → service:porta), TLS e endereço do LB.
    if (kind === 'ingress') {
      const rules = spec.rules || [];
      const ruleRows = rules
        .flatMap((r) =>
          ((r.http && r.http.paths) || []).map((p) => {
            const b = (p.backend && p.backend.service) || {};
            const port = b.port ? (b.port.number || b.port.name || '') : '';
            return `<tr><td>${esc(r.host || '*')}</td><td>${esc(p.path || '/')}</td>` +
              `<td>${esc(b.name || '')}${port ? ':' + esc(String(port)) : ''}</td></tr>`;
          })
        )
        .join('');
      let ing = kvHTML([
        ['IngressClass', spec.ingressClassName || '—'],
        ['Endereço', ((obj.status && obj.status.loadBalancer && obj.status.loadBalancer.ingress) || [])
          .map((i) => i.ip || i.hostname).filter(Boolean).join(', ') || '—'],
      ]);
      if (ruleRows)
        ing += `<table class="data-table" style="margin-top:var(--space-2)"><thead><tr><th>Host</th><th>Path</th><th>Backend</th></tr></thead><tbody>${ruleRows}</tbody></table>`;
      const tls = spec.tls || [];
      if (tls.length)
        ing += '<h4 style="margin:var(--space-3) 0 var(--space-2);color:var(--text-tertiary);font-size:var(--font-size-xs)">TLS</h4>' +
          kvHTML(tls.map((t) => [t.secretName || '(sem secret)', (t.hosts || []).join(', ') || '*']));
      out.push(detailSection('Ingress', ing));
    }

    const conds = (obj.status && obj.status.conditions) || [];
    if (conds.length)
      out.push(detailSection('Condições', kvRaw(conds.map((c) => {
        const extras = [c.reason, c.lastTransitionTime ? 'há ' + ageOf(c.lastTransitionTime) : ''].filter(Boolean).map(esc).join(' · ');
        const val = condBadge(c.status) + (extras ? ` <span class="cond-extra">${extras}</span>` : '') +
          (c.message ? `<div class="sub" title="${esc(c.message)}">${esc(c.message)}</div>` : '');
        return [c.type, val];
      }))));

    $('#detail-body').innerHTML = out.join('') + '<div id="detail-extra"></div>';

    if (kind === 'pods' || kind === 'nodes') startDetailMetrics(cur, kind, name, ns);
    loadDetailEvents(cur, name, ns);
  }

  // Métricas ao vivo (CPU/memória) com gráfico de área, para pods e nós.
  let detailMetricsTimer = null;
  const detailMetrics = { cpu: [], mem: [] };

  function stopDetailMetrics() {
    if (detailMetricsTimer) {
      clearInterval(detailMetricsTimer);
      detailMetricsTimer = null;
    }
  }

  function startDetailMetrics(cur, kind, name, ns) {
    stopDetailMetrics();
    detailMetrics.cpu = [];
    detailMetrics.mem = [];
    $('#detail-extra').insertAdjacentHTML('beforeend', '<div id="detail-metrics"></div>');
    const topKind = kind === 'nodes' ? 'nodes' : 'pods';
    const poll = async () => {
      const host = $('#detail-metrics');
      if (!host) return stopDetailMetrics(); // drawer fechou/trocou
      try {
        const nsq = kind === 'pods' && ns ? `&namespace=${encodeURIComponent(ns)}` : '';
        const data = await api(`/metrics/top?cluster_id=${cur.id}&kind=${topKind}${nsq}`);
        if (!data.available) {
          host.innerHTML = detailSection('Métricas', `<p style="color:var(--text-tertiary)">${esc(data.message || 'Indisponível')}</p>`);
          return stopDetailMetrics();
        }
        const m = data.rows.find((r) => r.name === name);
        if (!m) {
          host.innerHTML = detailSection('Métricas', '<p style="color:var(--text-tertiary)">Sem amostras para este recurso.</p>');
          return;
        }
        detailMetrics.cpu.push(m.cpu);
        detailMetrics.mem.push(m.memory);
        if (detailMetrics.cpu.length > 60) {
          detailMetrics.cpu.shift();
          detailMetrics.mem.shift();
        }
        const cpuLabel = `${m.cpu} m` + (m.cpu_pct != null ? ` · ${m.cpu_pct}%` : '');
        const memVal = m.memory >= 1024 ? `${(m.memory / 1024).toFixed(1)} GiB` : `${m.memory} MiB`;
        const memLabel = memVal + (m.memory_pct != null ? ` · ${m.memory_pct}%` : '');
        host.innerHTML = detailSection(
          'Métricas',
          metricChart('CPU', detailMetrics.cpu, cpuLabel, 'var(--color-primary)') +
            metricChart('Memória', detailMetrics.mem, memLabel, 'var(--color-success)')
        );
      } catch (_) {}
    };
    poll();
    detailMetricsTimer = setInterval(poll, 5000);
  }

  function metricChart(title, values, label, color) {
    return (
      `<div class="metric-chart"><div class="mc-head"><span>${esc(title)}</span><strong>${esc(label)}</strong></div>` +
      areaSpark(values, 480, 56, color) +
      '</div>'
    );
  }

  function areaSpark(values, w, h, color) {
    if (values.length < 2) return '<div class="mc-empty">coletando amostras…</div>';
    const max = Math.max(...values, 1);
    const min = Math.min(...values, 0);
    const span = max - min || 1;
    const pts = values.map((v, i) => [
      (i / (values.length - 1)) * w,
      h - ((v - min) / span) * (h - 6) - 3,
    ]);
    const line = pts.map((p) => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' ');
    const area = `0,${h} ${line} ${pts[pts.length - 1][0].toFixed(1)},${h}`;
    return (
      `<svg class="mc-svg" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">` +
      `<polygon points="${area}" fill="${color}" fill-opacity="0.15"></polygon>` +
      `<polyline points="${line}" fill="none" stroke="${color}" stroke-width="2"></polyline></svg>`
    );
  }

  async function loadDetailEvents(cur, name, ns) {
    try {
      const data = await api(`/resources?cluster_id=${cur.id}&kind=events` + (ns ? `&namespace=${encodeURIComponent(ns)}` : ''));
      const evs = data.rows.filter((r) => (r.object || '').endsWith('/' + name)).slice(-8).reverse();
      if (!evs.length) return;
      const html = evs
        .map(
          (e) =>
            `<div class="detail-card"><div class="name">${cell('type', e.type)} ${esc(e.reason)}</div>` +
            `<div class="sub">${esc(e.message || '')}</div><div class="sub">${esc(e.age)}</div></div>`
        )
        .join('');
      $('#detail-extra').insertAdjacentHTML('beforeend', detailSection('Eventos', html));
    } catch (_) {}
  }

  // ---------- modais ----------
  function openModal(sel) {
    $(sel).classList.add('open');
  }
  function closeModal(sel) {
    $(sel).classList.remove('open');
  }

  // ---------- painel inferior (dock estilo Lens, múltiplas abas) ----------
  // Cada aba é uma instância independente (logs/term/yaml) — inclusive várias do
  // mesmo tipo (logs de pods diferentes, vários terminais, etc.). Reabrir uma
  // ferramenta para um alvo já aberto FOCA a aba existente (dedupe por `key`).
  const DOCK_ICON = { logs: 'fa-file-lines', term: 'fa-terminal', yaml: 'fa-file-code', ai: 'fa-robot' };
  const DOCK_NAME = { logs: 'Logs', term: 'Console', yaml: 'YAML', ai: 'Assistente IA' };
  let dockSeq = 0;

  const dockTab = (id) => state.dock.tabs.find((t) => t.id === id) || null;
  const findDockTab = (key) => state.dock.tabs.find((t) => t.key === key) || null;
  const dockPane = (id) => $('#dock-body').querySelector(`[data-inst="${id}"]`);

  // Cria uma nova aba/instância: clona o <template> do tipo, insere o painel e ativa.
  function addDockTab(type, key, title, meta) {
    const id = ++dockSeq;
    const pane = $('#tpl-dock-' + type).content.firstElementChild.cloneNode(true);
    pane.dataset.inst = id;
    pane.hidden = true;
    $('#dock-body').appendChild(pane);
    // `meta` guarda o necessário para reabrir a aba ao voltar ao cluster.
    state.dock.tabs.push({ id, type, title: title || DOCK_NAME[type], key, meta: meta || {} });
    $('#dock').hidden = false;
    applyDockHeight();
    activateDockTab(id);
    persistSession();
    return { id, pane };
  }

  // Foca a aba existente para `key`, ou cria uma nova. Retorna { id, pane, isNew }.
  function openOrFocusTab(type, key, title, meta) {
    const found = findDockTab(key);
    if (found) {
      if (title) found.title = title;
      activateDockTab(found.id);
      return { id: found.id, pane: dockPane(found.id), isNew: false };
    }
    return { ...addDockTab(type, key, title, meta), isNew: true };
  }

  function renderDockTabs() {
    $('#dock-tabs').innerHTML = state.dock.tabs
      .map(
        (t) =>
          `<button class="dock-tab ${t.id === state.dock.activeId ? 'active' : ''}" data-tabid="${t.id}" title="${esc(t.title)}">` +
          `<i class="fas ${DOCK_ICON[t.type]}"></i> <span>${esc(t.title)}</span>` +
          `<i class="fas fa-xmark dock-tab-x" data-tabclose="${t.id}" title="Fechar aba"></i></button>`
      )
      .join('');
    $('#dock-tabs')
      .querySelectorAll('[data-tabid]')
      .forEach((b) =>
        b.addEventListener('click', (e) => {
          if (e.target.closest('[data-tabclose]')) return;
          activateDockTab(parseInt(b.dataset.tabid, 10));
        })
      );
    $('#dock-tabs')
      .querySelectorAll('[data-tabclose]')
      .forEach((b) => b.addEventListener('click', () => closeDockTab(parseInt(b.dataset.tabclose, 10))));
  }

  function activateDockTab(id) {
    if (!dockTab(id)) return;
    state.dock.activeId = id;
    $('#dock-body')
      .querySelectorAll('.dock-pane')
      .forEach((p) => (p.hidden = parseInt(p.dataset.inst, 10) !== id));
    renderDockTabs();
    requestAnimationFrame(() => relayoutDock());
    setTimeout(relayoutDock, 60);
    persistSession();
  }

  function closeDockTab(id) {
    cleanupInstance(id);
    const pane = dockPane(id);
    if (pane) pane.remove();
    const idx = state.dock.tabs.findIndex((t) => t.id === id);
    if (idx >= 0) state.dock.tabs.splice(idx, 1);
    if (!state.dock.tabs.length) {
      state.dock.activeId = null;
      $('#dock').hidden = true;
      persistSession();
      return;
    }
    if (state.dock.activeId === id) {
      const next = state.dock.tabs[Math.min(idx, state.dock.tabs.length - 1)];
      activateDockTab(next.id);
    } else {
      renderDockTabs();
    }
    persistSession();
  }

  // Libera os recursos da instância (socket/xterm/editor) ao fechar a aba.
  function cleanupInstance(id) {
    const tab = dockTab(id);
    if (!tab) return;
    if (tab.type === 'logs') {
      closeLogSocket(logsInst[id]);
      delete logsInst[id];
    } else if (tab.type === 'term') {
      closeTermSocket(termInst[id]);
      delete termInst[id];
    } else if (tab.type === 'yaml') {
      const inst = yamlInst[id];
      if (inst) {
        try { inst.editor && inst.editor.dispose(); } catch (_) {}
        disposeDiff(inst); // dispõe diff editor + seus dois models
      }
      delete yamlInst[id];
    } else if (tab.type === 'ai') {
      const inst = aiInst[id];
      if (inst && inst.socket) { try { inst.socket.close(); } catch (_) {} }
      delete aiInst[id];
    }
  }

  function closeDock(persist = true) {
    // fecha TODAS as abas (botão × do painel; também na troca de cluster)
    state.dock.tabs.slice().forEach((t) => cleanupInstance(t.id));
    state.dock.tabs = [];
    state.dock.activeId = null;
    $('#dock-body').innerHTML = '';
    $('#dock').hidden = true;
    if (persist) persistSession(); // no switch de cluster passamos false (já salvamos antes)
  }

  function applyDockHeight() {
    const dock = $('#dock');
    if (state.dockMax) {
      const content = dock.parentElement;
      dock.style.height = Math.max(160, content.clientHeight - 44) + 'px';
    } else {
      dock.style.height = (state.dockHeight || 320) + 'px';
    }
  }

  function toggleDockMax() {
    state.dockMax = !state.dockMax;
    $('#dock-max').classList.toggle('active', state.dockMax);
    applyDockHeight();
    relayoutDock();
  }

  // Re-mede a instância ATIVA quando o dock muda de tamanho (xterm/Monaco não se ajustam sozinhos).
  function relayoutDock() {
    const tab = dockTab(state.dock.activeId);
    if (!tab) return;
    if (tab.type === 'term') {
      const inst = termInst[tab.id];
      if (inst) {
        safeFit(inst);
        sendTermResize(inst);
      }
    } else if (tab.type === 'yaml') {
      const inst = yamlInst[tab.id];
      if (inst) {
        try {
          inst.editor && inst.editor.layout();
          inst.diffEditor && inst.diffEditor.layout();
        } catch (_) {}
      }
    }
  }

  function bindDockResize() {
    const handle = $('#dock-resize');
    const dock = $('#dock');
    let raf = 0;
    handle.addEventListener('mousedown', (e) => {
      e.preventDefault();
      if (state.dockMax) return; // não redimensiona enquanto maximizado
      handle.classList.add('resizing');
      document.body.style.userSelect = 'none';
      document.body.style.cursor = 'ns-resize';
      const bottom = dock.parentElement.getBoundingClientRect().bottom;
      const maxH = dock.parentElement.clientHeight - 90;
      const onMove = (ev) => {
        const h = Math.max(140, Math.min(bottom - ev.clientY, maxH));
        state.dockHeight = h;
        dock.style.height = h + 'px';
        if (!raf) raf = requestAnimationFrame(() => {
          raf = 0;
          relayoutDock();
        });
      };
      const onUp = () => {
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
        handle.classList.remove('resizing');
        document.body.style.userSelect = '';
        document.body.style.cursor = '';
        try {
          localStorage.setItem('lensfy.dockHeight', String(state.dockHeight));
        } catch (_) {}
        relayoutDock();
      };
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    });
    // restaura altura salva
    try {
      const saved = parseInt(localStorage.getItem('lensfy.dockHeight') || '', 10);
      if (saved >= 140) state.dockHeight = saved;
    } catch (_) {}
  }

  // Modal genérico baseado em promessa (substitui confirm/prompt nativos).
  // actions: [{label, value, cls, primary, onClick(bd)->valor|undefined}].
  // onClick que retorna undefined mantém o modal aberto (ex.: validação falhou).
  function uiModal({ title, body, actions, width, onOpen, icon }) {
    return new Promise((resolve) => {
      const bd = document.createElement('div');
      bd.className = 'modal-backdrop open';
      bd.innerHTML =
        `<div class="modal modal-sm" style="width:${width || 'min(440px,94vw)'}">` +
        `<div class="modal-header"><h3>${icon ? `<i class="fas ${icon}"></i> ` : ''}${esc(title)}</h3>` +
        `<button class="close-btn" data-x>&times;</button></div>` +
        `<div class="modal-body">${body}</div>` +
        `<div class="modal-footer"></div></div>`;
      document.body.appendChild(bd);
      const done = (v) => {
        document.removeEventListener('keydown', onKey);
        bd.remove();
        resolve(v);
      };
      const footer = bd.querySelector('.modal-footer');
      (actions || []).forEach((a) => {
        const b = el(`<button class="btn ${a.cls || ''}">${a.label}</button>`);
        b.addEventListener('click', () => {
          if (a.onClick) {
            const r = a.onClick(bd);
            if (r !== undefined) done(r);
          } else done(a.value);
        });
        footer.appendChild(b);
        if (a.primary) bd._primary = b;
      });
      bd.querySelector('[data-x]').addEventListener('click', () => done(undefined));
      bd.addEventListener('click', (e) => {
        if (e.target === bd) done(undefined);
      });
      const onKey = (e) => {
        if (e.key === 'Escape') done(undefined);
        else if (e.key === 'Enter' && bd._primary && !/textarea/i.test(e.target.tagName)) bd._primary.click();
      };
      document.addEventListener('keydown', onKey);
      if (onOpen) onOpen(bd, done);
      setTimeout(() => (bd.querySelector('[autofocus]') || bd._primary)?.focus(), 30);
    });
  }

  // Confirmação estilizada -> Promise<bool>.
  function uiConfirm({ title, message, confirmLabel = 'Confirmar', danger = false, icon }) {
    return uiModal({
      title,
      icon: icon || (danger ? 'fa-triangle-exclamation' : 'fa-circle-question'),
      body: `<p class="modal-text">${message}</p>`,
      actions: [
        { label: 'Cancelar', value: false },
        {
          label: `<i class="fas ${danger ? 'fa-trash' : 'fa-check'}"></i> ${confirmLabel}`,
          value: true,
          cls: danger ? 'btn-danger' : 'btn-primary',
          primary: true,
        },
      ],
    }).then((v) => v === true);
  }

  // Modal de escala com stepper + presets -> Promise<number|null>.
  function uiScale({ title, current = 1, count = 1 }) {
    const start = Number.isFinite(+current) && current !== '' ? +current : 1;
    const body =
      (count > 1 ? `<p class="modal-text">Aplicar a <b>${count}</b> workload(s).</p>` : '') +
      `<label class="form-label">Número de réplicas</label>` +
      `<div class="scale-stepper">` +
      `<button class="btn" type="button" data-step="-1" aria-label="Diminuir">−</button>` +
      `<input class="input" id="scl-val" type="number" min="0" step="1" value="${start}" autofocus>` +
      `<button class="btn" type="button" data-step="1" aria-label="Aumentar">+</button>` +
      `</div>` +
      `<div class="scale-presets">` +
      [0, 1, 2, 3, 5, 10].map((n) => `<button class="btn btn-sm" type="button" data-preset="${n}">${n}</button>`).join('') +
      `</div>`;
    return uiModal({
      title,
      icon: 'fa-up-down',
      body,
      onOpen: (bd) => {
        const inp = bd.querySelector('#scl-val');
        const clamp = (v) => Math.max(0, v | 0);
        bd.querySelectorAll('[data-step]').forEach((b) =>
          b.addEventListener('click', () => {
            inp.value = clamp((parseInt(inp.value, 10) || 0) + +b.dataset.step);
          })
        );
        bd.querySelectorAll('[data-preset]').forEach((b) =>
          b.addEventListener('click', () => {
            inp.value = b.dataset.preset;
          })
        );
      },
      actions: [
        { label: 'Cancelar', value: null },
        {
          label: '<i class="fas fa-up-down"></i> Escalar',
          cls: 'btn-primary',
          primary: true,
          onClick: (bd) => {
            const v = parseInt(bd.querySelector('#scl-val').value, 10);
            if (Number.isNaN(v) || v < 0) {
              window.toast('Valor inválido', 'warning');
              return; // mantém aberto
            }
            return v;
          },
        },
      ],
    }).then((v) => (v === undefined || v === null ? null : v));
  }

  // ---------- init ----------
  function bind() {
    // Seletor de cluster (dropdown)
    $('#cluster-btn').addEventListener('click', (e) => {
      e.stopPropagation();
      const m = $('#cluster-menu');
      const opening = m.hidden;
      m.hidden = !opening;
      if (opening) {
        $('#cluster-search').value = '';
        renderClusterMenu();
        positionClusterMenu();
        $('#cluster-search').focus();
      }
    });
    window.addEventListener('resize', () => {
      if (!$('#cluster-menu').hidden) positionClusterMenu();
    });
    $('#cluster-menu').addEventListener('click', (e) => e.stopPropagation());
    $('#cluster-search').addEventListener('input', renderClusterMenu);
    // Reordenar por drag-and-drop: move o item arrastado ao vivo na lista.
    $('#cluster-options').addEventListener('dragover', (e) => {
      const dragging = $('#cluster-options .dragging');
      if (!dragging) return;
      e.preventDefault();
      const after = dragAfterElement($('#cluster-options'), e.clientY);
      if (after == null) $('#cluster-options').appendChild(dragging);
      else $('#cluster-options').insertBefore(dragging, after);
    });
    $('#cluster-add').addEventListener('click', () => {
      $('#cluster-menu').hidden = true;
      openImport();
    });
    document.addEventListener('click', () => {
      $('#cluster-menu').hidden = true;
    });

    // Importar cluster (acessado pelo "+ Importar cluster" no menu de clusters)
    $('#import-detect').addEventListener('click', detectContexts);
    $('#gcloud-list').addEventListener('click', gcloudListClusters);
    $('#import-do').addEventListener('click', doImport);
    $('#import-source')
      .querySelectorAll('[data-src]')
      .forEach((b) => b.addEventListener('click', () => selectImportSource(b.dataset.src)));
    $('#import-file').addEventListener('change', async (e) => {
      const f = e.target.files[0];
      importState.fileContent = f ? await f.text() : '';
    });

    $('#btn-ai').addEventListener('click', () => openAI(current()));
    // Navegação promovida ao menu superior.
    $('#nav-issues').addEventListener('click', () => goView('issues'));
    $('#nav-budget').addEventListener('click', () => goView('budget'));
    $('#nav-map').addEventListener('click', () => goView('map'));
    $('#nav-security').addEventListener('click', () => goView('security'));
    $('#nav-rbac').addEventListener('click', () => goView('rbac'));
    $('#nav-capacity').addEventListener('click', () => goView('capacity'));
    $('#nav-impact').addEventListener('click', () => goView('impact'));
    $('#nav-search').addEventListener('click', () => goView('search'));
    $('#btn-refresh').addEventListener('click', refreshCluster);
    document.querySelectorAll('[data-close]').forEach((b) =>
      b.addEventListener('click', () => b.closest('.modal-backdrop').classList.remove('open'))
    );
    document.querySelectorAll('.modal-backdrop').forEach((bd) =>
      bd.addEventListener('click', (e) => {
        if (e.target === bd) bd.classList.remove('open');
      })
    );

    // Os controles de logs/console/YAML são ligados por painel ao criar cada
    // aba (ver showLogs/openTerminal/openYaml) — não há mais binding global.

    // Busca global (command palette)
    const gs = $('#global-search');
    gs.addEventListener('input', runSearch);
    gs.addEventListener('focus', runSearch);
    gs.addEventListener('blur', () => setTimeout(() => ($('#search-results').hidden = true), 120));
    gs.addEventListener('keydown', (e) => {
      if (!searchMatches.length) {
        if (e.key === 'Escape') gs.blur();
        return;
      }
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        searchIdx = (searchIdx + 1) % searchMatches.length;
        renderSearch();
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        searchIdx = (searchIdx - 1 + searchMatches.length) % searchMatches.length;
        renderSearch();
      } else if (e.key === 'Enter') {
        e.preventDefault();
        chooseSearch(searchIdx);
      } else if (e.key === 'Escape') {
        gs.value = '';
        runSearch();
        gs.blur();
      }
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === '/' && !/^(INPUT|TEXTAREA)$/.test(document.activeElement.tagName) && !document.activeElement.isContentEditable) {
        e.preventDefault();
        gs.focus();
      }
    });

    // kubectl shell
    $('#btn-kubectl').addEventListener('click', () => openKubectlTerminal(current()));

    // Port forwards
    $('#btn-forwards').addEventListener('click', openForwards);

    // Helm
    $('#helm-do-install').addEventListener('click', helmInstall);

    // Painel de detalhes
    $('#detail-close').addEventListener('click', closeDetail);
    $('#detail-backdrop').addEventListener('click', closeDetail);
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') closeDetail();
    });

    // Deploy de manifestos
    $('#btn-deploy').addEventListener('click', openDeploy);
    $('#deploy-apply').addEventListener('click', applyDeploy);
    $('#deploy-validate').addEventListener('click', validateDeploy);
    $('#deploy-diff').addEventListener('click', diffDeploy);
    $('#deploy-copy').addEventListener('click', copyDeploy);
    $('#deploy-download').addEventListener('click', downloadDeploy);
    $('#deploy-clear').addEventListener('click', clearDeploy);
    // Construtor de manifestos
    $('#btn-builder').addEventListener('click', () => {
      const panel = $('#deploy-builder');
      panel.hidden = !panel.hidden;
      if (!panel.hidden) {
        initBuilderKinds();
        renderBuilderFields();
      }
    });
    $('#builder-gen').addEventListener('click', generateManifest);
    const dz = $('#drop-zone');
    ['dragover', 'dragenter'].forEach((ev) =>
      dz.addEventListener(ev, (e) => {
        e.preventDefault();
        dz.classList.add('dragover');
      })
    );
    ['dragleave', 'dragend'].forEach((ev) =>
      dz.addEventListener(ev, () => dz.classList.remove('dragover'))
    );
    dz.addEventListener('drop', handleDrop);
    $('#dz-files-btn').addEventListener('click', () => $('#dz-files').click());
    $('#dz-dir-btn').addEventListener('click', () => $('#dz-dir').click());
    $('#dz-files').addEventListener('change', (e) => filesToYaml(e.target.files));
    $('#dz-dir').addEventListener('change', (e) => filesToYaml(e.target.files));
    $('#deploy-template').addEventListener('change', (e) => {
      insertTemplate(e.target.value);
      e.target.value = '';
    });
    // Seletor multi de namespaces (popover com checkboxes)
    $('#ns-btn').addEventListener('click', (e) => {
      e.stopPropagation();
      const menu = $('#ns-menu');
      const opening = menu.hidden;
      menu.hidden = !opening;
      if (opening) {
        $('#ns-search').value = '';
        renderNamespacePicker();
        $('#ns-search').focus();
      }
    });
    // Debounce: clusters com muitos namespaces reconstroem 100+ nós por tecla.
    let nsSearchTimer = 0;
    $('#ns-search').addEventListener('input', () => {
      clearTimeout(nsSearchTimer);
      nsSearchTimer = setTimeout(renderNamespacePicker, 120);
    });
    $('#ns-menu').addEventListener('click', (e) => e.stopPropagation());
    document.addEventListener('click', () => {
      $('#ns-menu').hidden = true;
    });
    // Painel inferior (dock): fechar / maximizar / redimensionar.
    $('#dock-close').addEventListener('click', () => closeDock(true));
    $('#dock-max').addEventListener('click', toggleDockMax);
    bindDockResize();
  }

  // ---------- onboarding (primeira execução: gera o token de dispositivo) ----------
  // Busca o token já provisionado neste equipamento (endpoint isento de token).
  // Retorna o token, ou null se ainda não houver (=> mostrar onboarding).
  async function loadDeviceToken() {
    try {
      const data = await api('/onboarding/token'); // GET, sem gerar
      return data.token || null;
    } catch (_) {
      return null;
    }
  }

  function showOnboarding() {
    const scr = $('#onboarding');
    scr.hidden = false;
    const gen = $('#onb-generate');
    gen.addEventListener('click', async () => {
      gen.disabled = true;
      gen.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Gerando…';
      try {
        const data = await api('/onboarding/token', { method: 'POST' });
        DEVICE_TOKEN = data.token; // passa a autenticar /api e /ws
        $('#onb-token').value = data.token;
        $('#onb-intro').hidden = true;
        $('#onb-done').hidden = false;
      } catch (e) {
        gen.disabled = false;
        gen.innerHTML = '<i class="fas fa-key"></i> Gerar token e começar';
        window.toast('Falha ao gerar o token: ' + e.message, 'error');
      }
    });
    $('#onb-copy').addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText($('#onb-token').value);
        window.toast('Token copiado', 'success');
      } catch (_) {
        $('#onb-token').select();
        document.execCommand('copy');
      }
    });
    $('#onb-enter').addEventListener('click', () => {
      scr.hidden = true;
      loadClusters(); // só agora a SPA começa a falar com a API (já com token)
      checkForUpdate();
    });
  }

  // ---------- aviso de atualização disponível ----------
  async function checkForUpdate() {
    let d;
    try {
      d = await api('/update/status');
    } catch (_) {
      return; // best-effort: nunca atrapalha o uso
    }
    if (!d || !d.available) return;
    try {
      if (localStorage.getItem('lensfy.update.dismissed') === d.latest) return; // já dispensado
    } catch (_) {}
    showUpdateBanner(d);
  }

  function showUpdateBanner(d) {
    const bar = $('#update-banner');
    if (!bar) return;
    const ver = d.latest
      ? ` (<b>${esc(d.latest)}</b>${d.current ? ` · atual ${esc(d.current)}` : ''})`
      : '';
    const msg = d.latest_message ? ` <span class="upd-msg">${esc(d.latest_message)}</span>` : '';
    const link = d.latest_url
      ? ` <a href="${esc(d.latest_url)}" target="_blank" rel="noopener">ver no GitHub</a>`
      : '';
    bar.innerHTML =
      `<i class="fas fa-circle-arrow-up"></i> <span>Nova versão do Lensfy disponível${ver}.${msg} ` +
      `Atualize com <code>lensfy update</code>.${link}</span>` +
      `<span class="upd-spacer"></span>` +
      `<button class="upd-x" title="Dispensar" aria-label="Dispensar">&times;</button>`;
    bar.hidden = false;
    bar.querySelector('.upd-x').addEventListener('click', () => {
      bar.hidden = true;
      try {
        localStorage.setItem('lensfy.update.dismissed', d.latest || '');
      } catch (_) {}
    });
  }

  async function boot() {
    bind();
    if (!window.LENSFY_AUTH) {
      loadClusters(); // segurança desligada
      return checkForUpdate();
    }
    const token = await loadDeviceToken();
    if (token) {
      DEVICE_TOKEN = token;
      loadClusters();
      checkForUpdate();
    } else {
      showOnboarding(); // ainda sem token neste equipamento
    }
  }

  document.addEventListener('DOMContentLoaded', boot);
})();
