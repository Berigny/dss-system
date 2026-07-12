from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.projection import router as projection_router
from backend.fieldx_kernel.e6_packet import unpack_header_v0


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(projection_router)
    return TestClient(app)


def test_projection_topologies_exposes_kernel_plus_six_domains() -> None:
    client = _client()
    response = client.get('/projection/mmf/topologies')
    assert response.status_code == 200

    payload = response.json()
    assert payload['projection_version'] == 'mmf-projection-v1'
    assert payload['domain_count'] == 6
    assert payload['topology_count'] == 7
    assert payload['required_domain_primes'] == 48
    assert payload['taxonomy_mode'] == 'indefeasible'

    topologies = payload['topologies']
    assert 'kernel' in topologies
    assert len(topologies['kernel']['cube_primes']) == 8

    all_extensions = []
    for domain in ('verbal', 'visual', 'auditory', 'olfactory', 'spatial', 'behavioral'):
        topo = topologies[domain]
        assert len(topo['anchor_primes']) == 4
        assert len(topo['extension_primes']) == 4
        assert len(topo['cube_primes']) == 8
        assert set(topo['cube_primes']).isdisjoint(set(topologies['kernel']['cube_primes']))
        all_extensions.extend(topo['cube_primes'])

    assert len(all_extensions) == 48
    assert len(set(all_extensions)) == 48


def test_projection_evaluate_returns_packet_and_commit_decision() -> None:
    client = _client()
    response = client.post(
        '/projection/mmf/evaluate',
        json={
            'domain': 'visual',
            'node': 'S1-N2',
            'mode': 2,
            'K': 1,
            'P': 1,
            'E': 1,
            'V_q': 52000,
            'momentum_min': 1000,
            'seq': 9,
            't_ms': 321,
            'dW': 0,
            'source_event_id': 'evt-001',
            'payload_hash': 'abc123',
        },
    )
    assert response.status_code == 200

    payload = response.json()
    assert payload['domain'] == 'visual'
    assert payload['node'] == 'S1-N2'
    assert payload['commit'] is True
    assert payload['decision']['commit'] is True
    assert isinstance(payload.get('posture_policy'), dict)
    assert payload['posture_policy']['policy_gate_version'] == 'policy-gate-v1'
    assert payload['header']['crc_ok'] is True
    assert len(payload['header128']) == 32
    assert payload['topology']['anchor_nodes']
    assert payload['topology']['extension_primes']
    assert payload['topology']['taxonomy_mode'] == 'indefeasible'


def test_projection_evaluate_rejects_unprivileged_overrides(monkeypatch) -> None:
    monkeypatch.delenv('PROJECTION_POLICY_ALLOW_CLIENT_OVERRIDES', raising=False)
    client = _client()

    response = client.post(
        '/projection/mmf/evaluate',
        json={
            'domain': 'auditory',
            'node': 'S2-N1',
            'mode': 0,
            'K': 0,
            'P': 0,
            'E': 0,
            'V_q': 60000,
            'momentum_min': 100,
            'seq': 8,
            't_ms': 333,
        },
    )
    assert response.status_code == 200

    payload = response.json()
    policy = payload['policy_controls']
    assert policy['override_authorized'] is False
    assert policy['effective_mode'] == 2
    assert policy['effective_gates'] == {'K': 1, 'P': 1, 'E': 1}
    assert 'projection_mode_override_rejected' in policy['rejected_overrides']
    assert payload['decision']['mode'] == 2


def test_projection_evaluate_allows_authorized_overrides(monkeypatch) -> None:
    monkeypatch.setenv('PROJECTION_POLICY_ALLOW_CLIENT_OVERRIDES', '1')
    client = _client()

    response = client.post(
        '/projection/mmf/evaluate',
        headers={'Authorization': 'Bearer token-1'},
        json={
            'domain': 'auditory',
            'node': 'S2-N1',
            'mode': 1,
            'K': 1,
            'P': 0,
            'E': 1,
            'V_q': 60000,
            'momentum_min': 100,
            'seq': 8,
            't_ms': 333,
            'principal_did': 'did:key:z6MkUnit',
            'session_jti': 'jti-unit-1',
        },
    )
    assert response.status_code == 200

    payload = response.json()
    policy = payload['policy_controls']
    assert policy['override_authorized'] is True
    assert policy['effective_mode'] == 1
    assert policy['effective_gates']['P'] == 0
    assert policy['rejected_overrides'] == []
    assert payload['decision']['mode'] == 1


def test_projection_evaluate_is_deterministic_for_fixed_inputs() -> None:
    client = _client()
    fixed = {
        'domain': 'auditory',
        'node': 'S2-N1',
        'mode': 3,
        'K': 1,
        'P': 1,
        'E': 1,
        'V_q': 60000,
        'momentum_min': 200,
        'seq': 77,
        't_ms': 654321,
        'dW': -1,
        'source_event_id': 'evt-fixed',
        'payload_hash': 'h-fixed',
    }

    first = client.post('/projection/mmf/evaluate', json=fixed)
    second = client.post('/projection/mmf/evaluate', json=fixed)
    assert first.status_code == 200
    assert second.status_code == 200

    left = first.json()
    right = second.json()

    assert left['decision'] == right['decision']
    assert left['header128'] == right['header128']
    assert left['header'] == right['header']
    assert left['commit'] == right['commit']


def test_projection_packet_tamper_is_detected_by_crc() -> None:
    client = _client()
    response = client.post(
        '/projection/mmf/evaluate',
        json={
            'domain': 'behavioral',
            'node': 'S1-N1',
            'mode': 2,
            'K': 1,
            'P': 1,
            'E': 1,
            'V_q': 51000,
            'momentum_min': 500,
            'seq': 12,
            't_ms': 444,
            'dW': 0,
        },
    )
    assert response.status_code == 200
    payload = response.json()

    header_bytes = bytes.fromhex(payload['header128'])
    pristine = unpack_header_v0(header_bytes)
    assert pristine['crc_ok'] is True

    tampered = bytearray(header_bytes)
    tampered[10] ^= 0x01
    parsed_tampered = unpack_header_v0(bytes(tampered))
    assert parsed_tampered['crc_ok'] is False


def test_projection_evaluate_rejects_unknown_domain() -> None:
    client = _client()
    response = client.post('/projection/mmf/evaluate', json={'domain': 'tactile'})
    assert response.status_code == 422


def test_eval_contract_explains_failure_with_actionable_repairs(monkeypatch) -> None:
    monkeypatch.setenv('PROJECTION_POLICY_ALLOW_CLIENT_OVERRIDES', '1')
    client = _client()

    response = client.post(
        '/projection/mmf/evaluate',
        headers={'Authorization': 'Bearer token-1'},
        json={
            'domain': 'verbal',
            'node': 'S1-N0',
            'mode': 1,
            'K': 1,
            'P': 0,
            'E': 1,
            'V_q': 5,
            'momentum_min': 100,
            'principal_did': 'did:key:z6MkEval',
            'session_jti': 'jti-eval-1',
        },
    )
    assert response.status_code == 200
    payload = response.json()

    contract = payload['eval_contract']
    assert contract['blocked'] is True
    assert contract['commit_allowed'] is False
    assert contract['failed_eq'] in {'eq6_awareness', 'eq7_unity', 'eq9_telos', 'eq8_ethics'}
    assert isinstance(contract['failed_checks'], list) and contract['failed_checks']
    assert isinstance(contract['repair_actions'], list) and contract['repair_actions']
    assert contract['required_thresholds']['P_required'] == 1
    posture = payload.get('posture_policy')
    assert isinstance(posture, dict)
    assert posture.get('policy_decision') == 'deny'
    assert str(posture.get('reason_code') or '').startswith('eq_blocked')


def test_eval_contract_reports_eq9_yield_per_token() -> None:
    client = _client()
    response = client.post(
        '/projection/mmf/evaluate',
        json={
            'domain': 'visual',
            'node': 'S1-N2',
            'mode': 2,
            'K': 1,
            'P': 1,
            'E': 1,
            'V_q': 52000,
            'momentum_min': 1000,
            'output_tokens_est': 100,
            'law_score': 0.9,
            'grace_score': 0.8,
        },
    )
    assert response.status_code == 200
    contract = response.json()['eval_contract']
    eq9 = contract['eq9_metrics']
    assert eq9['tokens'] == 100
    assert eq9['fulfillment'] > 0.0
    assert eq9['yield_per_token'] > 0.0
    assert 'provenance_confidence' in eq9
    assert 'anti_gaming' in eq9
    posture = response.json().get('posture_policy')
    assert isinstance(posture, dict)
    assert posture.get('trust_class') in {'T0', 'T2', 'T3'}
