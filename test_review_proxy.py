"""Trusted proxy handling must not let untrusted clients choose their IP."""

import ipaddress
import copy

import pytest

import app


@pytest.mark.parametrize(('peer', 'forwarded', 'expected'), [
    ('192.0.2.9', '198.51.100.1', '192.0.2.9'),
    ('127.0.0.1', '198.51.100.2', '198.51.100.2'),
    ('127.0.0.1', '203.0.113.8, 198.51.100.2', '198.51.100.2'),
    ('127.0.0.1', 'bad, 198.51.100.2', '127.0.0.1'),
    ('127.0.0.1', '2001:db8::2', '2001:db8::2'),
])
def test_forwarded_address_only_from_trusted_peer(monkeypatch, peer, forwarded, expected):
    monkeypatch.setattr(app, 'TRUSTED_PROXIES', (ipaddress.ip_network('127.0.0.1/32'),))
    seen = []
    monkeypatch.setattr(app, '_check_rate_limit', lambda ip: seen.append(ip) or False)
    monkeypatch.setattr(app, '_check_api_key', lambda: None)
    with app.app.test_client() as client:
        client.post('/speech/stream', json={}, headers={'X-Forwarded-For': forwarded},
                    environ_base={'REMOTE_ADDR': peer})
    assert seen == [expected]


def test_no_implicit_localhost_whitelist():
    assert '127.0.0.1' not in app.RATE_LIMIT_WHITELIST


@pytest.mark.parametrize('limit_kind', ['rate', 'quota'])
def test_trusted_proxy_limits_clients_separately(monkeypatch, limit_kind):
    monkeypatch.setattr(app, 'TRUSTED_PROXIES', (ipaddress.ip_network('127.0.0.1/32'),))
    monkeypatch.setattr(app, 'RATE_LIMIT_WHITELIST', set())
    monkeypatch.setattr(app, 'RATE_LIMIT_RPM', 1 if limit_kind == 'rate' else 0)
    monkeypatch.setattr(app, 'DAILY_CHAR_QUOTA', 1 if limit_kind == 'quota' else 0)
    monkeypatch.setattr(app, 'API_KEYS_REQUIRED', False)
    monkeypatch.setattr(app, 'ADMIN_TOKEN', '')
    monkeypatch.setattr(app, '_rate_limits', {})
    monkeypatch.setattr(app, '_daily_char_usage', {})
    monkeypatch.setattr(app, 'load_config', lambda: copy.deepcopy(app.DEFAULT_CONFIG))
    monkeypatch.setattr(app, '_edge_stream_iter', lambda *a, **kw: iter((b'audio',)))
    monkeypatch.setattr(app, 'update_stats', lambda *a, **kw: None)
    with app.app.test_client() as client:
        def speak(ip):
            return client.post('/speech/stream', json={'text': 'a'},
                               headers={'X-Forwarded-For': ip})
        assert speak('198.51.100.1').status_code == 200
        assert speak('198.51.100.1').status_code == 429
        assert speak('198.51.100.2').status_code == 200
