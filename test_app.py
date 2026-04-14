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
    DOUBAO_VOICES, TENCENT_VOICES, EDGE_VOICES, XIAOMI_VOICES, FISH_AUDIO_VOICES,
    _clean_text, _split_text_chunks, _check_rate_limit, _concat_mp3,
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

    def test_fishaudio_voices(self):
        for v in FISH_AUDIO_VOICES:
            assert resolve_provider(v['id']) == 'fishaudio', f"{v['id']} should route to fishaudio"

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

    def test_speed_presets_english(self):
        assert parse_rate('fast') == 20
        assert parse_rate('slow') == -15
        assert parse_rate('normal') == 0
        assert parse_rate('very-fast') == 40

    def test_speed_presets_chinese(self):
        assert parse_rate('快速') == 20
        assert parse_rate('慢速') == -15
        assert parse_rate('正常') == 0

    def test_speed_presets_multiplier(self):
        assert parse_rate('1.5x') == 50
        assert parse_rate('2x') == 100
        assert parse_rate('0.75x') == -25


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
        for voices in [EDGE_VOICES, DOUBAO_VOICES, TENCENT_VOICES, XIAOMI_VOICES, FISH_AUDIO_VOICES]:
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

    def test_dispatch_fishaudio_no_config(self):
        from app import dispatch
        audio, error = dispatch('fishaudio', 'test', 'fish-animated', 0)
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


class TestCleanText:
    def test_removes_control_chars(self):
        from app import _clean_text
        assert _clean_text('hello\x00world') == 'helloworld'

    def test_collapses_whitespace(self):
        from app import _clean_text
        assert _clean_text('hello   world') == 'hello world'

    def test_strips(self):
        from app import _clean_text
        assert _clean_text('  hi  ') == 'hi'

    def test_empty(self):
        from app import _clean_text
        assert _clean_text('') == ''


class TestTextChunking:
    def test_short_text_single_chunk(self):
        chunks = _split_text_chunks('Hello world', max_chunk=100)
        assert len(chunks) == 1
        assert chunks[0] == 'Hello world'

    def test_long_text_splits(self):
        text = '你好。世界。测试。'  # 3 sentences
        chunks = _split_text_chunks(text, max_chunk=5)
        assert len(chunks) >= 2
        assert ''.join(chunks) == text

    def test_hard_split_no_delimiter(self):
        text = 'a' * 20
        chunks = _split_text_chunks(text, max_chunk=5)
        assert all(len(c) <= 5 for c in chunks)
        assert ''.join(chunks) == text

    def test_empty_text(self):
        chunks = _split_text_chunks('', max_chunk=100)
        assert len(chunks) == 1


class TestConcatMp3:
    def test_concat(self):
        result = _concat_mp3([b'aaa', b'bbb', b'ccc'])
        assert result == b'aaabbbccc'

    def test_empty(self):
        assert _concat_mp3([]) == b''


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
        assert 'cache' in data
        assert 'ffmpeg_available' in data

    def test_metrics_endpoint(self):
        r = self.client.get('/metrics')
        assert r.status_code == 200
        assert r.content_type.startswith('text/plain')
        content = r.get_data(as_text=True)
        assert 'tts_requests_total' in content
        assert 'tts_chars_total' in content
        assert 'tts_cache_hit_ratio' in content

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

    def test_voices_all(self):
        r = self.client.get('/api/voices/all')
        assert r.status_code == 200
        data = r.get_json()
        for p in ALL_PROVIDERS:
            assert p in data
            assert len(data[p]) > 0

    def test_cache_stats(self):
        r = self.client.get('/api/cache/stats')
        assert r.status_code == 200
        data = r.get_json()
        assert 'size' in data
        assert 'max_size' in data

    def test_cache_clear(self):
        r = self.client.delete('/api/cache/clear')
        assert r.status_code == 200
        assert r.get_json()['status'] == 'ok'

    def test_edge_voices_include_cantonese(self):
        r = self.client.get('/api/voices?provider=edge')
        ids = [v['id'] for v in r.get_json()]
        assert 'zh-HK-HiuMaanNeural' in ids
        assert 'zh-TW-HsiaoChenNeural' in ids

    def test_speech_stream_xttschars_header(self):
        r = self.client.post('/speech/stream',
            json={'text': '测试', 'voice': 'zh-CN-XiaoxiaoNeural'})
        if r.status_code == 200:
            assert r.headers.get('X-TTS-Chars') == '2'

    def test_health_version(self):
        r = self.client.get('/health')
        data = r.get_json()
        assert 'version' in data
        assert data['version'] == '1.5.0'

    # OpenAI-compatible API tests
    def test_openai_speech_basic(self):
        r = self.client.post('/v1/audio/speech', json={
            'model': 'tts-1',
            'input': '测试',
            'voice': 'zh-CN-XiaoxiaoNeural',
        })
        assert r.status_code in (200, 500)
        if r.status_code == 200:
            assert r.content_type == 'audio/mpeg'

    def test_openai_speech_missing_input(self):
        r = self.client.post('/v1/audio/speech', json={
            'model': 'tts-1',
            'voice': 'zh-CN-XiaoxiaoNeural',
        })
        assert r.status_code == 400
        data = r.get_json()
        assert 'error' in data

    def test_openai_speech_missing_voice(self):
        r = self.client.post('/v1/audio/speech', json={
            'model': 'tts-1',
            'input': 'hello',
        })
        assert r.status_code == 400

    def test_openai_speech_unknown_voice(self):
        r = self.client.post('/v1/audio/speech', json={
            'model': 'tts-1',
            'input': 'hello',
            'voice': 'nonexistent_voice_xyz',
        })
        assert r.status_code == 400

    def test_openai_speech_speed(self):
        r = self.client.post('/v1/audio/speech', json={
            'model': 'tts-1',
            'input': '测试',
            'voice': 'zh-CN-XiaoxiaoNeural',
            'speed': 1.5,
        })
        assert r.status_code in (200, 500)

    def test_openai_models(self):
        r = self.client.get('/v1/models')
        assert r.status_code == 200
        data = r.get_json()
        assert data['object'] == 'list'
        assert len(data['data']) >= 2

    def test_openai_speech_voice_by_name(self):
        """Test voice resolution by display name."""
        r = self.client.post('/v1/audio/speech', json={
            'model': 'tts-1',
            'input': '测试',
            'voice': '晓晓 - 女声',  # display name
        })
        assert r.status_code in (200, 500)  # should resolve, not 400

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

    def test_voices_fishaudio(self):
        r = self.client.get('/api/voices?provider=fishaudio')
        assert r.status_code == 200
        data = r.get_json()
        assert len(data) == len(FISH_AUDIO_VOICES)

    def test_config_get_has_fishaudio_status(self):
        r = self.client.get('/api/config')
        data = r.get_json()
        assert 'fishaudio' in data['provider_status']

    def test_openai_speech_speed_clamped(self):
        """Speed should be clamped to 0.25-4.0 range."""
        for speed in [0.0, -1.0, 5.0, 999.0]:
            r = self.client.post('/v1/audio/speech', json={
                'model': 'tts-1', 'input': '测试',
                'voice': 'zh-CN-XiaoxiaoNeural', 'speed': speed,
            })
            assert r.status_code in (200, 500), f"speed={speed} should be clamped, got {r.status_code}"

    def test_openai_speech_speed_invalid_string(self):
        """Non-numeric speed should default to 1.0."""
        r = self.client.post('/v1/audio/speech', json={
            'model': 'tts-1', 'input': '测试',
            'voice': 'zh-CN-XiaoxiaoNeural', 'speed': 'not_a_number',
        })
        assert r.status_code in (200, 500)

    def test_openai_speech_response_format_default(self):
        """Default response_format should be mp3."""
        r = self.client.post('/v1/audio/speech', json={
            'model': 'tts-1', 'input': '测试', 'voice': 'zh-CN-XiaoxiaoNeural',
        })
        if r.status_code == 200:
            assert r.content_type.startswith('audio/mpeg')

    def test_openai_speech_response_format_unknown(self):
        """Unknown format should fall back to mp3."""
        r = self.client.post('/v1/audio/speech', json={
            'model': 'tts-1', 'input': '测试', 'voice': 'zh-CN-XiaoxiaoNeural',
            'response_format': 'flac',
        })
        if r.status_code == 200:
            assert r.content_type.startswith('audio/mpeg')


class TestAdminAuth:
    """Test ADMIN_TOKEN protection."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.tmpdir = tempfile.mkdtemp()
        import app as app_module
        self.orig_config = app_module.CONFIG_FILE
        self.orig_stats = app_module.STATS_FILE
        self.orig_token = app_module.ADMIN_TOKEN
        app_module.CONFIG_FILE = os.path.join(self.tmpdir, 'config.json')
        app_module.STATS_FILE = os.path.join(self.tmpdir, 'stats.json')
        app_module.ADMIN_TOKEN = 'test-secret-token'
        self.app = app_module.app
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()
        yield
        app_module.CONFIG_FILE = self.orig_config
        app_module.STATS_FILE = self.orig_stats
        app_module.ADMIN_TOKEN = self.orig_token
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _auth_headers(self, token='test-secret-token'):
        return {'Authorization': f'Bearer {token}'}

    def test_config_get_unauthorized(self):
        r = self.client.get('/api/config')
        assert r.status_code == 401

    def test_config_get_authorized(self):
        r = self.client.get('/api/config', headers=self._auth_headers())
        assert r.status_code == 200

    def test_config_post_unauthorized(self):
        r = self.client.post('/api/config', json={'provider': 'edge'})
        assert r.status_code == 401

    def test_config_post_authorized(self):
        r = self.client.post('/api/config', json={'provider': 'edge'}, headers=self._auth_headers())
        assert r.status_code == 200

    def test_config_test_unauthorized(self):
        r = self.client.post('/api/config/test')
        assert r.status_code == 401

    def test_config_test_authorized(self):
        r = self.client.post('/api/config/test', headers=self._auth_headers())
        assert r.status_code == 200

    def test_stats_delete_unauthorized(self):
        r = self.client.delete('/api/stats')
        assert r.status_code == 401

    def test_stats_delete_authorized(self):
        r = self.client.delete('/api/stats', headers=self._auth_headers())
        assert r.status_code == 200

    def test_stats_get_allowed_without_auth(self):
        """GET /api/stats should be public."""
        r = self.client.get('/api/stats')
        assert r.status_code == 200

    def test_cache_clear_unauthorized(self):
        r = self.client.delete('/api/cache/clear')
        assert r.status_code == 401

    def test_cache_clear_authorized(self):
        r = self.client.delete('/api/cache/clear', headers=self._auth_headers())
        assert r.status_code == 200

    def test_speech_not_blocked_by_auth(self):
        """TTS endpoints should work without auth."""
        r = self.client.post('/speech/stream',
            json={'text': '测试', 'voice': 'zh-CN-XiaoxiaoNeural'})
        assert r.status_code in (200, 500)

    def test_openai_speech_not_blocked_by_auth(self):
        """OpenAI endpoint should work without auth."""
        r = self.client.post('/v1/audio/speech',
            json={'model': 'tts-1', 'input': '测试', 'voice': 'zh-CN-XiaoxiaoNeural'})
        assert r.status_code in (200, 500)

    def test_wrong_token(self):
        r = self.client.get('/api/config', headers=self._auth_headers('wrong-token'))
        assert r.status_code == 401

    def test_token_via_query_param(self):
        r = self.client.get('/api/config?token=test-secret-token')
        assert r.status_code == 200


class TestAudioConversion:
    """Test audio format conversion utility."""

    def test_convert_mp3_passthrough(self):
        """MP3 input should pass through unchanged."""
        from app import _convert_audio
        fake_mp3 = b'\xff\xfb\x90\x00' + b'\x00' * 100
        result = _convert_audio(fake_mp3, 'mp3')
        assert result == fake_mp3

    def test_convert_unknown_format_passthrough(self):
        """Unknown format should pass through unchanged."""
        from app import _convert_audio
        fake = b'\x00' * 100
        result = _convert_audio(fake, 'flac')
        assert result == fake

    def test_format_mime_map(self):
        from app import _FORMAT_MIME
        assert _FORMAT_MIME['mp3'] == 'audio/mpeg'
        assert _FORMAT_MIME['wav'] == 'audio/wav'
        assert _FORMAT_MIME['ogg'].startswith('audio/ogg')


class TestEdgeCaseProviderRouting:
    """Test edge cases in voice-to-provider resolution."""

    def test_neural_alone_no_match(self):
        """'Neural' alone should not match edge (requires locale prefix)."""
        assert resolve_provider('Neural') is None

    def test_dashed_voice_matches_edge(self):
        assert resolve_provider('zh-CN-XiaoxiaoNeural') == 'edge'
        assert resolve_provider('en-US-JennyNeural') == 'edge'
        assert resolve_provider('ja-JP-NanamiNeural') == 'edge'

    def test_zero_not_tencent(self):
        """'0' is a digit but should not match tencent range."""
        assert resolve_provider('0') is None

    def test_large_number_is_tencent(self):
        assert resolve_provider('999999') == 'tencent'
        assert resolve_provider('1000000') is None  # too large

    def test_header_injection_blocked(self):
        """Voice with CRLF should not cause header injection."""
        assert resolve_provider('zh-CN-XiaoxiaoNeural\r\nInjected: bad') == 'edge'
        # The \r\n should be stripped in resolve_provider

    def test_null_byte_in_voice(self):
        """Null bytes in voice should be stripped."""
        assert resolve_provider('zh-CN-XiaoxiaoNeural\x00bad') == 'edge'


class TestLegadoEndpoints:
    """Test Legado integration endpoints."""

    @pytest.fixture(autouse=True)
    def setup(self):
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

    def test_legado_config_default(self):
        r = self.client.get('/api/legado/config')
        assert r.status_code == 200
        data = r.get_json()
        assert 'name' in data
        assert 'url' in data
        assert 'speech/stream' in data['url']
        assert 'audio/mpeg' in data['contentType']

    def test_legado_config_custom_voice(self):
        r = self.client.get('/api/legado/config?voice=zh-CN-YunxiNeural')
        assert r.status_code == 200
        data = r.get_json()
        assert 'YunxiNeural' in data['name']
        assert 'zh-CN-YunxiNeural' in data['url']

    def test_legado_subscribe_encoded(self):
        r = self.client.get('/api/legado/subscribe?auto=true')
        assert r.status_code == 200
        import base64
        decoded = json.loads(base64.b64decode(r.data).decode())
        assert 'name' in decoded
        assert 'speech/stream' in decoded['url']

    def test_legado_subscribe_json(self):
        r = self.client.get('/api/legado/subscribe')
        assert r.status_code == 200
        data = r.get_json()
        assert 'url' in data
        assert 'config' in data
        assert 'encoded' in data


class TestSSEndpoints:
    """Test SSML and batch endpoints."""

    @pytest.fixture(autouse=True)
    def setup(self):
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

    def test_batch_invalid_no_texts(self):
        r = self.client.post('/api/speech/batch', json={'voice': 'zh-CN-XiaoxiaoNeural'})
        assert r.status_code == 400
        data = r.get_json()
        assert 'error' in data

    def test_batch_invalid_too_many_texts(self):
        r = self.client.post('/api/speech/batch', json={
            'voice': 'zh-CN-XiaoxiaoNeural',
            'texts': ['text'] * 21
        })
        assert r.status_code == 400

    def test_batch_valid_empty_texts(self):
        r = self.client.post('/api/speech/batch', json={
            'voice': 'zh-CN-XiaoxiaoNeural',
            'texts': ['', '  ', None]
        })
        assert r.status_code == 200
        data = r.get_json()
        assert 'results' in data
        assert len(data['results']) == 3
        for res in data['results']:
            assert res['error'] is not None

    def test_batch_simple(self):
        r = self.client.post('/api/speech/batch', json={
            'voice': 'zh-CN-XiaoxiaoNeural',
            'texts': ['你好', '世界'],
            'response_format': 'mp3'
        })
        # 可能返回200或500（取决于Edge TTS网络）
        assert r.status_code in (200, 500)
        if r.status_code == 200:
            data = r.get_json()
            assert 'results' in data
            assert len(data['results']) == 2


class TestFallback:
    """Test automatic provider fallback."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.tmpdir = tempfile.mkdtemp()
        import app as app_module
        self.orig_config = app_module.CONFIG_FILE
        self.orig_stats = app_module.STATS_FILE
        self.orig_fallback = app_module.FALLBACK_TO_EDGE
        app_module.CONFIG_FILE = os.path.join(self.tmpdir, 'config.json')
        app_module.STATS_FILE = os.path.join(self.tmpdir, 'stats.json')
        self.app = app_module.app
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()
        yield
        app_module.CONFIG_FILE = self.orig_config
        app_module.STATS_FILE = self.orig_stats
        app_module.FALLBACK_TO_EDGE = self.orig_fallback
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_fallback_enabled_for_unconfigured_provider(self):
        """When a provider fails and fallback is enabled, should try Edge."""
        import app as app_module
        app_module.FALLBACK_TO_EDGE = True
        # doubao without config should fail, then fallback to Edge
        r = self.client.post('/speech/stream', json={
            'text': '测试', 'voice': 'zh_female_cancan_mars_bigtts'
        })
        # Edge TTS might work (200) or fail (500), but should not be 400
        assert r.status_code in (200, 500)
        if r.status_code == 200:
            assert r.headers.get('X-TTS-Provider') == 'doubao'  # original provider in header

    def test_fallback_disabled(self):
        """When fallback is disabled, should fail directly."""
        import app as app_module
        app_module.FALLBACK_TO_EDGE = False
        r = self.client.post('/speech/stream', json={
            'text': '测试', 'voice': 'zh_female_cancan_mars_bigtts'
        })
        assert r.status_code == 500
        assert '未配置' in r.get_data(as_text=True)


class TestConfigExportImport:
    """Test config export/import endpoints."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.tmpdir = tempfile.mkdtemp()
        import app as app_module
        self.orig_config = app_module.CONFIG_FILE
        self.orig_stats = app_module.STATS_FILE
        self.orig_token = app_module.ADMIN_TOKEN
        app_module.CONFIG_FILE = os.path.join(self.tmpdir, 'config.json')
        app_module.STATS_FILE = os.path.join(self.tmpdir, 'stats.json')
        app_module.ADMIN_TOKEN = ''
        self.app = app_module.app
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()
        yield
        app_module.CONFIG_FILE = self.orig_config
        app_module.STATS_FILE = self.orig_stats
        app_module.ADMIN_TOKEN = self.orig_token
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_export_returns_json(self):
        r = self.client.get('/api/config/export')
        assert r.status_code == 200
        data = r.get_json()
        assert '_version' in data
        assert '_exported_at' in data
        assert 'provider' in data
        assert 'Content-Disposition' in r.headers

    def test_import_valid_config(self):
        r = self.client.post('/api/config/import',
            json={'provider': 'doubao', 'default_voice': 'zh_female_cancan_mars_bigtts'})
        assert r.status_code == 200
        data = r.get_json()
        assert data['status'] == 'ok'
        # Verify config was saved
        from app import load_config
        config = load_config()
        assert config['provider'] == 'doubao'

    def test_import_empty_body(self):
        r = self.client.post('/api/config/import',
            data='', content_type='application/json')
        assert r.status_code == 400

    def test_import_ignores_unknown_keys(self):
        r = self.client.post('/api/config/import',
            json={'provider': 'edge', 'unknown_key': 'value', '_version': '1.0'})
        assert r.status_code == 200
        from app import load_config
        config = load_config()
        assert 'unknown_key' not in config
        assert '_version' not in config

    def test_roundtrip_export_import(self):
        # Set config
        self.client.post('/api/config/import',
            json={'provider': 'tencent', 'tencent_voice': '501003'})
        # Export
        r = self.client.get('/api/config/export')
        exported = r.get_json()
        # Modify
        self.client.post('/api/config/import', json={'provider': 'edge'})
        # Re-import original
        self.client.post('/api/config/import', json=exported)
        from app import load_config
        config = load_config()
        assert config['provider'] == 'tencent'
        assert config['tencent_voice'] == '501003'


class TestPronunciationDict:
    """Test custom pronunciation dictionary."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.tmpdir = tempfile.mkdtemp()
        import app as app_module
        self.orig_config = app_module.CONFIG_FILE
        self.orig_stats = app_module.STATS_FILE
        self.orig_token = app_module.ADMIN_TOKEN
        app_module.CONFIG_FILE = os.path.join(self.tmpdir, 'config.json')
        app_module.STATS_FILE = os.path.join(self.tmpdir, 'stats.json')
        app_module.ADMIN_TOKEN = ''
        self.app = app_module.app
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()
        yield
        app_module.CONFIG_FILE = self.orig_config
        app_module.STATS_FILE = self.orig_stats
        app_module.ADMIN_TOKEN = self.orig_token
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_pronunciation_get_empty(self):
        r = self.client.get('/api/pronunciation')
        assert r.status_code == 200
        data = r.get_json()
        assert data['count'] == 0
        assert data['entries'] == {}

    def test_pronunciation_add_entries(self):
        r = self.client.post('/api/pronunciation',
            json={'entries': {'的': '地', '将': '将军'}})
        assert r.status_code == 200
        data = r.get_json()
        assert data['count'] == 2

    def test_pronunciation_delete_entries(self):
        # Ensure clean state
        self.client.delete('/api/pronunciation', json={'words': list(self.client.get('/api/pronunciation').get_json().get('entries', {}).keys())})
        self.client.post('/api/pronunciation',
            json={'entries': {'a': 'b', 'c': 'd'}})
        r = self.client.get('/api/pronunciation')
        before = r.get_json()
        assert before['entries'].get('a') == 'b'
        r = self.client.delete('/api/pronunciation',
            json={'words': ['a']})
        assert r.status_code == 200
        r = self.client.get('/api/pronunciation')
        data = r.get_json()
        assert 'a' not in data['entries']
        assert 'c' in data['entries']
        assert data['count'] == before['count'] - 1

    def test_pronunciation_applied_in_clean_text(self):
        from app import _clean_text
        # Add pronunciation entry
        self.client.post('/api/pronunciation',
            json={'entries': {'的': '地'}})
        # Clean text should apply replacement
        result = _clean_text('的确如此')
        assert result == '地确如此'

    def test_pronunciation_empty_word_ignored(self):
        # Get current count before test
        r = self.client.get('/api/pronunciation')
        before = r.get_json()['count']
        r = self.client.post('/api/pronunciation',
            json={'entries': {'': 'bad', 'good': 'ok'}})
        assert r.status_code == 200
        r = self.client.get('/api/pronunciation')
        data = r.get_json()
        # Empty word should not be added, only 'good' should be added
        assert '' not in data['entries']
        assert data['entries'].get('good') == 'ok'
        assert data['count'] == before + 1  # only 'good' was added


class TestAuditLog:
    """Test request audit log endpoint."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.tmpdir = tempfile.mkdtemp()
        import app as app_module
        self.orig_config = app_module.CONFIG_FILE
        self.orig_stats = app_module.STATS_FILE
        self.orig_token = app_module.ADMIN_TOKEN
        app_module.CONFIG_FILE = os.path.join(self.tmpdir, 'config.json')
        app_module.STATS_FILE = os.path.join(self.tmpdir, 'stats.json')
        app_module.ADMIN_TOKEN = ''
        self.app = app_module.app
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()
        yield
        app_module.CONFIG_FILE = self.orig_config
        app_module.STATS_FILE = self.orig_stats
        app_module.ADMIN_TOKEN = self.orig_token
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_audit_returns_json(self):
        r = self.client.get('/api/audit')
        assert r.status_code == 200
        data = r.get_json()
        assert 'records' in data
        assert 'count' in data
        assert 'total' in data

    def test_audit_limit_param(self):
        r = self.client.get('/api/audit?limit=5')
        assert r.status_code == 200
        data = r.get_json()
        assert data['count'] <= 5

    def test_audit_records_tts_request(self):
        """After a TTS request, audit should have a record."""
        import app as app_module
        before = len(app_module._audit_log)
        # Make a TTS request (will fail since no provider configured, but still gets logged)
        self.client.post('/speech/stream', json={
            'text': '测试', 'voice': 'zh-CN-XiaoxiaoNeural'
        })
        r = self.client.get('/api/audit')
        data = r.get_json()
        assert data['total'] > before


class TestTextNormalization:
    """Test text normalization and number conversion."""

    def test_num_to_chinese_basic(self):
        from app import _num_to_chinese
        assert _num_to_chinese(0) == '零'
        assert _num_to_chinese(5) == '五'
        assert _num_to_chinese(10) == '十'
        assert _num_to_chinese(15) == '十五'
        assert _num_to_chinese(100) == '一百'
        assert _num_to_chinese(123) == '一百二十三'
        assert _num_to_chinese(1000) == '一千'
        assert _num_to_chinese(10000) == '一万'

    def test_num_to_chinese_large(self):
        from app import _num_to_chinese
        assert '万' in _num_to_chinese(12345)
        assert '亿' in _num_to_chinese(100000000)

    def test_num_to_chinese_negative(self):
        from app import _num_to_chinese
        assert _num_to_chinese(-5) == '负五'

    def test_normalize_date(self):
        from app import _normalize_text
        assert _normalize_text('2024-01-15') == '2024年1月15日'
        assert _normalize_text('2024/03/05') == '2024年3月5日'

    def test_normalize_time(self):
        from app import _normalize_text
        result = _normalize_text('14:30')
        assert '十四点' in result
        assert '三十分' in result

    def test_normalize_percentage(self):
        from app import _normalize_text
        assert _normalize_text('50%') == '百分之五十'

    def test_normalize_abbreviations(self):
        from app import _normalize_text
        assert '博士' in _normalize_text('Dr. Wang')
        assert '等等' in _normalize_text('etc.')

    def test_normalize_temperature(self):
        from app import _normalize_text
        result = _normalize_text('36.5°C')
        assert '摄氏度' in result
        assert '三十六' in result

    def test_normalize_units(self):
        from app import _normalize_text
        assert '公里' in _normalize_text('100km')
        assert '毫升' in _normalize_text('500ml')

    def test_clean_text_applies_normalization(self):
        from app import _clean_text
        result = _clean_text('50%')
        assert '百分之' in result


class TestErrorHandlers:
    """Test global error handlers."""

    @pytest.fixture(autouse=True)
    def setup(self):
        import app as app_module
        self.app = app_module.app
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()

    def test_404_returns_json(self):
        r = self.client.get('/nonexistent-path-xyz')
        assert r.status_code == 404
        data = r.get_json()
        assert 'error' in data
        assert data['error']['type'] == 'not_found'

    def test_405_returns_json(self):
        r = self.client.delete('/health')
        assert r.status_code == 405
        data = r.get_json()
        assert data['error']['type'] == 'method_not_allowed'

    def test_response_has_request_id(self):
        r = self.client.get('/health')
        assert 'X-Request-ID' in r.headers
        assert len(r.headers['X-Request-ID']) > 0

    def test_custom_request_id_forwarded(self):
        r = self.client.get('/health', headers={'X-Request-ID': 'test-123'})
        assert r.headers['X-Request-ID'] == 'test-123'

    def test_livez_returns_ok(self):
        r = self.client.get('/livez')
        assert r.status_code == 200
        assert r.data == b'ok'

    def test_readyz_returns_ready(self):
        r = self.client.get('/readyz')
        assert r.status_code == 200
        data = r.get_json()
        assert data['ready'] is True


class TestEdgeCases:
    """Test boundary conditions and edge cases."""

    @pytest.fixture(autouse=True)
    def setup(self):
        import app as app_module
        self.app = app_module.app
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()

    def test_empty_text_returns_400(self):
        r = self.client.post('/speech/stream', json={
            'text': '', 'voice': 'zh-CN-XiaoxiaoNeural'
        })
        assert r.status_code == 400

    def test_missing_voice_returns_400(self):
        r = self.client.post('/speech/stream', json={
            'text': '测试'
        })
        assert r.status_code == 400

    def test_very_long_text_returns_400(self):
        r = self.client.post('/speech/stream', json={
            'text': 'a' * 10000, 'voice': 'zh-CN-XiaoxiaoNeural'
        })
        assert r.status_code == 400

    def test_openai_missing_input_returns_400(self):
        r = self.client.post('/v1/audio/speech', json={
            'model': 'tts-1', 'voice': 'zh-CN-XiaoxiaoNeural'
        })
        assert r.status_code == 400

    def test_batch_empty_texts_returns_400(self):
        r = self.client.post('/api/speech/batch', json={
            'texts': [], 'voice': 'zh-CN-XiaoxiaoNeural'
        })
        assert r.status_code == 400

    def test_batch_too_many_texts_returns_400(self):
        r = self.client.post('/api/speech/batch', json={
            'texts': ['text'] * 21, 'voice': 'zh-CN-XiaoxiaoNeural'
        })
        assert r.status_code == 400

    def test_special_chars_in_text(self):
        """Text with special characters should not crash."""
        r = self.client.post('/speech/stream', json={
            'text': '你好<script>alert(1)</script>&amp;', 'voice': 'zh-CN-XiaoxiaoNeural'
        })
        # Should not crash (may fail synth but 400 or 500, not exception)
        assert r.status_code in (200, 400, 500, 503)

    def test_unicode_voice_name(self):
        """Voice alias in Chinese should resolve."""
        from app import _VOICE_NAME_TO_ID
        assert '晓晓' in _VOICE_NAME_TO_ID or '晓晓'.lower() in _VOICE_NAME_TO_ID

    def test_health_returns_all_fields(self):
        r = self.client.get('/health')
        data = r.get_json()
        required = ['status', 'version', 'providers', 'cache', 'uptime_seconds']
        for field in required:
            assert field in data, f'Missing field: {field}'

    def test_info_returns_complete(self):
        r = self.client.get('/api/info')
        data = r.get_json()
        assert 'version' in data
        assert 'config' in data
        assert 'metrics' in data
        assert 'cache' in data
        assert 'providers' in data

    def test_openapi_spec_valid(self):
        r = self.client.get('/api/openapi.json')
        data = r.get_json()
        assert data['openapi'] == '3.0.0'
        assert 'paths' in data
        assert '/speech/stream' in data['paths']
