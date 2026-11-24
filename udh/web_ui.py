"""Assets estáticos Python puros (HTML/CSS/JS) para a interface guiada mínima local (B1.7).

Regra inviolável: zero arquivos não-.py sob udh/, zero CDNs/frameworks externos,
zero innerHTML no JavaScript (apenas textContent/createElement contra DOM XSS).
"""

INDEX_HTML = """<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Universal Dev Harness — Painel Guiado</title>
  <link rel="stylesheet" href="/assets/app.css">
</head>
<body>
  <header class="topbar">
    <div>
      <h1>Universal Dev Harness</h1>
      <p class="subtitle">Acompanhamento guiado com verificações reais e visualização isolada</p>
    </div>
    <div id="env-badge" class="badge badge-neutral">Verificando ambiente...</div>
  </header>

  <main class="layout">
    <section id="auth-panel" class="card hidden">
      <h2>Código da sessão local</h2>
      <p>Cole o código de sessão exibido ao iniciar o painel para habilitar consultas e ações seguras.</p>
      <form id="token-form" class="inline-form">
        <input id="token-input" type="password" autocomplete="off" placeholder="Código da sessão" required>
        <button type="submit" class="btn btn-primary">Conectar sessão</button>
      </form>
    </section>

    <section id="feedback-banner" class="card hidden" aria-live="polite"></section>

    <section class="card" id="health-section">
      <div class="section-head">
        <h2>Saúde do ambiente</h2>
        <button id="refresh-btn" type="button" class="btn">Atualizar</button>
      </div>
      <p id="health-summary" class="muted">Carregando estado verificado...</p>
      <div id="health-alerts" class="stack"></div>
    </section>

    <section class="card">
      <h2>Progresso real das execuções</h2>
      <div class="counters" id="state-counters">
        <div class="counter"><span class="counter-label">Pendentes</span><strong id="cnt-PENDING">0</strong></div>
        <div class="counter"><span class="counter-label">Em andamento</span><strong id="cnt-IN_PROGRESS">0</strong></div>
        <div class="counter"><span class="counter-label">Bloqueadas</span><strong id="cnt-BLOCKED">0</strong></div>
        <div class="counter"><span class="counter-label">Concluídas</span><strong id="cnt-DONE">0</strong></div>
      </div>

      <form id="create-run-form" class="grid-form">
        <input id="spec-id-input" type="text" placeholder="Nome do projeto / especificação (ex.: app-notas)" required>
        <input id="spec-ver-input" type="text" value="1.0" placeholder="Versão" required>
        <input id="ticket-id-input" type="text" placeholder="Etapa (opcional, ex.: entrega-1)">
        <select id="mode-select">
          <option value="STANDARD">Modo Padrão (STANDARD)</option>
          <option value="FAST">Modo Rápido (FAST)</option>
          <option value="DEEP">Modo Completo (DEEP)</option>
        </select>
        <button type="submit" class="btn btn-primary">Nova execução</button>
      </form>

      <div id="runs-list" class="runs-list"></div>
    </section>

    <section class="card hidden" id="run-detail-section">
      <div class="section-head">
        <div>
          <h2 id="detail-title">Detalhes da execução</h2>
          <p id="detail-meta" class="muted"></p>
        </div>
        <span id="detail-state-badge" class="badge badge-neutral"></span>
      </div>

      <div class="stages" id="delivery-stages"></div>
      <p id="delivery-scope-note" class="muted small"></p>

      <div id="block-card-container" class="stack"></div>
      <div id="delivery-card-container" class="stack"></div>

      <div class="actions-bar" id="run-actions">
        <button id="btn-start" type="button" class="btn btn-primary hidden">Iniciar execução</button>
        <button id="btn-verify" type="button" class="btn hidden">Verificar testes oficiais</button>
        <button id="btn-smoke" type="button" class="btn hidden">Executar teste de uso real</button>
        <button id="btn-preview-tab" type="button" class="btn btn-accent hidden">Ver o app</button>
        <button id="btn-preview-iframe" type="button" class="btn hidden">Tentar visualização embutida</button>
        <button id="btn-deliver" type="button" class="btn btn-success hidden">Concluir entrega</button>
        <button id="btn-block" type="button" class="btn btn-warn hidden">Pausar execução</button>
        <button id="btn-unblock" type="button" class="btn btn-warn hidden">Retornar para pendente (auditado)</button>
      </div>

      <div id="preview-panel" class="preview-panel hidden">
        <div class="demo-banner">
          <strong>Visualização ativa — dados de demonstração</strong>
          <span>Os dados criados durante esta visualização são isolados e descartados ao encerrar.</span>
        </div>
        <div class="actions-bar">
          <button id="btn-preview-reopen" type="button" class="btn btn-accent">Abrir em aba separada</button>
          <button id="btn-preview-stop" type="button" class="btn btn-danger">Encerrar visualização</button>
        </div>
        <div id="preview-iframe-wrap" class="iframe-wrap hidden">
          <p class="small muted">Se o navegador bloquear cookies isolados na moldura embutida, use "Abrir em aba separada".</p>
          <iframe id="preview-iframe" title="Visualização isolada do aplicativo" sandbox="allow-scripts allow-same-origin allow-forms"></iframe>
        </div>
      </div>
    </section>
  </main>

  <script src="/assets/app.js"></script>
</body>
</html>
"""

APP_CSS = """* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: system-ui, -apple-system, sans-serif;
  background: #f5f7fa;
  color: #1a2433;
  line-height: 1.5;
}
.topbar {
  background: #10243e;
  color: #ffffff;
  padding: 1rem 1.5rem;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 1rem;
  flex-wrap: wrap;
}
.topbar h1 { margin: 0; font-size: 1.25rem; }
.subtitle { margin: 0.2rem 0 0; font-size: 0.9rem; color: #cbd8ea; }
.layout { max-width: 980px; margin: 1.25rem auto; padding: 0 1rem 2rem; display: grid; gap: 1rem; }
.card {
  background: #ffffff;
  border: 1px solid #d9e1ec;
  border-radius: 10px;
  padding: 1rem 1.25rem;
  box-shadow: 0 1px 2px rgba(16, 36, 62, 0.04);
}
.section-head { display: flex; justify-content: space-between; align-items: center; gap: 1rem; flex-wrap: wrap; }
h2 { margin: 0 0 0.5rem; font-size: 1.05rem; }
.muted { color: #526277; margin: 0.25rem 0; }
.small { font-size: 0.85rem; }
.hidden { display: none !important; }
.stack { display: grid; gap: 0.75rem; margin-top: 0.75rem; }
.badge {
  display: inline-block;
  padding: 0.3rem 0.7rem;
  border-radius: 999px;
  font-size: 0.82rem;
  font-weight: 600;
}
.badge-ready { background: #dff5e6; color: #135c2c; }
.badge-warn { background: #fff3d6; color: #7a4b00; }
.badge-error { background: #fde2e2; color: #8c1d1d; }
.badge-neutral { background: #e6ecf5; color: #243b5a; }
.counters { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 0.75rem; margin: 0.75rem 0 1rem; }
.counter { background: #f8fafd; border: 1px solid #e1e8f2; border-radius: 8px; padding: 0.75rem; text-align: center; }
.counter-label { display: block; font-size: 0.82rem; color: #526277; }
.counter strong { font-size: 1.4rem; }
.grid-form { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 0.5rem; margin-bottom: 1rem; }
.inline-form { display: flex; gap: 0.5rem; flex-wrap: wrap; }
input, select {
  padding: 0.55rem 0.7rem;
  border: 1px solid #c3cfdf;
  border-radius: 6px;
  font-size: 0.92rem;
}
.btn {
  padding: 0.55rem 0.9rem;
  border-radius: 6px;
  border: 1px solid #b9c7da;
  background: #ffffff;
  color: #1a2433;
  font-weight: 600;
  cursor: pointer;
}
.btn:hover { background: #eef3fa; }
.btn-primary { background: #1d5fd1; color: #ffffff; border-color: #1d5fd1; }
.btn-primary:hover { background: #164ca8; }
.btn-accent { background: #0f766e; color: #ffffff; border-color: #0f766e; }
.btn-accent:hover { background: #0c5d57; }
.btn-success { background: #15803d; color: #ffffff; border-color: #15803d; }
.btn-success:hover { background: #116630; }
.btn-warn { background: #b45309; color: #ffffff; border-color: #b45309; }
.btn-danger { background: #b91c1c; color: #ffffff; border-color: #b91c1c; }
.runs-list { display: grid; gap: 0.5rem; }
.run-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 0.75rem;
  padding: 0.7rem 0.9rem;
  border: 1px solid #dde5f0;
  border-radius: 8px;
  background: #fbfcfe;
  cursor: pointer;
  text-align: left;
  width: 100%;
}
.run-item:hover { background: #eef4fc; }
.run-item.selected { border-color: #1d5fd1; background: #eef4ff; }
.stages { display: flex; flex-wrap: wrap; gap: 0.45rem; margin: 0.75rem 0; }
.stage-pill {
  padding: 0.3rem 0.65rem;
  border-radius: 6px;
  font-size: 0.82rem;
  border: 1px solid #d4dfee;
  background: #f5f8fc;
  color: #526277;
}
.stage-pill.done { background: #dff5e6; border-color: #9dd7b0; color: #135c2c; font-weight: 600; }
.adr-card {
  border-left: 4px solid #b45309;
  background: #fffbeb;
  border-radius: 6px;
  padding: 0.85rem 1rem;
  border-top: 1px solid #f3e0b2;
  border-right: 1px solid #f3e0b2;
  border-bottom: 1px solid #f3e0b2;
}
.adr-card.error { border-left-color: #b91c1c; background: #fef2f2; border-color: #f3c4c4; }
.adr-card.info { border-left-color: #1d5fd1; background: #eff6ff; border-color: #c8defd; }
.adr-card h3 { margin: 0 0 0.4rem; font-size: 0.95rem; }
.adr-field { margin: 0.25rem 0; font-size: 0.9rem; }
.actions-bar { display: flex; flex-wrap: wrap; gap: 0.5rem; margin-top: 1rem; }
.preview-panel { margin-top: 1rem; padding-top: 1rem; border-top: 1px solid #dde5f0; }
.demo-banner {
  background: #fff3d6;
  border: 1px solid #ecc97b;
  color: #6b4200;
  border-radius: 8px;
  padding: 0.75rem 0.9rem;
  display: grid;
  gap: 0.2rem;
}
.iframe-wrap { margin-top: 0.75rem; }
.iframe-wrap iframe {
  width: 100%;
  min-height: 420px;
  border: 1px solid #c3cfdf;
  border-radius: 8px;
  background: #ffffff;
}
"""

APP_JS = """(function () {
  'use strict';

  let sessionToken = '';
  let selectedRunId = null;
  let currentRunDetail = null;
  let activePreview = null;

  const STATE_LABELS = {
    PENDING: 'Pendente',
    IN_PROGRESS: 'Em andamento',
    BLOCKED: 'Bloqueada',
    DONE: 'Concluída'
  };

  const STAGE_ORDER = [
    ['PENDING', 'Pendente'],
    ['IMPLEMENTED', 'Implementado'],
    ['TESTED', 'Testado'],
    ['ACCEPTED', 'Aceito'],
    ['DELIVERED', 'Entregue']
  ];

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function clearNode(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function badgeClassForState(state) {
    if (state === 'DONE' || state === 'READY') return 'badge badge-ready';
    if (state === 'BLOCKED' || state === 'NOT_READY') return 'badge badge-error';
    if (state === 'IN_PROGRESS' || state === 'READY_WITH_LIMITS') return 'badge badge-warn';
    return 'badge badge-neutral';
  }

  function extractTokenFromHash() {
    const hash = window.location.hash || '';
    if (hash.startsWith('#')) {
      const params = new URLSearchParams(hash.slice(1));
      const found = params.get('token');
      if (found) {
        sessionToken = found;
        window.history.replaceState(null, '', window.location.pathname);
      }
    }
  }

  function randomRequestId(prefix) {
    const bytes = new Uint8Array(8);
    window.crypto.getRandomValues(bytes);
    const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
    return prefix + '-' + hex;
  }

  function renderAdrCard(card) {
    const box = el('div', 'adr-card ' + (card.severity || 'warning'));
    box.appendChild(el('h3', '', card.category_label || 'Explicação'));
    const fields = [
      ['O que ocorreu: ', card.what_happened],
      ['Por que importa: ', card.why_it_matters],
      ['O que foi realmente verificado: ', card.what_was_verified],
      ['Próxima ação segura: ', card.safe_next_action]
    ];
    for (const [label, value] of fields) {
      const p = el('p', 'adr-field');
      p.appendChild(el('strong', '', label));
      p.appendChild(document.createTextNode(String(value || '')));
      box.appendChild(p);
    }
    if (card.unblock_note) {
      box.appendChild(el('p', 'small muted', card.unblock_note));
    }
    return box;
  }

  function showFeedback(message, card) {
    const banner = document.getElementById('feedback-banner');
    clearNode(banner);
    if (!message && !card) {
      banner.classList.add('hidden');
      return;
    }
    if (message) banner.appendChild(el('p', '', message));
    if (card) banner.appendChild(renderAdrCard(card));
    banner.classList.remove('hidden');
  }

  async function apiFetch(path, options) {
    const opts = options || {};
    const headers = Object.assign({}, opts.headers || {});
    if (sessionToken) headers['X-UDH-Token'] = sessionToken;
    if (opts.body !== undefined) headers['Content-Type'] = 'application/json';
    const resp = await window.fetch(path, {
      method: opts.method || 'GET',
      headers: headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      credentials: 'omit',
      cache: 'no-store'
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      if (resp.status === 401 || resp.status === 403) {
        document.getElementById('auth-panel').classList.remove('hidden');
      }
      const err = new Error(data.error || ('Falha na operação (' + resp.status + ').'));
      err.explanation = data.explanation || null;
      throw err;
    }
    document.getElementById('auth-panel').classList.add('hidden');
    return data;
  }

  async function loadOverview() {
    if (!sessionToken) {
      document.getElementById('auth-panel').classList.remove('hidden');
      return;
    }
    try {
      const data = await apiFetch('/api/overview');
      const health = data.health || {};
      const envBadge = document.getElementById('env-badge');
      envBadge.className = badgeClassForState(health.overall_status);
      envBadge.textContent = health.status_label || health.overall_status || 'Indisponível';

      document.getElementById('health-summary').textContent =
        'Checagens aprovadas: ' + String(health.checks_ready || 0) + ' de ' + String(health.checks_total || 0) + '.';
      const alertsBox = document.getElementById('health-alerts');
      clearNode(alertsBox);
      for (const alert of (health.alerts || [])) {
        alertsBox.appendChild(renderAdrCard(alert));
      }

      const counts = data.counts || {};
      for (const st of ['PENDING', 'IN_PROGRESS', 'BLOCKED', 'DONE']) {
        const target = document.getElementById('cnt-' + st);
        if (target) target.textContent = String(counts[st] || 0);
      }

      const listEl = document.getElementById('runs-list');
      clearNode(listEl);
      const runs = data.runs || [];
      if (!runs.length) {
        listEl.appendChild(el('p', 'muted', 'Nenhuma execução registrada ainda. Crie uma nova execução acima.'));
      } else {
        for (const item of runs) {
          const btn = el('button', 'run-item' + (item.run_id === selectedRunId ? ' selected' : ''));
          btn.type = 'button';
          const left = el('div');
          left.appendChild(el('strong', '', item.spec_id + ' (v' + item.spec_version + ')'));
          if (item.ticket_id) {
            left.appendChild(el('div', 'small muted', 'Etapa: ' + item.ticket_id));
          }
          const right = el('span', badgeClassForState(item.state), STATE_LABELS[item.state] || item.state);
          btn.appendChild(left);
          btn.appendChild(right);
          btn.addEventListener('click', () => {
            selectedRunId = item.run_id;
            loadRunDetail(item.run_id);
          });
          listEl.appendChild(btn);
        }
      }

      if (selectedRunId) {
        await loadRunDetail(selectedRunId);
      }
    } catch (err) {
      showFeedback(err.message, err.explanation);
    }
  }

  async function loadRunDetail(runId) {
    try {
      const data = await apiFetch('/api/runs/' + encodeURIComponent(runId));
      currentRunDetail = data;
      const run = data.run;
      const delivery = data.delivery;
      const section = document.getElementById('run-detail-section');
      section.classList.remove('hidden');

      document.getElementById('detail-title').textContent = run.spec_id + ' — versão ' + run.spec_version;
      document.getElementById('detail-meta').textContent =
        'Revisão oficial: ' + String(run.revision) + ' | Modo: ' + String(run.mode || 'STANDARD');
      const stBadge = document.getElementById('detail-state-badge');
      stBadge.className = badgeClassForState(run.state);
      stBadge.textContent = STATE_LABELS[run.state] || run.state;

      const stagesEl = document.getElementById('delivery-stages');
      clearNode(stagesEl);
      const currentIdx = STAGE_ORDER.findIndex((pair) => pair[0] === delivery.delivery_stage);
      STAGE_ORDER.forEach((pair, idx) => {
        const pill = el('span', 'stage-pill' + (idx <= currentIdx ? ' done' : ''), pair[1]);
        stagesEl.appendChild(pill);
      });

      document.getElementById('delivery-scope-note').textContent =
        'Escopo verificado: ambiente Linux local isolado (offline). Não verificado nesta etapa: WSL2 e serviços externos.';

      const blockBox = document.getElementById('block-card-container');
      clearNode(blockBox);
      if (data.block_explanation) {
        blockBox.appendChild(renderAdrCard(data.block_explanation));
      }

      const delivBox = document.getElementById('delivery-card-container');
      clearNode(delivBox);
      if (data.delivery_explanation) {
        delivBox.appendChild(renderAdrCard(data.delivery_explanation));
      }

      const toggle = (id, show) => document.getElementById(id).classList.toggle('hidden', !show);
      toggle('btn-start', run.state === 'PENDING');
      toggle('btn-block', run.state === 'PENDING' || run.state === 'IN_PROGRESS');
      toggle('btn-unblock', run.state === 'BLOCKED');
      toggle('btn-verify', run.state === 'IN_PROGRESS' && Boolean(delivery.current_artifact_digest));
      toggle('btn-smoke', run.state === 'IN_PROGRESS' && Boolean(delivery.current_artifact_digest));
      toggle('btn-preview-tab', Boolean(delivery.current_artifact_digest));
      toggle('btn-preview-iframe', Boolean(delivery.current_artifact_digest));
      toggle('btn-deliver', Boolean(delivery.can_deliver));

      const previews = data.previews || [];
      if (previews.length) {
        activePreview = previews[0];
        document.getElementById('preview-panel').classList.remove('hidden');
      } else {
        activePreview = null;
        document.getElementById('preview-panel').classList.add('hidden');
        document.getElementById('preview-iframe-wrap').classList.add('hidden');
        document.getElementById('preview-iframe').removeAttribute('src');
      }
    } catch (err) {
      showFeedback(err.message, err.explanation);
    }
  }

  async function startPreview(embedMode) {
    if (!selectedRunId) return;
    showFeedback('Iniciando visualização isolada...', null);
    try {
      const res = await apiFetch('/api/runs/' + encodeURIComponent(selectedRunId) + '/preview/start', {
        method: 'POST',
        body: { embed_mode: embedMode }
      });
      activePreview = res;
      document.getElementById('preview-panel').classList.remove('hidden');
      const wrap = document.getElementById('preview-iframe-wrap');
      const frame = document.getElementById('preview-iframe');
      if (embedMode === 'iframe') {
        wrap.classList.remove('hidden');
        frame.src = res.launch_url;
      } else {
        wrap.classList.add('hidden');
        frame.removeAttribute('src');
        window.open(res.launch_url, '_blank', 'noopener,noreferrer');
      }
      showFeedback('Visualização isolada pronta (dados de demonstração).', null);
    } catch (err) {
      showFeedback(err.message, err.explanation);
    }
  }

  async function reopenPreviewInTab() {
    if (!activePreview) return;
    try {
      const res = await apiFetch('/api/previews/' + encodeURIComponent(activePreview.preview_id) + '/grant', {
        method: 'POST',
        body: {}
      });
      window.open(res.launch_url, '_blank', 'noopener,noreferrer');
      showFeedback('Nova autorização emitida para abrir a visualização em aba separada.', null);
    } catch (err) {
      showFeedback(err.message, err.explanation);
    }
  }

  async function stopActivePreview() {
    if (!activePreview) return;
    try {
      await apiFetch('/api/previews/' + encodeURIComponent(activePreview.preview_id) + '/stop', {
        method: 'POST',
        body: {}
      });
      activePreview = null;
      document.getElementById('preview-panel').classList.add('hidden');
      document.getElementById('preview-iframe-wrap').classList.add('hidden');
      document.getElementById('preview-iframe').removeAttribute('src');
      showFeedback('Visualização encerrada e dados de demonstração descartados.', null);
    } catch (err) {
      showFeedback(err.message, err.explanation);
    }
  }

  function bindEvents() {
    document.getElementById('token-form').addEventListener('submit', (ev) => {
      ev.preventDefault();
      const input = document.getElementById('token-input');
      sessionToken = input.value.trim();
      input.value = '';
      showFeedback('', null);
      loadOverview();
    });

    document.getElementById('refresh-btn').addEventListener('click', () => {
      showFeedback('', null);
      loadOverview();
    });

    document.getElementById('create-run-form').addEventListener('submit', async (ev) => {
      ev.preventDefault();
      try {
        const specId = document.getElementById('spec-id-input').value.trim();
        const specVer = document.getElementById('spec-ver-input').value.trim();
        const ticketId = document.getElementById('ticket-id-input').value.trim() || null;
        const mode = document.getElementById('mode-select').value;
        const created = await apiFetch('/api/runs', {
          method: 'POST',
          body: {
            spec_id: specId,
            spec_version: specVer,
            request_id: randomRequestId('ui-create'),
            ticket_id: ticketId,
            mode: mode
          }
        });
        selectedRunId = created.run.run_id;
        showFeedback('Execução criada com sucesso.', null);
        await loadOverview();
      } catch (err) {
        showFeedback(err.message, err.explanation);
      }
    });

    document.getElementById('btn-start').addEventListener('click', async () => {
      if (!currentRunDetail) return;
      try {
        await apiFetch('/api/runs/' + encodeURIComponent(selectedRunId) + '/start', {
          method: 'POST',
          body: {
            expected_revision: currentRunDetail.run.revision,
            request_id: randomRequestId('ui-start')
          }
        });
        showFeedback('Execução iniciada.', null);
        await loadOverview();
      } catch (err) {
        showFeedback(err.message, err.explanation);
      }
    });

    document.getElementById('btn-block').addEventListener('click', async () => {
      if (!currentRunDetail) return;
      const reason = window.prompt('Motivo da pausa:', 'Pausa solicitada pelo usuário para revisão.');
      if (!reason) return;
      try {
        await apiFetch('/api/runs/' + encodeURIComponent(selectedRunId) + '/block', {
          method: 'POST',
          body: {
            expected_revision: currentRunDetail.run.revision,
            request_id: randomRequestId('ui-block'),
            category: 'ADMIN_PAUSE',
            reason_code: 'USER_REQUESTED_PAUSE',
            reason: reason,
            next_action: 'Revise o andamento e retorne para pendente quando estiver pronto.'
          }
        });
        showFeedback('Execução pausada com registro auditável.', null);
        await loadOverview();
      } catch (err) {
        showFeedback(err.message, err.explanation);
      }
    });

    document.getElementById('btn-unblock').addEventListener('click', async () => {
      if (!currentRunDetail) return;
      const reason = window.prompt('Motivo do retorno para pendente:', 'Causa revisada; retornando para pendente.');
      if (!reason) return;
      try {
        await apiFetch('/api/runs/' + encodeURIComponent(selectedRunId) + '/unblock', {
          method: 'POST',
          body: {
            expected_revision: currentRunDetail.run.revision,
            request_id: randomRequestId('ui-unblock'),
            reason: reason
          }
        });
        showFeedback('Execução retornada para pendente com registro auditável.', null);
        await loadOverview();
      } catch (err) {
        showFeedback(err.message, err.explanation);
      }
    });

    document.getElementById('btn-verify').addEventListener('click', async () => {
      if (!currentRunDetail) return;
      showFeedback('Executando testes oficiais de aceite em ambiente isolado...', null);
      try {
        const res = await apiFetch('/api/runs/' + encodeURIComponent(selectedRunId) + '/verify', {
          method: 'POST',
          body: { request_id: randomRequestId('ui-verify') }
        });
        showFeedback('Verificação oficial concluída: ' + String(res.report.verdict), null);
        await loadOverview();
      } catch (err) {
        showFeedback(err.message, err.explanation);
      }
    });

    document.getElementById('btn-smoke').addEventListener('click', async () => {
      if (!currentRunDetail) return;
      showFeedback('Executando teste de uso real (smoke test)...', null);
      try {
        const res = await apiFetch('/api/runs/' + encodeURIComponent(selectedRunId) + '/smoke', {
          method: 'POST',
          body: { request_id: randomRequestId('ui-smoke') }
        });
        showFeedback('Teste de uso real concluído: ' + String(res.report.verdict), null);
        await loadOverview();
      } catch (err) {
        showFeedback(err.message, err.explanation);
      }
    });

    document.getElementById('btn-deliver').addEventListener('click', async () => {
      if (!currentRunDetail) return;
      try {
        await apiFetch('/api/runs/' + encodeURIComponent(selectedRunId) + '/deliver', {
          method: 'POST',
          body: {
            expected_revision: currentRunDetail.run.revision,
            request_id: randomRequestId('ui-deliver')
          }
        });
        showFeedback('Entrega concluída e registrada como DELIVERED.', null);
        await loadOverview();
      } catch (err) {
        showFeedback(err.message, err.explanation);
      }
    });

    document.getElementById('btn-preview-tab').addEventListener('click', () => startPreview('tab'));
    document.getElementById('btn-preview-iframe').addEventListener('click', () => startPreview('iframe'));
    document.getElementById('btn-preview-reopen').addEventListener('click', () => reopenPreviewInTab());
    document.getElementById('btn-preview-stop').addEventListener('click', () => stopActivePreview());
  }

  extractTokenFromHash();
  bindEvents();
  loadOverview();
})();
"""
