import json
import pathlib
import subprocess


APP_JS = pathlib.Path(__file__).resolve().parents[1] / "static" / "js" / "app.js"


def _extract_function_source(source: str, name: str) -> str:
    async_marker = f"async function {name}("
    marker = f"function {name}("
    prefix_start = source.find(async_marker)
    start = prefix_start
    if start < 0:
        start = source.find(marker)
        prefix_start = start
    if start < 0:
        raise AssertionError(f"Missing function {name} in app.js")
    paren_start = source.find("(", start)
    if paren_start < 0:
        raise AssertionError(f"Malformed function {name} in app.js")
    paren_depth = 0
    body_start = -1
    for idx in range(paren_start, len(source)):
        ch = source[idx]
        if ch == "(":
            paren_depth += 1
        elif ch == ")":
            paren_depth -= 1
            if paren_depth == 0:
                body_start = source.find("{", idx)
                break
    if body_start < 0:
        raise AssertionError(f"Malformed function body for {name} in app.js")
    depth = 0
    for idx in range(body_start, len(source)):
        ch = source[idx]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return source[prefix_start : idx + 1]
    raise AssertionError(f"Unterminated function {name} in app.js")


def _run_ticker_formatter(payload: dict) -> dict:
    source = APP_JS.read_text()
    functions = "\n\n".join(
        _extract_function_source(source, name)
        for name in (
            "renderStatusPayload",
            "renderUiStatusPayload",
            "summarizeCoordChainTrace",
            "collectPipelineTickerMessages",
            "buildMetaTickerFallback",
            "createThinkingTickerUpdater",
        )
    )
    script = f"""
{functions}
const payload = {json.dumps(payload)};
const result = collectPipelineTickerMessages(payload);
console.log(JSON.stringify(result));
"""
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout.strip())


def test_collect_pipeline_ticker_messages_for_context_meta_and_ui_status():
    result = _run_ticker_formatter(
        {
            "type": "context_meta",
            "queued_coords": ["chat-demo:WX-1"],
            "resolved_coords": ["chat-demo:WX-2"],
            "candidate_trace": [{"coord": "chat-demo:WX-3", "tier_rank": 2}],
            "autonomy_decision": {"action": "resolve", "reason": "top_candidate"},
            "coord_chain_trace": [
                {"coord": "chat-demo:WX-1", "planned": True, "opened": True, "admitted": True}
            ],
        }
    )

    thinking = result.get("thinking") or []
    overlay = result.get("overlay") or []
    assert "Queued: chat-demo:WX-1" in thinking
    assert "Resolved: chat-demo:WX-2" in thinking
    assert "Top candidate: chat-demo:WX-3 (R2)" in thinking
    assert "Autonomy: resolve (top_candidate)" in thinking
    assert "Coord chain: chat-demo:WX-1(planned/opened/admitted)" in thinking
    assert "Resolving Coords: chat-demo:WX-1" in overlay

    ui_result = _run_ticker_formatter(
        {
            "type": "ui_status",
            "payload": {
                "stage": "posture_backstop",
                "message": "Posture backstop: context pressure; preserving breadth",
            },
        }
    )
    assert "Posture backstop: context pressure; preserving breadth" in (ui_result.get("thinking") or [])


def test_collect_pipeline_ticker_messages_for_walk_posture_delta():
    result = _run_ticker_formatter(
        {
            "type": "walk_posture_delta",
            "payload": {
                "coord": "chat-demo:ATT-child-001",
                "reason": "posture_under_walk_risk",
                "under_walk_risk": True,
            },
        }
    )

    thinking = result.get("thinking") or []
    overlay = result.get("overlay") or []
    expected = "Eq9 posture · under-walk risk · chat-demo:ATT-child-001 · posture_under_walk_risk"
    assert expected in thinking
    assert expected in overlay


def test_collect_pipeline_ticker_messages_for_coord_decode_status():
    result = _run_ticker_formatter(
        {
            "type": "status",
            "message": "Resolving chat-demo:WX-9C2621E0-1773714156...",
        }
    )

    thinking = result.get("thinking") or []
    overlay = result.get("overlay") or []
    expected = "Decoding COORD: chat-demo:WX-9C2621E0-1773714156"
    assert expected in thinking
    assert expected in overlay


def test_frontend_ticker_text_updates_beyond_request_accepted():
    source = APP_JS.read_text()
    functions = "\n\n".join(
        _extract_function_source(source, name)
        for name in (
            "renderStatusPayload",
            "renderUiStatusPayload",
            "summarizeCoordChainTrace",
            "collectPipelineTickerMessages",
            "buildMetaTickerFallback",
            "createThinkingTickerUpdater",
        )
    )
    script = f"""
{functions}
const assistant = {{ ticker: {{ style: {{ display: 'none' }}, textContent: '' }} }};
let overlayStreamingActive = true;
const pushThinking = createThinkingTickerUpdater({{
  assistant,
  inlineTickerEnabled: true,
  overlayStreamingActiveRef: () => overlayStreamingActive,
  maxMessages: 8,
}});
  const payloads = [
  {{
    type: 'status',
    message: 'Resolving chat-demo:WX-9C2621E0-1773714156...',
  }},
  {{
    type: 'context_meta',
    queued_coords: ['chat-demo:WX-9C2621E0-1773574181'],
    candidate_trace: [{{ coord: 'chat-demo:WX-9C2621E0-1773574181', tier_rank: 3 }}],
    autonomy_decision: {{ action: 'resolve', reason: 'top_candidate' }},
  }},
  {{
    type: 'ui_status',
    payload: {{
      stage: 'posture_backstop',
      message: 'Posture backstop: context pressure; preserving breadth',
    }},
  }},
  {{
    type: 'walk_posture_delta',
    payload: {{
      coord: 'chat-demo:ATT-child-001',
      reason: 'posture_under_walk_risk',
      under_walk_risk: true,
    }},
  }},
];
for (const payload of payloads) {{
  const lines = collectPipelineTickerMessages(payload);
  for (const line of lines.thinking) pushThinking(line);
}}
console.log(JSON.stringify({{ display: assistant.ticker.style.display, text: assistant.ticker.textContent }}));
"""
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    parsed = json.loads(result.stdout.strip())
    text = str(parsed.get("text") or "")
    assert parsed.get("display") == ""
    assert text.startswith("Ticker: ")
    assert "Decoding COORD: chat-demo:WX-9C2621E0-1773714156" in text
    assert "Queued: chat-demo:WX-9C2621E0-1773574181" in text
    assert "Top candidate: chat-demo:WX-9C2621E0-1773574181 (R3)" in text
    assert "Posture backstop: context pressure; preserving breadth" in text
    assert "Eq9 posture · under-walk risk · chat-demo:ATT-child-001 · posture_under_walk_risk" in text


def test_frontend_ticker_preserves_coord_messages_under_message_pressure():
    source = APP_JS.read_text()
    functions = "\n\n".join(
        _extract_function_source(source, name)
        for name in (
            "createThinkingTickerUpdater",
        )
    )
    script = f"""
{functions}
const assistant = {{ ticker: {{ style: {{ display: 'none' }}, textContent: '' }} }};
let overlayStreamingActive = true;
const pushThinking = createThinkingTickerUpdater({{
  assistant,
  inlineTickerEnabled: true,
  overlayStreamingActiveRef: () => overlayStreamingActive,
  maxMessages: 8,
}});
[
  'Queued: chat-demo:ATT-757550a9-1773571355346',
  'Top candidate: chat-demo:WX-9C2621E0-1773726350 (R1)',
  'Resolver capability: available',
  'Resolve summary: 0/4',
  'Unresolved COORDs: 4',
  'Autonomy: reuse_path (max_utility:reuse_path)',
  'Thinking: assembling context',
  'Thinking: resolving coordinates',
  'Thinking: opening planned path',
  'Thinking: admission complete',
].forEach((line) => pushThinking(line));
console.log(JSON.stringify({{ display: assistant.ticker.style.display, text: assistant.ticker.textContent }}));
"""
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    parsed = json.loads(result.stdout.strip())
    text = str(parsed.get("text") or "")
    assert parsed.get("display") == ""
    assert "Queued: chat-demo:ATT-757550a9-1773571355346" in text
    assert "Top candidate: chat-demo:WX-9C2621E0-1773726350 (R1)" in text
    assert "Thinking: admission complete" in text


def test_build_upstream_loading_messages_for_initial_stream_plumbing():
    source = APP_JS.read_text()
    functions = "\n\n".join(
        _extract_function_source(source, name)
        for name in (
            "buildUpstreamLoadingMessages",
        )
    )
    script = f"""
{functions}
const requestPayload = {{
  entity: 'chat-demo',
  provider: 'google/gemini-2.5-flash',
  history: [{{ role: 'user' }}, {{ role: 'assistant' }}],
  attachments: [],
}};
const headers = new Map([['x-ds-upstream-url', 'https://middleware.example.internal/orchestrator/chat/stream']]);
const response = {{
  headers: {{
    get(name) {{
      return headers.get(name) || '';
    }},
  }},
}};
console.log(JSON.stringify({{
  initial: buildUpstreamLoadingMessages(requestPayload),
  connected: buildUpstreamLoadingMessages(requestPayload, response),
}}));
"""
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    parsed = json.loads(result.stdout.strip())
    assert parsed["initial"] == [
        "Request created",
        "Session bound · entity=chat-demo",
        "Payload ready · history=2 · attachments=0 · provider=google/gemini-2.5-flash",
        "Opening turn stream",
        "Opening thinking trace",
    ]
    assert parsed["connected"][-2:] == [
        "Turn stream connected",
        "Routing to middleware",
    ]


def test_enqueue_overlay_status_promotes_latest_message_immediately():
    source = APP_JS.read_text()
    functions = "\n\n".join(
        _extract_function_source(source, name)
        for name in (
            "updateLoadingStatus",
            "resetOverlayStatusQueue",
            "enqueueOverlayStatus",
        )
    )
    script = f"""
let overlayStatusQueueTimer = null;
let overlayStatusQueueIndex = 0;
const overlayStatusQueue = [];
const overlayStatusSeen = new Set();
let overlayManualStatus = false;
let overlayTickerTimer = null;
const window = {{
  setInterval(fn, _ms) {{
    return 1;
  }},
  clearInterval(_id) {{}},
}};
const status = {{ textContent: '' }};
const document = {{
  getElementById(id) {{
    if (id === 'loading-status') return status;
    return null;
  }},
}};
{functions}
enqueueOverlayStatus('Thinking: Request accepted');
enqueueOverlayStatus('Autonomy: answer_from_priors (no_candidates)');
console.log(JSON.stringify({{
  text: status.textContent,
  index: overlayStatusQueueIndex,
  queue: overlayStatusQueue,
}}));
"""
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    parsed = json.loads(result.stdout.strip())
    assert parsed["text"] == "Autonomy: answer_from_priors (no_candidates)"
    assert parsed["index"] == 1
    assert parsed["queue"] == [
        "Thinking: Request accepted",
        "Autonomy: answer_from_priors (no_candidates)",
    ]


def test_update_loading_status_manual_resets_overlay_timer():
    source = APP_JS.read_text()
    functions = "\n\n".join(
        _extract_function_source(source, name)
        for name in (
            "updateLoadingStatus",
        )
    )
    script = f"""
let overlayManualStatus = false;
let overlayTickerTimer = null;
let overlayShownAt = 10;
const Date = {{
  now() {{
    return 12345;
  }},
}};
const window = {{
  clearInterval(_id) {{}},
}};
const status = {{ textContent: '' }};
const document = {{
  getElementById(id) {{
    if (id === 'loading-status') return status;
    return null;
  }},
}};
{functions}
updateLoadingStatus('Thinking: Assembling context', true);
console.log(JSON.stringify({{
  text: status.textContent,
  overlayManualStatus,
  overlayShownAt,
}}));
"""
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    parsed = json.loads(result.stdout.strip())
    assert parsed["text"] == "Thinking: Assembling context"
    assert parsed["overlayManualStatus"] is True
    assert parsed["overlayShownAt"] == 12345


def test_collect_pipeline_ticker_messages_for_coord_action_plan():
    result = _run_ticker_formatter(
        {
            "type": "coord_action_plan",
            "payload": {
                "action": "answer_from_priors",
                "coord": None,
                "reason": "no_candidates",
            },
        }
    )

    thinking = result.get("thinking") or []
    overlay = result.get("overlay") or []
    expected = "Model action: answer_from_priors · no_candidates"
    assert expected in thinking
    assert expected in overlay


def test_collect_pipeline_ticker_messages_for_coord_context_admitted():
    """DSS-281 regression: coord_context_admitted yields thinking/overlay ticker lines."""
    result = _run_ticker_formatter(
        {
            "type": "coord_context_admitted",
            "payload": {
                "coord": "chat-demo:WX-1772505927152",
                "admission": "opened_payload",
                "chars": 42,
            },
        }
    )
    thinking = result.get("thinking") or []
    overlay = result.get("overlay") or []
    expected = "Context admitted: chat-demo:WX-1772505927152 (opened_payload)"
    assert expected in thinking
    assert expected in overlay


def test_collect_pipeline_ticker_messages_for_coord_catalog():
    """DSS-281 regression: coord_catalog yields COORD ticker lines for each entry."""
    result = _run_ticker_formatter(
        {
            "type": "coord_catalog",
            "payload": {
                "kind": "coord_catalog",
                "entries": [
                    {"coord": "chat-demo:WX-1772505927152"},
                    {"coord": "chat-demo:WX-1772505000000"},
                ],
            },
        }
    )
    thinking = result.get("thinking") or []
    overlay = result.get("overlay") or []
    assert "COORD: chat-demo:WX-1772505927152" in thinking
    assert "COORD: chat-demo:WX-1772505000000" in thinking
    assert "COORD: chat-demo:WX-1772505927152" in overlay
    assert "COORD: chat-demo:WX-1772505000000" in overlay


def test_build_meta_ticker_fallback_for_no_candidate_turn():
    source = APP_JS.read_text()
    functions = "\n\n".join(
        _extract_function_source(source, name)
        for name in (
            "buildMetaTickerFallback",
        )
    )
    script = f"""
{functions}
const result = buildMetaTickerFallback({{
  autonomy_decision: {{ action: 'answer_from_priors', reason: 'no_candidates' }},
  candidate_trace: [],
  resolved_coords: [],
  resolve_summary: {{ requested: 0, unresolved: 0 }},
}});
console.log(JSON.stringify(result));
"""
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    parsed = json.loads(result.stdout.strip())
    thinking = parsed.get("thinking") or []
    overlay = parsed.get("overlay") or []
    assert "Autonomy: answer_from_priors (no_candidates)" in thinking
    assert "No candidate COORDs available" in thinking
    assert "Autonomy: answer_from_priors (no_candidates)" in overlay
    assert "No candidate COORDs available" in overlay


def test_read_stream_error_includes_upstream_url():
    source = APP_JS.read_text()
    functions = "\n\n".join(
        _extract_function_source(source, name)
        for name in (
            "readStream",
        )
    )
    script = f"""
{functions}
const response = {{
  ok: false,
  body: null,
  status: 502,
  async text() {{
    return JSON.stringify({{
      detail: 'Thinking trace stream upstream unavailable: connect failed',
      upstream_url: 'https://middleware.example.internal/api/thinking_trace/stream',
    }});
  }},
}};
(async () => {{
  try {{
    await readStream(response, () => {{}});
  }} catch (error) {{
    console.log(JSON.stringify({{ message: String(error.message || error) }}));
  }}
}})();
"""
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    parsed = json.loads(result.stdout.strip())
    message = str(parsed.get("message") or "")
    assert "Stream failed (502)" in message
    assert "upstream=https://middleware.example.internal/api/thinking_trace/stream" in message


def test_persist_streamed_turn_posts_commit_payload():
    source = APP_JS.read_text()
    functions = "\n\n".join(
        _extract_function_source(source, name)
        for name in (
            "persistStreamedTurn",
        )
    )
    script = f"""
{functions}
let captured = null;
global.fetch = async (url, options) => {{
  captured = {{ url, options }};
  return {{
    ok: true,
    async json() {{
      return {{ status: 'ok' }};
    }},
  }};
}};
(async () => {{
  await persistStreamedTurn({{
    message: 'hello',
    reply: 'world',
    latencyMs: 321,
    metadata: {{ coordinate: 'chat-demo:WX-1' }},
  }});
  console.log(JSON.stringify(captured));
}})();
"""
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    stdout_lines = [line for line in result.stdout.strip().splitlines() if line.strip()]
    parsed = json.loads(stdout_lines[-1])
    assert parsed["url"] == "/api/chat/stream/commit"
    options = parsed["options"]
    assert options["method"] == "POST"
    assert options["headers"]["Content-Type"] == "application/json"
    body = json.loads(options["body"])
    assert body["message"] == "hello"
    assert body["reply"] == "world"
    assert body["latency_ms"] == 321
    assert body["metadata"]["coordinate"] == "chat-demo:WX-1"


def test_delegation_attribution_helpers_mirror_python_semantics():
    source = APP_JS.read_text()
    functions = "\n\n".join(
        _extract_function_source(source, name)
        for name in (
            "_promptPrincipalLabel",
            "_requestedByLabel",
            "_delegatedPromptPathIsDistinctOperatorDelegation",
            "_answeredByLabel",
            "_askedByLabel",
        )
    )
    script = f"""
{functions}
const payloads = {{
  nonDelegated: {{
    prompt_principal_label: 'Operator',
  }},
  distinctByFlag: {{
    delegated_prompt_path: {{
      requested_by_is_distinct_from_prompt_principal: true,
      requested_by_principal_did: 'did:key:operator',
      prompt_principal_did: 'did:key:codex',
      prompt_principal_display_name: 'openai/codex',
    }},
  }},
  distinctByDid: {{
    delegated_prompt_path: {{
      requested_by_principal_did: 'did:key:operator',
      prompt_principal_did: 'did:key:kimi',
      prompt_principal_display_name: 'Moonshot: Kimi-code',
    }},
  }},
  sameDid: {{
    delegated_prompt_path: {{
      requested_by_principal_did: 'did:key:operator',
      prompt_principal_did: 'did:key:operator',
      prompt_principal_display_name: 'Operator',
    }},
  }},
}};
const result = {{}};
for (const [key, payload] of Object.entries(payloads)) {{
  result[key] = {{
    asked: _askedByLabel(payload),
    answered: _answeredByLabel(payload),
    distinct: _delegatedPromptPathIsDistinctOperatorDelegation(payload),
  }};
}}
console.log(JSON.stringify(result));
"""
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    parsed = json.loads(result.stdout.strip())
    assert parsed["nonDelegated"]["asked"] == "Operator"
    assert parsed["nonDelegated"]["answered"] == "Operator"
    assert parsed["nonDelegated"]["distinct"] is False
    assert parsed["distinctByFlag"]["asked"] == "did:key:operator"
    assert parsed["distinctByFlag"]["answered"] == "openai/codex"
    assert parsed["distinctByFlag"]["distinct"] is True
    assert parsed["distinctByDid"]["asked"] == "did:key:operator"
    assert parsed["distinctByDid"]["answered"] == "Moonshot: Kimi-code"
    assert parsed["distinctByDid"]["distinct"] is True
    assert parsed["sameDid"]["asked"] == "Operator"
    assert parsed["sameDid"]["answered"] == "Operator"
    assert parsed["sameDid"]["distinct"] is False


def test_build_stream_request_payload_includes_current_entity():
    source = APP_JS.read_text()
    functions = "\n\n".join(
        _extract_function_source(source, name)
        for name in (
            "getCurrentSessionEntity",
            "buildStreamRequestPayload",
        )
    )
    script = f"""
function resolveBackendStreamFlag() {{
  return true;
}}
function getCookieValue(name) {{
  if (name === 'ds_session') return 'sess-123';
  return '';
}}
const document = {{
  getElementById(id) {{
    if (id === 'entity-id') return {{ value: 'chat-demo' }};
    if (id === 'session-id') return {{ value: 'sess-123' }};
    return null;
  }},
}};
function getPromptAsCodexEnabled() {{
  return false;
}}
function normalizeModelId(modelId) {{
  return modelId;
}}
{functions}
const payload = buildStreamRequestPayload({{
  message: 'hello',
  provider: 'openai:gpt-5',
  sessionId: 'sess-123',
  currentHistory: [{{ role: 'user', content: 'prior' }}],
  uniqueAttachmentCoordinates: ['chat-demo:ATT-1'],
  timeRange: null,
}});
console.log(JSON.stringify(payload));
"""
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout.strip())
    assert payload["entity"] == "chat-demo"
    assert payload["session_id"] == "sess-123"
    assert payload["context_coords"] == ["chat-demo:ATT-1"]


