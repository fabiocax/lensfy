/* Toast notifications — modelo Fluxy. window.toast(msg, type). */
(function () {
  let container;
  function ensure() {
    if (!container) {
      container = document.createElement('div');
      container.id = 'toast-container';
      container.style.cssText =
        'position:fixed;top:16px;right:16px;display:flex;flex-direction:column;gap:8px;z-index:11000;';
      document.body.appendChild(container);
    }
    return container;
  }
  const ICONS = {
    success: 'fa-circle-check',
    error: 'fa-circle-exclamation',
    warning: 'fa-triangle-exclamation',
    info: 'fa-circle-info',
  };
  const COLORS = {
    success: 'var(--color-success)',
    error: 'var(--color-danger)',
    warning: 'var(--color-warning)',
    info: 'var(--color-primary)',
  };
  window.toast = function (message, type = 'info', timeout = 4000) {
    const el = document.createElement('div');
    el.style.cssText =
      'display:flex;align-items:center;gap:10px;min-width:240px;max-width:380px;' +
      'padding:12px 14px;border-radius:8px;background:var(--bg-light);' +
      'border:1px solid var(--border-color);border-left:3px solid ' +
      (COLORS[type] || COLORS.info) +
      ';box-shadow:var(--shadow-lg);color:var(--text-color);font-size:14px;' +
      'animation:toastIn .2s ease;';
    // Build with textContent for the message — it carries backend/Kubernetes
    // error text (resource names, field values) that must never be parsed as
    // HTML (XSS via e.g. a pod named `<img src=x onerror=…>`).
    const icon = document.createElement('i');
    icon.className = `fas ${ICONS[type] || ICONS.info}`;
    icon.style.color = COLORS[type] || COLORS.info;
    const span = document.createElement('span');
    span.style.flex = '1';
    span.textContent = message == null ? '' : String(message);
    el.append(icon, span);
    ensure().appendChild(el);
    setTimeout(() => {
      el.style.opacity = '0';
      el.style.transition = 'opacity .2s';
      setTimeout(() => el.remove(), 200);
    }, timeout);
  };
  const style = document.createElement('style');
  style.textContent =
    '@keyframes toastIn{from{transform:translateX(20px);opacity:0}to{transform:none;opacity:1}}';
  document.head.appendChild(style);
})();
