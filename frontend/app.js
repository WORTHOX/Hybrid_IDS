/* ═══════════════════════════════════════════════════════════════════════════
   HNIDS — Client Application Engine
   Routes through /api/* proxy → FastAPI backend
   ═══════════════════════════════════════════════════════════════════════════ */

(() => {
  'use strict';

  // ─── Configuration ───
  const API_BASE = '/api';

  const CLASSES = [
    'BENIGN',
    'DoS',
    'DDoS',
    'PortScan',
    'BruteForce',
    'WebAttack',
    'Bot',
    'Rare'
  ];

  // Palette matching CSS tokens
  const CLASS_HEX = {
    BENIGN:     '#4A6741', // Deep Olive
    DoS:        '#A0522D', // Sienna
    DDoS:       '#8B3A3A', // Deep Rust
    PortScan:   '#9B7A3A', // Ochre
    BruteForce: '#7A4A5A', // Wine Mauve
    WebAttack:  '#8B4A3A', // Terracotta
    Bot:        '#3D4E6D', // Slate Indigo
    Rare:       '#6A5A7A', // Amethyst Slate
  };

  const CLASS_SHORT_CODES = {
    BENIGN:     '01',
    DoS:        '02',
    DDoS:       '03',
    PortScan:   '04',
    BruteForce: '05',
    WebAttack:  '06',
    Bot:        '07',
    Rare:       '08',
  };

  // ─── State ───
  const state = {
    detections: [],
    classCounts: Object.fromEntries(CLASSES.map(c => [c, 0])),
    totalRequests: 0,
    totalAlerts: 0,
    confidenceSum: 0,
    confidenceCount: 0,
    bufferCount: 0,
    windowLength: 20,
  };

  // ─── DOM References ───
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => document.querySelectorAll(sel);

  const dom = {
    statusMark:        $('#status-mark'),
    statusText:        $('#status-text'),
    bundleVersion:     $('#bundle-version'),
    totalRequests:     $('#total-requests'),
    totalAlerts:       $('#total-alerts'),
    avgConfidence:     $('#avg-confidence'),
    distribution:      $('#distribution'),
    detectionFeed:     $('#detection-feed'),
    detectionCount:    $('#detection-count'),
    detectInput:       $('#detect-input'),
    apiKeyDetect:      $('#api-key-detect'),
    btnDetect:         $('#btn-detect'),
    btnLoadExample:    $('#btn-load-example'),
    resultPlaceholder: $('#result-placeholder'),
    resultContent:     $('#result-content'),
    resultClass:       $('#result-class'),
    resultConfidence:  $('#result-confidence'),
    resultDetails:     $('#result-details'),
    resultProbs:       $('#result-probs'),
    streamInput:       $('#stream-input'),
    apiKeyStream:      $('#api-key-stream'),
    btnStream:         $('#btn-stream'),
    bufferFill:        $('#buffer-fill'),
    bufferLabel:       $('#buffer-label'),
    streamLog:         $('#stream-log'),
    btnRefreshMetrics: $('#btn-refresh-metrics'),
    metricsContent:    $('#metrics-content'),
    toast:             $('#toast'),
    toastText:         $('#toast-text'),
    toastClose:        $('#toast-close'),
  };

  // ═══════════════════════════════════════════════════════════════════════════
  //  1. NAVIGATION (TABS + KEYBOARD [1-4])
  // ═══════════════════════════════════════════════════════════════════════════
  function initNav() {
    const navBtns = $$('.nav-btn');
    navBtns.forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.preventDefault();
        const view = btn.dataset.view;
        switchTab(view);
      });
    });

    // Keyboard navigation (Keys 1-4)
    document.addEventListener('keydown', (e) => {
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
      const idx = parseInt(e.key, 10) - 1;
      if (idx >= 0 && idx < navBtns.length) {
        navBtns[idx].click();
      }
    });
  }

  function switchTab(viewName) {
    const navBtns = $$('.nav-btn');
    navBtns.forEach(b => {
      const isMatch = b.dataset.view === viewName;
      b.classList.toggle('active', isMatch);
      b.setAttribute('aria-selected', isMatch ? 'true' : 'false');
    });

    $$('.view').forEach(v => {
      const isMatch = v.id === `view-${viewName}`;
      v.classList.toggle('active', isMatch);
    });

    if (viewName === 'metrics') {
      loadMetrics();
    }
  }

  // ═══════════════════════════════════════════════════════════════════════════
  //  2. OVERCLOCK POSTER WALL (8-COLUMN MATRIX)
  // ═══════════════════════════════════════════════════════════════════════════
  function buildPosterWall() {
    if (!dom.distribution) return;
    dom.distribution.innerHTML = CLASSES.map(cls => `
      <div class="poster-tile" data-class="${cls}">
        <span class="poster-tile__key" aria-hidden="true">${CLASS_SHORT_CODES[cls]}</span>
        <span class="poster-tile__label">${cls}</span>
        <div class="poster-tile__num" id="poster-num-${cls}">0</div>
        <div class="poster-tile__bar-wrap">
          <div class="poster-tile__bar-fill" id="poster-bar-${cls}" style="width: 0%; background-color: ${CLASS_HEX[cls]};"></div>
        </div>
        <span class="poster-tile__pct" id="poster-pct-${cls}">0.0%</span>
      </div>
    `).join('');
  }

  function updatePosterWall() {
    const total = Math.max(1, Object.values(state.classCounts).reduce((a, b) => a + b, 0));
    CLASSES.forEach(cls => {
      const count = state.classCounts[cls];
      const pct = (count / total * 100).toFixed(1);
      const numEl = $(`#poster-num-${cls}`);
      const barEl = $(`#poster-bar-${cls}`);
      const pctEl = $(`#poster-pct-${cls}`);

      if (numEl) numEl.textContent = count;
      if (barEl) barEl.style.width = `${pct}%`;
      if (pctEl) pctEl.textContent = `${pct}%`;
    });
  }

  // ═══════════════════════════════════════════════════════════════════════════
  //  3. HEALTH CHECK & SYSTEM READINESS
  // ═══════════════════════════════════════════════════════════════════════════
  async function checkHealth() {
    try {
      const res = await fetch(`${API_BASE}/readyz`);
      const data = await res.json();
      if (data.ready) {
        if (dom.statusMark) dom.statusMark.className = 'status-mark ok';
        if (dom.statusText) dom.statusText.textContent = 'SYSTEM READY';
        if (dom.bundleVersion) dom.bundleVersion.textContent = data.bundle_version || 'b20261111T112951Z';
        return true;
      } else {
        if (dom.statusMark) dom.statusMark.className = 'status-mark bad';
        if (dom.statusText) dom.statusText.textContent = 'NOT READY';
        return false;
      }
    } catch {
      if (dom.statusMark) dom.statusMark.className = 'status-mark bad';
      if (dom.statusText) dom.statusText.textContent = 'OFFLINE';
      return false;
    }
  }

  // ═══════════════════════════════════════════════════════════════════════════
  //  4. API CLIENT
  // ═══════════════════════════════════════════════════════════════════════════
  async function apiPost(endpoint, body, apiKey) {
    const headers = { 'Content-Type': 'application/json' };
    if (apiKey) headers['X-Api-Key'] = apiKey;
    const res = await fetch(`${API_BASE}${endpoint}`, {
      method: 'POST',
      headers,
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: { message: res.statusText } }));
      throw new Error(err.error?.message || `HTTP ${res.status}`);
    }
    return res.json();
  }

  // ═══════════════════════════════════════════════════════════════════════════
  //  5. DETECTION VIEW LOGIC
  // ═══════════════════════════════════════════════════════════════════════════
  async function runDetect() {
    const raw = dom.detectInput.value.trim();
    if (!raw) return showToast('Input required: paste JSON window payload.');

    let payload;
    try {
      payload = JSON.parse(raw);
    } catch {
      return showToast('Malformed JSON: verify syntax.');
    }

    dom.btnDetect.disabled = true;
    dom.btnDetect.innerHTML = '<span class="btn__key">…</span> EVALUATING';

    try {
      const result = await apiPost('/v1/detect', payload, dom.apiKeyDetect.value);
      state.totalRequests++;
      recordDetection(result);
      showResult(result);
    } catch (err) {
      showToast(`Detection Error: ${err.message}`);
    } finally {
      dom.btnDetect.disabled = false;
      dom.btnDetect.innerHTML = '<span class="btn__key" aria-hidden="true">⏎</span> RUN DETECTION';
    }
  }

  function showResult(event) {
    if (!dom.resultPlaceholder || !dom.resultContent) return;
    dom.resultPlaceholder.classList.add('hidden');
    dom.resultContent.classList.remove('hidden');

    const cls = event.hnids.predicted_class;
    const conf = event.hnids.confidence;
    const color = CLASS_HEX[cls] || '#1C1A17';

    dom.resultClass.textContent = cls;
    dom.resultClass.style.color = color;

    dom.resultConfidence.textContent = `${(conf * 100).toFixed(2)}% CONFIDENCE`;
    dom.resultConfidence.style.color = conf >= 0.9 ? 'var(--c-ok)' : conf >= 0.6 ? 'var(--bronze)' : 'var(--c-alert)';

    // Key-Value Grid
    dom.resultDetails.innerHTML = [
      ['EVENT KIND', event.event.kind.toUpperCase()],
      ['ALERT STATUS', event.hnids.is_alert ? 'ACTION REQUIRED' : 'NORMAL'],
      ['BUNDLE VERSION', event.hnids.bundle_version],
      ['VALIDATION', event.hnids.validation_status.toUpperCase()],
      ['SANITIZED VALUES', event.hnids.sanitized_values],
      ['DETECTION REF', event.detection_id.slice(0, 12)],
    ].map(([k, v]) => `
      <div class="result-meta-row">
        <span class="result-meta-row__key">${k}</span>
        <span class="result-meta-row__val">${v}</span>
      </div>
    `).join('');

    // Probabilities breakdown
    const probs = event.hnids.probabilities || {};
    dom.resultProbs.innerHTML = Object.entries(probs)
      .sort((a, b) => b[1] - a[1])
      .map(([name, prob]) => {
        const pct = (prob * 100).toFixed(2);
        const pColor = CLASS_HEX[name] || 'var(--ink-muted)';
        return `
          <div class="prob-row">
            <span class="prob-row__label">${name}</span>
            <div class="prob-row__track">
              <div class="prob-row__fill" style="width: ${pct}%; background-color: ${pColor};"></div>
            </div>
            <span class="prob-row__val">${pct}%</span>
          </div>
        `;
      }).join('');
  }

  // Preset Sample Loader
  async function loadPresetSample(sampleKey) {
    const filenameMap = {
      ddos:      'detect_ddos.request.json',
      dos:       'detect_dos.request.json',
      benign:    'detect_benign.request.json',
      portscan:  'detect_portscan.request.json',
      webattack: 'detect_webattack.request.json',
    };

    const targetFile = filenameMap[sampleKey] || 'detect_ddos.request.json';

    try {
      const res = await fetch(`/examples/${targetFile}`);
      if (res.ok) {
        const data = await res.json();
        dom.detectInput.value = JSON.stringify(data, null, 2);
        showToast(`Loaded ${sampleKey.toUpperCase()} sample window.`);
      } else {
        dom.detectInput.value = JSON.stringify(FALLBACK_PAYLOAD, null, 2);
      }
    } catch {
      dom.detectInput.value = JSON.stringify(FALLBACK_PAYLOAD, null, 2);
    }
  }

  function initSampleChips() {
    const chips = $$('.sample-chip');
    chips.forEach(chip => {
      chip.addEventListener('click', () => {
        const sample = chip.dataset.sample;
        loadPresetSample(sample);
      });
    });
  }

  // ═══════════════════════════════════════════════════════════════════════════
  //  6. STREAM VIEW LOGIC
  // ═══════════════════════════════════════════════════════════════════════════
  async function pushFlow() {
    const raw = dom.streamInput.value.trim();
    if (!raw) return showToast('Flow record JSON required.');

    let payload;
    try {
      payload = JSON.parse(raw);
    } catch {
      return showToast('Malformed flow JSON record.');
    }

    dom.btnStream.disabled = true;

    try {
      const result = await apiPost('/v1/flows', payload, dom.apiKeyStream.value);
      state.bufferCount = result.buffered;
      state.windowLength = result.window_length;
      updateBuffer();

      const time = new Date().toLocaleTimeString();
      let logHtml = '';

      if (result.detection) {
        recordDetection(result.detection);
        const cls = result.detection.hnids.predicted_class;
        const conf = (result.detection.hnids.confidence * 100).toFixed(1);
        logHtml = `
          <div class="stream-event-item stream-event-item--alert">
            <span class="stream-event-time">${time}</span>
            <span class="stream-event-msg">⚡ DETECTION WINDOW FIRED: <strong>${cls}</strong> (${conf}%)</span>
          </div>
        `;
        state.bufferCount = 0;
        updateBuffer();
      } else {
        logHtml = `
          <div class="stream-event-item">
            <span class="stream-event-time">${time}</span>
            <span class="stream-event-msg">Flow buffered [${result.buffered} / ${result.window_length}]</span>
          </div>
        `;
      }

      const logEl = dom.streamLog;
      const emptyEl = logEl.querySelector('.feed-empty');
      if (emptyEl) emptyEl.remove();
      logEl.insertAdjacentHTML('afterbegin', logHtml);

    } catch (err) {
      showToast(`Stream push error: ${err.message}`);
    } finally {
      dom.btnStream.disabled = false;
    }
  }

  function updateBuffer() {
    const pct = Math.min(100, (state.bufferCount / state.windowLength) * 100);
    if (dom.bufferFill) {
      dom.bufferFill.style.width = `${pct}%`;
      dom.bufferFill.style.backgroundColor = pct >= 80 ? 'var(--c-alert)' : 'var(--bronze)';
    }
    if (dom.bufferLabel) {
      dom.bufferLabel.textContent = `${state.bufferCount} / ${state.windowLength}`;
    }
  }

  // ═══════════════════════════════════════════════════════════════════════════
  //  7. INCIDENT RECORDING & DASHBOARD UPDATES
  // ═══════════════════════════════════════════════════════════════════════════
  function recordDetection(event) {
    const cls = event.hnids.predicted_class;
    const conf = event.hnids.confidence;
    const isAlert = event.hnids.is_alert;

    state.detections.unshift(event);
    if (state.detections.length > 80) state.detections.pop();

    state.classCounts[cls] = (state.classCounts[cls] || 0) + 1;
    if (isAlert) state.totalAlerts++;
    state.confidenceSum += conf;
    state.confidenceCount++;

    updateDashboard();
    addFeedEntry(event);

    if (isAlert && cls !== 'BENIGN') {
      showToast(`${cls} incident detected (${(conf * 100).toFixed(1)}% conf)`);
    }
  }

  function updateDashboard() {
    if (dom.totalRequests) dom.totalRequests.textContent = state.totalRequests;
    if (dom.totalAlerts) dom.totalAlerts.textContent = state.totalAlerts;

    if (state.confidenceCount > 0 && dom.avgConfidence) {
      const avg = (state.confidenceSum / state.confidenceCount * 100).toFixed(1);
      dom.avgConfidence.textContent = `${avg}%`;
    }

    if (dom.detectionCount) dom.detectionCount.textContent = state.detections.length;
    updatePosterWall();
  }

  function addFeedEntry(event) {
    const feed = dom.detectionFeed;
    if (!feed) return;
    const empty = feed.querySelector('.feed-empty');
    if (empty) empty.remove();

    const cls = event.hnids.predicted_class;
    const conf = (event.hnids.confidence * 100).toFixed(1);
    const kind = event.event.kind;
    const isAlert = event.hnids.is_alert;
    const time = new Date(event.event.created).toLocaleTimeString();
    const tagClass = isAlert ? 'entry-tag--alert' : 'entry-tag--signal';
    const tagText = isAlert ? 'ALERT' : 'SIGNAL';

    const entry = document.createElement('div');
    entry.className = 'detection-entry';
    entry.innerHTML = `
      <span class="entry-tag ${tagClass}">${tagText}</span>
      <span class="entry-class" style="color: ${CLASS_HEX[cls] || 'var(--ink)'};">${cls}</span>
      <span class="entry-conf">${conf}%</span>
      <span class="entry-time">${time}</span>
    `;

    feed.insertBefore(entry, feed.firstChild);

    while (feed.children.length > 50) {
      feed.removeChild(feed.lastChild);
    }
  }

  // ═══════════════════════════════════════════════════════════════════════════
  //  8. METRICS
  // ═══════════════════════════════════════════════════════════════════════════
  async function loadMetrics() {
    if (!dom.metricsContent) return;
    dom.metricsContent.textContent = 'Querying Prometheus /metrics exporter…';
    try {
      const res = await fetch(`${API_BASE}/metrics`);
      if (res.ok) {
        const text = await res.text();
        dom.metricsContent.textContent = text || 'No metrics returned.';
      } else {
        dom.metricsContent.textContent = `HTTP ${res.status}: Exporter endpoint unavailable.`;
      }
    } catch {
      dom.metricsContent.textContent = 'Connection refused on metrics endpoint.';
    }
  }

  // ═══════════════════════════════════════════════════════════════════════════
  //  9. TOAST
  // ═══════════════════════════════════════════════════════════════════════════
  let toastTimer = null;

  function showToast(msg) {
    if (!dom.toast || !dom.toastText) return;
    dom.toastText.textContent = msg;
    dom.toast.classList.remove('hidden');

    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      dom.toast.classList.add('hidden');
    }, 4500);
  }

  function hideToast() {
    if (dom.toast) dom.toast.classList.add('hidden');
    clearTimeout(toastTimer);
  }

  // ─── Minimal Fallback Payload ───
  const FALLBACK_PAYLOAD = {
    "flows": Array.from({ length: 20 }, (_, i) => ({
      "session_id": "seed-001",
      "timestamp": new Date(Date.now() + i * 1000).toISOString(),
      "features": {
        "protocol": 6, "flow_duration": 100000 + i * 1000,
        "total_fwd_packets": 10 + i, "total_backward_packets": 8 + i,
        "total_length_of_fwd_packets": 1500 + i * 100,
        "total_length_of_bwd_packets": 1200 + i * 80,
        "fwd_packet_length_max": 1460, "fwd_packet_length_min": 40,
        "fwd_packet_length_mean": 500, "fwd_packet_length_std": 200,
        "bwd_packet_length_max": 1460, "bwd_packet_length_min": 40,
        "bwd_packet_length_mean": 400, "bwd_packet_length_std": 180,
        "flow_bytes_per_s": 50000, "flow_packets_per_s": 100,
        "flow_iat_mean": 10000, "flow_iat_std": 5000,
        "flow_iat_max": 20000, "flow_iat_min": 1000,
        "fwd_iat_total": 50000, "fwd_iat_mean": 5000,
        "fwd_iat_std": 2500, "fwd_iat_max": 10000, "fwd_iat_min": 500,
        "bwd_iat_total": 40000, "bwd_iat_mean": 4000,
        "bwd_iat_std": 2000, "bwd_iat_max": 8000, "bwd_iat_min": 400,
        "fwd_psh_flags": 1, "bwd_psh_flags": 0,
        "fwd_urg_flags": 0, "bwd_urg_flags": 0,
        "fwd_header_length": 320, "bwd_header_length": 280,
        "fwd_packets_per_s": 50, "bwd_packets_per_s": 40,
        "min_packet_length": 40, "max_packet_length": 1460,
        "packet_length_mean": 450, "packet_length_std": 300,
        "packet_length_variance": 90000,
        "fin_flag_count": 0, "syn_flag_count": 1,
        "rst_flag_count": 0, "psh_flag_count": 1,
        "ack_flag_count": 1, "urg_flag_count": 0,
        "cwe_flag_count": 0, "ece_flag_count": 0,
        "down_up_ratio": 0.8,
        "average_packet_size": 450,
        "avg_fwd_segment_size": 500, "avg_bwd_segment_size": 400,
        "fwd_avg_bytes_per_bulk": 0, "fwd_avg_packets_per_bulk": 0,
        "fwd_avg_bulk_rate": 0,
        "bwd_avg_bytes_per_bulk": 0, "bwd_avg_packets_per_bulk": 0,
        "bwd_avg_bulk_rate": 0,
        "subflow_fwd_packets": 10, "subflow_fwd_bytes": 1500,
        "subflow_bwd_packets": 8, "subflow_bwd_bytes": 1200,
        "init_win_bytes_forward": 65535, "init_win_bytes_backward": 65535,
        "act_data_pkt_fwd": 8, "min_seg_size_forward": 40,
        "active_mean": 50000, "active_std": 10000,
        "active_max": 80000, "active_min": 20000,
        "idle_mean": 100000, "idle_std": 50000,
        "idle_max": 200000, "idle_min": 50000,
        "rate_nonfinite": 0
      }
    }))
  };

  // ═══════════════════════════════════════════════════════════════════════════
  //  10. INITIALIZATION
  // ═══════════════════════════════════════════════════════════════════════════
  function init() {
    initNav();
    buildPosterWall();
    initSampleChips();
    updateDashboard();

    // Event listeners
    if (dom.btnDetect) dom.btnDetect.addEventListener('click', runDetect);
    if (dom.btnLoadExample) dom.btnLoadExample.addEventListener('click', () => loadPresetSample('ddos'));
    if (dom.btnStream) dom.btnStream.addEventListener('click', pushFlow);
    if (dom.btnRefreshMetrics) dom.btnRefreshMetrics.addEventListener('click', loadMetrics);
    if (dom.toastClose) dom.toastClose.addEventListener('click', hideToast);

    // Keyboard shortcuts (Cmd+Enter or Ctrl+Enter to submit)
    if (dom.detectInput) {
      dom.detectInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
          e.preventDefault();
          runDetect();
        }
      });
    }
    if (dom.streamInput) {
      dom.streamInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
          e.preventDefault();
          pushFlow();
        }
      });
    }

    // Initial default sample load
    loadPresetSample('ddos');

    // Initial health check & recurring check every 12s
    checkHealth();
    setInterval(checkHealth, 12000);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
