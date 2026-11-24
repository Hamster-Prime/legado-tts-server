"""Regression tests for fail-closed authentication and synthesis quotas."""

import copy
from datetime import datetime

import pytest

import app as server


@pytest.fixture
def isolated_server(monkeypatch):
    monkeypatch.setattr(server, 'API_KEYS_REQUIRED', True)
    monkeypatch.setattr(server, 'API_KEYS', set())
    monkeypatch.setattr(server, 'ADMIN_TOKEN', '')
    monkeypatch.setattr(server, 'RATE_LIMIT_RPM', 0)
    monkeypatch.setattr(server, 'DAILY_CHAR_QUOTA', 100)
    monkeypatch.setattr(server, '_daily_char_usage', {})
    monkeypatch.setattr(server, '_daily_cleanup_counter', 0)
    monkeypatch.setattr(server, 'load_config',
                        lambda: copy.deepcopy(server.DEFAULT_CONFIG))
    monkeypatch.setattr(server, 'update_stats', lambda *args, **kwargs: None)
    return server.app.test_client()


@pytest.mark.parametrize('headers', [{}, {'Authorization': 'Bearer arbitrary'}])
def test_required_auth_with_no_keys_fails_closed(isolated_server, headers):
    response = isolated_server.post('/speech/stream', json={'text': 'hello'},
                                    headers=headers)
    assert response.status_code == 401


def test_admin_key_still_works_without_api_keys(isolated_server, monkeypatch):
    monkeypatch.setattr(server, 'ADMIN_TOKEN', 'admin-test-key')
    with server.app.test_request_context(
            '/speech/stream', headers={'Authorization': 'Bearer admin-test-key'}):
        assert server._check_api_key() is None


def test_optional_auth_remains_optional(isolated_server, monkeypatch):
    monkeypatch.setattr(server, 'API_KEYS_REQUIRED', False)
    with server.app.test_request_context('/speech/stream'):
        assert server._check_api_key() is None


def test_configured_api_key_accepted(isolated_server, monkeypatch):
    monkeypatch.setattr(server, 'API_KEYS', {'tts-test-key'})
    with server.app.test_request_context(
            '/speech/stream', headers={'Authorization': 'bearer tts-test-key'}):
        assert server._check_api_key() is None


def test_preview_charges_quota_and_stops_synthesis_at_limit(isolated_server, monkeypatch):
    monkeypatch.setattr(server, 'API_KEYS_REQUIRED', False)
    text = '你好，我是您的语音助手，很高兴认识您。'
    monkeypatch.setattr(server, 'DAILY_CHAR_QUOTA', len(text))
    calls = []

    def synth(provider, utterance, voice, rate):
        calls.append(utterance)
        return b'audio', None, provider, voice

    monkeypatch.setattr(server, 'dispatch', synth)
    first = isolated_server.post('/api/tts/preview', json={})
    second = isolated_server.post('/api/tts/preview', json={})
    assert first.status_code == 200
    assert second.status_code == 429
    assert second.get_json()['error']['type'] == 'quota_exceeded'
    assert calls == [text]
    today = datetime.now().strftime('%Y-%m-%d')
    assert server._daily_char_usage['127.0.0.1'][today] == len(text)


def test_quota_gc_removes_absent_old_ips_and_preserves_today(isolated_server, monkeypatch):
    today = datetime.now().strftime('%Y-%m-%d')
    monkeypatch.setattr(server, '_daily_cleanup_counter', 99)
    monkeypatch.setattr(server, '_daily_char_usage', {
        'old-client': {'2000-01-01': 3},
        'active-client': {'2000-01-01': 5, today: 7},
        'empty-client': {},
    })
    with server.app.test_request_context('/speech/stream'):
        assert server._check_daily_quota('new-client', 2) is None
    assert server._daily_char_usage == {
        'active-client': {today: 7}, 'new-client': {today: 2},
    }


def test_quota_gc_runs_on_rejected_requests(isolated_server, monkeypatch):
    today = datetime.now().strftime('%Y-%m-%d')
    monkeypatch.setattr(server, '_daily_cleanup_counter', 99)
    monkeypatch.setattr(server, '_daily_char_usage', {
        'old-client': {'2000-01-01': 3},
        'exhausted-client': {today: 100},
    })
    with server.app.test_request_context('/speech/stream'):
        response = server._check_daily_quota('exhausted-client', 1)
        assert response.status_code == 429
    assert 'old-client' not in server._daily_char_usage
    assert server._daily_char_usage['exhausted-client'] == {today: 100}
