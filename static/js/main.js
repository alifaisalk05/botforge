// ---------------------------------------------------------------------------
// Scroll reveal
// ---------------------------------------------------------------------------
(function () {
  const items = document.querySelectorAll('.reveal');
  if (!items.length) return;
  const io = new IntersectionObserver((entries) => {
    entries.forEach((e) => {
      if (e.isIntersecting) {
        e.target.classList.add('in');
        io.unobserve(e.target);
      }
    });
  }, { threshold: 0.12 });
  items.forEach((el) => io.observe(el));
})();

// ---------------------------------------------------------------------------
// Flash messages auto-dismiss
// ---------------------------------------------------------------------------
(function () {
  document.querySelectorAll('.flash').forEach((el, i) => {
    setTimeout(() => {
      el.style.transition = 'opacity .4s ease, transform .4s ease';
      el.style.opacity = '0';
      el.style.transform = 'translateX(30px)';
      setTimeout(() => el.remove(), 400);
    }, 4500 + i * 300);
  });
})();

// ---------------------------------------------------------------------------
// Copy to clipboard
// ---------------------------------------------------------------------------
document.querySelectorAll('[data-copy]').forEach((btn) => {
  btn.addEventListener('click', () => {
    const target = document.querySelector(btn.getAttribute('data-copy'));
    if (!target) return;
    navigator.clipboard.writeText(target.value || target.textContent).then(() => {
      const original = btn.textContent;
      btn.textContent = 'Copied!';
      setTimeout(() => (btn.textContent = original), 1500);
    });
  });
});

// ---------------------------------------------------------------------------
// Mobile nav toggle
// ---------------------------------------------------------------------------
(function () {
  const toggle = document.querySelector('.nav-toggle');
  const links = document.querySelector('.nav-links');
  if (!toggle || !links) return;
  toggle.addEventListener('click', () => links.classList.toggle('open'));
})();

// ---------------------------------------------------------------------------
// Confirm dialogs for destructive actions
// ---------------------------------------------------------------------------
document.querySelectorAll('[data-confirm]').forEach((form) => {
  form.addEventListener('submit', (e) => {
    if (!confirm(form.getAttribute('data-confirm'))) {
      e.preventDefault();
    }
  });
});

// ---------------------------------------------------------------------------
// Animated counters (stat numbers on dashboard / landing)
// ---------------------------------------------------------------------------
(function () {
  const counters = document.querySelectorAll('[data-count]');
  if (!counters.length) return;
  const io = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      const el = entry.target;
      const target = parseFloat(el.getAttribute('data-count'));
      const suffix = el.getAttribute('data-suffix') || '';
      const duration = 1200;
      const start = performance.now();
      function tick(t) {
        const progress = Math.min((t - start) / duration, 1);
        const eased = 1 - Math.pow(1 - progress, 3);
        const value = target * eased;
        el.textContent = (target % 1 === 0 ? Math.floor(value).toLocaleString() : value.toFixed(1)) + suffix;
        if (progress < 1) requestAnimationFrame(tick);
      }
      requestAnimationFrame(tick);
      io.unobserve(el);
    });
  }, { threshold: 0.5 });
  counters.forEach((c) => io.observe(c));
})();

// ---------------------------------------------------------------------------
// Code editor bootstrap (CodeMirror if available via CDN, else plain textarea)
// ---------------------------------------------------------------------------
(function () {
  const el = document.getElementById('code-editor');
  if (!el || typeof CodeMirror === 'undefined') return;
  const cm = CodeMirror.fromTextArea(el, {
    mode: 'python',
    theme: 'dracula',
    lineNumbers: true,
    indentUnit: 4,
    tabSize: 4,
    matchBrackets: true,
    autoCloseBrackets: true,
    viewportMargin: Infinity,
  });
  cm.on('change', () => cm.save());
  window.__cm = cm;
  const form = el.closest('form');
  if (form) form.addEventListener('submit', () => cm.save());
})();

// ---------------------------------------------------------------------------
// Notification bell dropdown
// ---------------------------------------------------------------------------
(function () {
  const bell = document.querySelector('[data-bell]');
  const panel = document.querySelector('[data-bell-panel]');
  if (!bell || !panel) return;
  bell.addEventListener('click', (e) => {
    e.stopPropagation();
    panel.classList.toggle('open');
  });
  document.addEventListener('click', (e) => {
    if (!panel.contains(e.target)) panel.classList.remove('open');
  });
})();

// ---------------------------------------------------------------------------
// Insert snippet into editor from the reference sidebar
// ---------------------------------------------------------------------------
document.querySelectorAll('[data-insert]').forEach((btn) => {
  btn.addEventListener('click', () => {
    const snippet = btn.getAttribute('data-insert');
    if (window.__cm) {
      window.__cm.replaceSelection(snippet);
      window.__cm.focus();
    } else {
      const ta = document.getElementById('code-editor');
      if (ta) { ta.value += (ta.value.endsWith('\n') || !ta.value ? '' : '\n') + snippet; }
    }
  });
});
