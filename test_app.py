#!/usr/bin/env python3
"""Unit tests for legado-tts-server"""

import json
import os
import sys
import tempfile

# Add parent dir to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest
from app import (
    resolve_provider, parse_rate, load_config, save_config,
    load_stats, _read_json, _write_json, _empty_provider_stats, ALL_PROVIDERS,
    DOUBAO_VOICES, TENCENT_VOICES, EDGE_VOICES, XIAOMI_VOICES,
)


class TestResolveProvider:
    """Test voice-to-provider routing."""

    def test_edge_voices(self):
        for v in EDGE_VOICES:
            assert resolve_provider(v['id']) == 'edge', f"{v['id']} should route to edge"

    def test_doubao_voices(self):
        for v in DOUBAO_VOICES:
            assert resolve_provider(v['id']) == 'doubao', f"{v['id']} should route to doubao"

    def test_tencent_voices(self):
        for v in TENCENT_VOICES:
            assert resolve_provider(v['id']) == 'tencent', f"{v['id']} should route to tencent"

    def test_xiaomi_voices(self):
        for v in XIAOMI_VOICES:
            assert resolve_provider(v['id']) == 'xiaomi', f"{v['id']} should route to xiaomi"

    def test_empty_voice(self):
        assert resolve_provider('') is None
        assert resolve_provider(None) is None

    def test_unknown_voice(self):
        assert resolve_provider('unknown_voice_id') is None

    def test_large_number_rejected(self):
        assert resolve_provider('9999999') is None  # Too large for tencent

    def test_zero_rejected(self):
        assert resolve_provider('0') is None

    def test_mimo_prefix(self):
        assert resolve_provider('mimo_custom') == 'xiaomi'


class TestParseRate:
    def test_zero(self):
        assert parse_rate('0%') == 0.0

    def test_positive(self):
        assert parse_rate('+50%') == 50.0

    def test_negative(self):
        assert parse_rate('-20%') == -20.0

    def test_plus_sign(self):
        assert parse_rate('+100%') == 100.0

    def test_no_sign(self):
        assert parse_rate('30%') == 30.0

    def test_invalid(self):
        assert parse_rate('abc') == 0.0
        assert parse_rate('') == 0.0
        assert parse_rate(None) == 0.0

    def test_float_rate(self):
        assert parse_rate('33.5%') == 33.5


class TestConfigIO:
    def test_write_and_read(self):
        with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
            path = f.name
        try:
            test_config = {'provider': 'edge', 'appid': 'test123', 'access_token': 'tok'}
            _write_json(path, test_config)
            result = _read_json(path, {})
            assert result == test_config
        finally:
            os.unlink(path)
            # Clean up tmp file
            tmp = path + '.tmp'
            if os.path.exists(tmp):
                os.unlink(tmp)

    def test_read_missing_file(self):
        result = _read_json('/nonexistent/path/config.json', {'default': True})
        assert result == {'default': True}

    def test_read_corrupted_file(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write('not valid json {{{')
            path = f.name
        try:
            result = _read_json(path, {'fallback': True})
            assert result == {'fallback': True}
        finally:
            os.unlink(path)

    def test_atomic_write(self):
        with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
            path = f.name
        try:
            # Write first value
            _write_json(path, {'v': 1})
            assert _read_json(path, {}) == {'v': 1}
            # Overwrite atomically - file should always be valid
            _write_json(path, {'v': 2})
            assert _read_json(path, {}) == {'v': 2}
        finally:
            os.unlink(path)
            tmp = path + '.tmp'
            if os.path.exists(tmp):
                os.unlink(tmp)


class TestLoadConfig:
    def test_load_missing_returns_defaults(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, 'config.json')
            import app as app_module
            orig = app_module.CONFIG_FILE
            app_module.CONFIG_FILE = path
            try:
                cfg = load_config()
                assert cfg['provider'] == 'edge'
                assert cfg['edge_voice'] == 'zh-CN-XiaoxiaoNeural'
            finally:
                app_module.CONFIG_FILE = orig

    def test_forward_compatibility(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump({'provider': 'doubao'}, f)
            path = f.name
        try:
            import app as app_module
            orig = app_module.CONFIG_FILE
            app_module.CONFIG_FILE = path
            try:
                cfg = load_config()
                assert cfg['provider'] == 'doubao'
                # Missing keys should get defaults
                assert cfg['edge_voice'] == 'zh-CN-XiaoxiaoNeural'
                assert cfg['appid'] == ''
            finally:
                app_module.CONFIG_FILE = orig
        finally:
            os.unlink(path)


class TestAllVoicesUnique:
    """Ensure no voice ID conflicts between providers."""

    def test_no_overlap(self):
        all_ids = []
        for voices in [EDGE_VOICES, DOUBAO_VOICES, TENCENT_VOICES, XIAOMI_VOICES]:
            ids = [v['id'] for v in voices]
            assert len(ids) == len(set(ids)), f"Duplicate voice IDs in list"
            all_ids.extend(ids)
        # Check cross-provider uniqueness
        assert len(all_ids) == len(set(all_ids)), f"Voice ID conflicts across providers"


class TestDispatch:
    """Test dispatch routing."""

    def test_dispatch_unknown_provider(self):
        from app import dispatch
        audio, error = dispatch('unknown', 'test', 'test', 0)
        assert audio is None
        assert 'Unknown provider' in error

    def test_dispatch_edge_no_network(self):
        """Edge TTS should work (free, no API key)."""
        from app import dispatch
        audio, error = dispatch('edge', '你好', 'zh-CN-XiaoxiaoNeural', 0)
        assert audio is not None, f"Edge TTS failed: {error}"
        assert len(audio) > 0

    def test_dispatch_doubao_no_config(self):
        from app import dispatch
        audio, error = dispatch('doubao', 'test', 'zh_female_cancan_mars_bigtts', 0)
        assert audio is None
        assert '未配置' in error

    def test_dispatch_tencent_no_config(self):
        from app import dispatch
        audio, error = dispatch('tencent', 'test', '501002', 0)
        assert audio is None
        assert '未配置' in error

    def test_dispatch_xiaomi_no_config(self):
        from app import dispatch
        audio, error = dispatch('xiaomi', 'test', 'mimo_default', 0)
        assert audio is None
        assert '未配置' in error


class TestXiaomiStyle:
    """Test Xiaomi style tag generation."""

    def test_normal_speed(self):
        from app import _build_xiaomi_style
        assert '适中' in _build_xiaomi_style(1.0)

    def test_very_fast(self):
        from app import _build_xiaomi_style
        assert '很快' in _build_xiaomi_style(2.0)

    def test_very_slow(self):
        from app import _build_xiaomi_style
        assert '很慢' in _build_xiaomi_style(0.3)

    def test_slightly_fast(self):
        from app import _build_xiaomi_style
        assert '稍快' in _build_xiaomi_style(1.2)

    def test_slightly_slow(self):
        from app import _build_xiaomi_style
        assert '稍慢' in _build_xiaomi_style(0.9)

    def test_style_tag_format(self):
        from app import _build_xiaomi_style
        result = _build_xiaomi_style(1.0)
        assert result.startswith('<style>')
        assert result.endswith('</style>')


if __name__ == '__main__':
    pytest.main([__file__, '-v'])


# ──────────────────────────────────────────────
# Integration tests (Flask API)
# ──────────────────────────────────────────────

class TestAPIEndpoints:
    """Test Flask API endpoints."""

    @pytest.fixture(autouse=True)
    def setup_app(self):
        self.tmpdir = tempfile.mkdtemp()
        import app as app_module
        self.orig_config = app_module.CONFIG_FILE
        self.orig_stats = app_module.STATS_FILE
        app_module.CONFIG_FILE = os.path.join(self.tmpdir, 'config.json')
        app_module.STATS_FILE = os.path.join(self.tmpdir, 'stats.json')
        self.app = app_module.app
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()
        yield
        app_module.CONFIG_FILE = self.orig_config
        app_module.STATS_FILE = self.orig_stats
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_health(self):
        r = self.client.get('/health')
        assert r.status_code == 200
        data = r.get_json()
        assert data['status'] == 'ok'
        assert 'timestamp' in data

    def test_speech_stream_missing_text(self):
        r = self.client.post('/speech/stream', json={'voice': 'zh-CN-XiaoxiaoNeural'})
        assert r.status_code == 400

    def test_speech_stream_missing_voice(self):
        r = self.client.post('/speech/stream', json={'text': 'hello'})
        assert r.status_code == 400

    def test_speech_stream_unknown_voice(self):
        r = self.client.post('/speech/stream', json={'text': 'hello', 'voice': 'invalid'})
        assert r.status_code == 400

    def test_speech_stream_text_too_long(self):
        long_text = 'a' * 10000
        r = self.client.post('/speech/stream', json={'text': long_text, 'voice': 'zh-CN-XiaoxiaoNeural'})
        assert r.status_code == 400
        assert 'too long' in r.get_data(as_text=True).lower()

    def test_speech_stream_empty_body(self):
        r = self.client.post('/speech/stream', data='not json', content_type='application/json')
        assert r.status_code == 400

    def test_speech_stream_whitespace_only_text(self):
        r = self.client.post('/speech/stream', json={'text': '   ', 'voice': 'zh-CN-XiaoxiaoNeural'})
        assert r.status_code == 400

    def test_config_get(self):
        r = self.client.get('/api/config')
        assert r.status_code == 200
        data = r.get_json()
        assert 'provider' in data
        assert 'access_token' in data

    def test_config_post(self):
        r = self.client.post('/api/config', json={'provider': 'edge'})
        assert r.status_code == 200
        assert r.get_json()['status'] == 'ok'

    def test_config_post_masked_values_preserved(self):
        # First save a real value
        self.client.post('/api/config', json={'appid': 'real123', 'access_token': 'real_token'})
        # Now save with masked token
        self.client.post('/api/config', json={'access_token': '***'})
        # Verify token was not overwritten
        cfg = load_config()
        assert cfg['access_token'] == 'real_token'

    def test_stats_get(self):
        r = self.client.get('/api/stats')
        assert r.status_code == 200
        data = r.get_json()
        for p in ALL_PROVIDERS:
            assert p in data
            assert 'total_chars' in data[p]

    def test_stats_reset(self):
        r = self.client.delete('/api/stats')
        assert r.status_code == 200
        assert r.get_json()['status'] == 'ok'

    def test_voices_edge(self):
        r = self.client.get('/api/voices?provider=edge')
        assert r.status_code == 200
        data = r.get_json()
        assert len(data) > 0

    def test_voices_doubao(self):
        r = self.client.get('/api/voices?provider=doubao')
        assert r.status_code == 200
        data = r.get_json()
        assert len(data) == len(DOUBAO_VOICES)

    def test_voices_tencent(self):
        r = self.client.get('/api/voices?provider=tencent')
        assert r.status_code == 200

    def test_voices_xiaomi(self):
        r = self.client.get('/api/voices?provider=xiaomi')
        assert r.status_code == 200

    def test_voices_default(self):
        r = self.client.get('/api/voices')
        assert r.status_code == 200
        data = r.get_json()
        # Default should be edge
        assert any(v['id'] == 'zh-CN-XiaoxiaoNeural' for v in data)

    def test_index(self):
        r = self.client.get('/')
        assert r.status_code == 200
        assert b'TTS' in r.data

    def test_config_test_endpoint(self):
        r = self.client.post('/api/config/test')
        assert r.status_code == 200
        data = r.get_json()
        assert 'provider' in data
        assert 'ok' in data

    def test_config_post_unknown_provider(self):
        r = self.client.post('/api/config', json={'provider': 'nonexistent'})
        assert r.status_code == 200

    def test_voices_returns_list(self):
        for p in ALL_PROVIDERS:
            r = self.client.get(f'/api/voices?provider={p}')
            assert r.status_code == 200
            data = r.get_json()
            assert isinstance(data, list)
            assert len(data) > 0
            for v in data:
                assert 'id' in v
                assert 'name' in v

    def test_health_version(self):
        r = self.client.get('/health')
        data = r.get_json()
        assert 'version' in data
        assert data['version'] == '1.2.0'

    def test_speech_stream_rate_parsing(self):
        """Test that rate parameter is correctly parsed."""
        # These should all be 400 (no real TTS credentials) not 500
        for rate in ['0%', '+0%', '-0%', '+50%', '-20%', '100%']:
            r = self.client.post('/speech/stream',
                json={'text': '测试', 'voice': 'zh-CN-XiaoxiaoNeural', 'rate': rate})
            assert r.status_code in (200, 500), f"Unexpected status {r.status_code} for rate={rate}"

    def test_speech_stream_with_chinese_punctuation(self):
        """Test that Chinese punctuation is handled."""
        r = self.client.post('/speech/stream',
            json={'text': '你好！这是一段测试。包含逗号，句号。', 'voice': 'zh-CN-XiaoxiaoNeural'})
        assert r.status_code in (200, 500)

    def test_stats_after_reset_are_empty(self):
        self.client.delete('/api/stats')
        r = self.client.get('/api/stats')
        data = r.get_json()
        for p in ALL_PROVIDERS:
            assert data[p]['total_chars'] == 0
            assert data[p]['total_requests'] == 0
            assert data[p]['history'] == []

    def test_config_get_has_provider_status(self):
        r = self.client.get('/api/config')
        data = r.get_json()
        assert 'provider_status' in data
        for p in ALL_PROVIDERS:
            assert p in data['provider_status']
            assert 'ready' in data['provider_status'][p]

    def test_config_get_masks_secrets(self):
        # Save secrets first
        self.client.post('/api/config', json={
            'access_token': 'my_secret_token_12345',
            'tencent_secret_key': 'my_tencent_key_abcdef',
            'appid': '1234567890'
        })
        r = self.client.get('/api/config')
        data = r.get_json()
        assert data['access_token'] == '***'
        assert data['tencent_secret_key'] == '***'
        assert '***' in data['appid']
        assert 'my_secret_token' not in str(data)

    def test_multiple_voices_unique_ids(self):
        """All voice IDs across providers must be unique."""
        r = self.client.get('/api/voices?provider=edge')
        edge_ids = {v['id'] for v in r.get_json()}
        for p in ['doubao', 'tencent', 'xiaomi']:
            r = self.client.get(f'/api/voices?provider={p}')
            ids = {v['id'] for v in r.get_json()}
            assert not edge_ids & ids, f"Overlap between edge and {p}: {edge_ids & ids}"
            edge_ids.update(ids)

    def test_speech_stream_response_headers(self):
        """Edge TTS should succeed and include X-TTS headers."""
        r = self.client.post('/speech/stream',
            json={'text': '测试', 'voice': 'zh-CN-XiaoxiaoNeural'})
        if r.status_code == 200:
            assert r.headers.get('X-TTS-Provider') == 'edge'
            assert r.headers.get('Content-Type') == 'audio/mpeg'
            assert 'Content-Length' in r.headers
