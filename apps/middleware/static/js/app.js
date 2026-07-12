/**
 * ourIP.AI Frontend JavaScript (Threadless Edition)
 * Handles chat helpers, Settings Panel, Stats, and Ledger Management.
 */

// DSS-245: BigInt-safe coordinate fields.
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

// --- UI Interactivity (Settings Panel) ---

const threadlessMetrics = {
    isGhostState: false,
    sessionCost: 0,
    totalLatency: 0,
    requestCount: 0,
};

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

const OVERLAY_MIN_MS = 300;
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
const overlayStatusQueue = [];
const overlayStatusSeen = new Set();
const TIMING_ENABLED = Boolean(window?.dsTimingDebug);
const SERVER_LOG_ENABLED = Boolean(window?.dsServerLog);
const RESOLVE_DEBUG_ENABLED = (() => {
    try {
        const params = new URLSearchParams(window.location.search);
        const value = (params.get('debug') || '').trim().toLowerCase();
        return ['1', 'true', 'yes', 'on'].includes(value);
    } catch (error) {
        return false;
    }
})();
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
    if (!overlayStatusQueueTimer) {
        overlayStatusQueueIndex = 0;
        updateLoadingStatus(overlayStatusQueue[0], true);
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
    if (!timing && !coords) return;
    const payload = {
        timing_ms: timing,
        coord_counts: coords,
        coordinate: meta.coordinate || meta.web4_key,
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

function setLoadingOverlay(active) {
    const overlay = document.getElementById('loading-overlay');
    if (!overlay) return;
    const nextState = Boolean(active);

    if (nextState) {
        if (overlayHideTimer) {
            clearTimeout(overlayHideTimer);
            overlayHideTimer = null;
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
        overlayHideTimer = null;
    }, remaining);
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
        const content = message.querySelector('.message-content');
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
    const historyList = document.getElementById('history-list');
    if (historyList) {
        if (historyList.contains(node)) return;
        historyList.prepend(node);
        return;
    }
    const chatStream = document.getElementById('chat-stream');
    if (!chatStream) return;
    if (chatStream.contains(node)) return;
    chatStream.prepend(node);
}

function _createUserBubble(text, msgId) {
    const wrapper = document.createElement('div');
    wrapper.className = 'message user';
    wrapper.id = `msg-user-${msgId}`;
    const content = document.createElement('div');
    content.className = 'message-content';
    content.textContent = text;
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

    const content = document.createElement('div');
    content.className = 'prose prose-xl prose-p:font-serif prose-headings:font-serif markdown-content max-w-none text-gray-900 leading-loose';
    content.dataset.markdown = 'true';
    content.textContent = '';

    contentWrap.appendChild(content);
    bubble.appendChild(contentWrap);

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
    const modelText = document.createElement('span');
    modelText.className = 'meta-model';
    modelText.textContent = '';
    meta.appendChild(modelText);

    bubble.appendChild(meta);
    container.appendChild(bubble);

    return { container, content, coord, metaText, modelText, walkText };
}

async function readStream(response, onEvent) {
    if (!response.ok || !response.body) {
        const detail = await response.text();
        throw new Error(detail || `Stream failed (${response.status})`);
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
    const userNode = _createUserBubble(message, msgId);
    const assistant = _createAssistantBubble(msgId + 1);
    turn.appendChild(userNode);
    turn.appendChild(assistant.container);
    _prependChatNode(turn);

    input.value = '';
    adjustInputHeight(input);
    overlayManualStatus = false;
    overlayStreamingActive = false;
    setLoadingOverlay(true);
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
    const timingStart = performance.now();
    const STREAM_RENDER_INTERVAL = 250;

    const renderStreamingMarkdown = () => {
        if (fullReply === lastRenderedReply) {
            return;
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
        window.setTimeout(() => {
            unwrapStreamingTail(assistant.content);
            assistant.content.classList.remove('streaming-complete');
        }, 500);
    };

    try {
        const sessionId = _getCookieValue('ds_session');
        const provider = document.getElementById('agent-select')?.value || '';
        const response = await fetch('/api/chat/smart_stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message,
                provider,
                agent: provider,
                model: provider,
                session_id: sessionId || undefined,
                enable_ledger: true,
                backend_stream: resolveBackendStreamFlag(),
                history: currentHistory,
                attachments: uniqueAttachmentCoordinates,
                context_coords: uniqueAttachmentCoordinates,
                time_range: timeRange || undefined,
            }),
        });
        let hasFirstToken = false;

        await readStream(response, (payload) => {
            stopPreStreamTicker();
            if (payload.type === 'status') {
                const statusMessage = payload.message ? String(payload.message) : '';
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

            if (payload.type === 'context_meta') {
                if (Array.isArray(payload.queued_coords)) {
                    payload.queued_coords.forEach((coord) => {
                        enqueueOverlayStatus(`Resolving Coords: ${coord}`);
                    });
                }
                return;
            }

            if (payload.type === 'context_item') {
                if (payload.coord) {
                    enqueueOverlayStatus(`Resolving Coords: ${payload.coord}`);
                }
                return;
            }

            if (payload.type === 'hop_enrich') {
                const hopPayload = payload.payload || {};
                const hop = Number.isFinite(Number(hopPayload.hop)) ? Number(hopPayload.hop) : 0;
                const skim = hopPayload.skim ? String(hopPayload.skim) : '';
                if (skim) {
                    enqueueOverlayStatus(`Hop ${hop + 1}: ${skim}`);
                }
                return;
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
                    enqueueOverlayStatus(summary);
                } else if (skipped) {
                    enqueueOverlayStatus(`Hop ${hop + 1} → no choice (${reason || 'skipped'})`);
                }
                return;
            }

            if (payload.type === 'guardian_note') {
                if (payload.message) {
                    enqueueOverlayStatus(`Guardian: ${payload.message}`);
                }
                return;
            }

            if (payload.type === 'token' && payload.content) {
                const isFirstToken = !hasFirstToken;
                if (isFirstToken) {
                    setLoadingOverlay(false);
                    hasFirstToken = true;
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
                logTiming('meta_received', timingStart, {
                    coordinate: payload.coordinate || payload.web4_key,
                });
                if (Array.isArray(payload.resolved_coords) && payload.resolved_coords.length) {
                    payload.resolved_coords.slice(0, 6).forEach((coord) => {
                        enqueueOverlayStatus(`Resolved: ${coord}`);
                    });
                }
                const coordinate = payload.coordinate || payload.web4_key || '—';
                resetOverlayStatusQueue();
                assistant.coord.textContent = coordinate;
                assistant.coord.dataset.coordinate = coordinate;
                assistant.metaText.textContent = `${formatMetaTimestamp(new Date())} | `;
                const blocked = payload?.blocked === true
                    || (payload.audit_mode && payload.audit_mode.blocked === true);
                if (blocked) {
                    const reason = payload?.audit_mode?.reason || 'blocked';
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
                if (payload.appraisal && typeof payload.appraisal === 'object') {
                    window.dsAgentFeedback = payload.appraisal;
                    updateAppraisalPanel(payload.appraisal);
                    logTiming('appraisal_received', timingStart, payload.appraisal);
                }
                updateResolveDebugPanel(payload);
                logResolveMeta(payload);
                if (Number.isFinite(Number(payload.latency_ms))) {
                    latencyMs = Number(payload.latency_ms);
                    performanceState.latencyMs = latencyMs;
                }
                const promptTokens = Number(payload.gen_input_tokens);
                const completionTokens = Number(payload.gen_output_tokens);
                const totalTokens =
                    (Number.isFinite(promptTokens) ? promptTokens : 0)
                    + (Number.isFinite(completionTokens) ? completionTokens : 0);
                if (Number.isFinite(totalTokens) && Number.isFinite(latencyMs) && latencyMs > 0 && totalTokens > 0) {
                    performanceState.tokensPerSecond = (totalTokens / latencyMs) * 1000.0;
                }
                if (Number.isFinite(Number(payload.session_cost_usd))) {
                    updateSessionCost(Number(payload.session_cost_usd));
                }
                updatePerformancePanel();
                updateLoadingStatus('Complete.');
            }
        });

        const cleanedReply = stripTrailingJsonMetadata(fullReply);
        if (cleanedReply !== fullReply) {
            fullReply = cleanedReply;
        }
        if (fullReply.trim()) {
            finalizeStreamRender();
        } else {
            assistant.content.classList.remove('streaming-active', 'streaming-complete');
            assistant.content.textContent = '';
            renderAssistantMarkdown(assistant.container);
        }
        refreshPanelStats();
    } catch (error) {
        console.error('Streaming chat failed', error);
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
    } finally {
        overlayStreamingActive = false;
        overlayManualStatus = false;
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
    initBackendStreamToggle();
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

    const streamModeEl = document.getElementById('panel-stream-mode');
    if (streamModeEl) {
        const override = resolveBackendStreamFlag();
        streamModeEl.textContent = override ? 'on' : 'off';
    }
    const walkDebugEl = document.getElementById('panel-walk-debug');
    if (walkDebugEl) {
        const debug = meta?.walk_debug;
        if (debug && typeof debug === 'object') {
            const queued = Number.isFinite(Number(debug.queued)) ? Number(debug.queued) : 0;
            const resolved = Number.isFinite(Number(debug.resolved)) ? Number(debug.resolved) : 0;
            const triggered = debug.walk_triggered ? 'yes' : 'no';
            walkDebugEl.textContent = `${triggered} (${resolved}/${queued})`;
        } else {
            walkDebugEl.textContent = '—';
        }
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

    const govMetrics = meta?.introspect_snapshot_post?.governance?.metrics;
    if (govMetrics && typeof govMetrics === 'object') {
        const fieldMap = {
            L: 'panel-gov-L',
            H: 'panel-gov-H',
            U: 'panel-gov-U',
            V: 'panel-gov-V',
            I1: 'panel-gov-I1',
            I2: 'panel-gov-I2',
            dW: 'panel-gov-dW',
        };
        Object.entries(fieldMap).forEach(([key, id]) => {
            const node = document.getElementById(id);
            if (!node) return;
            const value = govMetrics[key];
            if (value === null || value === undefined || Number.isNaN(Number(value))) {
                node.textContent = '—';
                return;
            }
            const num = Number(value);
            node.textContent = Number.isFinite(num) ? num.toFixed(3) : String(value);
        });
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
    const entity =
        loader.dataset.historyEntity
        || document.getElementById('entity-id')?.value?.trim()
        || document.getElementById('session-id')?.value?.trim()
        || getCookieValue('ds_session')
        || 'demo';
    historyLoading = true;
    loader.dataset.historyLimit = String(nextLimit);
    if (window.htmx) {
        const encodedEntity = encodeURIComponent(entity);
        window.htmx.ajax('GET', `/ui/history/${encodedEntity}?limit=${nextLimit}`, {
            target: '#history-list',
            swap: 'outerHTML',
        });
    } else {
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
        event.target.form?.requestSubmit();
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

        // Refresh stats whenever the panel opens
            // no-op: stats only refresh on turn completion
        scheduleBillingSnapshot(200, true);
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

// --- Billing Snapshot (Total Cost + Session Cost) ---

const billingState = {
    loaded: false,
    totalCost: null,
    remainingCost: null,
    sessionCost: 0,
};
const performanceState = {
    latencyMs: null,
    tokensPerSecond: null,
    resolvedPerTurn: null,
};

const formatCurrency = (amount = 0, decimals = 2) => `$${(Number(amount) || 0).toFixed(decimals)}`;

function refreshCostUI() {
    const totalEl = document.getElementById('panel-total-cost');
    const sessionEl = document.getElementById('panel-session-cost');
    const sessionSpend = Number.isFinite(Number(billingState.sessionCost))
        ? Number(billingState.sessionCost)
        : 0;
    if (totalEl) totalEl.textContent = formatCurrency(sessionSpend, 4);
    if (sessionEl) sessionEl.textContent = formatCurrency(sessionSpend, 4);
}

function updateSessionCost(amount) {
    if (typeof amount === 'number' && !Number.isNaN(amount)) {
        billingState.sessionCost = amount;

        if (typeof billingState.totalCost === 'number') {
            billingState.remainingCost = Math.max(billingState.totalCost - amount, 0);
        }

        refreshCostUI();
    }
}

function updatePerformancePanel() {
    const el = document.getElementById('panel-performance');
    if (!el) return;
    const parts = [];
    if (Number.isFinite(Number(performanceState.latencyMs)) && Number(performanceState.latencyMs) > 0) {
        parts.push(`${Math.round(Number(performanceState.latencyMs))} ms`);
    }
    if (Number.isFinite(Number(performanceState.tokensPerSecond)) && Number(performanceState.tokensPerSecond) > 0) {
        parts.push(`${Number(performanceState.tokensPerSecond).toFixed(1)} tok/s`);
    }
    if (Number.isFinite(Number(performanceState.resolvedPerTurn)) && Number(performanceState.resolvedPerTurn) >= 0) {
        parts.push(`${Number(performanceState.resolvedPerTurn).toFixed(1)} res/turn`);
    }
    el.textContent = parts.length ? parts.join(' | ') : '—';
}

const STATS_BASELINE = {
    retrievalRate: 1,
    chatUnitCost: 0.0001,
    memoryUnitCost: 0.0,
};

async function fetchStatsWithFallback(primaryUrl, fallbackUrl) {
    const primaryResponse = await fetch(primaryUrl);
    if (primaryResponse.ok) {
        return primaryResponse;
    }
    if (primaryResponse.status === 404 && fallbackUrl) {
        const fallbackResponse = await fetch(fallbackUrl);
        return fallbackResponse;
    }
    return primaryResponse;
}

function coerceNumber(value) {
    if (typeof value === 'number' && Number.isFinite(value)) return value;
    if (typeof value === 'string' && value.trim() !== '') {
        const parsed = Number(value);
        return Number.isFinite(parsed) ? parsed : null;
    }
    return null;
}

function normalizeStats(stats) {
    if (!stats || typeof stats !== 'object') return null;
    const metrics = stats.metrics || {};
    const totals = stats.totals || {};
    const retrievalRate = coerceNumber(
        stats.retrieval_rate
        ?? stats.retrievalRate
        ?? metrics.verifiable_response_rate
        ?? metrics.verifiableResponseRate
        ?? metrics.resolve_success_rate
        ?? metrics.resolveSuccessRate
    );
    const accuracyNumerator = coerceNumber(stats.accuracy_numerator ?? stats.accuracyNumerator);
    const accuracyDenominator = coerceNumber(stats.accuracy_denominator ?? stats.accuracyDenominator);
    const events = coerceNumber(stats.event_count ?? stats.events ?? totals.events);
    const totalCost = coerceNumber(totals.cost ?? stats.total_cost ?? stats.totalCost);
    const chatUnitCostCents = coerceNumber(
        stats.chat_cost_per_turn_cents
        ?? stats.chatCostPerTurnCents
        ?? metrics.chat_cost_per_turn_cents
        ?? metrics.chatCostPerTurnCents
    );
    const chatUnitCost = Number.isFinite(chatUnitCostCents) ? chatUnitCostCents / 100 : coerceNumber(
        stats.chat_unit_cost ?? stats.chatUnitCost
    );
    const chatTurns = coerceNumber(totals.chat_turns ?? totals.chatTurns);
    const chatCostTotal = coerceNumber(totals.chat_cost ?? totals.chatCost);
    const derivedChatUnitCost =
        Number.isFinite(chatCostTotal) && Number.isFinite(chatTurns) && chatTurns > 0
            ? chatCostTotal / chatTurns
            : (Number.isFinite(totalCost) && Number.isFinite(events) && events > 0
                ? totalCost / events
                : null);
    const memoryUnitCost = coerceNumber(
        stats.memory_unit_cost
        ?? stats.memoryUnitCost
        ?? metrics.chat_cost_per_1m_tokens
        ?? metrics.chatCostPer1mTokens
        ?? metrics.memory_cost_per_1m_tokens
        ?? metrics.memoryCostPer1mTokens
        ?? metrics.memory_cost_per_10k_words
        ?? metrics.memoryCostPer10kWords
    );
    const resolvedPerTurn = coerceNumber(
        stats.resolved_per_turn
        ?? stats.resolvedPerTurn
        ?? stats.resolved_coords_per_turn
        ?? stats.resolvedCoordsPerTurn
        ?? metrics.resolved_per_turn
        ?? metrics.resolvedPerTurn
        ?? metrics.resolved_coords_per_turn
        ?? metrics.resolvedCoordsPerTurn
    );

    return {
        retrievalRate,
        accuracyNumerator,
        accuracyDenominator,
        events,
        chatUnitCost: Number.isFinite(chatUnitCost) ? chatUnitCost : derivedChatUnitCost,
        memoryUnitCost,
        resolvedPerTurn,
    };
}

function buildPanelStats(sessionStats, globalStats) {
    const session = normalizeStats(sessionStats);
    const global = normalizeStats(globalStats);
    const pick = (field, fallback = null) => {
        const sessionValue = session ? session[field] : null;
        if (Number.isFinite(sessionValue)) return sessionValue;
        const globalValue = global ? global[field] : null;
        if (Number.isFinite(globalValue)) return globalValue;
        return fallback;
    };
    const sessionHasData = session && (
        (Number.isFinite(session.events) && session.events > 0)
        || Number.isFinite(session.retrievalRate)
        || Number.isFinite(session.chatUnitCost)
        || Number.isFinite(session.memoryUnitCost)
    );
    const globalHasData = global && (
        (Number.isFinite(global.events) && global.events > 0)
        || Number.isFinite(global.retrievalRate)
        || Number.isFinite(global.chatUnitCost)
        || Number.isFinite(global.memoryUnitCost)
    );
    const source = sessionHasData ? session : (globalHasData ? global : null);

    return {
        retrievalRate: pick('retrievalRate', STATS_BASELINE.retrievalRate),
        chatUnitCost: pick('chatUnitCost', STATS_BASELINE.chatUnitCost),
        memoryUnitCost: pick('memoryUnitCost', STATS_BASELINE.memoryUnitCost),
        resolvedPerTurn: pick('resolvedPerTurn', 0),
        accuracyNumerator: Number.isFinite(source?.accuracyNumerator) ? source.accuracyNumerator : null,
        accuracyDenominator: Number.isFinite(source?.accuracyDenominator) ? source.accuracyDenominator : null,
    };
}

async function loadSessionStats() {
    let sessionData = null;
    let globalData = null;
    try {
        const sessionResult = await fetchStatsWithFallback('/api/stats', '/stats');
        if (sessionResult.ok) {
            const text = await sessionResult.text();
            sessionData = text ? JSON.parse(text) : {};
        } else {
            console.warn(`Session stats fetch failed (${sessionResult.status})`);
        }
    } catch (err) {
        console.error('Unable to load session stats', err);
    }
    try {
        const globalResult = await fetchStatsWithFallback('/api/stats/global', '/stats/global');
        if (globalResult.ok) {
            const text = await globalResult.text();
            if (text) {
                globalData = JSON.parse(text);
            } else {
                globalData = {};
            }
        } else {
            console.warn(`Global stats fetch failed (${globalResult.status})`);
        }
    } catch (err) {
        console.error('Unable to load global stats', err);
    }
    const panelStats = buildPanelStats(sessionData, globalData);
    updateSessionPanelStats(panelStats);
    performanceState.resolvedPerTurn = Number.isFinite(panelStats?.resolvedPerTurn)
        ? Number(panelStats.resolvedPerTurn)
        : null;
    updatePerformancePanel();
}

let billingSnapshotTimer = null;
let billingSnapshotForce = false;

function scheduleBillingSnapshot(delay = 300, force = false) {
    billingSnapshotForce = billingSnapshotForce || force;
    if (billingSnapshotTimer !== null) return;
    billingSnapshotTimer = window.setTimeout(() => {
        const shouldForce = billingSnapshotForce;
        billingSnapshotTimer = null;
        billingSnapshotForce = false;
        loadBillingSnapshot(shouldForce);
    }, delay);
}

async function uploadAttachment(input) {
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
    setLoadingOverlay(true);
    updateLoadingStatus('Uploading...', true);
    const localHash = await computeFileHash(file).catch(() => '');
    if (localHash) {
        const cached = getCachedAttachmentCoord(localHash);
        if (cached) {
            setAttachmentCoordinate(cached);
            updateStickyStack();
            updateLoadingStatus('Attachment already uploaded.', true);
            overlayManualStatus = false;
            setLoadingOverlay(false);
            input.value = '';
            return;
        }
    }
    const payload = new FormData();
    payload.append('file', file);
    payload.append('kind', 'attachment');
    const sessionEntity =
        document.getElementById('entity-id')?.value?.trim()
        || document.getElementById('session-id')?.value?.trim()
        || getCookieValue('ds_session')
        || 'default';
    const entity = sessionEntity;
    payload.append('entity', entity);

    try {
        const rawApiBase = typeof window !== 'undefined' ? String(window.dsApiBase || '') : '';
        const apiBase = rawApiBase.replace(/\/+$/, '');
        const ingestUrl = apiBase ? `${apiBase}/api/ingest/stream-file` : '/api/ingest/stream-file';
        const response = await fetch(ingestUrl, {
            method: 'POST',
            body: payload,
        });
        let coordinate = '';
        await readStream(response, (event) => {
            if (event.type === 'status' && event.message) {
                updateLoadingStatus(event.message, true);
                scheduleStickyUpdate();
                return;
            }
            if (event.type === 'error') {
                throw new Error(event.detail || 'Attachment upload failed');
            }
            if (event.type === 'meta') {
                coordinate = event.coordinate || '';
                if (coordinate && !String(coordinate).includes(':')) {
                    coordinate = `${entity}:ATT-${coordinate}`;
                }
                if (coordinate) {
                    setAttachmentCoordinate(coordinate);
                    if (localHash) {
                        cacheAttachmentCoord(localHash, coordinate);
                    }
                }
                scheduleStickyUpdate();
                updateLoadingStatus('Ingestion Complete.', true);
            }
        });
        if (!coordinate) {
            updateLoadingStatus('Attachment ingested without coordinate.', true);
        }
    } catch (error) {
        console.error('Attachment upload failed', error);
        alert(error.message || 'Attachment upload failed');
    } finally {
        input.value = '';
        overlayManualStatus = false;
        setLoadingOverlay(false);
    }
}

async function computeFileHash(file) {
    if (!window.crypto?.subtle) return '';
    const buffer = await file.arrayBuffer();
    const digest = await window.crypto.subtle.digest('SHA-256', buffer);
    const hashArray = Array.from(new Uint8Array(digest));
    return hashArray.map((b) => b.toString(16).padStart(2, '0')).join('');
}

function getCachedAttachmentCoord(hash) {
    try {
        const raw = sessionStorage.getItem('ds-attachment-cache');
        const cache = raw ? JSON.parse(raw) : {};
        return cache[hash] || '';
    } catch (error) {
        return '';
    }
}

function cacheAttachmentCoord(hash, coord) {
    try {
        const raw = sessionStorage.getItem('ds-attachment-cache');
        const cache = raw ? JSON.parse(raw) : {};
        cache[hash] = coord;
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

async function loadBillingSnapshot(force = false) {
    if (billingState.loaded && !force) {
        refreshCostUI();
        return;
    }

    try {
        const response = await fetch('/api/costs');
        const data = await response.json();

        if (!response.ok) {
            throw new Error(data?.detail || 'Failed to fetch costs');
        }

        const billing = (data && typeof data === 'object' ? data.billing : null) || {};
        const credits = (billing && typeof billing === 'object' && billing.credits && typeof billing.credits === 'object')
            ? billing.credits
            : {};

        const coerceNumber = (value) => {
            const num = Number(value);
            return Number.isFinite(num) ? num : null;
        };

        const pickNumber = (source, keys) => {
            if (!source || typeof source !== 'object') return null;
            for (const key of keys) {
                const candidate = coerceNumber(source[key]);
                if (candidate !== null) return candidate;
            }
            return null;
        };

        const totalFromResponse = coerceNumber(data.total_cost)
            ?? pickNumber(credits, ['total', 'total_usd', 'credits_total', 'usd_total', 'balance_total'])
            ?? pickNumber(billing, ['balance', 'balance_usd']);

        const remainingFromResponse = coerceNumber(data.remaining_cost)
            ?? pickNumber(credits, ['remaining', 'available', 'balance', 'usd', 'usd_cents', 'credits_remaining'])
            ?? pickNumber(billing, ['available', 'balance']);

        if (typeof data.session_cost === 'number') {
            billingState.sessionCost = data.session_cost;
        }

        if (totalFromResponse !== null) {
            billingState.totalCost = totalFromResponse;
        }

        if (remainingFromResponse !== null) {
            billingState.remainingCost = remainingFromResponse;
        } else if (billingState.totalCost !== null && typeof billingState.sessionCost === 'number') {
            billingState.remainingCost = Math.max(billingState.totalCost - billingState.sessionCost, 0);
        }

        billingState.loaded = true;
        refreshCostUI();
    } catch (error) {
        console.error('Unable to load billing snapshot', error);
    }
}

let activeLedgerState = window.dsActiveLedger || '';

function getActiveLedger() {
    return activeLedgerState || '';
}

// --- Agent Management Helpers ---
function toggleAgentAddGroup(show) {
    const addGroup = document.getElementById('agent-add-group');
    if (addGroup) {
        show ? addGroup.classList.remove('hidden') : addGroup.classList.add('hidden');
    }
}

async function populateAgentAddDropdown() {
    const select = document.getElementById('agent-add-select');
    if (!select) return;

    select.innerHTML = '';

    try {
        const response = await fetch('/api/models?mode=full', {
            headers: { Accept: 'application/json' },
        });

        if (!response.ok) throw new Error('Failed to load agents');

        const data = await response.json();
        const models = data.models || [];

        models.forEach(({ id, name }) => {
            const option = document.createElement('option');
            option.value = id;
            option.textContent = name || id;
            select.appendChild(option);
        });
    } catch (error) {
        console.error('Failed to load agents', error);
        const fallback = document.createElement('option');
        fallback.value = '';
        fallback.textContent = 'Unable to load agents';
        select.appendChild(fallback);
    }
}

async function addAgentFromSelect(event) {
    event?.preventDefault();
    const select = document.getElementById('agent-add-select');
    const agentSelect = document.getElementById('agent-select');
    if (!select || !agentSelect) return;

    const newAgent = select.value;
    const newAgentLabel = select.options[select.selectedIndex]?.textContent || newAgent;

    if (!newAgent) {
        alert('Please choose an agent to add.');
        return;
    }

    const formData = new FormData();
    formData.append('agent', newAgent);

    try {
        const response = await fetch('/api/set-agent', {
            method: 'POST',
            body: formData,
        });

        if (!response.ok) throw new Error('Failed to add agent');

        const existingOption = Array.from(agentSelect.options).find((opt) => opt.value === newAgent);
        if (!existingOption) {
            const option = document.createElement('option');
            option.value = newAgent;
            option.textContent = newAgentLabel;
            agentSelect.insertBefore(option, agentSelect.lastElementChild);
        }

        agentSelect.value = newAgent;
        setCookieValue('ds_agent', newAgent);
        toggleAgentAddGroup(false);
    } catch (error) {
        alert(error.message || 'Unable to add agent.');
    }
}

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
    const savedAgent = _getCookieValue('ds_agent');
    if (!savedAgent) return;

    const existingOption = Array.from(agentSelect.options).find((opt) => opt.value === savedAgent);
    if (!existingOption) {
        const savedOption = document.createElement('option');
        savedOption.value = savedAgent;
        savedOption.textContent = `${savedAgent} (Saved)`;
        const addNewOption = Array.from(agentSelect.options).find((opt) => opt.value === 'add_new');
        if (addNewOption) {
            agentSelect.insertBefore(savedOption, addNewOption);
        } else {
            agentSelect.appendChild(savedOption);
        }
    }

    agentSelect.value = savedAgent;
    postAgentSelection(savedAgent).catch((error) => {
        console.error('Unable to restore agent', error);
    });
}

// Ledger Event Listeners
document.addEventListener('DOMContentLoaded', () => {
    maybeDisableHtmxForStream();
    ensureChatStreamPlacement();
    initStreamedChat();
    initAttachmentObserver();
    const toastMessage = sessionStorage.getItem('dsToastMessage');
    if (toastMessage) {
        sessionStorage.removeItem('dsToastMessage');
        showToast(toastMessage);
    }
    updateSessionPanelStats(buildPanelStats(null, null));
    loadSessionStats();

    const agentSelect = document.getElementById('agent-select');
    if (agentSelect) {
        applyAgentFromCookie(agentSelect);
        agentSelect.addEventListener('change', async (event) => {
            const value = event.target.value;
            if (value === 'add_new') {
                toggleAgentAddGroup(true);
                populateAgentAddDropdown();
            } else {
                toggleAgentAddGroup(false);
                if (!value) return;
                try {
                    await postAgentSelection(value);
                    setCookieValue('ds_agent', value);
                } catch (error) {
                    console.error('Unable to set agent', error);
                }
            }
        });
    }

    // Stats refresh happens after turn completion or panel open.
});

document.addEventListener('htmx:afterSwap', (event) => {
    const target = event.detail?.target;
    if (target?.id === 'agent-select') {
        applyAgentFromCookie(target);
    }
});

document.addEventListener('htmx:afterSettle', (event) => {
    const target = event.detail?.target;
    if (target?.id === 'history-list' || target?.id === 'chat-stream') {
        renderAssistantMarkdown(target);
    }
});

function updateSessionPanelStats(stats) {
    if (!stats || typeof stats !== 'object') return;

    const setText = (id, value) => {
        const el = document.getElementById(id);
        if (el) el.textContent = value;
    };

    const resolvedPerTurn = Number.isFinite(stats.resolvedPerTurn)
        ? stats.resolvedPerTurn
        : 0;
    const resolvedDisplay = `${resolvedPerTurn.toFixed(1)} / turn`;
    setText('panel-accuracy-rate', resolvedDisplay);

    const chatCost = Number.isFinite(stats.chatUnitCost) ? stats.chatUnitCost : 0;
    const cents = chatCost * 100;
    const costString = `${cents.toFixed(2)} cents`;
    setText('panel-chat-unit-cost', costString);

    const memoryCost = Number.isFinite(stats.memoryUnitCost) ? stats.memoryUnitCost : 0;
    setText('panel-memory-cost', `$${memoryCost.toFixed(3)}`);
}



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

let sessionStatsRefreshTimer = null;

function scheduleSessionStatsRefresh(delay = 500) {
    if (sessionStatsRefreshTimer !== null) return;
    sessionStatsRefreshTimer = window.setTimeout(() => {
        sessionStatsRefreshTimer = null;
        loadSessionStats();
    }, delay);
}

function refreshPanelStats() {
    scheduleSessionStatsRefresh(2000);
    scheduleBillingSnapshot(300, true);
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
        if (!overlayStreamingActive) {
            setLoadingOverlay(false);
        }
        historyLoading = false;
        return;
    }
    if (target?.id === 'history-list') {
        const chatStream = document.getElementById('chat-stream');
        if (chatStream) {
            chatStream.dataset.historyLoaded = 'true';
        }
        if (!overlayStreamingActive) {
            setLoadingOverlay(false);
        }
        historyLoading = false;
    }
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
        let message = 'Manual sync completed';
        if (xhr?.responseText) {
            try {
                const payload = JSON.parse(xhr.responseText);
                if (payload?.message) message = String(payload.message);
            } catch (err) {
                // ignore non-JSON payloads
            }
        }
        sessionStorage.setItem('dsToastMessage', message);
    }
});

document.addEventListener('htmx:beforeRequest', (event) => {
    const target = event.detail?.target;
    const elt = event.detail?.elt;
    if (!target) return;
    if (target.id === 'chat-stream') {
        setLoadingOverlay(true);
        return;
    }
    if (target.id === 'history-list') {
        historyLoading = true;
        if (!overlayStreamingActive) {
            setLoadingOverlay(true);
        }
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
        setLoadingOverlay(false);
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
        setLoadingOverlay(false);
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
        setLoadingOverlay(false);
        historyLoading = false;
    }
});

window.addEventListener('load', () => {
    initThreadlessInput();
    updateStickyStack();
    maybeDisableHtmxForStream();
    document.getElementById('cmd-input')?.dispatchEvent(new Event('input'));
    renderAssistantMarkdown();
    initMarkdownObserver();
});

window.addEventListener('scroll', handleHistoryScroll, { passive: true });
window.addEventListener('resize', updateStickyStack);
document.addEventListener('DOMContentLoaded', updateStickyStack);
