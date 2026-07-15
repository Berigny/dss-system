/**
 * ourIP.AI Frontend JavaScript (Threadless Edition)
 * Handles chat helpers, Settings Panel, Stats, and Ledger Management.
 */

// --- UI Interactivity (Settings Panel) ---

const HISTORY_ENTITY_ALL = '__all__';

// DSS-245: BigInt-safe coordinate fields. These are emitted as decimal strings
// by the backend when they exceed the JS safe integer range.
const _BIGINT_COORDINATE_KEYS = new Set([
    'prime_multiplicative_value',
    'token_prime_product',
    'body_prime',
    'numerator',
    'denominator',
]);

function parseCoordinateJson(text) {
    return JSON.parse(text, (key, value) => {
        if (
            _BIGINT_COORDINATE_KEYS.has(key)
            && typeof value === 'string'
            && /^-?\d+$/.test(value)
        ) {
            try {
                return BigInt(value);
            } catch (_) {
                return value;
            }
        }
        return value;
    });
}

const threadlessMetrics = {
    isGhostState: false,
    sessionCost: 0,
    totalLatency: 0,
    requestCount: 0,
};

function buildControlPlaneLoginUrl() {
    const rawBase = typeof window?.dsControlPlaneBase === 'string' && window.dsControlPlaneBase.trim()
        ? window.dsControlPlaneBase.trim()
        : '';  // configured by the server via window.dsControlPlaneBase
    const nextTarget = `${window.location.origin}${window.location.pathname}${window.location.search || ''}`;
    return `${rawBase.replace(/\/$/, '')}/login?next=${encodeURIComponent(nextTarget)}`;
}

function redirectToControlPlaneLogin(loginUrl) {
    const target = (typeof loginUrl === 'string' && loginUrl.trim()) || buildControlPlaneLoginUrl();
    window.location.href = target;
}

// DSS-083: Global fetch interceptor — redirect all surfaces to Control Plane login on 401
(function _installGlobalAuthInterceptor() {
    const originalFetch = window.fetch;
    window.fetch = async function dsFetch(...args) {
        const response = await originalFetch.apply(this, args);
        if (response.status === 401) {
            try {
                const cloned = response.clone();
                const data = await cloned.json();
                const loginUrl = typeof data?.login_url === 'string'
                    ? data.login_url.trim()
                    : (typeof data?.detail?.login_url === 'string' ? data.detail.login_url.trim() : '');
                redirectToControlPlaneLogin(loginUrl);
            } catch (_e) {
                redirectToControlPlaneLogin();
            }
        }
        return response;
    };
})();

// DSS-083: HTMX 401 handler — catch XHR-based auth failures
(function _installHtmxAuthInterceptor() {
    function _handleHtmx401(event) {
        const xhr = event.detail?.xhr;
        if (!xhr || xhr.status !== 401) return;
        try {
            const data = JSON.parse(xhr.responseText);
            const loginUrl = typeof data?.login_url === 'string'
                ? data.login_url.trim()
                : (typeof data?.detail?.login_url === 'string' ? data.detail.login_url.trim() : '');
            redirectToControlPlaneLogin(loginUrl);
        } catch (_e) {
            redirectToControlPlaneLogin();
        }
    }
    document.addEventListener('htmx:responseError', _handleHtmx401);
})();

async function loadAttachmentLimits() {
    try {
        const rawApiBase = typeof window !== 'undefined' ? String(window.dsApiBase || '') : '';
        const apiBase = rawApiBase.replace(/\/+$/, '');
        const primaryResponse = await fetch('/api/ingest/limits');
        if (primaryResponse.ok) {
            const text = await primaryResponse.text();
            if (text) {
                const data = JSON.parse(text);
                if (Number.isFinite(Number(data?.attachment_max_bytes))) {
                    window.dsAttachmentMaxBytes = Number(data.attachment_max_bytes);
                }
            }
            return;
        }
        if (apiBase) {
            const fallbackResponse = await fetch(`${apiBase}/api/ingest/limits`);
            if (!fallbackResponse.ok) return;
            const text = await fallbackResponse.text();
            if (!text) return;
            const data = JSON.parse(text);
            if (Number.isFinite(Number(data?.attachment_max_bytes))) {
                window.dsAttachmentMaxBytes = Number(data.attachment_max_bytes);
            }
        }
    } catch (error) {
        console.warn('Failed to load attachment limits', error);
    }
}

loadAttachmentLimits();

const DEPRECATED_MODEL_IDS = {
    'x-ai/grok-4-fast': 'x-ai/grok-4.3',
};

function normalizeModelId(modelId) {
    return DEPRECATED_MODEL_IDS[modelId] || modelId;
}

async function loadLedgerFoundingPurpose() {
    const ledgerId = typeof window.dsActiveLedger === 'string' ? window.dsActiveLedger.trim() : '';
    if (!ledgerId) return;
    const storageKey = `ds_ledger_purpose_${ledgerId}`;
    const subtitleEl = document.getElementById('ledger-purpose-subtitle');
    try {
        const cached = sessionStorage.getItem(storageKey);
        if (cached) {
            if (subtitleEl) subtitleEl.textContent = cached;
            return;
        }
    } catch (_e) {
        // sessionStorage may be unavailable; continue to fetch
    }
    try {
        const response = await fetch(`/api/ledger/${encodeURIComponent(ledgerId)}/purpose`);
        if (!response.ok) return;
        const data = await response.json();
        const purpose = typeof data?.purpose === 'string' && data.purpose.trim() ? data.purpose.trim() : null;
        if (!purpose) return;
        try {
            sessionStorage.setItem(storageKey, purpose);
        } catch (_e) {
            // ignore storage errors
        }
        if (subtitleEl) subtitleEl.textContent = purpose;
    } catch (error) {
        console.warn('Failed to load ledger founding purpose', error);
    }
}

function setupStatusLabel(status) {
    const text = String(status || '').replace(/_/g, ' ').trim();
    return text ? text.charAt(0).toUpperCase() + text.slice(1) : 'Unknown';
}

function renderSetupChecklist(checklist) {
    const root = document.getElementById('account-setup-checklist');
    if (!root) return;
    const items = Array.isArray(checklist?.items) ? checklist.items : [];
    root.innerHTML = '';
    const summary = document.createElement('div');
    summary.className = 'setup-checklist-summary';
    const complete = checklist?.summary?.required_complete ?? 0;
    const required = checklist?.summary?.required ?? 0;
    summary.textContent = `${complete}/${required} required setup tasks complete`;
    root.appendChild(summary);
    if (!items.length) {
        const empty = document.createElement('div');
        empty.className = 'setup-checklist-empty';
        empty.textContent = 'No setup checklist items are available.';
        root.appendChild(empty);
        return;
    }
    const list = document.createElement('div');
    list.className = 'setup-checklist-items';
    for (const item of items) {
        const card = document.createElement('article');
        const state = String(item?.state || 'unknown');
        card.className = `setup-checklist-card state-${state.replace(/_/g, '-')}`;
        card.id = String(item?.item_id || '');

        const head = document.createElement('div');
        head.className = 'setup-checklist-card-head';
        const title = document.createElement('h2');
        title.textContent = String(item?.label || item?.item_id || 'Setup item');
        const badge = document.createElement('span');
        badge.className = 'setup-checklist-badge';
        badge.textContent = setupStatusLabel(state);
        head.append(title, badge);

        const explanation = document.createElement('p');
        explanation.className = 'setup-checklist-explanation';
        explanation.textContent = String(item?.explanation || 'Status is derived from backend account truth.');

        const meta = document.createElement('div');
        meta.className = 'setup-checklist-meta';
        const requiredLabel = item?.required === true ? 'Required' : 'Optional';
        const actionability = setupStatusLabel(item?.actionability || 'informational');
        meta.textContent = `${requiredLabel} · ${actionability}`;

        card.append(head, explanation, meta);
        if (item?.actionable === true && item?.action_label) {
            const action = document.createElement('a');
            action.className = 'setup-checklist-action';
            action.href = String(item?.action_href || `#${item?.item_id || ''}`);
            action.textContent = String(item.action_label);
            card.appendChild(action);
        }
        list.appendChild(card);
    }
    root.appendChild(list);
}

async function fetchSetupChecklist() {
    const apiBase = setupPromptApiBase();
    if (!apiBase) return null;
    const response = await fetch(`${apiBase}/account/current/setup-checklist`, {
        headers: { Accept: 'application/json' },
    });
    if (!response.ok) return null;
    const payload = await response.json().catch(() => ({}));
    return payload?.setup_checklist || null;
}

async function initSetupChecklistPage() {
    if (!document.getElementById('account-setup-checklist')) return;
    try {
        const checklist = await fetchSetupChecklist();
        renderSetupChecklist(checklist);
    } catch (error) {
        console.warn('Unable to load setup checklist', error);
    }
}

function adjustInputHeight(el) {
    if (!el) return;
    el.style.height = 'auto';
    const maxHeight = Math.max(window.innerHeight * 0.25, 200);
    const nextHeight = Math.min(el.scrollHeight, maxHeight);
    el.style.height = `${nextHeight}px`;
    el.style.overflowY = el.scrollHeight > maxHeight ? 'auto' : 'hidden';
    updateStickyStack();
}

function updateStickyStack() {
    const inputShell = document.getElementById('input-shell');
    const chatStream = document.getElementById('chat-stream');

    const headerHeight = inputShell?.getBoundingClientRect().height || 0;
    const topOffset = 110;

    const stackHeight = 1.5 * parseFloat(getComputedStyle(document.documentElement).fontSize);

    document.documentElement.style.setProperty(
        '--header-height',
        `${headerHeight + topOffset}px`,
    );
    document.documentElement.style.setProperty('--sticky-stack-height', `${stackHeight}px`);
    document.documentElement.style.setProperty('--reply-top-gap', '0rem');

    if (chatStream) {
        chatStream.style.scrollPaddingTop = '0px';
        chatStream.style.marginTop = '0px';
    }
}

let stickyUpdateTimer = null;

function scheduleStickyUpdate(delay = 50) {
    if (stickyUpdateTimer !== null) return;
    stickyUpdateTimer = window.setTimeout(() => {
        stickyUpdateTimer = null;
        updateStickyStack();
    }, delay);
}

const OVERLAY_MIN_MS = 1200;
const LOADING_MESSAGES = [
    'Initializing...',
    'Fetching history...',
    'Eq6 pressure shaping hops...',
    'Thinking...',
    'Synthesizing response...',
    'Lawfulness/CW steering choices...',
    'Finalizing...',
];
let overlayShownAt = 0;
let overlayHideTimer = null;
let overlayTickerTimer = null;
let overlayMessageIndex = 0;
let overlayManualStatus = false;
let overlayStreamingActive = false;
let overlayStatusQueueTimer = null;
let overlayStatusQueueIndex = 0;
let overlayPreStreamTimer = null;
let overlayMode = 'generic';
let lastResolveSummarySignature = '';
const overlayStatusQueue = [];
const overlayStatusSeen = new Set();
const TIMING_ENABLED = Boolean(window?.dsTimingDebug);
const SERVER_LOG_ENABLED = Boolean(window?.dsServerLog);
const INLINE_TICKER_ENABLED = window?.dsInlineTicker === true;
const PIPELINE_EVENT_BUFFER_MAX = 80;
const RESOLVE_DEBUG_ENABLED = (() => {
    try {
        const params = new URLSearchParams(window.location.search);
        const value = (params.get('debug') || '').trim().toLowerCase();
        return ['1', 'true', 'yes', 'on'].includes(value);
    } catch (error) {
        return false;
    }
})();

const DEMO_CONNECTIVITY_PROBE_MS = 30000;
const demoConnectivityState = {
    offline: false,
    ticker: null,
    probeInFlight: false,
    probeMbps: null,
};

function getConnectionDownlink() {
    const conn = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
    const downlink = conn && Number.isFinite(Number(conn.downlink)) ? Number(conn.downlink) : null;
    return downlink;
}

async function sampleDemoConnectivitySpeed() {
    if (demoConnectivityState.offline) {
        demoConnectivityState.probeMbps = null;
        return;
    }
    if (demoConnectivityState.probeInFlight) return;
    demoConnectivityState.probeInFlight = true;
    const startedAt = performance.now();
    try {
        const response = await fetch(`/api/demo/network-probe?ts=${Date.now()}`, { cache: 'no-store' });
        if (!response.ok) return;
        const text = await response.text();
        const elapsedMs = Math.max(performance.now() - startedAt, 1);
        const bytes = Math.max(new TextEncoder().encode(text).length, 1);
        const mbps = (bytes * 8) / (elapsedMs / 1000) / 1_000_000;
        if (!Number.isFinite(mbps) || mbps <= 0) return;
        demoConnectivityState.probeMbps = mbps;
    } catch (error) {
        const browserDownlink = getConnectionDownlink();
        demoConnectivityState.probeMbps = Number.isFinite(browserDownlink) && browserDownlink > 0
            ? Number(browserDownlink)
            : null;
        return;
    } finally {
        demoConnectivityState.probeInFlight = false;
    }
}

function isOllamaModelSelected() {
    const selected = String(document.getElementById('agent-select')?.value || '').trim().toLowerCase();
    return selected.startsWith('ollama/');
}

function isOpenRouterModelOption(option) {
    if (!option) return false;
    const value = String(option.value || '').trim().toLowerCase();
    if (value.startsWith('openrouter/')) return true;
    const parent = option.parentElement;
    if (parent && parent.tagName === 'OPTGROUP') {
        const label = String(parent.getAttribute('label') || '').trim().toLowerCase();
        if (label.includes('openrouter')) return true;
    }
    return false;
}

function enforceOfflineModelSelection(agentSelect = document.getElementById('agent-select'), persist = true) {
    if (!agentSelect) return;
    const offline = demoConnectivityState.offline === true;
    const options = Array.from(agentSelect.options || []);
    let firstOllama = '';
    for (const option of options) {
        const value = String(option.value || '').trim();
        if (!value) continue;
        const isOllama = value.toLowerCase().startsWith('ollama/');
        if (isOllama && !firstOllama) firstOllama = value;
        if (isOpenRouterModelOption(option)) {
            option.disabled = offline;
        } else {
            option.disabled = false;
        }
    }
    if (!offline) return;
    const current = String(agentSelect.value || '').trim();
    if (current.toLowerCase().startsWith('ollama/')) return;
    if (!firstOllama) return;
    if (current !== firstOllama) {
        agentSelect.value = firstOllama;
        setCookieValue('ds_agent', firstOllama);
        if (persist) {
            postAgentSelection(firstOllama).catch((error) => {
                console.error('Unable to switch to offline default agent', error);
            });
        }
    }
}

function renderDemoConnectivity() {
    const speedEl = document.getElementById('panel-demo-speed');
    const button = document.getElementById('demo-offline-toggle-btn');
    const offline = demoConnectivityState.offline === true;
    if (button) {
        button.textContent = offline ? 'Go Online' : 'Go Offline';
        button.classList.toggle('is-offline', offline);
    }
    if (speedEl) {
        if (offline) {
            speedEl.textContent = '0.00 Mbps';
        } else {
            const downlink = getConnectionDownlink();
            const measuredMbps = Number.isFinite(demoConnectivityState.probeMbps) && demoConnectivityState.probeMbps > 0
                ? demoConnectivityState.probeMbps
                : (Number.isFinite(downlink) && downlink > 0 ? downlink : null);
            speedEl.textContent = Number.isFinite(measuredMbps) && measuredMbps > 0
                ? `${measuredMbps.toFixed(3)} Mbps`
                : 'Measuring...';
        }
    }
}

function setDemoOfflineState(offline) {
    demoConnectivityState.offline = Boolean(offline);
    renderDemoConnectivity();
    if (!demoConnectivityState.offline) {
        sampleDemoConnectivitySpeed().finally(() => renderDemoConnectivity());
    }
    enforceOfflineModelSelection(undefined, true);
}

async function toggleDemoOffline() {
    const next = !demoConnectivityState.offline;
    try {
        const response = await fetch('/api/demo/offline-toggle', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ offline: next }),
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(payload?.detail || 'Failed to toggle demo connectivity');
        }
        setDemoOfflineState(Boolean(payload?.offline));
    } catch (error) {
        console.error('Unable to toggle demo connectivity', error);
        alert(error?.message || 'Unable to toggle demo connectivity');
    }
}

function initDemoConnectivityTicker() {
    renderDemoConnectivity();

    const runProbe = () => {
        if (document.hidden) return;
        sampleDemoConnectivitySpeed().finally(() => renderDemoConnectivity());
    };

    runProbe();
    if (demoConnectivityState.ticker !== null) return;
    demoConnectivityState.ticker = window.setInterval(runProbe, DEMO_CONNECTIVITY_PROBE_MS);

    document.addEventListener('visibilitychange', () => {
        if (!document.hidden) runProbe();
    });
}
async function persistBackendStreamEnabled(value) {
    try {
        await fetch('/api/preferences/backend_stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled: Boolean(value) }),
        });
    } catch (error) {
        return;
    }
}

function resolveBackendStreamFlag() {
    return window?.dsBackendStreamEnabled === true;
}

function initBackendStreamToggle() {
    const toggle = document.getElementById('backend-stream-toggle');
    if (!toggle) return;
    const initial = resolveBackendStreamFlag();
    toggle.checked = Boolean(initial);
    const streamMode = document.getElementById('panel-stream-mode');
    if (streamMode) {
        streamMode.textContent = initial ? 'on' : 'off';
    }
    toggle.addEventListener('change', () => {
        const nextValue = toggle.checked === true;
        window.dsBackendStreamEnabled = nextValue;
        persistBackendStreamEnabled(nextValue);
        if (streamMode) {
            streamMode.textContent = nextValue ? 'on' : 'off';
        }
    });
}

function updateLoadingStatus(nextText, manual = false) {
    const status = document.getElementById('loading-status');
    if (!status) return;
    if (manual) {
        overlayManualStatus = true;
        overlayShownAt = Date.now();
        if (overlayTickerTimer) {
            clearInterval(overlayTickerTimer);
            overlayTickerTimer = null;
        }
    }
    status.textContent = nextText;
}

function formatMetaTimestamp(date) {
    const value = date instanceof Date ? date : new Date(date);
    return value.toLocaleString('en-GB', {
        day: '2-digit',
        month: '2-digit',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
    });
}

function resetOverlayStatusQueue() {
    if (overlayStatusQueueTimer) {
        clearInterval(overlayStatusQueueTimer);
        overlayStatusQueueTimer = null;
    }
    overlayStatusQueueIndex = 0;
    overlayStatusQueue.length = 0;
    overlayStatusSeen.clear();
}

function enqueueOverlayStatus(message, interval = 850) {
    const text = String(message || '').trim();
    if (!text || overlayStatusSeen.has(text)) return;
    overlayStatusSeen.add(text);
    overlayStatusQueue.push(text);
    overlayStatusQueueIndex = Math.max(overlayStatusQueue.length - 1, 0);
    updateLoadingStatus(text, true);
    if (!overlayStatusQueueTimer) {
        overlayStatusQueueTimer = window.setInterval(() => {
            if (!overlayStatusQueue.length) {
                resetOverlayStatusQueue();
                return;
            }
            overlayStatusQueueIndex = (overlayStatusQueueIndex + 1) % overlayStatusQueue.length;
            updateLoadingStatus(overlayStatusQueue[overlayStatusQueueIndex], true);
        }, interval);
    }
}

function startPreStreamTicker(messages, interval = 900) {
    if (overlayPreStreamTimer) {
        clearInterval(overlayPreStreamTimer);
        overlayPreStreamTimer = null;
    }
    if (!Array.isArray(messages) || messages.length === 0) return;
    let index = 0;
    overlayPreStreamTimer = window.setInterval(() => {
        if (!overlayStreamingActive || overlayStatusQueue.length) return;
        updateLoadingStatus(messages[index], false);
        index = (index + 1) % messages.length;
    }, interval);
}

function stopPreStreamTicker() {
    if (overlayPreStreamTimer) {
        clearInterval(overlayPreStreamTimer);
        overlayPreStreamTimer = null;
    }
}

function logTiming(label, start, extra = {}) {
    if (!TIMING_ENABLED) return;
    const elapsed = Math.round(performance.now() - start);
    console.info(`[timing] ${label} ${elapsed}ms`, extra);
    if (SERVER_LOG_ENABLED) {
        fetch('/api/log', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                level: 'info',
                message: label,
                data: { elapsed_ms: elapsed, ...extra },
            }),
        }).catch(() => undefined);
    }
}

function logResolveMeta(meta) {
    if (!meta || typeof meta !== 'object') return;
    const timing = meta.timing_ms;
    const coords = meta.coord_counts;
    const resolveSummary = meta.resolve_summary;
    if (!timing && !coords && !resolveSummary) return;
    const payload = {
        timing_ms: timing,
        coord_counts: coords,
        coordinate: meta.coordinate || meta.web4_key,
        resolve_summary: resolveSummary,
        candidate_trace: meta.candidate_trace || null,
        autonomy_decision: meta.autonomy_decision || null,
    };
    console.info('[timing] resolve_meta', payload);
    if (SERVER_LOG_ENABLED) {
        fetch('/api/log', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                level: 'info',
                message: 'resolve_meta',
                data: payload,
            }),
        }).catch(() => undefined);
    }
}

function applyResolveSummary(summary) {
    if (!summary || typeof summary !== 'object') return null;
    const supports = summary.supports_coord_resolution;
    const requestedCount = Number(summary.requested_count);
    const resolvedCount = Number(summary.resolved_count);
    const unresolvedCount = Number(summary.unresolved_count);

    const capabilityEl = document.getElementById('panel-resolver-capability');
    const requestedEl = document.getElementById('panel-resolver-requested');
    const resolvedEl = document.getElementById('panel-resolver-resolved');
    const unresolvedEl = document.getElementById('panel-resolver-unresolved');
    if (capabilityEl) {
        capabilityEl.textContent = supports === true ? 'yes' : supports === false ? 'no' : '—';
    }
    if (requestedEl && Number.isFinite(requestedCount)) {
        requestedEl.textContent = String(Math.max(0, requestedCount));
    }
    if (resolvedEl && Number.isFinite(resolvedCount)) {
        resolvedEl.textContent = String(Math.max(0, resolvedCount));
    }
    if (unresolvedEl && Number.isFinite(unresolvedCount)) {
        unresolvedEl.textContent = String(Math.max(0, unresolvedCount));
    }

    const queuedEl = document.getElementById('panel-coords-queued');
    const decodedEl = document.getElementById('panel-coords-decoded');
    if (queuedEl && Number.isFinite(requestedCount)) {
        queuedEl.textContent = String(Math.max(0, requestedCount));
    }
    if (decodedEl && Number.isFinite(resolvedCount)) {
        decodedEl.textContent = String(Math.max(0, resolvedCount));
    }
    const signature = `${supports}|${requestedCount}|${resolvedCount}|${unresolvedCount}`;
    return {
        supports,
        requestedCount,
        resolvedCount,
        unresolvedCount,
        signature,
    };
}

function logPipelineEvent(label, payload = {}) {
    const current = Array.isArray(window.dsPipelineEventBuffer) ? window.dsPipelineEventBuffer : [];
    current.push({
        ts: new Date().toISOString(),
        label,
        payload,
    });
    if (current.length > PIPELINE_EVENT_BUFFER_MAX) {
        current.splice(0, current.length - PIPELINE_EVENT_BUFFER_MAX);
    }
    window.dsPipelineEventBuffer = current;
    if (!RESOLVE_DEBUG_ENABLED) return;
    console.info(`[pipeline] ${label}`, payload);
}

function setLoadingOverlay(active, mode = 'generic') {
    const overlay = document.getElementById('loading-overlay');
    if (!overlay) return;
    const nextState = Boolean(active);

    if (nextState) {
        overlayMode = mode || 'generic';
        if (overlayHideTimer) {
            clearTimeout(overlayHideTimer);
            overlayHideTimer = null;
        }
        if (overlayMode === 'history') {
            overlayManualStatus = true;
            stopPreStreamTicker();
            resetOverlayStatusQueue();
            if (overlayTickerTimer) {
                clearInterval(overlayTickerTimer);
                overlayTickerTimer = null;
            }
            updateLoadingStatus('Loading history...', true);
        }
        if (!overlayTickerTimer && !overlayManualStatus && !overlayStreamingActive) {
            overlayMessageIndex = 0;
            updateLoadingStatus(LOADING_MESSAGES[overlayMessageIndex]);
            overlayTickerTimer = window.setInterval(() => {
                overlayMessageIndex = (overlayMessageIndex + 1) % LOADING_MESSAGES.length;
                updateLoadingStatus(LOADING_MESSAGES[overlayMessageIndex]);
            }, 1200);
        }
        overlayShownAt = Date.now();
        overlay.classList.add('active');
        return;
    }

    const elapsed = Date.now() - overlayShownAt;
    const remaining = Math.max(OVERLAY_MIN_MS - elapsed, 0);
    if (remaining === 0) {
        overlay.classList.remove('active');
        resetOverlayStatusQueue();
        if (overlayTickerTimer) {
            clearInterval(overlayTickerTimer);
            overlayTickerTimer = null;
        }
        if (overlayMode === 'history') {
            overlayManualStatus = false;
        }
        overlayMode = 'generic';
        return;
    }

    if (overlayHideTimer) {
        clearTimeout(overlayHideTimer);
    }
    overlayHideTimer = setTimeout(() => {
        overlay.classList.remove('active');
        resetOverlayStatusQueue();
        if (overlayTickerTimer) {
            clearInterval(overlayTickerTimer);
            overlayTickerTimer = null;
        }
        if (overlayMode === 'history') {
            overlayManualStatus = false;
        }
        overlayMode = 'generic';
        overlayHideTimer = null;
    }, remaining);
}

function setHistoryLoading(active) {
    const spinner = document.getElementById('history-spinner');
    if (!spinner) return;
    spinner.style.display = active ? '' : 'none';
}

function setAttachmentLoading(active) {
    const spinner = document.getElementById('attachment-spinner');
    if (!spinner) return;
    spinner.style.display = active ? '' : 'none';
}

function initThreadlessInput() {
    const input = document.getElementById('cmd-input');
    if (!input) return;
    adjustInputHeight(input);
    input.addEventListener('input', () => adjustInputHeight(input));
    input.addEventListener('keydown', handleInputKeydown);
    input.addEventListener('focus', (event) => handleInputFocus(event.target));
}

function disableHtmxChatForm(form) {
    if (!form) return;
    form.removeAttribute('hx-post');
    form.removeAttribute('hx-target');
    form.removeAttribute('hx-swap');
}

function disableHtmxChatStream() {
    const chatStream = document.getElementById('chat-stream');
    if (!chatStream || chatStream.dataset.hxDisabled === 'true') return;
    chatStream.removeAttribute('hx-get');
    chatStream.removeAttribute('hx-trigger');
    chatStream.removeAttribute('hx-swap');
    chatStream.dataset.hxDisabled = 'true';
}

function maybeDisableHtmxForStream() {
    const chatForm = document.getElementById('chat-form');
    const streamEnabled = window?.dsChatStreamEnabled === true;
    if (chatForm && streamEnabled) {
        disableHtmxChatForm(chatForm);
    }
}

function _getCookieValue(name) {
    const parts = document.cookie.split(';').map((part) => part.trim());
    for (const part of parts) {
        if (part.startsWith(`${name}=`)) {
            return decodeURIComponent(part.split('=')[1] || '');
        }
    }
    return '';
}

function getClientSideHistory() {
    const messages = Array.from(document.querySelectorAll('.message.user, .message.assistant'));
    const ignored = new Set(['No matching records found', 'Resolving references…']);
    const history = [];
    for (const message of messages) {
        const content = message.classList.contains('user')
            ? message.querySelector('.message-text') || message.querySelector('.message-content')
            : message.querySelector('.message-content');
        if (!content) continue;
        const text = (content.textContent || '').trim();
        if (!text || ignored.has(text)) continue;
        const role = message.classList.contains('assistant') ? 'assistant' : 'user';
        history.push({ role, content: text, metadata: {} });
    }
    return history.slice(-20);
}

function getCookieValue(name) {
    return _getCookieValue(name);
}

function setCookieValue(name, value, maxAgeSeconds = 60 * 60 * 24 * 30) {
    const encoded = encodeURIComponent(value);
    document.cookie = `${name}=${encoded}; path=/; max-age=${maxAgeSeconds}`;
}


function _firstNonEmptyString(...values) {
    for (const raw of values) {
        if (typeof raw !== 'string') continue;
        const text = raw.trim();
        if (text) return text;
    }
    return '';
}

function updateUpstreamDebugPanel(upstreamUrl, fallbackFlag) {
    const upstreamEl = document.getElementById('panel-upstream-url');
    const fallbackEl = document.getElementById('panel-upstream-fallback');

    const upstreamText = typeof upstreamUrl === 'string' ? upstreamUrl.trim() : '';
    if (upstreamEl) {
        upstreamEl.textContent = upstreamText || '—';
        upstreamEl.title = upstreamText || '';
    }

    let fallbackText = '—';
    if (typeof fallbackFlag === 'boolean') {
        fallbackText = fallbackFlag ? 'yes' : 'no';
    } else if (typeof fallbackFlag === 'string') {
        const lowered = fallbackFlag.trim().toLowerCase();
        if (['true', '1', 'yes', 'on'].includes(lowered)) fallbackText = 'yes';
        else if (['false', '0', 'no', 'off'].includes(lowered)) fallbackText = 'no';
    }
    if (fallbackEl) {
        fallbackEl.textContent = fallbackText;
    }
}

function updateAuthDebugPanel(meta) {
    if (!meta || typeof meta !== 'object') return;

    const metadata = meta && typeof meta.metadata === 'object' ? meta.metadata : {};
    const authz = meta && typeof meta.authz === 'object'
        ? meta.authz
        : metadata && typeof metadata.authz === 'object'
            ? metadata.authz
            : {};

    const principalSource = _firstNonEmptyString(
        authz.principal_source,
        meta.principal_source,
        metadata.principal_source,
    );
    const principalMode = _firstNonEmptyString(
        authz.principal_mode,
        meta.principal_mode,
        metadata.principal_mode,
    );
    const authContextId = _firstNonEmptyString(
        authz.context_id,
        meta.context_id,
        metadata.context_id,
    );
    const authzReason = _firstNonEmptyString(
        authz.authz_reason,
        meta.authz_reason,
        metadata.authz_reason,
    );

    const principalSourceEl = document.getElementById('panel-auth-principal-source');
    const principalModeEl = document.getElementById('panel-auth-principal-mode');
    const contextIdEl = document.getElementById('panel-auth-context-id');
    const reasonEl = document.getElementById('panel-authz-reason');

    if (principalSourceEl) principalSourceEl.textContent = principalSource || '—';
    if (principalModeEl) principalModeEl.textContent = principalMode || '—';
    if (contextIdEl) contextIdEl.textContent = authContextId || '—';
    if (reasonEl) reasonEl.textContent = authzReason || '—';
}

const SESSION_COOKIE_NAME = 'ds_session';
const SESSION_STORAGE_KEY = 'ds_session_local';

function _generateSessionId() {
    const nowPart = Date.now().toString(36);
    let randPart = Math.random().toString(36).slice(2, 12);
    try {
        if (window.crypto?.getRandomValues) {
            const bytes = new Uint8Array(8);
            window.crypto.getRandomValues(bytes);
            randPart = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
        }
    } catch (_err) {
        // Keep Math.random fallback.
    }
    return `web-${nowPart}-${randPart}`;
}

function ensureSessionCookie() {
    const existing = _getCookieValue(SESSION_COOKIE_NAME).trim();
    if (existing) return existing;

    let sessionId = '';
    try {
        sessionId = (window.localStorage?.getItem(SESSION_STORAGE_KEY) || '').trim();
    } catch (_err) {
        sessionId = '';
    }
    if (!sessionId) {
        sessionId = _generateSessionId();
    }
    try {
        window.localStorage?.setItem(SESSION_STORAGE_KEY, sessionId);
    } catch (_err) {
        // Ignore storage failures.
    }
    setCookieValue(SESSION_COOKIE_NAME, sessionId, 60 * 60 * 24 * 365);
    return sessionId;
}

function renderResolveDebugList(container, snippets) {
    if (!container) return;
    container.innerHTML = '';
    if (!Array.isArray(snippets) || snippets.length === 0) {
        container.textContent = 'No matching records found.';
        return;
    }
    const list = document.createElement('ul');
    for (const snippet of snippets) {
        const item = document.createElement('li');
        const coord = snippet?.coordinate ? `[${snippet.coordinate}] ` : '';
        const text = String(snippet?.text || '');
        const firstLine = text.split(/\r?\n/)[0]?.trim() || text.trim();
        item.textContent = `${coord}${firstLine}`;
        list.appendChild(item);
    }
    container.appendChild(list);
}

function _prependChatNode(node) {
    ensureChatStreamPlacement();
    const chatStream = document.getElementById('chat-stream');
    const historyList = document.getElementById('history-list');
    if (chatStream) {
        if (chatStream.contains(node)) return;
        if (historyList && historyList.parentNode === chatStream) {
            chatStream.insertBefore(node, historyList);
        } else {
            chatStream.prepend(node);
        }
        initializeUserPromptTruncation(node);
    }
}

const USER_PROMPT_MAX_LINES = 5;

function _userPromptLineHeight(element) {
    const style = window.getComputedStyle(element);
    const explicit = Number.parseFloat(style.lineHeight);
    if (Number.isFinite(explicit)) return explicit;
    const fontSize = Number.parseFloat(style.fontSize);
    return Number.isFinite(fontSize) ? fontSize * 1.5 : 33;
}

function applyUserPromptTruncation(message) {
    if (!(message instanceof Element) || !message.classList.contains('user')) return;
    const text = message.querySelector('.message-text');
    const toggle = message.querySelector('.message-toggle');
    if (!text || !toggle) return;

    message.classList.remove('is-expanded', 'is-collapsed');
    text.style.removeProperty('--user-prompt-max-height');
    toggle.hidden = true;

    const maxHeight = (_userPromptLineHeight(text) * USER_PROMPT_MAX_LINES) + 1;
    if (text.scrollHeight <= maxHeight) {
        toggle.setAttribute('aria-expanded', 'false');
        return;
    }

    text.style.setProperty('--user-prompt-max-height', `${maxHeight}px`);
    message.classList.add('is-collapsed');
    toggle.hidden = false;
    toggle.setAttribute('aria-expanded', 'false');
    toggle.setAttribute('aria-label', 'Expand full prompt');
    toggle.textContent = '▾';
}

function initializeUserPromptTruncation(root = document) {
    const messages = root instanceof Element && root.classList.contains('message user')
        ? [root]
        : Array.from(root.querySelectorAll?.('.message.user') || []);
    if (!messages.length) return;
    window.requestAnimationFrame(() => {
        messages.forEach((message) => applyUserPromptTruncation(message));
    });
}

function _createUserBubble(text, msgId) {
    const wrapper = document.createElement('div');
    wrapper.className = 'message user';
    wrapper.id = `msg-user-${msgId}`;
    const content = document.createElement('div');
    content.className = 'message-content';
    const textWrap = document.createElement('div');
    textWrap.className = 'message-text';
    textWrap.textContent = text;
    const toggle = document.createElement('button');
    toggle.className = 'message-toggle';
    toggle.type = 'button';
    toggle.hidden = true;
    toggle.setAttribute('aria-expanded', 'false');
    toggle.setAttribute('aria-label', 'Expand full prompt');
    toggle.textContent = '▾';
    toggle.addEventListener('click', () => {
        const expanded = wrapper.classList.toggle('is-expanded');
        wrapper.classList.toggle('is-collapsed', !expanded);
        toggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
        toggle.setAttribute('aria-label', expanded ? 'Collapse full prompt' : 'Expand full prompt');
        toggle.textContent = expanded ? '▴' : '▾';
    });
    content.appendChild(textWrap);
    content.appendChild(toggle);
    wrapper.appendChild(content);
    return wrapper;
}

function _createAssistantBubble(msgId) {
    const container = document.createElement('div');
    container.id = `msg-assistant-${msgId}`;

    const bubble = document.createElement('div');
    bubble.className = 'message assistant fade-in-up';

    const contentWrap = document.createElement('div');
    contentWrap.className = 'message-content';

    const spinner = document.createElement('div');
    spinner.className = 'ds-spinner';
    spinner.innerHTML = '<svg viewBox="0 0 44 44"><circle cx="22" cy="22" r="20"></circle></svg>';
    spinner.style.display = 'none';

    const thinkingIndicator = document.createElement('div');
    thinkingIndicator.className = 'thinking-indicator';
    thinkingIndicator.style.display = 'none';

    const content = document.createElement('div');
    content.className = 'prose prose-xl prose-p:font-serif prose-headings:font-serif markdown-content max-w-none text-gray-900 leading-loose';
    content.dataset.markdown = 'true';
    content.textContent = '';

    contentWrap.appendChild(spinner);
    contentWrap.appendChild(thinkingIndicator);
    contentWrap.appendChild(content);
    bubble.appendChild(contentWrap);

    const thinkingTrace = document.createElement('div');
    thinkingTrace.className = 'thinking-trace';
    thinkingTrace.style.display = 'none';

    const thinkingTraceLabel = document.createElement('span');
    thinkingTraceLabel.className = 'thinking-trace-label';
    thinkingTraceLabel.textContent = 'Thinking';

    const thinkingTraceToggle = document.createElement('button');
    thinkingTraceToggle.className = 'thinking-trace-toggle';
    thinkingTraceToggle.type = 'button';
    thinkingTraceToggle.hidden = true;
    thinkingTraceToggle.setAttribute('aria-expanded', 'false');
    thinkingTraceToggle.textContent = '▾';
    thinkingTraceToggle.addEventListener('click', () => {
        const expanded = bubble.classList.toggle('is-thinking-expanded');
        thinkingTraceToggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
        thinkingTraceToggle.textContent = expanded ? '▴' : '▾';
    });

    const thinkingTraceLines = document.createElement('div');
    thinkingTraceLines.className = 'thinking-trace-lines';

    thinkingTrace.appendChild(thinkingTraceLabel);
    thinkingTrace.appendChild(thinkingTraceToggle);
    thinkingTrace.appendChild(thinkingTraceLines);
    bubble.appendChild(thinkingTrace);

    const meta = document.createElement('div');
    meta.className = 'meta';
    const metaText = document.createTextNode('— | ');
    const coord = document.createElement('span');
    coord.className = 'coordinate';
    coord.dataset.coordinate = '';
    coord.style.textDecoration = 'underline';
    coord.style.cursor = 'pointer';
    coord.title = 'Copy coordinate';
    coord.addEventListener('click', () => {
        if (coord.dataset.coordinate) {
            navigator.clipboard.writeText(coord.dataset.coordinate);
        }
    });
    meta.appendChild(metaText);
    meta.appendChild(coord);
    const walkText = document.createElement('span');
    walkText.className = 'meta-walk';
    walkText.textContent = '';
    meta.appendChild(walkText);
    const promptText = document.createElement('span');
    promptText.className = 'meta-prompt';
    promptText.textContent = '';
    meta.appendChild(promptText);
    const requestedText = document.createElement('span');
    requestedText.className = 'meta-requested';
    requestedText.textContent = '';
    meta.appendChild(requestedText);
    const modelText = document.createElement('span');
    modelText.className = 'meta-model';
    modelText.textContent = '';
    meta.appendChild(modelText);
    const integrityText = document.createElement('span');
    integrityText.className = 'meta-integrity';
    integrityText.textContent = '';
    meta.appendChild(integrityText);

    bubble.appendChild(meta);
    const ticker = document.createElement('div');
    ticker.className = 'meta-thinking';
    ticker.textContent = '';
    if (!INLINE_TICKER_ENABLED) {
        ticker.style.display = 'none';
    }
    bubble.appendChild(ticker);
    container.appendChild(bubble);

    return { container, content, coord, metaText, modelText, walkText, promptText, requestedText, integrityText, ticker, spinner, thinkingIndicator, thinkingTrace, thinkingTraceLabel, thinkingTraceLines, thinkingTraceToggle, bubble };
}

function _promptPrincipalLabel(payload) {
    const delegated = payload && payload.delegated_prompt_path && typeof payload.delegated_prompt_path === 'object'
        ? payload.delegated_prompt_path
        : {};
    const contributor = payload && payload.contributor && typeof payload.contributor === 'object'
        ? payload.contributor
        : {};
    const displayName = String(
        delegated.prompt_principal_display_name
        || contributor.principal_display_name
        || payload.principal_display_name
        || ''
    ).trim();
    if (displayName) return displayName;
    const explicitLabel = String(payload.prompt_principal_label || '').trim();
    if (explicitLabel) return explicitLabel;
    const principalType = String(delegated.prompt_principal_type || contributor.principal_type || '').trim().toLowerCase();
    const principalId = String(delegated.prompt_principal_id || contributor.principal_id || '').trim();
    const principalDid = String(delegated.prompt_principal_did || contributor.principal_did || '').trim();
    if (principalType === 'agent' || principalId.startsWith('openai:agent:') || principalId.startsWith('openai:')) {
        if (principalId.startsWith('openai:agent:')) return `openai/${principalId.slice('openai:agent:'.length)}`;
        if (principalId.startsWith('openai:')) return `openai/${principalId.slice('openai:'.length)}`;
    }
    const marker = ':principals:agent:openai:';
    if (principalDid.includes(marker)) {
        return `openai/${principalDid.split(marker, 2)[1]}`;
    }
    return principalId || principalDid || '';
}

function _requestedByLabel(payload) {
    const delegated = payload && payload.delegated_prompt_path && typeof payload.delegated_prompt_path === 'object'
        ? payload.delegated_prompt_path
        : {};
    return String(delegated.requested_by_principal_did || '').trim();
}

function _answerSurfaceIntegrityText(payload) {
    const integrity = payload && payload.answer_surface_integrity && typeof payload.answer_surface_integrity === 'object'
        ? payload.answer_surface_integrity
        : {};
    const status = String(integrity.status || '').trim().toLowerCase();
    const reason = String(integrity.reason || '').trim().toLowerCase();
    if (status === 'diverged' && reason === 'assembly_summary_richer_than_visible_answer') {
        return 'summary richer than visible answer';
    }
    if (status === 'collapsed' && reason === 'visible_answer_preamble_collapse_under_blocked_context') {
        return 'visible answer collapsed under blocked context';
    }
    if (status) {
        return `answer integrity: ${status}`;
    }
    return '';
}


function buildTraceRequestId(prefix = 'trace') {
    const token = Math.random().toString(36).slice(2, 8);
    return `${String(prefix || 'trace')}-${Date.now().toString(36)}-${token}`;
}

function traceStatusFromPayload(tracePayload) {
    if (!tracePayload || typeof tracePayload !== 'object') return '';
    const explicit = tracePayload.step_label ? String(tracePayload.step_label) : '';
    if (explicit) return explicit;
    const stepCode = tracePayload.step_code ? String(tracePayload.step_code) : '';
    const stepMap = {
        REQ_ACCEPTED: 'Request accepted',
        CTX_ASSEMBLY_START: 'Assembling context',
        CTX_ASSEMBLY_DONE: 'Context assembly complete',
        MODEL_STREAM_START: 'Model stream started',
        MODEL_STREAM_DONE: 'Model stream completed',
        PERSIST_START: 'Persisting and auditing response',
        PERSIST_DONE: 'Persistence and audit complete',
        FINALIZE: 'Response finalized',
        HISTORY_LOAD_START: 'Loading history',
        HISTORY_LOAD_DONE: 'History loaded',
        ATTACH_UPLOAD_START: 'Uploading attachment',
        ATTACH_UPLOAD_DONE: 'Attachment uploaded',
        ATTACH_INGEST_DONE: 'Attachment ingestion complete',
    };
    if (stepCode && stepMap[stepCode]) return stepMap[stepCode];
    const eventType = tracePayload.type ? String(tracePayload.type) : '';
    if (eventType === 'process_started') return 'Process started';
    if (eventType === 'process_completed') return 'Process completed';
    if (eventType === 'process_failed') return 'Process failed';
    return '';
}

function extractTraceCoords(tracePayload) {
    if (!tracePayload || typeof tracePayload !== 'object') return [];
    const out = [];
    const seen = new Set();
    const maybePush = (value) => {
        const text = String(value || '').trim();
        if (!text || seen.has(text)) return;
        seen.add(text);
        out.push(text);
    };
    const details = tracePayload.details && typeof tracePayload.details === 'object' ? tracePayload.details : {};
    [tracePayload.coord, details.coord, details.coordinate].forEach(maybePush);
    [tracePayload.coords, details.coords, details.queued_coords, details.resolved_coords].forEach((value) => {
        if (Array.isArray(value)) {
            value.forEach(maybePush);
        }
    });
    return out;
}

function thinkingTraceOverlayLine(tracePayload) {
    if (!tracePayload || typeof tracePayload !== 'object') return '';
    const parts = [];
    const statusText = traceStatusFromPayload(tracePayload);
    if (statusText) {
        parts.push(statusText);
    }
    const traceCoords = extractTraceCoords(tracePayload).slice(0, 3);
    traceCoords.forEach((coord) => {
        parts.push(`COORD: ${coord}`);
    });
    const details = tracePayload.details && typeof tracePayload.details === 'object' ? tracePayload.details : {};
    const resolvedCount = Number(details.resolved_count);
    const coordCount = Number(details.coord_count);
    if (Number.isFinite(resolvedCount) && Number.isFinite(coordCount) && coordCount > 0) {
        parts.push(`Resolved ${resolvedCount}/${coordCount}`);
    }
    if (!parts.length) return '';
    return `Thinking: ${parts.join(' · ')}`;
}

function renderUiStatusPayload(uiPayload) {
    if (!uiPayload || typeof uiPayload !== 'object') return '';
    const message = uiPayload.message ? String(uiPayload.message).trim() : '';
    if (message) return message;
    return '';
}

function renderStatusPayload(payload) {
    if (!payload || typeof payload !== 'object') return '';
    const message = payload.message ? String(payload.message).trim() : '';
    if (!message) return '';
    const resolvingCoordMatch = message.match(/^Resolving\s+([A-Za-z0-9_-]+:(?:WX|ATT|EV-WALK|ATT-PART)-[A-Za-z0-9]+(?:-[0-9]+)?|(?:WX|ATT|EV-WALK|ATT-PART)-[A-Za-z0-9]+(?:-[0-9]+)?)\.\.\.$/);
    if (resolvingCoordMatch) {
        return `Decoding COORD: ${resolvingCoordMatch[1]}`;
    }
    return message;
}

function summarizeCoordChainTrace(chainTrace) {
    const items = Array.isArray(chainTrace) ? chainTrace : [];
    if (!items.length) return '';
    const parts = [];
    items.slice(0, 3).forEach((item) => {
        if (!item || typeof item !== 'object') return;
        const coord = item.coord ? String(item.coord).trim() : '';
        if (!coord) return;
        const states = [];
        if (item.planned === true) states.push('planned');
        if (item.opened === true) states.push('opened');
        if (item.admitted === true) states.push('admitted');
        if (!states.length) return;
        parts.push(`${coord}(${states.join('/')})`);
    });
    if (!parts.length) return '';
    return `Coord chain: ${parts.join(' -> ')}`;
}

function collectPipelineTickerMessages(payload) {
    const thinking = [];
    const overlay = [];
    if (!payload || typeof payload !== 'object') {
        return { thinking, overlay };
    }

    if (payload.type === 'ui_status') {
        const uiPayload = payload.payload || {};
        const uiLine = renderUiStatusPayload(uiPayload);
        if (uiLine) {
            thinking.push(uiLine);
            overlay.push(uiLine);
        }
        return { thinking, overlay };
    }

    if (payload.type === 'status') {
        const statusLine = renderStatusPayload(payload);
        if (statusLine) {
            thinking.push(statusLine);
            overlay.push(statusLine);
        }
        return { thinking, overlay };
    }

    if (payload.type === 'context_meta') {
        if (Array.isArray(payload.queued_coords)) {
            payload.queued_coords.forEach((coord) => {
                thinking.push(`Queued: ${coord}`);
                overlay.push(`Resolving Coords: ${coord}`);
            });
        }
        if (Array.isArray(payload.resolved_coords)) {
            payload.resolved_coords.forEach((coord) => {
                thinking.push(`Resolved: ${coord}`);
            });
        }
        if (Array.isArray(payload.candidate_trace) && payload.candidate_trace.length) {
            const top = payload.candidate_trace[0] || {};
            const topCoord = top.coord ? String(top.coord) : '';
            const topTier = Number.isFinite(Number(top.tier_rank)) ? `R${Number(top.tier_rank)}` : '';
            if (topCoord) {
                const topText = topTier ? `Top candidate: ${topCoord} (${topTier})` : `Top candidate: ${topCoord}`;
                thinking.push(topText);
                overlay.push(topText);
            }
        }
        if (payload.autonomy_decision && typeof payload.autonomy_decision === 'object') {
            const action = payload.autonomy_decision.action ? String(payload.autonomy_decision.action) : 'unknown';
            const reason = payload.autonomy_decision.reason ? String(payload.autonomy_decision.reason) : '';
            const msg = reason ? `Autonomy: ${action} (${reason})` : `Autonomy: ${action}`;
            thinking.push(msg);
            overlay.push(msg);
        }
        const chainText = summarizeCoordChainTrace(payload.coord_chain_trace);
        if (chainText) {
            thinking.push(chainText);
            overlay.push(chainText);
        }
        return { thinking, overlay };
    }

    if (payload.type === 'context_item' && payload.coord) {
        thinking.push(`Context: ${payload.coord}`);
        overlay.push(`Resolving Coords: ${payload.coord}`);
        return { thinking, overlay };
    }

    if (payload.type === 'hop_enrich') {
        const hopPayload = payload.payload || {};
        const hop = Number.isFinite(Number(hopPayload.hop)) ? Number(hopPayload.hop) : 0;
        const skim = hopPayload.skim ? String(hopPayload.skim) : '';
        if (skim) {
            const text = `Hop ${hop + 1}: ${skim}`;
            thinking.push(text);
            overlay.push(text);
        }
        return { thinking, overlay };
    }

    if (payload.type === 'candidate_trace') {
        const tracePayload = payload.payload || {};
        const topK = Array.isArray(tracePayload.top_k) ? tracePayload.top_k : [];
        if (topK.length) {
            const first = topK[0] || {};
            const coord = first.coord ? String(first.coord) : '';
            const tier = Number.isFinite(Number(first.tier_rank)) ? `R${Number(first.tier_rank)}` : '';
            if (coord) {
                const txt = tier ? `Top candidate: ${coord} (${tier})` : `Top candidate: ${coord}`;
                thinking.push(txt);
                overlay.push(txt);
            }
        }
        return { thinking, overlay };
    }

    if (payload.type === 'autonomy_decision') {
        const decision = payload.payload || {};
        const action = decision.action ? String(decision.action) : 'unknown';
        const reason = decision.reason ? String(decision.reason) : '';
        const text = reason ? `Autonomy: ${action} (${reason})` : `Autonomy: ${action}`;
        thinking.push(text);
        overlay.push(text);
        return { thinking, overlay };
    }

    if (payload.type === 'coord_action_plan') {
        const actionPlan = payload.payload || {};
        const action = actionPlan.action ? String(actionPlan.action) : 'unknown';
        const coord = actionPlan.coord ? String(actionPlan.coord) : '';
        const reason = actionPlan.reason ? String(actionPlan.reason) : '';
        let text = `Model action: ${action}`;
        if (coord) {
            text += ` · COORD: ${coord}`;
        }
        if (reason) {
            text += ` · ${reason}`;
        }
        thinking.push(text);
        overlay.push(text);
        return { thinking, overlay };
    }

    if (payload.type === 'decision_trace') {
        const trace = payload.payload || {};
        const hop = Number.isFinite(Number(trace.hop)) ? Number(trace.hop) : 0;
        const choice = trace.choice ? String(trace.choice) : '';
        const reason = trace.reason ? String(trace.reason) : '';
        const skipped = trace.skipped === true;
        const candidates = Array.isArray(trace.candidates) ? trace.candidates : [];
        let cwTag = '';
        let lawTag = '';
        if (choice && candidates.length) {
            const match = candidates.find((item) => item && item.coord === choice);
            if (match && Number.isFinite(Number(match.eq6_cw))) {
                cwTag = `cw=${Number(match.eq6_cw)}`;
            }
            if (match && Number.isFinite(Number(match.eq6_lawfulness_level))) {
                lawTag = `L${Number(match.eq6_lawfulness_level)}`;
            }
        }
        const tags = [cwTag, lawTag].filter(Boolean).join(', ');
        if (choice) {
            const suffix = tags ? ` · ${tags}` : '';
            const summary = reason ? `Hop ${hop + 1} → ${choice} (${reason})${suffix}` : `Hop ${hop + 1} → ${choice}${suffix}`;
            thinking.push(summary);
            overlay.push(summary);
        } else if (skipped) {
            thinking.push(`Hop ${hop + 1} skipped (${reason || 'skipped'})`);
            overlay.push(`Hop ${hop + 1} → no choice (${reason || 'skipped'})`);
        }
        return { thinking, overlay };
    }

    if (payload.type === 'walk_metric_delta') {
        const metric = payload.payload || payload;
        const hop = Number.isFinite(Number(metric.hop)) ? Number(metric.hop) + 1 : null;
        const coord = metric.coord ? String(metric.coord) : '';
        const law = Number.isFinite(Number(metric.law)) ? `L${Number(metric.law)}` : '';
        const drift = Number.isFinite(Number(metric.drift)) ? `drift=${Number(metric.drift).toFixed(2)}` : '';
        const parts = [hop !== null ? `Walk ${hop}` : 'Walk', coord, law, drift].filter(Boolean);
        if (parts.length) {
            const text = parts.join(' · ');
            thinking.push(text);
            overlay.push(text);
        }
        return { thinking, overlay };
    }

    if (payload.type === 'walk_posture_delta') {
        const metric = payload.payload || payload;
        const coord = metric.coord ? String(metric.coord) : '';
        const reason = metric.reason ? String(metric.reason) : '';
        const risk = metric.over_walk_risk === true ? 'over-walk risk' : metric.under_walk_risk === true ? 'under-walk risk' : 'posture';
        const text = ['Eq9 posture', risk, coord, reason].filter(Boolean).join(' · ');
        if (text) {
            thinking.push(text);
            overlay.push(text);
        }
        return { thinking, overlay };
    }

    return { thinking, overlay };
}

function buildMetaTickerFallback(payload) {
    if (!payload || typeof payload !== 'object') {
        return { thinking: [], overlay: [] };
    }
    const thinking = [];
    const overlay = [];

    const resolvedCoords = Array.isArray(payload.resolved_coords) ? payload.resolved_coords : [];
    const candidateTrace = Array.isArray(payload.candidate_trace) ? payload.candidate_trace : [];
    const autonomyDecision = payload.autonomy_decision && typeof payload.autonomy_decision === 'object'
        ? payload.autonomy_decision
        : {};
    const resolveSummary = payload.resolve_summary && typeof payload.resolve_summary === 'object'
        ? payload.resolve_summary
        : {};

    if (!candidateTrace.length && autonomyDecision.action) {
        const action = String(autonomyDecision.action || 'unknown');
        const reason = String(autonomyDecision.reason || '').trim();
        const text = reason ? `Autonomy: ${action} (${reason})` : `Autonomy: ${action}`;
        thinking.push(text);
        overlay.push(text);
    }

    if (!candidateTrace.length && !resolvedCoords.length) {
        const requested = Number(resolveSummary.requested || 0);
        const unresolved = Number(resolveSummary.unresolved || 0);
        const text = requested > 0 || unresolved > 0
            ? `No usable COORDs resolved (${requested - unresolved}/${requested})`
            : 'No candidate COORDs available';
        thinking.push(text);
        overlay.push(text);
    }

    return { thinking, overlay };
}

function createThinkingTickerUpdater({ assistant, inlineTickerEnabled, overlayStreamingActiveRef, maxMessages = 8 }) {
    const thinkingMessages = [];
    const thinkingSeen = new Set();
    return function pushThinking(messageText) {
        const isCoordPriorityMessage = (text) => (
            /\bCOORD\b/.test(text)
            || /\bQueued:\s+[A-Za-z0-9_-]+:/.test(text)
            || /\bResolved:\s+[A-Za-z0-9_-]+:/.test(text)
            || /\bTop candidate:\s+[A-Za-z0-9_-]+:/.test(text)
            || /\bContext:\s+[A-Za-z0-9_-]+:/.test(text)
        );
        let text = String(messageText || '').replace(/\s+/g, ' ').trim();
        if (!text) return;
        if (text.length > 180) {
            text = `${text.slice(0, 177)}...`;
        }
        if (thinkingSeen.has(text)) return;
        thinkingSeen.add(text);
        thinkingMessages.push(text);
        if (thinkingMessages.length > maxMessages) {
            let removalIndex = 0;
            const firstNonPriority = thinkingMessages.findIndex((item) => !isCoordPriorityMessage(item));
            if (firstNonPriority >= 0) {
                removalIndex = firstNonPriority;
            }
            const [removed] = thinkingMessages.splice(removalIndex, 1);
            if (removed) {
                thinkingSeen.delete(removed);
            }
        }
        if (inlineTickerEnabled && assistant?.ticker && overlayStreamingActiveRef()) {
            assistant.ticker.style.display = '';
            assistant.ticker.textContent = `Ticker: ${thinkingMessages.join(' | ')}`;
        }
        if (assistant?.thinkingTrace && assistant?.thinkingTraceLines) {
            assistant.thinkingTrace.style.display = '';
            assistant.thinkingTraceLines.innerHTML = '';
            thinkingMessages.forEach((msg) => {
                const line = document.createElement('div');
                line.className = 'thinking-trace-line';
                line.textContent = msg;
                assistant.thinkingTraceLines.appendChild(line);
            });
            const latest = thinkingMessages[thinkingMessages.length - 1] || '';
            if (assistant.thinkingTraceLabel) {
                assistant.thinkingTraceLabel.style.display = '';
            }
            if (assistant.thinkingTraceToggle) {
                const hasMultiple = thinkingMessages.length > 1;
                const isLong = latest.length > 80;
                assistant.thinkingTraceToggle.hidden = !(hasMultiple || isLong);
            }
        }
    };
}

async function emitThinkingTraceEvent(eventPayload) {
    if (!eventPayload || typeof eventPayload !== 'object') return;
    const sessionId = ensureSessionCookie();
    const payload = {
        ...eventPayload,
        session_id: eventPayload.session_id || sessionId,
    };
    try {
        await fetch('/api/thinking_trace/emit', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
    } catch (error) {
        // Non-blocking observability path.
    }
}

function startThinkingTraceOverlayWatch({ requestId, flow = 'generic', onTrace = null } = {}) {
    const sessionId = ensureSessionCookie();
    const controller = new AbortController();
    const seen = new Set();
    let stopped = false;

    const run = (async () => {
        try {
            const params = new URLSearchParams({
                session_id: sessionId,
                replay: '1',
            });
            const response = await fetch(`/api/thinking_trace/stream?${params.toString()}`, {
                headers: { Accept: 'application/x-ndjson' },
                signal: controller.signal,
            });
            await readStream(response, (event) => {
                if (!event || typeof event !== 'object') return;
                if (event.type !== 'thinking_trace' || !event.payload || typeof event.payload !== 'object') return;
                const payload = event.payload;
                const traceRequestId = payload.request_id ? String(payload.request_id) : '';
                if (requestId && traceRequestId && traceRequestId !== requestId) return;
                const seq = Number(payload.trace_seq);
                const dedupeKey = traceRequestId
                    ? `${traceRequestId}:${Number.isFinite(seq) ? seq : 'na'}`
                    : JSON.stringify(payload);
                if (seen.has(dedupeKey)) return;
                seen.add(dedupeKey);

                const overlayLine = thinkingTraceOverlayLine(payload);
                if (overlayLine) {
                    updateLoadingStatus(overlayLine, true);
                    enqueueOverlayStatus(overlayLine);
                }
                if (typeof onTrace === 'function') {
                    const statusText = traceStatusFromPayload(payload);
                    if (statusText) onTrace(statusText);
                    const traceCoords = extractTraceCoords(payload);
                    traceCoords.forEach((coord) => onTrace(`COORD: ${coord}`));
                    if (overlayLine) {
                        const clean = overlayLine.replace(/^Thinking:\s*/, '').trim();
                        if (clean) onTrace(clean);
                    }
                }

                const eventType = payload.type ? String(payload.type) : '';
                if (flow !== 'turn' && (eventType === 'process_completed' || eventType === 'process_failed')) {
                    if (!overlayStreamingActive) {
                        setLoadingOverlay(false);
                    }
                }
            });
        } catch (error) {
            if (error && error.name === 'AbortError') return;
            const fallbackText = flow === 'turn'
                ? 'Thinking trace stream unavailable; using main stream only'
                : 'Thinking trace stream unavailable';
            updateLoadingStatus(`Thinking: ${fallbackText}`, true);
            enqueueOverlayStatus(fallbackText);
            console.warn('Thinking trace watcher failed', error);
        }
    })();

    return {
        stop: () => {
            if (stopped) return;
            stopped = true;
            controller.abort();
        },
        done: run,
    };
}

async function readStream(response, onEvent) {
    if (!response.ok || !response.body) {
        const raw = await response.text();
        let detail = '';
        let loginUrl = '';
        try {
            const parsed = raw ? JSON.parse(raw) : null;
            if (parsed && typeof parsed === 'object') {
                const d = parsed.detail;
                if (typeof d === 'string' && d.trim()) {
                    detail = d.trim();
                } else if (d && typeof d === 'object') {
                    detail = JSON.stringify(d);
                }
                const upstreamUrl = typeof parsed.upstream_url === 'string' ? parsed.upstream_url.trim() : '';
                if (upstreamUrl) {
                    detail = detail ? `${detail} | upstream=${upstreamUrl}` : `upstream=${upstreamUrl}`;
                }
                loginUrl = typeof parsed.login_url === 'string' ? parsed.login_url.trim() : '';
            }
        } catch (_err) {
            // Response was not JSON; we'll fall back to raw text.
        }
        if (response.status === 401) {
            redirectToControlPlaneLogin(loginUrl);
            return;
        }
        const fallback = String(raw || '').trim();
        const message = detail || fallback || `Stream failed (${response.status})`;
        throw new Error(`Stream failed (${response.status}): ${message}`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
            const trimmed = line.trim();
            if (!trimmed) continue;
            let payload;
            try {
                payload = parseCoordinateJson(trimmed);
            } catch (err) {
                continue;
            }
            if (onEvent) {
                onEvent(payload);
            }
        }
    }

    const trailing = buffer.trim();
    if (trailing) {
        try {
            const payload = parseCoordinateJson(trailing);
            if (onEvent) {
                onEvent(payload);
            }
        } catch (err) {
            // Ignore incomplete trailing data
        }
    }
}

function describeAttachmentContextFailure(attachmentContext) {
    if (!attachmentContext || typeof attachmentContext !== 'object') return '';
    if (attachmentContext.skipped !== true) return '';
    const skipReason = typeof attachmentContext.skip_reason === 'string'
        ? attachmentContext.skip_reason.trim()
        : '';
    if (skipReason === 'attachment_context_not_queued') {
        return 'Attachment context was requested but not queued for this turn.';
    }
    if (skipReason === 'attachment_parts_unavailable') {
        return 'Attachment context was requested but attachment parts were unavailable.';
    }
    if (skipReason === 'attachment_context_not_resolved') {
        return 'Attachment context was requested but not resolved for this turn.';
    }
    return 'Attachment context was requested but could not be used for this turn.';
}

function deriveExplicitStreamFailure({ finalMetaPayload, latestAttachmentContext, sawMeta }) {
    const blocked = finalMetaPayload?.blocked === true
        || (finalMetaPayload?.audit_mode && finalMetaPayload.audit_mode.blocked === true);
    if (blocked) {
        const reason = finalMetaPayload?.audit_mode?.reason || 'blocked';
        return `Response blocked: ${reason}`;
    }
    const attachmentFailure = describeAttachmentContextFailure(latestAttachmentContext);
    if (attachmentFailure) {
        return attachmentFailure;
    }
    if (sawMeta) {
        return 'No answer was returned for this prompt.';
    }
    return 'The response stream ended before an answer was returned.';
}

function stripTrailingJsonMetadata(text) {
    if (!text) return text;
    let trimmed = text.trimEnd();
    trimmed = trimmed.replace(/\n?\s*\(COORD\s*\{[\s\S]*\}\)\s*$/i, '').trimEnd();
    trimmed = trimmed.replace(/```json\s*$/i, '').trimEnd();
    trimmed = trimmed.replace(/```\s*$/i, '').trimEnd();
    for (let i = trimmed.length - 1; i >= 0; i -= 1) {
        if (trimmed[i] !== '{') continue;
        let tail = trimmed.slice(i).trimEnd();
        tail = tail.replace(/```[a-zA-Z0-9]*\s*$/, '').trimEnd();
        try {
            const parsed = JSON.parse(tail);
            if (parsed && typeof parsed === 'object') {
                return trimmed.slice(0, i).trimEnd();
            }
        } catch (error) {
            // Continue scanning
        }
    }
    return text;
}

function scrollViewportToTopIfNeeded() {
    if (window.scrollY <= 8) return;
    window.scrollTo({ top: 0, behavior: 'smooth' });
}

function getCachedAttachmentCoordinates() {
    return Array.from(
        document.querySelectorAll('#attachment-coordinate-list .attachment-coordinate[data-coordinate]'),
    )
        .map((node) => node.dataset.coordinate?.trim())
        .filter((coord) => coord);
}

function parseTimeRange(message) {
    if (!message) return null;
    const text = String(message).toLowerCase();
    const now = new Date();

    const unitToMs = (unit) => {
        switch (unit) {
            case 'minute': return 60 * 1000;
            case 'hour': return 60 * 60 * 1000;
            case 'day': return 24 * 60 * 60 * 1000;
            case 'week': return 7 * 24 * 60 * 60 * 1000;
            case 'month': return 30 * 24 * 60 * 60 * 1000;
            default: return 0;
        }
    };

    const range = { source: 'parsed' };

    if (text.includes('yesterday')) {
        const startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
        const startYesterday = new Date(startToday.getTime() - 24 * 60 * 60 * 1000);
        range.since = startYesterday.toISOString();
        range.until = startToday.toISOString();
        return range;
    }

    if (text.includes('today')) {
        const startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
        range.since = startToday.toISOString();
        return range;
    }

    const lastMatch = text.match(/\b(?:last|past)\s+(\d+)?\s*(minute|hour|day|week|month)s?\b/);
    if (lastMatch) {
        const count = Number(lastMatch[1] || 1);
        const unit = lastMatch[2];
        const ms = unitToMs(unit) * Math.max(count, 1);
        if (ms > 0) {
            range.since = new Date(now.getTime() - ms).toISOString();
            return range;
        }
    }

    const agoMatch = text.match(/\b(\d+)\s*(minute|hour|day|week|month)s?\s+ago\b/);
    if (agoMatch) {
        const count = Number(agoMatch[1]);
        const unit = agoMatch[2];
        const ms = unitToMs(unit);
        if (ms > 0) {
            const end = new Date(now.getTime() - count * ms);
            const start = new Date(now.getTime() - (count + 1) * ms);
            range.since = start.toISOString();
            range.until = end.toISOString();
            return range;
        }
    }

    if (text.includes('last hour or so')) {
        range.since = new Date(now.getTime() - unitToMs('hour')).toISOString();
        return range;
    }

    return null;
}

async function handleStreamedChatSubmit(event) {
    event.preventDefault();
    if (demoConnectivityState.offline && !isOllamaModelSelected()) {
        enforceOfflineModelSelection(undefined, true);
        if (!isOllamaModelSelected()) {
            showToast('Offline demo mode requires an Ollama model', 'error');
            return;
        }
    }
    const input = document.getElementById('cmd-input');
    if (!input) return;
    const message = (input.value || '').trim();
    if (!message) return;
    const attachmentCoordinates = getCachedAttachmentCoordinates();
    const uniqueAttachmentCoordinates = Array.from(new Set(attachmentCoordinates));
    const timeRange = parseTimeRange(message);
    clearAttachmentCoordinates();
    const currentHistory = getClientSideHistory();

    const msgId = Date.now();
    const turn = document.createElement('div');
    turn.className = 'chat-turn';
    turn.dataset.streamedTurn = 'true';
    const userNode = _createUserBubble(message, msgId);
    const assistant = _createAssistantBubble(msgId + 1);
    const pushThinking = createThinkingTickerUpdater({
        assistant,
        inlineTickerEnabled: INLINE_TICKER_ENABLED,
        overlayStreamingActiveRef: () => overlayStreamingActive,
        maxMessages: 8,
    });
    turn.appendChild(userNode);
    turn.appendChild(assistant.container);
    _prependChatNode(turn);

    input.value = '';
    adjustInputHeight(input);
    overlayManualStatus = false;
    overlayStreamingActive = false;
    if (assistant.spinner) {
        assistant.spinner.style.display = '';
    }
    resetOverlayStatusQueue();
    scrollViewportToTopIfNeeded();
    overlayStreamingActive = true;
    const preStreamMessages = uniqueAttachmentCoordinates.length
        ? [
            'Resolving attachments...',
            'Scanning attachment parts...',
            'Preparing context...',
        ]
        : [
            'Resolving context...',
            'Gathering references...',
            'Preparing response...',
        ];
    startPreStreamTicker(preStreamMessages);
    const chatStream = document.getElementById('chat-stream');
    if (chatStream?.dataset.historyLoaded === 'true') {
        disableHtmxChatStream();
    }
    maybeRefreshChatHistory();

    let fullReply = '';
    let latencyMs = null;
    let streamRenderTimer = null;
    let streamRenderFrame = null;
    let lastRenderedReply = '';
    let hasPendingTokens = false;
    let firstTokenArrived = false;
    let overlayReleased = false;
    let finalMetaPayload = null;
    let firstTokenRendered = false;
    let visibleAnswerRenderComplete = false;
    const streamedCoordsSeen = new Set();
    const timingStart = performance.now();
    const STREAM_RENDER_INTERVAL = 250;
    const thinkingTraceSeen = new Set();


    const _handleThinkingTracePayload = (tracePayload, requestId) => {
        if (!tracePayload || typeof tracePayload !== 'object') return;
        const traceRequestId = tracePayload.request_id ? String(tracePayload.request_id) : '';
        if (requestId && traceRequestId && traceRequestId !== requestId) return;
        const seq = Number(tracePayload.trace_seq);
        const dedupeKey = traceRequestId ? `${traceRequestId}:${Number.isFinite(seq) ? seq : 'na'}` : '';
        if (dedupeKey) {
            if (thinkingTraceSeen.has(dedupeKey)) return;
            thinkingTraceSeen.add(dedupeKey);
        }

        const statusText = traceStatusFromPayload(tracePayload);
        if (statusText) {
            pushThinking(statusText);
        }
        const traceCoords = extractTraceCoords(tracePayload);
        traceCoords.forEach((coord) => {
            pushThinking(`COORD: ${coord}`);
        });
        const overlayLine = thinkingTraceOverlayLine(tracePayload);
        if (overlayLine) {
            markCoordDiagnosticsSeen();
            updateLoadingStatus(overlayLine, true);
            enqueueOverlayStatus(overlayLine);
        }

        const eventType = tracePayload.type ? String(tracePayload.type) : '';
        if (eventType === 'process_completed' || eventType === 'process_failed') {
            markCoordDiagnosticsSeen();
            if (!firstTokenArrived) {
                releaseOverlayOnce();
            }
        }
    };

    const releaseOverlayOnce = () => {
        if (overlayReleased) return;
        overlayReleased = true;
        setLoadingOverlay(false);
    };

    const markFirstTokenRendered = () => {
        if (firstTokenRendered) return;
        firstTokenRendered = true;
        logTiming('first_token_rendered', timingStart, { chars: fullReply.length });
        if (assistant.spinner) {
            assistant.spinner.style.display = 'none';
        }
        releaseOverlayOnce();
    };

    const markVisibleAnswerRenderComplete = () => {
        if (visibleAnswerRenderComplete) return;
        visibleAnswerRenderComplete = true;
        logTiming('visible_answer_render_complete', timingStart, { chars: fullReply.length });
    };

    const markCoordDiagnosticsSeen = () => {
        return;
    };

    const enqueueCoordsFromPayload = (payload) => {
        let serialized = '';
        try {
            serialized = JSON.stringify(payload);
        } catch (_err) {
            return;
        }
        if (!serialized) return;
        const coordRegex = /[A-Za-z0-9_-]+:(?:WX|ATT|EV-WALK)-[A-Za-z0-9]+(?:-[0-9]+)?|(?:WX|ATT|EV-WALK)-[A-Za-z0-9]+(?:-[0-9]+)?/g;
        const matches = serialized.match(coordRegex);
        if (!Array.isArray(matches) || !matches.length) return;
        for (const raw of matches) {
            const coord = String(raw || '').trim();
            if (!coord || streamedCoordsSeen.has(coord)) continue;
            streamedCoordsSeen.add(coord);
            enqueueOverlayStatus(`COORD: ${coord}`);
        }
    };

    const renderStreamingMarkdown = () => {
        if (fullReply === lastRenderedReply) {
            return;
        }
        if (fullReply.trim()) {
            markFirstTokenRendered();
        }
        assistant.content.textContent = fullReply;
        delete assistant.content.dataset.rendered;
        renderAssistantMarkdown(assistant.container, { typesetMath: false });
        applyStreamingTail(assistant.content, 0.15);
        assistant.content.classList.add('streaming-active');
        assistant.content.classList.remove('streaming-complete');
        lastRenderedReply = fullReply;
    };

    const scheduleStreamRender = () => {
        if (streamRenderTimer !== null) return;
        streamRenderTimer = window.setTimeout(() => {
            streamRenderTimer = null;
            if (!hasPendingTokens) return;
            if (streamRenderFrame !== null) {
                cancelAnimationFrame(streamRenderFrame);
            }
            streamRenderFrame = requestAnimationFrame(() => {
                streamRenderFrame = null;
                if (!hasPendingTokens) return;
                hasPendingTokens = false;
                renderStreamingMarkdown();
            });
        }, STREAM_RENDER_INTERVAL);
    };

    const finalizeStreamRender = () => {
        if (streamRenderTimer !== null) {
            clearTimeout(streamRenderTimer);
            streamRenderTimer = null;
        }
        if (streamRenderFrame !== null) {
            cancelAnimationFrame(streamRenderFrame);
            streamRenderFrame = null;
        }
        hasPendingTokens = false;
        assistant.content.textContent = fullReply;
        delete assistant.content.dataset.rendered;
        renderAssistantMarkdown(assistant.container);
        applyStreamingTail(assistant.content, 0.15);
        assistant.content.classList.remove('streaming-active');
        assistant.content.classList.add('streaming-complete');
        lastRenderedReply = fullReply;
        if (fullReply.trim()) {
            markFirstTokenRendered();
            markVisibleAnswerRenderComplete();
        }
        window.setTimeout(() => {
            unwrapStreamingTail(assistant.content);
            assistant.content.classList.remove('streaming-complete');
        }, 500);
        if (assistant.ticker) {
            assistant.ticker.textContent = '';
            assistant.ticker.style.display = 'none';
        }
        if (thinkingTextHideTimer) {
            clearTimeout(thinkingTextHideTimer);
            thinkingTextHideTimer = null;
        }
        if (assistant.thinkingIndicator) {
            assistant.thinkingIndicator.style.display = 'none';
            assistant.thinkingIndicator.innerHTML = '';
        }
        if (assistant.thinkingTrace) {
            assistant.thinkingTrace.style.display = 'none';
            if (assistant.thinkingTraceLabel) {
                assistant.thinkingTraceLabel.style.display = 'none';
            }
            if (assistant.thinkingTraceLines) {
                assistant.thinkingTraceLines.innerHTML = '';
            }
            if (assistant.thinkingTraceToggle) {
                assistant.thinkingTraceToggle.hidden = true;
                assistant.thinkingTraceToggle.textContent = '▾';
                assistant.thinkingTraceToggle.setAttribute('aria-expanded', 'false');
            }
            assistant.bubble.classList.remove('is-thinking-expanded');
        }
    };

    let turnTraceWatch = null;
    let turnTraceRequestId = '';
    let latestAttachmentContext = null;
    let sawFinalMeta = false;
    let thinkingTextHideTimer = null;
    try {
        const sessionId = ensureSessionCookie();
        const provider = document.getElementById('agent-select')?.value || '';
        const requestPayload = buildStreamRequestPayload({
            message,
            provider,
            sessionId,
            currentHistory,
            uniqueAttachmentCoordinates,
            timeRange,
        });
        buildUpstreamLoadingMessages(requestPayload).forEach((messageText) => {
            pushThinking(messageText);
            enqueueOverlayStatus(messageText);
        });
        turnTraceRequestId = String(requestPayload.request_id || '').trim();
        if (turnTraceRequestId) {
            turnTraceWatch = startThinkingTraceOverlayWatch({ requestId: turnTraceRequestId, flow: 'turn', onTrace: pushThinking });
            await emitThinkingTraceEvent({
                request_id: turnTraceRequestId,
                type: 'process_started',
                status: 'in_progress',
                step_code: 'REQ_ACCEPTED',
                step_label: 'Request accepted',
                details: { flow: 'turn' },
            });
        }
        if (typeof window?.dsEligibleForSearch === 'boolean') {
            requestPayload.eligible_for_search = window.dsEligibleForSearch;
        }
        if (typeof window?.dsSearchUsed === 'boolean') {
            requestPayload.search_used = window.dsSearchUsed;
        }
        const response = await fetch('/api/chat/smart_stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(requestPayload),
        });
        buildUpstreamLoadingMessages(requestPayload, response).slice(-2).forEach((messageText) => {
            pushThinking(messageText);
            enqueueOverlayStatus(messageText);
        });
        updateUpstreamDebugPanel(
            response.headers.get('x-ds-upstream-url'),
            response.headers.get('x-ds-upstream-fallback'),
        );
        let hasFirstToken = false;
        thinkingTextHideTimer = null;

        await readStream(response, (payload) => {
            stopPreStreamTicker();
            enqueueCoordsFromPayload(payload);
            if (payload.type === 'thinking_trace' && payload.payload) {
                _handleThinkingTracePayload(payload.payload, requestPayload.request_id);
                return;
            }
            if (payload.type === 'thinking_trace_heartbeat') {
                return;
            }
            if (payload.type === 'status') {
                const statusMessage = payload.message ? String(payload.message) : '';
                if (statusMessage) {
                    pushThinking(statusMessage);
                }
                const statusTickerMessages = collectPipelineTickerMessages(payload);
                if (statusTickerMessages.thinking.length || statusTickerMessages.overlay.length) {
                    markCoordDiagnosticsSeen();
                    statusTickerMessages.thinking.forEach(pushThinking);
                    statusTickerMessages.overlay.forEach(enqueueOverlayStatus);
                    return;
                }
                if (
                    payload.step === 3
                    || payload.stage === 'guardian'
                    || /guardian/i.test(statusMessage)
                ) {
                    enqueueOverlayStatus('Stabilizing (Guardian Check)...');
                    return;
                }
                if (statusMessage) {
                    enqueueOverlayStatus(statusMessage);
                }
                return;
            }

            if (payload.type === 'ui_status') {
                markCoordDiagnosticsSeen();
                const uiPayload = payload.payload || {};
                logPipelineEvent('ui_status', uiPayload);
                const tickerMessages = collectPipelineTickerMessages(payload);
                tickerMessages.thinking.forEach(pushThinking);
                tickerMessages.overlay.forEach(enqueueOverlayStatus);
                return;
            }

            if (payload.type === 'context_meta') {
                markCoordDiagnosticsSeen();
                if (payload.attachment_context && typeof payload.attachment_context === 'object') {
                    latestAttachmentContext = payload.attachment_context;
                    const attachmentFailure = describeAttachmentContextFailure(payload.attachment_context);
                    if (attachmentFailure) {
                        pushThinking(attachmentFailure);
                        enqueueOverlayStatus(attachmentFailure);
                    }
                }
                logPipelineEvent('context_meta', {
                    queued: Array.isArray(payload.queued_coords) ? payload.queued_coords.length : 0,
                    resolved: Array.isArray(payload.resolved_coords) ? payload.resolved_coords.length : 0,
                    router_decision: payload.router_decision || null,
                    anchor_resolution: payload.anchor_resolution || null,
                    resolve_summary: payload.resolve_summary || null,
                    candidate_trace: payload.candidate_trace || null,
                    autonomy_decision: payload.autonomy_decision || null,
                });
                const contextResolve = applyResolveSummary(payload.resolve_summary);
                if (contextResolve) {
                    if (contextResolve.signature !== lastResolveSummarySignature) {
                        lastResolveSummarySignature = contextResolve.signature;
                        const capabilityText = contextResolve.supports === true
                            ? 'available'
                            : contextResolve.supports === false
                                ? 'unavailable'
                                : 'unknown';
                        pushThinking(`Resolver capability: ${capabilityText}`);
                        const summaryText = `Resolve summary: ${Number.isFinite(contextResolve.resolvedCount) ? contextResolve.resolvedCount : 0}/${Number.isFinite(contextResolve.requestedCount) ? contextResolve.requestedCount : 0}`;
                        pushThinking(summaryText);
                        enqueueOverlayStatus(summaryText);
                        if (Number.isFinite(contextResolve.unresolvedCount) && contextResolve.unresolvedCount > 0) {
                            const unresolvedText = `Unresolved COORDs: ${contextResolve.unresolvedCount}`;
                            pushThinking(unresolvedText);
                            enqueueOverlayStatus(unresolvedText);
                        }
                    }
                }
                if (Array.isArray(payload.queued_coords)) {
                    // no-op: handled via collectPipelineTickerMessages below
                }
                const tickerMessages = collectPipelineTickerMessages(payload);
                tickerMessages.thinking.forEach(pushThinking);
                tickerMessages.overlay.forEach(enqueueOverlayStatus);
                updateResolveDebugPanel(payload);
                return;
            }

            if (payload.type === 'error') {
                const message = typeof payload.message === 'string' && payload.message.trim()
                    ? payload.message.trim()
                    : typeof payload.detail === 'string' && payload.detail.trim()
                        ? payload.detail.trim()
                        : 'Streaming chat failed';
                throw new Error(message);
            }

            if (payload.type === 'context_item') {
                markCoordDiagnosticsSeen();
                const tickerMessages = collectPipelineTickerMessages(payload);
                tickerMessages.thinking.forEach(pushThinking);
                tickerMessages.overlay.forEach(enqueueOverlayStatus);
                return;
            }

            if (payload.type === 'hop_enrich') {
                markCoordDiagnosticsSeen();
                const tickerMessages = collectPipelineTickerMessages(payload);
                tickerMessages.thinking.forEach(pushThinking);
                tickerMessages.overlay.forEach(enqueueOverlayStatus);
                return;
            }

            if (payload.type === 'candidate_trace') {
                markCoordDiagnosticsSeen();
                const tracePayload = payload.payload || {};
                const topK = Array.isArray(tracePayload.top_k) ? tracePayload.top_k : [];
                logPipelineEvent('candidate_trace', { top_k: topK.slice(0, 10) });
                const tickerMessages = collectPipelineTickerMessages(payload);
                tickerMessages.thinking.forEach(pushThinking);
                tickerMessages.overlay.forEach(enqueueOverlayStatus);
                return;
            }

            if (payload.type === 'autonomy_decision') {
                markCoordDiagnosticsSeen();
                const decision = payload.payload || {};
                logPipelineEvent('autonomy_decision', decision);
                const tickerMessages = collectPipelineTickerMessages(payload);
                tickerMessages.thinking.forEach(pushThinking);
                tickerMessages.overlay.forEach(enqueueOverlayStatus);
                updateResolveDebugPanel({ autonomy_decision: decision, candidate_trace: decision.top_k || [] });
                return;
            }

            if (payload.type === 'coord_action_plan') {
                markCoordDiagnosticsSeen();
                const actionPlan = payload.payload || {};
                logPipelineEvent('coord_action_plan', actionPlan);
                const tickerMessages = collectPipelineTickerMessages(payload);
                tickerMessages.thinking.forEach(pushThinking);
                tickerMessages.overlay.forEach(enqueueOverlayStatus);
                return;
            }

            if (payload.type === 'decision_trace') {
                markCoordDiagnosticsSeen();
                const trace = payload.payload || {};
                const tickerMessages = collectPipelineTickerMessages(payload);
                tickerMessages.thinking.forEach(pushThinking);
                tickerMessages.overlay.forEach(enqueueOverlayStatus);
                return;
            }

            if (payload.type === 'guardian_note') {
                if (payload.message) {
                    enqueueOverlayStatus(`Guardian: ${payload.message}`);
                }
                return;
            }

            if (payload.type === 'anchor_resolution') {
                logPipelineEvent('anchor_resolution', payload.payload || payload);
                return;
            }

            if (payload.type === 'walk_metric_delta') {
                const metric = payload.payload || payload;
                logPipelineEvent('walk_metric_delta', metric);
                const tickerMessages = collectPipelineTickerMessages(payload);
                tickerMessages.thinking.forEach(pushThinking);
                tickerMessages.overlay.forEach(enqueueOverlayStatus);
                return;
            }

            if (payload.type === 'walk_posture_delta') {
                const metric = payload.payload || payload;
                logPipelineEvent('walk_posture_delta', metric);
                const tickerMessages = collectPipelineTickerMessages(payload);
                tickerMessages.thinking.forEach(pushThinking);
                tickerMessages.overlay.forEach(enqueueOverlayStatus);
                return;
            }

            if (payload.type === 'walk_stop') {
                logPipelineEvent('walk_stop', payload);
                return;
            }

            if (payload.type === 'grounding_override') {
                logPipelineEvent('grounding_override', payload);
                return;
            }

            if (payload.type === 'meta_patch') {
                logPipelineEvent('meta_patch', payload);
                // Surface bounded refusal diagnostics from DS-REVIEW-196 patch status.
                if (payload.checksum_336_pass === false) {
                    const refusal = 'Governance refusal: 336 checksum gate failed.';
                    pushThinking(refusal);
                    enqueueOverlayStatus(refusal);
                } else if (payload.status === 'skipped' && payload.reason) {
                    const diagnostic = 'Governance patch skipped: ' + String(payload.reason);
                    pushThinking(diagnostic);
                    enqueueOverlayStatus(diagnostic);
                }
                return;
            }

            if (payload.type === 'governance_summary') {
                logPipelineEvent('governance_summary', payload);
                if (payload.checksum_336_pass === false) {
                    const refusal = 'Governance refusal: 336 checksum gate failed (summary).';
                    pushThinking(refusal);
                    enqueueOverlayStatus(refusal);
                }
                return;
            }

            if (payload.type === 'thinking_text' && payload.content) {
                const thinkingText = String(payload.content || '').trim();
                if (thinkingText) {
                    assistant.thinkingIndicator.innerHTML = '<span class="thinking-prose">' + thinkingText + '</span>';
                    assistant.thinkingIndicator.style.display = 'block';
                }
                return;
            }

            if (payload.type === 'token' && payload.content) {
                const isFirstToken = !hasFirstToken;
                if (isFirstToken) {
                    firstTokenArrived = true;
                    logTiming('first_token_received', timingStart, { chars: String(payload.content || '').length });
                    hasFirstToken = true;
                    if (thinkingTextHideTimer) {
                        clearTimeout(thinkingTextHideTimer);
                    }
                    thinkingTextHideTimer = setTimeout(() => {
                        if (assistant.thinkingIndicator) {
                            assistant.thinkingIndicator.style.display = 'none';
                            assistant.thinkingIndicator.innerHTML = '';
                        }
                    }, 300);
                }
                fullReply += payload.content;
                hasPendingTokens = true;
                if (isFirstToken) {
                    requestAnimationFrame(() => {
                        if (!hasPendingTokens) return;
                        hasPendingTokens = false;
                        renderStreamingMarkdown();
                    });
                } else {
                    scheduleStreamRender();
                }
                return;
            }

            if (payload.type === 'meta') {
                // Some deployments may deliver most diagnostics on final meta.
                finalMetaPayload = payload && typeof payload === 'object' ? payload : null;
                sawFinalMeta = true;
                if (payload.attachment_context && typeof payload.attachment_context === 'object') {
                    latestAttachmentContext = payload.attachment_context;
                    const attachmentFailure = describeAttachmentContextFailure(payload.attachment_context);
                    if (attachmentFailure) {
                        pushThinking(attachmentFailure);
                        enqueueOverlayStatus(attachmentFailure);
                    }
                }
                markCoordDiagnosticsSeen();
                logTiming('meta_received', timingStart, {
                    coordinate: payload.coordinate || payload.web4_key,
                });
                const latencyPolicy = payload?.latency_diagnostics?.policy;
                const policyControls = payload?.policy_controls;
                if (latencyPolicy && typeof latencyPolicy === 'object') {
                    if (latencyPolicy.applied === true) {
                        logPipelineEvent('pressure_policy_applied', {
                            threshold_ms: latencyPolicy.threshold_ms,
                            s_mode: payload?.governance_path?.s_mode,
                            guardian_fast_path: payload?.governance_path?.guardian_fast_path,
                            walk_termination_reason: payload?.walk_termination_reason,
                            rolling_ms: payload?.latency_diagnostics?.rolling_ms,
                            transition_reason: latencyPolicy.transition_reason,
                            s_mode_before: latencyPolicy.s_mode_before,
                            s_mode_after: latencyPolicy.s_mode_after,
                        });
                    } else {
                        logPipelineEvent('pressure_policy_not_applied', {
                            threshold_ms: latencyPolicy.threshold_ms,
                            rolling_ms: payload?.latency_diagnostics?.rolling_ms,
                        });
                    }
                }
                if (policyControls && typeof policyControls === 'object') {
                    logPipelineEvent('policy_controls', {
                        effective_enable_ledger: policyControls.effective_enable_ledger,
                        effective_s_mode: policyControls.effective_s_mode,
                        break_glass_profile_active: policyControls.break_glass_profile_active,
                        runtime_profile_markers: policyControls.runtime_profile_markers,
                    });
                    if (policyControls.break_glass_profile_active === true) {
                        enqueueOverlayStatus('Break-glass runtime profile active');
                    }
                }
                if (Array.isArray(payload.resolved_coords) && payload.resolved_coords.length) {
                    payload.resolved_coords.slice(0, 6).forEach((coord) => {
                        pushThinking(`Resolved: ${coord}`);
                        enqueueOverlayStatus(`Resolved: ${coord}`);
                    });
                }
                const metaTickerMessages = buildMetaTickerFallback(payload);
                metaTickerMessages.thinking.forEach(pushThinking);
                metaTickerMessages.overlay.forEach(enqueueOverlayStatus);
                applyResolveSummary(payload.resolve_summary || payload?.metadata?.resolve_summary);
                updateResolveDebugPanel(payload);
                const coordinate = payload.coordinate || payload.web4_key || '—';
                assistant.coord.textContent = coordinate;
                assistant.coord.dataset.coordinate = coordinate;
                assistant.metaText.textContent = `${formatMetaTimestamp(new Date())} | `;
                const blocked = payload?.blocked === true
                    || (payload.audit_mode && payload.audit_mode.blocked === true);
                if (blocked) {
                    const reason = payload?.audit_mode?.reason || 'blocked';
                    pushThinking(`Audit Mode: ${reason}`);
                    enqueueOverlayStatus(`Audit Mode: ${reason}`);
                    assistant.metaText.textContent = `${formatMetaTimestamp(new Date())} | Audit Mode: ${reason} | `;
                    assistant.content.classList.add('audit-blocked');
                } else {
                    assistant.content.classList.remove('audit-blocked');
                }
                const modelLabel = payload.model || provider || '';
                if (assistant.modelText) {
                    assistant.modelText.textContent = modelLabel ? ` | ${modelLabel}` : '';
                }
                if (assistant.walkText) {
                    const walkIds = payload?.metadata?.walk_ids;
                    if (Array.isArray(walkIds) && walkIds.length > 0) {
                        const walkCoord = String(walkIds[0]);
                        assistant.walkText.textContent = '';
                        assistant.walkText.appendChild(document.createTextNode(' | '));
                        const walkSpan = document.createElement('span');
                        walkSpan.className = 'coordinate';
                        walkSpan.dataset.coordinate = walkCoord;
                        walkSpan.style.textDecoration = 'underline';
                        walkSpan.style.cursor = 'pointer';
                        walkSpan.title = 'Copy coordinate';
                        walkSpan.addEventListener('click', () => {
                            if (walkSpan.dataset.coordinate) {
                                navigator.clipboard.writeText(walkSpan.dataset.coordinate);
                            }
                        });
                        walkSpan.textContent = walkCoord;
                        assistant.walkText.appendChild(walkSpan);
                    } else {
                        assistant.walkText.textContent = '';
                    }
                }
                if (assistant.promptText) {
                    const promptLabel = _promptPrincipalLabel(payload);
                    assistant.promptText.textContent = promptLabel ? ` | asked by: ${promptLabel}` : '';
                }
                if (assistant.requestedText) {
                    const requestedBy = _requestedByLabel(payload);
                    assistant.requestedText.textContent = requestedBy ? ` | requested by: ${requestedBy}` : '';
                }
                if (assistant.integrityText) {
                    const integrityText = _answerSurfaceIntegrityText(payload);
                    assistant.integrityText.textContent = integrityText ? ` | ${integrityText}` : '';
                }
                if (payload.appraisal && typeof payload.appraisal === 'object') {
                    window.dsAgentFeedback = payload.appraisal;
                    updateAppraisalPanel(payload.appraisal);
                    logTiming('appraisal_received', timingStart, payload.appraisal);
                }
                logResolveMeta(payload);
                if (Number.isFinite(Number(payload.latency_ms))) {
                    latencyMs = Number(payload.latency_ms);
                }
                updateLoadingStatus('Complete.');
            }
        });

        const cleanedReply = stripTrailingJsonMetadata(fullReply);
        if (cleanedReply !== fullReply) {
            fullReply = cleanedReply;
        }
        if (fullReply.trim()) {
            finalizeStreamRender();
            const metaCoordinate = finalMetaPayload?.coordinate;
            const shouldPersist =
                !finalMetaPayload ||
                !metaCoordinate ||
                finalMetaPayload.fallback_coordinate === true ||
                Boolean(finalMetaPayload.persistence_error) ||
                finalMetaPayload.commit_status === 'error';
            console.log('[chat] persist decision', {
                shouldPersist,
                coordinate: metaCoordinate,
                fallback_coordinate: finalMetaPayload?.fallback_coordinate,
                persistence_error: finalMetaPayload?.persistence_error,
                commit_status: finalMetaPayload?.commit_status,
                commit_error: finalMetaPayload?.commit_error,
            });
            if (shouldPersist) {
                try {
                    await persistStreamedTurn({
                        message,
                        reply: fullReply,
                        latencyMs,
                        metadata: finalMetaPayload || {},
                        precomputedAppraisal: finalMetaPayload?.appraisal,
                    });
                } catch (persistError) {
                    console.error('Unable to persist streamed turn', persistError);
                    enqueueOverlayStatus('Warning: response was not persisted.');
                }
            }
            await finalizeStreamedHistory();
        } else {
            throw new Error(deriveExplicitStreamFailure({
                finalMetaPayload,
                latestAttachmentContext,
                sawMeta: sawFinalMeta,
            }));
        }
        if (turnTraceRequestId) {
            await emitThinkingTraceEvent({
                request_id: turnTraceRequestId,
                type: 'process_completed',
                status: 'completed',
                step_code: 'FINALIZE',
                step_label: 'Response finalized',
                details: { flow: 'turn' },
            });
        }
    } catch (error) {
        console.error('Streaming chat failed', error);
        if (turnTraceRequestId) {
            await emitThinkingTraceEvent({
                request_id: turnTraceRequestId,
                type: 'process_failed',
                status: 'failed',
                step_code: 'TURN_STREAM_FAILED',
                step_label: 'Response stream failed',
                details: { flow: 'turn', error: String(error?.message || error || '') },
            });
        }
        resetOverlayStatusQueue();
        if (streamRenderTimer !== null) {
            clearTimeout(streamRenderTimer);
            streamRenderTimer = null;
        }
        if (streamRenderFrame !== null) {
            cancelAnimationFrame(streamRenderFrame);
            streamRenderFrame = null;
        }
        assistant.content.classList.remove('streaming-active', 'streaming-complete');
        assistant.content.textContent = error.message || 'Streaming chat failed';
        if (assistant.ticker) {
            assistant.ticker.textContent = '';
            assistant.ticker.style.display = 'none';
        }
        if (assistant.spinner) {
            assistant.spinner.style.display = 'none';
        }
        if (thinkingTextHideTimer) {
            clearTimeout(thinkingTextHideTimer);
            thinkingTextHideTimer = null;
        }
        if (assistant.thinkingIndicator) {
            assistant.thinkingIndicator.style.display = 'none';
            assistant.thinkingIndicator.innerHTML = '';
        }
        if (assistant.thinkingTrace) {
            assistant.thinkingTrace.style.display = 'none';
            if (assistant.thinkingTraceLabel) {
                assistant.thinkingTraceLabel.style.display = 'none';
            }
            if (assistant.thinkingTraceLines) {
                assistant.thinkingTraceLines.innerHTML = '';
            }
            if (assistant.thinkingTraceToggle) {
                assistant.thinkingTraceToggle.hidden = true;
                assistant.thinkingTraceToggle.textContent = '▾';
                assistant.thinkingTraceToggle.setAttribute('aria-expanded', 'false');
            }
            assistant.bubble.classList.remove('is-thinking-expanded');
        }
    } finally {
        if (turnTraceWatch) {
            turnTraceWatch.stop();
        }
        overlayStreamingActive = false;
        overlayManualStatus = false;
        if (assistant.ticker) {
            assistant.ticker.textContent = '';
            assistant.ticker.style.display = 'none';
        }
        if (assistant.spinner) {
            assistant.spinner.style.display = 'none';
        }
        if (assistant.thinkingTrace) {
            assistant.thinkingTrace.style.display = 'none';
            if (assistant.thinkingTraceLabel) {
                assistant.thinkingTraceLabel.style.display = 'none';
            }
            if (assistant.thinkingTraceLines) {
                assistant.thinkingTraceLines.innerHTML = '';
            }
            if (assistant.thinkingTraceToggle) {
                assistant.thinkingTraceToggle.hidden = true;
                assistant.thinkingTraceToggle.textContent = '▾';
                assistant.thinkingTraceToggle.setAttribute('aria-expanded', 'false');
            }
            assistant.bubble.classList.remove('is-thinking-expanded');
        }
        stopPreStreamTicker();
        resetOverlayStatusQueue();
        setLoadingOverlay(false);
    }
}

function initStreamedChat() {
    const chatForm = document.getElementById('chat-form');
    const streamEnabled = window?.dsChatStreamEnabled === true;
    if (!chatForm || !streamEnabled) return;
    disableHtmxChatForm(chatForm);
    chatForm.addEventListener('submit', handleStreamedChatSubmit);
    initCoordRefCopy();
}


function updateAppraisalPanel(stats) {
    if (!stats || typeof stats !== 'object') return;
    const law = document.getElementById('panel-law-score');
    const grace = document.getElementById('panel-grace-score');
    const drift = document.getElementById('panel-drift-score');
    if (law && Number.isFinite(Number(stats.law_score ?? stats.lawScore))) {
        law.textContent = Number(stats.law_score ?? stats.lawScore).toFixed(2);
    }
    if (grace && Number.isFinite(Number(stats.grace_score ?? stats.graceScore))) {
        grace.textContent = Number(stats.grace_score ?? stats.graceScore).toFixed(2);
    }
    if (drift && Number.isFinite(Number(stats.drift))) {
        drift.textContent = Number(stats.drift).toFixed(2);
    }
}

function updateResolveDebugPanel(meta) {
    if (!meta || typeof meta !== 'object') return;
    updateAuthDebugPanel(meta);
    const timing = meta.timing_ms;
    if (timing && typeof timing === 'object') {
        const assemble = document.getElementById('panel-assemble-ms');
        const decode = document.getElementById('panel-decode-ms');
        const llm = document.getElementById('panel-llm-ms');
        const assess = document.getElementById('panel-assess-ms');
        const commit = document.getElementById('panel-commit-ms');
        const total = document.getElementById('panel-total-ms');
        if (assemble && Number.isFinite(Number(timing.assemble_ms))) {
            assemble.textContent = `${Number(timing.assemble_ms)}ms`;
        }
        if (decode && Number.isFinite(Number(timing.decode_ms))) {
            decode.textContent = `${Number(timing.decode_ms)}ms`;
        }
        if (llm && Number.isFinite(Number(timing.llm_ms))) {
            llm.textContent = `${Number(timing.llm_ms)}ms`;
        }
        if (assess && Number.isFinite(Number(timing.assess_ms))) {
            assess.textContent = `${Number(timing.assess_ms)}ms`;
        }
        if (commit && Number.isFinite(Number(timing.commit_ms))) {
            commit.textContent = `${Number(timing.commit_ms)}ms`;
        }
        if (total && Number.isFinite(Number(timing.total_ms))) {
            total.textContent = `${Number(timing.total_ms)}ms`;
        }
    }

    const coords = meta.coord_counts;
    if (coords && typeof coords === 'object') {
        const queued = document.getElementById('panel-coords-queued');
        const decoded = document.getElementById('panel-coords-decoded');
        const child = document.getElementById('panel-coords-child');
        if (queued && Number.isFinite(Number(coords.queued))) {
            queued.textContent = String(coords.queued);
        }
        if (decoded && Number.isFinite(Number(coords.decoded))) {
            decoded.textContent = String(coords.decoded);
        }
        if (child && Number.isFinite(Number(coords.child_decoded))) {
            child.textContent = String(coords.child_decoded);
        }
    }

    const resolveSummary = meta.resolve_summary;
    if (resolveSummary && typeof resolveSummary === 'object') {
        applyResolveSummary(resolveSummary);
    }

    const decision = meta.router_decision;
    if (decision && typeof decision === 'object') {
        const route = document.getElementById('panel-router-route');
        const reason = document.getElementById('panel-router-reason');
        const walk = document.getElementById('panel-router-walk');
        if (route) {
            route.textContent = decision.route || '—';
        }
        if (reason) {
            reason.textContent = decision.reason || '—';
        }
        if (walk) {
            walk.textContent = decision.walk_triggered ? 'yes' : 'no';
        }
    }

    const autonomy = meta.autonomy_decision || meta?.metadata?.autonomy_decision;
    const candidateTrace = Array.isArray(meta.candidate_trace)
        ? meta.candidate_trace
        : Array.isArray(meta?.metadata?.candidate_trace)
            ? meta.metadata.candidate_trace
            : [];
    const actionEl = document.getElementById('panel-autonomy-action');
    const topEl = document.getElementById('panel-autonomy-top');
    const countEl = document.getElementById('panel-autonomy-count');
    if (actionEl) {
        actionEl.textContent = autonomy && typeof autonomy === 'object' && autonomy.action
            ? String(autonomy.action)
            : '—';
    }
    if (topEl) {
        const top = candidateTrace.length ? candidateTrace[0] : null;
        topEl.textContent = top && top.coord ? String(top.coord) : '—';
    }
    if (countEl) {
        countEl.textContent = String(candidateTrace.length || 0);
    }

    const posturePolicy = meta?.posture_policy;
    if (posturePolicy && typeof posturePolicy === 'object') {
        const decisionEl = document.getElementById('panel-policy-decision');
        const reasonCodeEl = document.getElementById('panel-policy-reason-code');
        const failedEqEl = document.getElementById('panel-policy-failed-eq');
        const repairActionsEl = document.getElementById('panel-policy-repair-actions');
        const trustClassEl = document.getElementById('panel-policy-trust-class');
        const postureClassEl = document.getElementById('panel-policy-eq9-posture-class');

        const decision = String(posturePolicy.policy_decision || '').trim() || '—';
        const reasonCode = String(posturePolicy.reason_code || '').trim() || '—';
        const failedEq = String(posturePolicy.failed_eq || '').trim() || '—';
        const trustClass = String(posturePolicy.trust_class || '').trim() || '—';
        const postureClass = String(posturePolicy.eq9_posture_class || '').trim() || '—';
        const repairsRaw = Array.isArray(posturePolicy.repair_actions) ? posturePolicy.repair_actions : [];
        const repairs = repairsRaw
            .filter((item) => typeof item === 'string' && item.trim())
            .slice(0, 3)
            .join('; ');

        if (decisionEl) decisionEl.textContent = decision;
        if (reasonCodeEl) reasonCodeEl.textContent = reasonCode;
        if (failedEqEl) failedEqEl.textContent = failedEq;
        if (repairActionsEl) repairActionsEl.textContent = repairs || '—';
        if (trustClassEl) trustClassEl.textContent = trustClass;
        if (postureClassEl) postureClassEl.textContent = postureClass;
    }

    const queryIntegrity = (meta?.query_integrity && typeof meta.query_integrity === 'object')
        ? meta.query_integrity
        : (meta?.metadata?.query_integrity && typeof meta.metadata.query_integrity === 'object')
            ? meta.metadata.query_integrity
            : null;
    if (queryIntegrity && typeof queryIntegrity === 'object') {
        const sourceTierEl = document.getElementById('panel-query-source-tier');
        const stalenessEl = document.getElementById('panel-query-staleness-ms');
        const integrityEl = document.getElementById('panel-query-integrity-status');
        const witnessEl = document.getElementById('panel-query-witness-status');
        const reconstructionEl = document.getElementById('panel-query-reconstruction-path');

        const sourceTier = String(queryIntegrity.source_tier || '').trim() || '—';
        const stalenessRaw = queryIntegrity.staleness_ms;
        const staleness = Number.isFinite(Number(stalenessRaw)) ? String(Number(stalenessRaw)) : '—';
        const integrity = String(queryIntegrity.integrity_status || '').trim() || '—';
        const witness = String(queryIntegrity.witness_status || '').trim() || '—';
        const reconstruction = String(queryIntegrity.reconstruction_path || '').trim() || '—';

        if (sourceTierEl) sourceTierEl.textContent = sourceTier;
        if (stalenessEl) stalenessEl.textContent = staleness;
        if (integrityEl) integrityEl.textContent = integrity;
        if (witnessEl) witnessEl.textContent = witness;
        if (reconstructionEl) reconstructionEl.textContent = reconstruction;
    }

    const context = meta?.metadata?.context_window;
    if (context && typeof context === 'object') {
        const promptEl = document.getElementById('panel-context-prompt');
        const completionEl = document.getElementById('panel-context-completion');
        const retrievedEl = document.getElementById('panel-context-retrieved');
        if (promptEl && Number.isFinite(Number(context.prompt_tokens))) {
            promptEl.textContent = String(Number(context.prompt_tokens));
        }
        if (completionEl && Number.isFinite(Number(context.completion_tokens))) {
            completionEl.textContent = String(Number(context.completion_tokens));
        }
        if (retrievedEl && Number.isFinite(Number(context.retrieved_count))) {
            retrievedEl.textContent = String(Number(context.retrieved_count));
        }
    }

    const eq6Commit = meta?.metadata?.eq6_commit_allowed;
    const eq6Law = meta?.metadata?.eq6_lawfulness_level;
    const eq6Panel = document.getElementById('panel-eq6-commit');
    if (eq6Panel) {
        if (eq6Commit === true) {
            eq6Panel.textContent = Number.isFinite(Number(eq6Law)) ? `yes (L${Number(eq6Law)})` : 'yes';
        } else if (eq6Commit === false) {
            eq6Panel.textContent = Number.isFinite(Number(eq6Law)) ? `no (L${Number(eq6Law)})` : 'no';
        } else {
            eq6Panel.textContent = '—';
        }
    }

}

function initCoordRefCopy() {
    document.addEventListener('click', async (event) => {
        const target = event.target;
        if (!(target instanceof HTMLElement)) return;
        const coord = target.dataset?.coord;
        if (!coord) return;
        try {
            await navigator.clipboard.writeText(coord);
        } catch (error) {
            const temp = document.createElement('textarea');
            temp.value = coord;
            document.body.appendChild(temp);
            temp.select();
            document.execCommand('copy');
            document.body.removeChild(temp);
        }
    });
}

function getAgentLabel(modelId) {
    const select = document.getElementById('agent-select');
    if (!select || !modelId) return '';
    const option = Array.from(select.options).find((opt) => opt.value === modelId);
    return option?.textContent?.trim() || '';
}

function getCurrentSessionEntity() {
    return (
        document.getElementById('entity-id')?.value?.trim()
        || document.getElementById('session-id')?.value?.trim()
        || getCookieValue('ds_session')
        || ''
    );
}

function buildStreamRequestPayload({
    message,
    provider,
    sessionId,
    currentHistory,
    uniqueAttachmentCoordinates,
    timeRange,
} = {}) {
    const entity = getCurrentSessionEntity();
    provider = normalizeModelId(provider);
    const payload = {
        message,
        provider,
        agent: provider,
        model: provider,
        entity: entity || undefined,
        session_id: sessionId || undefined,
        enable_ledger: true,
        backend_stream: resolveBackendStreamFlag(),
        history: currentHistory,
        attachments: uniqueAttachmentCoordinates,
        context_coords: uniqueAttachmentCoordinates,
        time_range: timeRange || undefined,
        include_pipeline_events: true,
        include_post_introspect_snapshot: true,
        request_id: `req-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`,
    };
    return payload;
}

function buildUpstreamLoadingMessages(requestPayload, response = null) {
    const payload = requestPayload && typeof requestPayload === 'object' ? requestPayload : {};
    const historyCount = Array.isArray(payload.history) ? payload.history.length : 0;
    const attachmentCount = Array.isArray(payload.attachments) ? payload.attachments.length : 0;
    const entity = payload.entity ? String(payload.entity).trim() : '';
    const provider = payload.provider ? String(payload.provider).trim() : '';
    const promptPrincipalMode = payload.prompt_principal_mode ? String(payload.prompt_principal_mode).trim() : '';
    const messages = ['Request created'];
    messages.push(entity ? `Session bound · entity=${entity}` : 'Session bound');
    const payloadParts = [`history=${historyCount}`, `attachments=${attachmentCount}`];
    if (provider) {
        payloadParts.push(`provider=${provider}`);
    }
    if (promptPrincipalMode) {
        payloadParts.push(`prompt=${promptPrincipalMode}`);
    }
    messages.push(`Payload ready · ${payloadParts.join(' · ')}`);
    if (response) {
        messages.push('Turn stream connected');
        const upstreamUrl = response.headers?.get ? String(response.headers.get('x-ds-upstream-url') || '').trim() : '';
        if (upstreamUrl) {
            messages.push('Routing to middleware');
        }
    } else {
        messages.push('Opening turn stream');
        messages.push('Opening thinking trace');
    }
    return messages;
}

function getHistoryLoader() {
    return document.getElementById('history-loader');
}

function getActiveHistoryEntity() {
    const loader = getHistoryLoader();
    return String(loader?.dataset?.historyEntity || HISTORY_ENTITY_ALL).trim() || HISTORY_ENTITY_ALL;
}

async function loadHistory() {
    const loader = getHistoryLoader();
    if (!loader) return;

    const entity = getActiveHistoryEntity();
    const configuredStep = Number(loader.dataset.historyStep || loader.dataset.historyLimit || '5');
    const initialLimit = Number.isFinite(configuredStep) && configuredStep > 0 ? configuredStep : 5;

    loader.dataset.historyEntity = entity;
    loader.dataset.historyLimit = String(initialLimit);
    historyLoading = true;
    const traceRequestId = buildTraceRequestId('history');
    const traceWatch = startThinkingTraceOverlayWatch({ requestId: traceRequestId, flow: 'history' });
    setHistoryLoading(true);
    await emitThinkingTraceEvent({
        request_id: traceRequestId,
        type: 'process_started',
        status: 'in_progress',
        step_code: 'HISTORY_LOAD_START',
        step_label: 'Loading history',
        details: { entity, limit: initialLimit },
    });
    try {
        await fetchAndSwapHistory(entity, initialLimit);
        await emitThinkingTraceEvent({
            request_id: traceRequestId,
            type: 'process_completed',
            status: 'completed',
            step_code: 'HISTORY_LOAD_DONE',
            step_label: 'History loaded',
            details: { entity, limit: initialLimit },
        });
    } catch (error) {
        console.error('Unable to load history', error);
        await emitThinkingTraceEvent({
            request_id: traceRequestId,
            type: 'process_failed',
            status: 'failed',
            step_code: 'HISTORY_LOAD_FAILED',
            step_label: 'History load failed',
            details: { entity, limit: initialLimit, error: String(error?.message || error || '') },
        });
    } finally {
        traceWatch.stop();
        setHistoryLoading(false);
        historyLoading = false;
    }
}

let historySwapGeneration = 0;

async function fetchAndSwapHistory(entityValue, limitValue) {
    const current = document.getElementById('history-list');
    if (!current) return false;
    const gen = ++historySwapGeneration;
    const encodedEntity = encodeURIComponent(entityValue);
    const response = await fetch(`/ui/history/${encodedEntity}?limit=${limitValue}`, {
        headers: { Accept: 'text/html' },
    });
    if (gen !== historySwapGeneration) return false;
    if (response.redirected && String(response.url || '').includes('/login')) {
        window.location.href = response.url;
        return false;
    }
    if (!response.ok) {
        throw new Error(`History load failed (${response.status})`);
    }
    const html = await response.text();
    const container = document.createElement('div');
    container.innerHTML = html.trim();
    const next = container.querySelector('#history-list') || container.firstElementChild;
    if (!(next instanceof HTMLElement)) {
        throw new Error('History payload missing #history-list');
    }
    current.replaceWith(next);
    renderAssistantMarkdown(next);
    return true;
}

async function finalizeStreamedHistory() {
    const loader = getHistoryLoader();
    if (!loader) return;
    const entity = getActiveHistoryEntity();
    const limit = Number(loader.dataset.historyLimit || '5');
    const streamed = document.querySelector('[data-streamed-turn="true"]');
    try {
        await fetchAndSwapHistory(entity, limit);
    } catch (error) {
        console.warn('Unable to refresh history after stream', error);
        return;
    }
    if (streamed && streamed.parentElement) {
        streamed.remove();
    }
}

async function persistStreamedTurn({ message, reply, latencyMs, metadata, precomputedAppraisal }) {
    const payload = {
        message: String(message || ''),
        reply: String(reply || ''),
        metadata: metadata && typeof metadata === 'object' ? metadata : {},
    };
    if (Number.isFinite(Number(latencyMs))) {
        payload.latency_ms = Number(latencyMs);
    }
    if (precomputedAppraisal && typeof precomputedAppraisal === 'object') {
        payload.precomputed_appraisal = precomputedAppraisal;
    }
    const response = await fetch('/api/chat/stream/commit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    });
    let body = {};
    try {
        body = await response.json();
    } catch (_error) {
        body = {};
    }
    console.log('[chat] stream commit response', response.status, body);
    if (!response.ok) {
        const detail = body && typeof body === 'object' ? (body.detail || body.error || JSON.stringify(body)) : '';
        throw new Error(String(detail || `Stream commit failed (${response.status})`));
    }
    return body;
}



let pendingChatHistoryRefresh = false;
let historyLoading = false;
let historyScrollPending = false;
let historyLastBottomDistance = null;
let historyLastScrollY = 0;

function scheduleChatHistoryRefresh() {
    pendingChatHistoryRefresh = false;
}

function refreshChatHistory() {
    // Intentionally disabled: streaming updates handle new content without swapping.
}

function maybeRefreshChatHistory() {
    pendingChatHistoryRefresh = false;
}

function maybeLoadMoreHistory() {
    const loader = document.getElementById('history-loader');
    const historyList = document.getElementById('history-list');
    if (!loader || !historyList) return;
    if (historyLoading) return;
    const scrollHeight = document.documentElement.scrollHeight || document.body.offsetHeight;
    const distanceFromBottom = scrollHeight - (window.innerHeight + window.scrollY);
    const nearBottom = distanceFromBottom <= 120;
    const isScrollingDown = window.scrollY > historyLastScrollY;
    const crossedThreshold = historyLastBottomDistance !== null
        && historyLastBottomDistance > 120
        && distanceFromBottom <= 120;
    historyLastBottomDistance = distanceFromBottom;
    historyLastScrollY = window.scrollY;
    if (!nearBottom || !isScrollingDown || !crossedThreshold) return;
    const limit = Number(loader.dataset.historyLimit || '5');
    const step = Number(loader.dataset.historyStep || '5');
    const nextLimit = Number.isFinite(limit + step) ? limit + step : 5;
    const entity = String(loader.dataset.historyEntity || HISTORY_ENTITY_ALL).trim() || HISTORY_ENTITY_ALL;
    historyLoading = true;
    loader.dataset.historyLimit = String(nextLimit);
    const traceRequestId = buildTraceRequestId('history');
    const traceWatch = startThinkingTraceOverlayWatch({ requestId: traceRequestId, flow: 'history' });
    setHistoryLoading(true);
    emitThinkingTraceEvent({
        request_id: traceRequestId,
        type: 'process_started',
        status: 'in_progress',
        step_code: 'HISTORY_LOAD_START',
        step_label: 'Loading history',
        details: { entity, limit: nextLimit },
    }).catch(() => undefined);
    fetchAndSwapHistory(entity, nextLimit)
        .then(() => {
            loader.dataset.historyLimit = String(nextLimit);
            return emitThinkingTraceEvent({
                request_id: traceRequestId,
                type: 'process_completed',
                status: 'completed',
                step_code: 'HISTORY_LOAD_DONE',
                step_label: 'History loaded',
                details: { entity, limit: nextLimit },
            });
        })
        .catch((error) => {
            console.error('Unable to load more history', error);
            return emitThinkingTraceEvent({
                request_id: traceRequestId,
                type: 'process_failed',
                status: 'failed',
                step_code: 'HISTORY_LOAD_FAILED',
                step_label: 'History load failed',
                details: { entity, limit: nextLimit, error: String(error?.message || error || '') },
            });
        })
        .finally(() => {
            traceWatch.stop();
            setHistoryLoading(false);
            historyLoading = false;
        });
    if (!window.fetch) {
        historyLoading = false;
    }
}

function handleHistoryScroll() {
    if (historyScrollPending) return;
    historyScrollPending = true;
    window.requestAnimationFrame(() => {
        historyScrollPending = false;
        maybeLoadMoreHistory();
    });
}

function ensureChatStreamPlacement() {
    const chatForm = document.getElementById('chat-form');
    let chatStream = document.getElementById('chat-stream');
    const loadingOverlay = document.getElementById('loading-overlay');
    const landingZone = document.getElementById('landing-zone');
    if (!chatStream) {
        if (!loadingOverlay?.parentElement && !landingZone?.parentElement) {
            console.warn('[chat-stream] missing from DOM');
            return;
        }
        chatStream = document.createElement('div');
        chatStream.id = 'chat-stream';
        chatStream.className = 'loading-history flex flex-col pt-48 px-4 pb-20 transition-opacity duration-300 z-0';
        if (loadingOverlay?.parentElement) {
            loadingOverlay.parentElement.insertBefore(chatStream, loadingOverlay.nextSibling);
        } else {
            landingZone.parentElement.insertBefore(chatStream, landingZone.nextSibling);
        }
    }
    if (!chatStream) {
        console.warn('[chat-stream] missing from DOM');
        return;
    }
    if (!chatForm) return;
    if (chatForm.contains(chatStream)) {
        console.warn('[chat-stream] nested inside #chat-form; relocating');
        if (loadingOverlay?.parentElement) {
            loadingOverlay.parentElement.insertBefore(chatStream, loadingOverlay.nextSibling);
        } else if (landingZone?.parentElement) {
            landingZone.parentElement.insertBefore(chatStream, landingZone.nextSibling);
        } else {
            chatForm.parentElement?.insertBefore(chatStream, chatForm.nextSibling);
        }
    }

    const misplacedMessages = chatForm.querySelectorAll(
        '.message, .reference-rack, [id^="msg-user-"], [id^="msg-assistant-"]',
    );
    if (misplacedMessages.length) {
        console.warn('[chat-stream] relocating misplaced messages from #chat-form');
        misplacedMessages.forEach((node) => {
            if (node.closest('#chat-form')) {
                chatStream.prepend(node);
            }
        });
    }
}

function handleInputKeydown(event) {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        if (window?.dsChatStreamEnabled === true && event.target.form?.id === 'chat-form') {
            handleStreamedChatSubmit(event);
        } else {
            event.target.form?.requestSubmit();
        }
    }
}

function handleInputFocus(el) {
    if (threadlessMetrics.isGhostState) {
        el.value = '';
        el.classList.remove('text-gray-400', 'italic');
        el.classList.add('text-gray-900');
        threadlessMetrics.isGhostState = false;
    }
    adjustInputHeight(el);
}

function renderAssistantMarkdown(root = document, options = {}) {
    if (typeof marked === 'undefined') return;
    const { typesetMath = true } = options;
    marked.setOptions({ mangle: false, headerIds: false, breaks: true });
    const blocks = Array.from(root.querySelectorAll('.markdown-content'));
    if (root?.classList?.contains('markdown-content')) {
        blocks.unshift(root);
    }
    let hasMathContent = false;
    blocks.forEach((block) => {
        if (block.dataset.rendered === 'true') return;
        const raw = block.textContent || '';
        if (!hasMathContent && /(\$|\\\(|\\\[|\\begin\{)/.test(raw)) {
            hasMathContent = true;
        }
        const normalized = normalizeMarkdownLists(raw);
        block.innerHTML = marked.parse(normalized);
        block.dataset.rendered = 'true';
    });

    if (
        typesetMath
        && window.MathJax?.typesetPromise
        && (hasMathContent || root.querySelector('mjx-container, math, .math, .MathJax'))
    ) {
        window.MathJax.typesetPromise();
    }
}

function normalizeMarkdownLists(text) {
    if (!text) return text;
    let value = String(text).replace(/\r\n/g, '\n');
    value = value.replace(/([.!?])\s+(-\s+)/g, '$1\n$2');
    value = value.replace(/([.!?])\s+(\d+\. )/g, '$1\n$2');
    value = value.replace(/:\s+(-\s+)/g, ':\n$1');
    value = value.replace(/:\s+(\d+\. )/g, ':\n$1');
    return value;
}

let markdownObserver = null;
let markdownRenderPending = false;
const markdownRenderTargets = new Set();

function scheduleMarkdownRender(target) {
    if (!target) return;
    markdownRenderTargets.add(target);
    if (markdownRenderPending) return;
    markdownRenderPending = true;
    requestAnimationFrame(() => {
        markdownRenderPending = false;
        for (const item of markdownRenderTargets) {
            renderAssistantMarkdown(item);
        }
        markdownRenderTargets.clear();
    });
}

function initMarkdownObserver() {
    if (markdownObserver) return;
    const root = document.getElementById('chat-stream') || document.body;
    markdownObserver = new MutationObserver((mutations) => {
        for (const mutation of mutations) {
            for (const node of mutation.addedNodes) {
                if (!(node instanceof Element)) continue;
                if (node.classList.contains('markdown-content')) {
                    scheduleMarkdownRender(node);
                    continue;
                }
                const block = node.querySelector?.('.markdown-content');
                if (block) {
                    scheduleMarkdownRender(node);
                }
            }
        }
    });
    markdownObserver.observe(root, { childList: true, subtree: true });
}

function unwrapStreamingTail(root) {
    if (!root) return;
    const tails = root.querySelectorAll('.streaming-tail');
    tails.forEach((tail) => {
        const parent = tail.parentNode;
        if (!parent) return;
        while (tail.firstChild) {
            parent.insertBefore(tail.firstChild, tail);
        }
        parent.removeChild(tail);
    });
}

function applyStreamingTail(root, ratio = 0.15) {
    if (!root) return;
    unwrapStreamingTail(root);

    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const textNodes = [];
    let totalLength = 0;
    let node = walker.nextNode();

    while (node) {
        const text = node.nodeValue || '';
        if (text.length) {
            textNodes.push({ node, length: text.length });
            totalLength += text.length;
        }
        node = walker.nextNode();
    }

    if (!totalLength) return;

    let remaining = Math.max(1, Math.floor(totalLength * ratio));

    for (let i = textNodes.length - 1; i >= 0 && remaining > 0; i -= 1) {
        const { node: textNode, length } = textNodes[i];
        if (!textNode.parentNode) continue;
        if (length <= remaining) {
            const span = document.createElement('span');
            span.className = 'streaming-tail';
            textNode.parentNode.replaceChild(span, textNode);
            span.appendChild(textNode);
            remaining -= length;
            continue;
        }

        const splitIndex = length - remaining;
        const tailNode = textNode.splitText(splitIndex);
        const span = document.createElement('span');
        span.className = 'streaming-tail';
        tailNode.parentNode?.replaceChild(span, tailNode);
        span.appendChild(tailNode);
        remaining = 0;
    }
}


function toggleMenu() {
    const panel = document.getElementById('settings-panel');
    const overlay = document.getElementById('menu-overlay');
    const icon = document.getElementById('hamburger-icon');
    const button = document.getElementById('hamburger-btn');

    const isOpen = panel.classList.contains('open');
    const nextOpen = !isOpen;

    if (nextOpen) {
        panel.classList.add('open');
        overlay.classList.add('active');
        document.body.style.overflow = 'hidden';
        if (icon) icon.classList.add('open');
        if (button) button.setAttribute('aria-label', 'Close menu');
        window.requestAnimationFrame(() => {
            window.setTimeout(() => {
                refreshSettingsIdentity(true);
                refreshTrustPanel(true);
            }, 120);
        });
    } else {
        panel.classList.remove('open');
        overlay.classList.remove('active');
        document.body.style.overflow = '';
        if (icon) icon.classList.remove('open');
        if (button) button.setAttribute('aria-label', 'Open menu');
    }
}

function toggleAccordion(button) {
    const wrapper = button?.closest?.('.accordion');
    const expanded = wrapper?.classList.toggle('open');
    if (button && typeof expanded === 'boolean') {
        button.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    }
}


// Close menu on escape
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        const panel = document.getElementById('settings-panel');
        if (panel.classList.contains('open')) {
            toggleMenu();
        }
    }
});


async function uploadAttachment(input) {
    if (demoConnectivityState.offline && !isOllamaModelSelected()) {
        enforceOfflineModelSelection(undefined, true);
        if (!isOllamaModelSelected()) {
            showToast('Offline demo mode requires an Ollama model', 'error');
            input.value = '';
            return;
        }
    }
    const file = input?.files?.[0];
    if (!file) return;
    const maxBytes = Number.isFinite(Number(window?.dsAttachmentMaxBytes))
        ? Number(window.dsAttachmentMaxBytes)
        : 50 * 1024 * 1024;
    if (file.size > maxBytes) {
        const maxMb = Math.floor(maxBytes / (1024 * 1024));
        alert(`File too large (max ${maxMb} MB).`);
        input.value = '';
        return;
    }

    setAttachmentCoordinate('');
    overlayManualStatus = true;
    setAttachmentLoading(true);
    const attachTraceRequestId = buildTraceRequestId('attach');
    const attachTraceWatch = startThinkingTraceOverlayWatch({ requestId: attachTraceRequestId, flow: 'attachment' });
    await emitThinkingTraceEvent({
        request_id: attachTraceRequestId,
        type: 'process_started',
        status: 'in_progress',
        step_code: 'ATTACH_UPLOAD_START',
        step_label: 'Uploading attachment',
        details: { filename: String(file?.name || ''), bytes: Number(file?.size || 0) },
    });
    const localHash = await computeFileHash(file).catch(() => '');
    const cacheScope = getAttachmentCacheScope();
    if (localHash) {
        const cached = getCachedAttachmentCoord(localHash, cacheScope);
        if (cached && await isCoordinateResolvable(cached)) {
            setAttachmentCoordinate(cached);
            updateStickyStack();
            updateLoadingStatus('Attachment already uploaded.', true);
            overlayManualStatus = false;
            setAttachmentLoading(false);
            input.value = '';
            return;
        }
    }
    const payload = new FormData();
    payload.append('file', file);
    payload.append('kind', 'attachment');
    const inventoryResp = await fetch('/api/ledgers/inventory', { headers: { Accept: 'application/json' } }).catch(() => null);
    const inventory = inventoryResp && inventoryResp.ok ? await inventoryResp.json().catch(() => ({})) : {};
    const invSession = (inventory && typeof inventory === 'object' && inventory.session && typeof inventory.session === 'object')
        ? inventory.session
        : {};
    const sessionEntity =
        String(invSession.current_entity || '').trim()
        || document.getElementById('entity-id')?.value?.trim()
        || document.getElementById('session-id')?.value?.trim()
        || getCookieValue('ds_session')
        || 'default';
    const entity = sessionEntity;
    payload.append('entity', entity);
    const selectedModel = String(document.getElementById('agent-select')?.value || '').trim();
    if (selectedModel) {
        payload.append('provider', selectedModel);
        payload.append('model', selectedModel);
    }
    const ledgerId =
        String(invSession.active_ledger || '').trim()
        || getActiveLedger()
        || 'default';
    const contextId = String(
        invSession.context_id
        || document.getElementById('panel-context-id')?.textContent
        || window.dsContextId
        || ''
    ).trim();
    payload.append('ledger_id', ledgerId);
    if (contextId) {
        payload.append('context_id', contextId);
    }

    try {
        const rawApiBase = typeof window !== 'undefined' ? String(window.dsApiBase || '') : '';
        const apiBase = rawApiBase.replace(/\/+$/, '');
        const host = typeof window !== 'undefined' ? String(window.location?.hostname || '') : '';
        const isVercelHost = /vercel\.app$/i.test(host);
        // Vercel serverless functions enforce request body limits. For larger
        // files, upload directly to middleware to bypass frontend function size
        // caps while keeping middleware as the integration surface.
        const directBackendUpload = isVercelHost && file.size >= (4 * 1024 * 1024) && !!apiBase;
        const ingestUrl = directBackendUpload
            ? `${apiBase}/api/ingest/file`
            : '/api/ingest/stream-file';
        const response = await fetch(ingestUrl, { method: 'POST', body: payload });
        let coordinate = '';
        let partCoordinates = [];
        if (directBackendUpload) {
            const body = await response.text();
            let parsed = {};
            try { parsed = body ? JSON.parse(body) : {}; } catch (_) { parsed = {}; }
            if (!response.ok) {
                throw new Error(body || `Attachment upload failed (${response.status})`);
            }
            coordinate = parsed.parent_coordinate || parsed.coordinate || parsed.entry_id || parsed.web4_key || '';
            if (coordinate && !String(coordinate).includes(':')) {
                coordinate = `${entity}:ATT-${coordinate}`;
            }
            partCoordinates = Array.isArray(parsed.part_coordinates)
                ? parsed.part_coordinates
                    .map((value) => String(value || '').trim())
                    .filter((value) => value)
                    .map((value) => (value.includes(':') ? value : `${entity}:ATT-${value}`))
                : [];
            if (coordinate) {
                setAttachmentCoordinate(coordinate);
                if (localHash) cacheAttachmentCoord(localHash, coordinate, cacheScope);
            }
            setAttachmentCoordinates(partCoordinates);
            scheduleStickyUpdate();
            updateLoadingStatus('Ingestion Complete.', true);
            await emitThinkingTraceEvent({
                request_id: attachTraceRequestId,
                type: 'step',
                status: 'in_progress',
                step_code: 'ATTACH_UPLOAD_DONE',
                step_label: 'Attachment uploaded',
                details: { coordinate: String(coordinate || ''), part_coordinates: partCoordinates },
            });
        } else {
            await readStream(response, (event) => {
                if (event.type === 'status' && event.message) {
                    updateLoadingStatus(event.message, true);
                    scheduleStickyUpdate();
                    emitThinkingTraceEvent({
                        request_id: attachTraceRequestId,
                        type: 'step',
                        status: 'in_progress',
                        step_code: 'ATTACH_STATUS',
                        step_label: String(event.message),
                    }).catch(() => undefined);
                    return;
                }
                if (event.type === 'error') {
                    throw new Error(event.detail || 'Attachment upload failed');
                }
                if (event.type === 'meta') {
                    coordinate = event.parent_coordinate || event.coordinate || '';
                    if (coordinate && !String(coordinate).includes(':')) {
                        coordinate = `${entity}:ATT-${coordinate}`;
                    }
                    partCoordinates = Array.isArray(event.part_coordinates)
                        ? event.part_coordinates
                            .map((value) => String(value || '').trim())
                            .filter((value) => value)
                            .map((value) => (value.includes(':') ? value : `${entity}:ATT-${value}`))
                        : [];
                    if (coordinate) {
                        setAttachmentCoordinate(coordinate);
                        if (localHash) {
                            cacheAttachmentCoord(localHash, coordinate, cacheScope);
                        }
                    }
                    setAttachmentCoordinates(partCoordinates);
                    scheduleStickyUpdate();
                    updateLoadingStatus('Ingestion Complete.', true);
                    emitThinkingTraceEvent({
                        request_id: attachTraceRequestId,
                        type: 'step',
                        status: 'in_progress',
                        step_code: 'ATTACH_INGEST_DONE',
                        step_label: 'Attachment ingestion complete',
                        details: { coordinate: String(coordinate || ''), part_coordinates: partCoordinates },
                    }).catch(() => undefined);
                }
            });
        }
        if (![coordinate, ...partCoordinates].some((value) => String(value || '').trim())) {
            throw new Error('Attachment ingested without usable coordinate.');
        }
        await emitThinkingTraceEvent({
            request_id: attachTraceRequestId,
            type: 'process_completed',
            status: 'completed',
            step_code: 'FINALIZE',
            step_label: 'Attachment processing complete',
            details: { coordinate: String(coordinate || ''), part_coordinates: partCoordinates },
        });
    } catch (error) {
        await emitThinkingTraceEvent({
            request_id: attachTraceRequestId,
            type: 'process_failed',
            status: 'failed',
            step_code: 'ATTACH_UPLOAD_FAILED',
            step_label: 'Attachment upload failed',
            details: { error: String(error?.message || error || '') },
        });
        console.error('Attachment upload failed', error);
        alert(error.message || 'Attachment upload failed');
    } finally {
        input.value = '';
        overlayManualStatus = false;
        setAttachmentLoading(false);
    }
}

async function computeFileHash(file) {
    if (!window.crypto?.subtle) return '';
    const buffer = await file.arrayBuffer();
    const digest = await window.crypto.subtle.digest('SHA-256', buffer);
    const hashArray = Array.from(new Uint8Array(digest));
    return hashArray.map((b) => b.toString(16).padStart(2, '0')).join('');
}

function getAttachmentCacheScope() {
    const ledger = String(getActiveLedger() || window?.dsActiveLedger || '').trim();
    const entity = String(document.getElementById('entity-id')?.value || '').trim();
    return `${ledger}::${entity}`;
}

async function isCoordinateResolvable(coord) {
    const candidate = String(coord || '').trim();
    if (!candidate) return false;
    try {
        const response = await fetch('/api/decode_coordinate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ coordinate: candidate }),
        });
        if (!response.ok) return false;
        const payload = await response.json().catch(() => ({}));
        return Boolean(payload?.coord || payload?.canonical_coord || payload?.content || payload?.payload);
    } catch (error) {
        return false;
    }
}

function getCachedAttachmentCoord(hash, scopeKey = '') {
    try {
        const raw = sessionStorage.getItem('ds-attachment-cache');
        const cache = raw ? JSON.parse(raw) : {};
        const entry = cache[hash];
        if (typeof entry === 'string') {
            return entry;
        }
        if (entry && typeof entry === 'object' && typeof entry.coord === 'string') {
            if (!scopeKey || !entry.scope || entry.scope === scopeKey) {
                return entry.coord;
            }
        }
        return '';
    } catch (error) {
        return '';
    }
}

function cacheAttachmentCoord(hash, coord, scopeKey = '') {
    try {
        const raw = sessionStorage.getItem('ds-attachment-cache');
        const cache = raw ? JSON.parse(raw) : {};
        cache[hash] = { coord, scope: scopeKey || getAttachmentCacheScope() };
        sessionStorage.setItem('ds-attachment-cache', JSON.stringify(cache));
    } catch (error) {
        // Ignore cache failures
    }
}

function setAttachmentCoordinate(coordinate) {
    const list = document.getElementById('attachment-coordinate-list');
    if (!list) return;
    const trimmed = (coordinate || '').trim();
    if (!trimmed) return;
    const existing = Array.from(list.querySelectorAll('.attachment-coordinate[data-coordinate]'))
        .some((node) => String(node.dataset.coordinate || '').trim() === trimmed);
    if (existing) return;
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'reference-rack attachment-coordinate';
    button.setAttribute('aria-label', 'Copy attachment coordinate');
    button.dataset.coordinate = trimmed;
    button.addEventListener('click', () => copyAttachmentCoordinate(button));
    const valueEl = document.createElement('span');
    valueEl.className = 'coordinate';
    valueEl.textContent = trimmed;
    button.appendChild(valueEl);
    list.appendChild(button);
    scheduleStickyUpdate();
}

function setAttachmentCoordinates(coordinates) {
    if (!Array.isArray(coordinates)) return;
    coordinates.forEach((coordinate) => setAttachmentCoordinate(String(coordinate || '').trim()));
}

async function copyAttachmentCoordinate(button) {
    const coordinate = button?.dataset?.coordinate?.trim();
    if (!coordinate) return;
    try {
        if (navigator.clipboard?.writeText) {
            await navigator.clipboard.writeText(coordinate);
        } else {
            const temp = document.createElement('textarea');
            temp.value = coordinate;
            document.body.appendChild(temp);
            temp.select();
            document.execCommand('copy');
            document.body.removeChild(temp);
        }
        button.classList.add('copied');
        window.setTimeout(() => button.classList.remove('copied'), 1200);
    } catch (error) {
        console.error('Unable to copy attachment coordinate', error);
    }
}

function clearAttachmentCoordinates() {
    const list = document.getElementById('attachment-coordinate-list');
    if (!list) return;
    list.innerHTML = '';
    scheduleStickyUpdate();
}

function initAttachmentObserver() {
    const list = document.getElementById('attachment-coordinate-list');
    if (!list || typeof MutationObserver === 'undefined') return;
    const observer = new MutationObserver(() => {
        scheduleStickyUpdate();
        window.requestAnimationFrame(updateStickyStack);
    });
    observer.observe(list, { childList: true });
}


let activeLedgerState = window.dsActiveLedger || '';

function getActiveLedger() {
    const hidden = document.getElementById('active-ledger-id')?.value?.trim();
    return hidden || activeLedgerState || '';
}

function setPanelText(id, value) {
    const el = document.getElementById(id);
    if (!el) return;
    const text = String(value || '').trim();
    el.textContent = text || '—';
}

function setTrustPanelFallback() {
    setPanelText('trust-panel-state', 'unknown');
    setPanelText('trust-panel-verification-state', 'unknown');
    setPanelText('trust-panel-posture-state', 'unknown');
    setPanelText('trust-panel-auth-method', '—');
    setPanelText('trust-panel-principal-did', '—');
    setPanelText('trust-panel-session-jti', '—');
    setPanelText('trust-panel-reason-code', 'trust_panel_unavailable');
    setPanelText('trust-panel-trust-class', '—');
    setPanelText('trust-panel-eq9-posture-class', '—');
    setPanelText('trust-panel-failed-eq', '—');
    setPanelText('trust-panel-headline', 'Trust panel unavailable');
    setPanelText('trust-panel-verification-copy', 'Source authenticity summary is currently unavailable.');
    setPanelText('trust-panel-posture-copy', 'Policy posture details are not available for this session yet.');
    setPanelText('trust-panel-repair-copy', 'Retry opening the menu or contact operator if this persists.');
}

async function refreshTrustPanel(force = false) {
    if (!force && refreshTrustPanel._loaded) return;
    try {
        const response = await fetch('/api/auth/identity_card', {
            headers: { Accept: 'application/json' },
        });
        if (!response.ok) {
            setTrustPanelFallback();
            return;
        }
        const payload = await response.json();
        const identity = (payload && typeof payload === 'object' && payload.identity_vc && typeof payload.identity_vc === 'object')
            ? payload.identity_vc
            : {};
        const eq9 = (payload && typeof payload === 'object' && payload.eq9 && typeof payload.eq9 === 'object')
            ? payload.eq9
            : {};
        const ui = (payload && typeof payload === 'object' && payload.ui && typeof payload.ui === 'object')
            ? payload.ui
            : {};

        setPanelText('trust-panel-state', ui.panel_state || 'unknown');
        setPanelText('trust-panel-verification-state', identity.verification_state || 'unknown');
        setPanelText('trust-panel-posture-state', ui.posture_state || 'unknown');
        setPanelText('trust-panel-auth-method', identity.auth_method || '—');
        setPanelText('trust-panel-principal-did', identity.principal_did || '—');
        setPanelText('trust-panel-session-jti', identity.session_jti || '—');
        setPanelText('trust-panel-reason-code', identity.reason_code || eq9.reason_code || '—');
        setPanelText('trust-panel-trust-class', eq9.trust_class || '—');
        setPanelText('trust-panel-eq9-posture-class', eq9.eq9_posture_class || '—');
        setPanelText('trust-panel-failed-eq', eq9.failed_eq || '—');
        setPanelText('trust-panel-headline', ui.headline || 'Trust panel');
        setPanelText('trust-panel-verification-copy', ui.verification_copy || '—');
        setPanelText('trust-panel-posture-copy', ui.posture_copy || '—');
        setPanelText('trust-panel-repair-copy', ui.repair_copy || '—');
        refreshTrustPanel._loaded = true;
    } catch (error) {
        setTrustPanelFallback();
    }
}

async function refreshSettingsIdentity(force = false) {
    if (!force && refreshSettingsIdentity._loaded) return;
    try {
        const response = await fetch('/api/ledgers/inventory', {
            headers: { Accept: 'application/json' },
        });
        if (!response.ok) return;
        const payload = await response.json();
        const session = (payload && typeof payload === 'object' && payload.session && typeof payload.session === 'object')
            ? payload.session
            : {};

        const middleware = (payload && typeof payload === 'object' && payload.middleware && typeof payload.middleware === 'object')
            ? payload.middleware
            : {};
        const ledgerId = String(session.active_ledger || middleware.active_ledger || getActiveLedger() || '').trim();
        const tenantId = String(session.tenant_id || '').trim();
        const contextId = String(session.context_id || '').trim();
        const contributorId = String(session.contributor_id || '').trim();
        const entityId = String(session.current_entity || document.getElementById('entity-id')?.value || '').trim();
        const sessionId = String(session.session_id || document.getElementById('session-id')?.value || '').trim();
        const demoOffline = Boolean(session.demo_offline);

        if (ledgerId) {
            activeLedgerState = ledgerId;
            const hiddenLedger = document.getElementById('active-ledger-id');
            if (hiddenLedger) hiddenLedger.value = ledgerId;
            const ledgerSelect = document.getElementById('ledger-select');
            if (ledgerSelect) ledgerSelect.value = ledgerId;
        }

        setDemoOfflineState(demoOffline);

        refreshSettingsIdentity._loaded = true;
    } catch (error) {
        return;
    }
}

// --- Agent Management Helpers ---

async function postAgentSelection(agentId) {
    if (!agentId) return;
    const formData = new FormData();
    formData.append('agent', agentId);
    const response = await fetch('/api/set-agent', {
        method: 'POST',
        body: formData,
    });
    if (!response.ok) throw new Error('Failed to set agent');
}

function applyAgentFromCookie(agentSelect) {
    if (!agentSelect) return;
    const savedAgent = normalizeModelId(_getCookieValue('ds_agent'));
    if (!savedAgent) {
        enforceOfflineModelSelection(agentSelect, true);
        return;
    }

    const existingOption = Array.from(agentSelect.options).find((opt) => opt.value === savedAgent);
    if (!existingOption) {
        const savedOption = document.createElement('option');
        savedOption.value = savedAgent;
        savedOption.textContent = `${savedAgent} (Saved)`;
        agentSelect.appendChild(savedOption);
    }

    agentSelect.value = savedAgent;
    postAgentSelection(savedAgent).catch((error) => {
        console.error('Unable to restore agent', error);
    });
    enforceOfflineModelSelection(agentSelect, true);
}

async function initLedgerSelector() {
    const select = document.getElementById('ledger-select');
    if (!select) return;

    let activeLedger = getActiveLedger() || String(select.value || '').trim();
    try {
        const response = await fetch('/api/ledgers', {
            headers: { Accept: 'application/json' },
        });
        if (!response.ok) {
            throw new Error(`Failed to load ledgers (${response.status})`);
        }
        const payload = await response.json().catch(() => ({}));
        const ledgers = Array.isArray(payload?.ledgers)
            ? payload.ledgers.filter((item) => typeof item === 'string' && item.trim())
            : [];
        activeLedger = String(payload?.active_ledger || activeLedger || '').trim();
        select.innerHTML = '';
        const choices = Array.from(new Set([activeLedger, ...ledgers].filter(Boolean)));
        choices.forEach((ledgerId) => {
            const option = document.createElement('option');
            option.value = ledgerId;
            option.textContent = ledgerId;
            select.appendChild(option);
        });
    } catch (error) {
        console.warn('Unable to load ledgers', error);
    }

    if (activeLedger) {
        select.value = activeLedger;
        const hiddenLedger = document.getElementById('active-ledger-id');
        if (hiddenLedger) hiddenLedger.value = activeLedger;
    }

    select.addEventListener('change', async (event) => {
        const nextLedger = String(event.target.value || '').trim();
        if (!nextLedger || nextLedger === getActiveLedger()) return;
        const previousLedger = getActiveLedger();
        try {
            const response = await fetch('/api/ledgers', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ledger_id: nextLedger }),
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(payload?.detail || `Failed to switch ledger (${response.status})`);
            }
            window.location.reload();
        } catch (error) {
            console.error('Unable to switch ledger', error);
            event.target.value = previousLedger;
        }
    });
}

// Ledger Event Listeners
document.addEventListener('DOMContentLoaded', () => {
    ensureSessionCookie();
    maybeDisableHtmxForStream();
    ensureChatStreamPlacement();
    initStreamedChat();
    initDemoConnectivityTicker();
    initSetupChecklistPage();
    initAttachmentObserver();
    const toastMessage = sessionStorage.getItem('dsToastMessage');
    if (toastMessage) {
        sessionStorage.removeItem('dsToastMessage');
        showToast(toastMessage);
    }
    initLedgerSelector();
    loadHistory();
    refreshSettingsIdentity(true);

    const agentSelect = document.getElementById('agent-select');
    if (agentSelect) {
        applyAgentFromCookie(agentSelect);
        enforceOfflineModelSelection(agentSelect, false);
        agentSelect.addEventListener('change', async (event) => {
            const value = event.target.value;
            if (!value) return;
            try {
                await postAgentSelection(value);
                setCookieValue('ds_agent', value);
            } catch (error) {
                console.error('Unable to set agent', error);
            }
        });
    }
});

document.addEventListener('htmx:afterSwap', (event) => {
    const target = event.detail?.target;
    if (target?.id === 'agent-select') {
        applyAgentFromCookie(target);
        enforceOfflineModelSelection(target, true);
    }
});

document.addEventListener('htmx:afterSettle', (event) => {
    const target = event.detail?.target;
    if (target?.id === 'history-list' || target?.id === 'chat-stream') {
        renderAssistantMarkdown(target);
    }
});

function setButtonLoading(button, active) {
    if (!button) return;
    button.classList.toggle('loading', Boolean(active));
    if (active) {
        button.setAttribute('aria-busy', 'true');
    } else {
        button.removeAttribute('aria-busy');
    }
}

function showToast(message, variant = 'success') {
    const container = document.getElementById('toast-container');
    if (!container) return;
    const toast = document.createElement('div');
    toast.className = `toast ${variant}`;
    toast.textContent = message;
    container.appendChild(toast);
    window.setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateY(6px)';
        window.setTimeout(() => toast.remove(), 200);
    }, 2200);
}

// --- Export Helper ---
async function exportChat(button) {
    setButtonLoading(button, true);
    try {
        const response = await fetch('/api/export', { method: 'GET' });
        const payload = await response.json();
        if (!response.ok) {
            throw new Error(payload?.detail || 'Export failed');
        }
        const entity = payload?.entity || 'chat';
        const stamp = new Date().toISOString().replace(/[:.]/g, '-');
        const filename = `${entity}-export-${stamp}.json`;
        const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(url);
        showToast('Export ready');
    } catch (error) {
        console.error('Export chat failed', error);
        showToast(error.message || 'Export chat failed', 'error');
    } finally {
        setButtonLoading(button, false);
    }
}


document.addEventListener('htmx:afterRequest', (event) => {
    const target = event.detail?.target;
    const elt = event.detail?.elt;
    if (target?.id === 'chat-stream') {
        target.dataset.historyLoaded = 'true';
        if (!overlayStreamingActive) {
            setLoadingOverlay(false);
        }
        const input = document.getElementById('cmd-input');
        if (!input) return;
        input.value = '';
        adjustInputHeight(input);
        if (window?.dsChatStreamEnabled === true) {
            disableHtmxChatStream();
        }
        return;
    }
    if (target?.id === 'history-list' && elt?.id === 'history-loader') {
        const chatStream = document.getElementById('chat-stream');
        if (chatStream) {
            chatStream.dataset.historyLoaded = 'true';
        }
        setHistoryLoading(false);
        historyLoading = false;
        return;
    }
    if (target?.id === 'history-list') {
        const chatStream = document.getElementById('chat-stream');
        if (chatStream) {
            chatStream.dataset.historyLoaded = 'true';
        }
        setHistoryLoading(false);
        historyLoading = false;
    }
});


document.addEventListener('htmx:swapError', () => {
    historyLoading = false;
});

document.addEventListener('htmx:responseError', () => {
    historyLoading = false;
});

document.addEventListener('htmx:beforeRequest', (event) => {
    const elt = event.detail?.elt;
    if (elt?.id === 'sync-ledgers-btn') {
        setButtonLoading(elt, true);
    }
});

document.addEventListener('htmx:afterRequest', (event) => {
    const elt = event.detail?.elt;
    if (elt?.id === 'sync-ledgers-btn') {
        setButtonLoading(elt, false);
        const xhr = event.detail?.xhr;
        let message = event.detail?.successful ? 'Manual sync completed' : 'Manual sync failed';
        let variant = event.detail?.successful ? 'success' : 'error';
        if (xhr?.responseText) {
            try {
                const parsed = JSON.parse(xhr.responseText);
                if (parsed?.message) message = String(parsed.message);
                if (parsed?.status === 'error') variant = 'error';
            } catch (error) {
                // no-op
            }
        }
        showToast(message, variant);
    }
});

document.addEventListener('htmx:beforeRequest', (event) => {
    const target = event.detail?.target;
    const elt = event.detail?.elt;
    if (!target) return;
    if (target.id === 'chat-stream') {
        setLoadingOverlay(true, 'turn');
        return;
    }
    if (target.id === 'history-list') {
        historyLoading = true;
        setHistoryLoading(true);
    }
});

document.addEventListener('htmx:requestError', (event) => {
    const target = event.detail?.target;
    if (!target) return;
    if (target.id === 'chat-stream') {
        setLoadingOverlay(false);
        return;
    }
    if (target.id === 'history-list') {
        setHistoryLoading(false);
        historyLoading = false;
    }
});

document.addEventListener('htmx:responseError', (event) => {
    const target = event.detail?.target;
    if (!target) return;
    if (target.id === 'chat-stream') {
        setLoadingOverlay(false);
        return;
    }
    if (target.id === 'history-list') {
        setHistoryLoading(false);
        historyLoading = false;
    }
});

document.addEventListener('htmx:timeout', (event) => {
    const target = event.detail?.target;
    if (!target) return;
    if (target.id === 'chat-stream') {
        setLoadingOverlay(false);
        return;
    }
    if (target.id === 'history-list') {
        setHistoryLoading(false);
        historyLoading = false;
    }
});

window.addEventListener('load', () => {
    initThreadlessInput();
    updateStickyStack();
    maybeDisableHtmxForStream();
    document.getElementById('cmd-input')?.dispatchEvent(new Event('input'));
    initializeUserPromptTruncation(document);
    renderAssistantMarkdown();
    initMarkdownObserver();
    loadLedgerFoundingPurpose();
});

window.addEventListener('scroll', handleHistoryScroll, { passive: true });
window.addEventListener('resize', updateStickyStack);
document.addEventListener('DOMContentLoaded', updateStickyStack);

(function initSharedSessionActivityRefresh() {
    const refreshPath = window.dsSessionRefreshPath || '/api/auth/session/refresh';
    const minIntervalMs = 5 * 60 * 1000;
    let lastRefreshAt = 0;
    let inFlight = false;

    const hasSessionCookie = () => (
        document.cookie.includes('ds_backend_refresh_token=')
        || document.cookie.includes('ds_backend_session_token=')
    );

    const maybeRefresh = async () => {
        if (!hasSessionCookie()) return;
        if (document.visibilityState === 'hidden') return;
        const now = Date.now();
        if (inFlight || (now - lastRefreshAt) < minIntervalMs) return;
        inFlight = true;
        try {
            const response = await fetch(refreshPath, {
                method: 'POST',
                credentials: 'same-origin',
                headers: { accept: 'application/json' },
            });
            if (response.ok) {
                lastRefreshAt = now;
                return;
            }
            if (response.status === 401) {
                let loginUrl = '';
                try {
                    const payload = await response.json();
                    loginUrl = typeof payload?.login_url === 'string' ? payload.login_url.trim() : '';
                } catch (_error) {
                    // no-op
                }
                redirectToControlPlaneLogin(loginUrl);
            }
        } catch (_error) {
            // no-op
        } finally {
            inFlight = false;
        }
    };

    ['pointerdown', 'keydown', 'submit'].forEach((eventName) => {
        document.addEventListener(eventName, () => {
            void maybeRefresh();
        }, true);
    });

    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') {
            void maybeRefresh();
        }
    });
})();
