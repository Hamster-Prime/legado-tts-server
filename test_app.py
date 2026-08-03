#!/usr/bin/env python3
"""Unit tests for legado-tts-server"""

import json
import logging
import os
import sys
import tempfile
import base64
import uuid

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
            test_config = {
                'provider': 'edge',
                'doubao_api_key': 'test-key',
            }
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


class TestStatsPersistenceFailure:
    """An unusable STATS_FILE must degrade quietly, not flood the log."""

    def test_unwritable_stats_latches_after_one_error(self, caplog, monkeypatch):
        import app as app_module
        monkeypatch.setattr(app_module, '_stats_readonly', False)
        monkeypatch.setattr(app_module, 'STATS_FILE', '/proc/nope/stats.json')
        with caplog.at_level('ERROR', logger=app_module.log.name):
            for _ in range(5):
                app_module.update_stats(10, 'edge', voice='v')
        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert len(errors) == 1, [r.getMessage() for r in errors]
        assert app_module._stats_readonly is True

    def test_metrics_still_counted_when_stats_unusable(self, monkeypatch):
        """Synthesis doesn't depend on stats; /metrics must stay accurate."""
        import app as app_module
        monkeypatch.setattr(app_module, '_stats_readonly', True)
        with app_module._metrics_lock:
            before = app_module._metrics['chars_total']
        app_module.update_stats(42, 'edge', voice='v')
        with app_module._metrics_lock:
            assert app_module._metrics['chars_total'] == before + 42

    def test_fatal_path_errors_are_not_retried(self, monkeypatch):
        """Retrying a permission/ENOENT failure only adds latency."""
        import app as app_module
        calls = []

        def boom(*a, **k):
            calls.append(1)
            raise PermissionError(13, 'Permission denied')

        monkeypatch.setattr(app_module, 'load_stats', boom)
        with pytest.raises(PermissionError):
            app_module._update_stats_with_retry(1, 'edge')
        assert len(calls) == 1


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
                assert cfg['doubao_api_key'] == ''
            finally:
                app_module.CONFIG_FILE = orig
        finally:
            os.unlink(path)

    def test_legacy_doubao_auth_is_purged_without_migration(self):
        legacy = {
            'provider': 'doubao',
            'appid': 'old-app-id',
            'access_token': 'old-token',
            'cluster': 'volcano_tts',
            'doubao_resource_id': 'seed-icl-2.0',
            'default_voice': 'zh_female_cancan_mars_bigtts',
        }
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(legacy, f)
            path = f.name
        try:
            import app as app_module
            orig = app_module.CONFIG_FILE
            app_module.CONFIG_FILE = path
            try:
                cfg = load_config()
                assert cfg['doubao_api_key'] == ''
                assert cfg['default_voice'] == 'zh_female_cancan_uranus_bigtts'
                for key in ('appid', 'access_token', 'cluster', 'doubao_resource_id'):
                    assert key not in cfg
                persisted = _read_json(path, {})
                for key in ('appid', 'access_token', 'cluster', 'doubao_resource_id'):
                    assert key not in persisted
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

    def test_dispatch_unknown_provider(self, monkeypatch):
        import app as app_module
        monkeypatch.setattr(app_module, 'FALLBACK_TO_EDGE', False)
        audio, error, actual_provider, actual_voice = app_module.dispatch(
            'unknown', 'test', 'test', 0
        )
        assert audio is None
        assert 'Unknown provider' in error
        assert actual_provider is None
        assert actual_voice is None

    def test_dispatch_edge_no_network(self, monkeypatch):
        """Provider routing can be tested without calling the public Edge service."""
        import app as app_module
        app_module._cache_clear()
        monkeypatch.setattr(
            app_module, 'synthesize_edge',
            lambda *args, **kwargs: (b'edge-audio', None),
        )
        audio, error, actual_provider, actual_voice = app_module.dispatch(
            'edge', '你好', 'zh-CN-XiaoxiaoNeural', 0
        )
        assert audio is not None, f"Edge TTS failed: {error}"
        assert len(audio) > 0
        assert actual_provider == 'edge'
        assert actual_voice == 'zh-CN-XiaoxiaoNeural'

    def test_dispatch_doubao_no_config(self, monkeypatch):
        import app as app_module
        monkeypatch.setattr(app_module, 'FALLBACK_TO_EDGE', False)
        audio, error, actual_provider, actual_voice = app_module.dispatch(
            'doubao', 'test', 'zh_female_cancan_uranus_bigtts', 0
        )
        assert audio is None
        assert '未配置' in error
        assert actual_provider is None
        assert actual_voice is None

    def test_dispatch_tencent_no_config(self, monkeypatch):
        import app as app_module
        monkeypatch.setattr(app_module, 'FALLBACK_TO_EDGE', False)
        audio, error, actual_provider, actual_voice = app_module.dispatch(
            'tencent', 'test', '501002', 0
        )
        assert audio is None
        assert '未配置' in error
        assert actual_provider is None
        assert actual_voice is None

    def test_dispatch_xiaomi_no_config(self, monkeypatch):
        import app as app_module
        monkeypatch.setattr(app_module, 'FALLBACK_TO_EDGE', False)
        audio, error, actual_provider, actual_voice = app_module.dispatch(
            'xiaomi', 'test', 'mimo_default', 0
        )
        assert audio is None
        assert '未配置' in error
        assert actual_provider is None
        assert actual_voice is None

    def test_dispatch_fishaudio_no_config(self, monkeypatch):
        import app as app_module
        monkeypatch.setattr(app_module, 'FALLBACK_TO_EDGE', False)
        audio, error, actual_provider, actual_voice = app_module.dispatch(
            'fishaudio', 'test', 'fish-animated', 0
        )
        assert audio is None
        assert '未配置' in error
        assert actual_provider is None
        assert actual_voice is None


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
    def setup_app(self, monkeypatch):
        self.tmpdir = tempfile.mkdtemp()
        import app as app_module
        self.orig_config = app_module.CONFIG_FILE
        self.orig_stats = app_module.STATS_FILE
        app_module.CONFIG_FILE = os.path.join(self.tmpdir, 'config.json')
        app_module.STATS_FILE = os.path.join(self.tmpdir, 'stats.json')
        app_module._cache_clear()
        monkeypatch.setattr(
            app_module, 'synthesize_edge',
            lambda *args, **kwargs: (b'edge-test-audio', None),
        )
        self.app = app_module.app
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()
        yield
        app_module._cache_clear()
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
        assert 'doubao_api_key' in data
        assert 'appid' not in data
        assert 'access_token' not in data

    def test_config_post(self):
        r = self.client.post('/api/config', json={'provider': 'edge'})
        assert r.status_code == 200
        assert r.get_json()['status'] == 'ok'

    def test_config_post_masked_values_preserved(self):
        # First save a real value
        self.client.post('/api/config', json={'doubao_api_key': 'real_key'})
        # Now save with masked token
        self.client.post('/api/config', json={'doubao_api_key': '***'})
        # Verify token was not overwritten
        cfg = load_config()
        assert cfg['doubao_api_key'] == 'real_key'

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
        import app as app_module
        r = self.client.get('/health')
        data = r.get_json()
        assert 'version' in data
        assert data['version'] == app_module.__version__

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
            'doubao_api_key': 'my_doubao_key_12345',
            'tencent_secret_key': 'my_tencent_key_abcdef',
        })
        r = self.client.get('/api/config')
        data = r.get_json()
        assert data['doubao_api_key'] == '***'
        assert data['tencent_secret_key'] == '***'
        assert 'my_doubao_key' not in str(data)
        assert 'appid' not in data
        assert 'access_token' not in data

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

    def test_openai_speech_response_format_flac(self):
        """A supported non-mp3 format is honoured when ffmpeg is available,
        and falls back to mp3 (advertised as such) when it is not."""
        from app import _FORMAT_MIME
        r = self.client.post('/v1/audio/speech', json={
            'model': 'tts-1', 'input': '测试', 'voice': 'zh-CN-XiaoxiaoNeural',
            'response_format': 'flac',
        })
        if r.status_code == 200:
            actual = r.headers.get('X-TTS-Format')
            assert actual in ('flac', 'mp3')
            # The content type must always describe the bytes actually sent.
            assert r.content_type.startswith(_FORMAT_MIME[actual])

    def test_openai_speech_response_format_unsupported(self):
        """An unrecognized format falls back to mp3."""
        r = self.client.post('/v1/audio/speech', json={
            'model': 'tts-1', 'input': '测试', 'voice': 'zh-CN-XiaoxiaoNeural',
            'response_format': 'nonsense',
        })
        if r.status_code == 200:
            assert r.content_type.startswith('audio/mpeg')
            assert r.headers.get('X-TTS-Format') == 'mp3'


class TestAdminAuth:
    """Test ADMIN_TOKEN protection."""

    @pytest.fixture(autouse=True)
    def setup(self, monkeypatch):
        self.tmpdir = tempfile.mkdtemp()
        import app as app_module
        self.orig_config = app_module.CONFIG_FILE
        self.orig_stats = app_module.STATS_FILE
        self.orig_token = app_module.ADMIN_TOKEN
        app_module.CONFIG_FILE = os.path.join(self.tmpdir, 'config.json')
        app_module.STATS_FILE = os.path.join(self.tmpdir, 'stats.json')
        app_module.ADMIN_TOKEN = 'test-secret-token'
        app_module._cache_clear()
        monkeypatch.setattr(
            app_module, 'synthesize_edge',
            lambda *args, **kwargs: (b'edge-test-audio', None),
        )
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
        result, fmt = _convert_audio(fake_mp3, 'mp3')
        assert result == fake_mp3
        assert fmt == 'mp3'

    def test_convert_undecodable_input_reports_mp3(self):
        """When ffmpeg cannot decode the input, the original bytes are returned
        and the reported format must be mp3 — not the requested format."""
        from app import _convert_audio
        fake = b'\x00' * 100
        result, fmt = _convert_audio(fake, 'flac')
        assert result == fake
        assert fmt == 'mp3'

    def test_convert_unsupported_format_reports_mp3(self):
        """An unrecognized format name is not passed to ffmpeg."""
        from app import _convert_audio
        fake = b'\xff\xfb\x90\x00' + b'\x00' * 100
        result, fmt = _convert_audio(fake, 'nonsense')
        assert result == fake
        assert fmt == 'mp3'

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
    def setup(self, monkeypatch):
        self.tmpdir = tempfile.mkdtemp()
        import app as app_module
        self.orig_config = app_module.CONFIG_FILE
        self.orig_stats = app_module.STATS_FILE
        app_module.CONFIG_FILE = os.path.join(self.tmpdir, 'config.json')
        app_module.STATS_FILE = os.path.join(self.tmpdir, 'stats.json')
        app_module._cache_clear()
        monkeypatch.setattr(
            app_module, 'synthesize_edge',
            lambda *args, **kwargs: (b'edge-test-audio', None),
        )
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
    def setup(self, monkeypatch):
        self.tmpdir = tempfile.mkdtemp()
        import app as app_module
        self.orig_config = app_module.CONFIG_FILE
        self.orig_stats = app_module.STATS_FILE
        self.orig_fallback = app_module.FALLBACK_TO_EDGE
        app_module.CONFIG_FILE = os.path.join(self.tmpdir, 'config.json')
        app_module.STATS_FILE = os.path.join(self.tmpdir, 'stats.json')
        app_module._cache_clear()
        monkeypatch.setattr(
            app_module, 'synthesize_edge',
            lambda *args, **kwargs: (b'edge-fallback-audio', None),
        )
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
            'text': '测试', 'voice': 'zh_female_cancan_uranus_bigtts'
        })
        # Edge TTS might work (200) or fail (500), but should not be 400
        assert r.status_code in (200, 500)
        if r.status_code == 200:
            # X-TTS-Provider reports who actually synthesized the audio; the
            # requested provider is disclosed separately.
            assert r.headers.get('X-TTS-Provider') == 'edge'
            assert r.headers.get('X-TTS-Fallback') == 'true'
            assert r.headers.get('X-TTS-Requested-Provider') == 'doubao'

    def test_fallback_disabled(self):
        """When fallback is disabled, should fail directly."""
        import app as app_module
        app_module.FALLBACK_TO_EDGE = False
        r = self.client.post('/speech/stream', json={
            'text': '测试', 'voice': 'zh_female_cancan_uranus_bigtts'
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
            json={'provider': 'doubao', 'default_voice': 'zh_female_cancan_uranus_bigtts'})
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
    def setup(self, monkeypatch):
        self.tmpdir = tempfile.mkdtemp()
        import app as app_module
        self.orig_config = app_module.CONFIG_FILE
        self.orig_stats = app_module.STATS_FILE
        self.orig_token = app_module.ADMIN_TOKEN
        app_module.CONFIG_FILE = os.path.join(self.tmpdir, 'config.json')
        app_module.STATS_FILE = os.path.join(self.tmpdir, 'stats.json')
        app_module.ADMIN_TOKEN = ''
        app_module._cache_clear()
        monkeypatch.setattr(
            app_module, 'synthesize_edge',
            lambda *args, **kwargs: (b'edge-test-audio', None),
        )
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
        # Years read digit by digit, month/day as numbers
        assert _normalize_text('2024-01-15') == '二零二四年一月十五日'
        assert _normalize_text('2024/03/05') == '二零二四年三月五日'

    def test_normalize_date_not_double_converted(self):
        """The year must not be re-processed by the later 4-digit-year rule."""
        from app import _normalize_text
        assert '2024' not in _normalize_text('2024-01-15')
        assert _normalize_text('2024-01-15').count('年') == 1

    def test_normalize_keeps_standalone_english_letters(self):
        """Single I/V/X/L are English words, not Roman numerals to speak."""
        from app import _normalize_text
        assert _normalize_text('I have 3 apples') == 'I have 3 apples'
        assert _normalize_text('V for victory') == 'V for victory'
        assert _normalize_text('X-ray') == 'X-ray'

    def test_normalize_multichar_roman(self):
        from app import _normalize_text
        assert _normalize_text('Chapter II') == 'Chapter 二'
        assert _normalize_text('Part VIII') == 'Part 八'

    def test_normalize_invalid_dates_and_times_untouched(self):
        from app import _normalize_text
        assert _normalize_text('99:99') == '99:99'

    def test_num_to_chinese_no_scientific_notation(self):
        """Huge floats must not leak 'e'/'+' into the digit table."""
        from app import _num_to_chinese
        for bad in ('e', '+', '.', 'n', 'i'):
            assert bad not in _num_to_chinese(1e21)
        assert _num_to_chinese(float('nan')) == ''
        assert _num_to_chinese(float('inf')) == ''

    def test_normalize_currency(self):
        from app import _normalize_text
        assert _normalize_text('$100') == '一百美元'
        assert _normalize_text('￥50') == '五十元'
        assert _normalize_text('100日元') == '一百日元'

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

    def test_normalize_thousands_separator(self):
        """Comma-grouped numbers must convert whole, not just the first group."""
        from app import _normalize_text
        assert _normalize_text('1,234元') == '一千二百三十四元'
        assert _normalize_text('¥1,000') == '一千元'
        assert _normalize_text('$1,234.56') == '一千二百三十四点五六美元'
        assert _normalize_text('1,000,000') == '一百万'
        assert _normalize_text('人口 1,400,000,000') == '人口 十四亿'
        assert _normalize_text('12,345km') == '一万二千三百四十五公里'
        assert _normalize_text('增长 1,234.5%') == '增长 百分之一千二百三十四点五'
        assert _normalize_text('第1,024章') == '第一千零二十四章'

    def test_normalize_thousands_never_leaves_bare_digits(self):
        """A stripped separator must not orphan digits no later rule speaks.

        '人'/'个' are in _NUM_UNIT_SUFFIXES but no rule converts them, so the
        grouped number has to be spoken by the thousands pass itself.
        """
        from app import _normalize_text
        for text in ('共 1,234 人', '有 5,000 个', '共 1,234 项', '1,234名学生'):
            assert not any(c.isdigit() for c in _normalize_text(text)), text

    def test_normalize_thousands_ignores_non_numbers(self):
        """Only true 3-digit grouping is stripped; CSV-ish text is left alone."""
        from app import _normalize_text
        for text in ('a,b,c', '1,2,3', '版本 1,2', '1,23', '12,34'):
            assert _normalize_text(text) == text, text

    def test_normalize_grouped_number_not_read_digit_by_digit(self):
        """Grouping means magnitude: it must not fall through to _LONG_NUM_RE."""
        from app import _normalize_text
        # Digit-by-digit would render 1,024 as '一零二四' rather than '一千零二十四'.
        assert _normalize_text('1,024') == '一千零二十四'
        # An ungrouped 11-digit run is still an identifier.
        assert _normalize_text('13800138000') == '一三八零零一三八零零零'

    def test_clean_text_strips_unmapped_emoji(self):
        """Unmapped pictographs are dropped, not passed upstream verbatim."""
        from app import _clean_text, _RESIDUAL_EMOJI_RE
        assert _clean_text('😀未映射') == '未映射'
        assert _clean_text('测试🚀🔥火箭') == '测试火箭'
        assert _clean_text('🇨🇳 国旗') == '国旗'
        # Nothing the residual sweep targets may survive (CJK text is unaffected;
        # it sits outside the pictograph ranges the regex covers).
        for text in ('😀未映射', '测试🚀🔥火箭', '🇨🇳 国旗', '👍🏽 手势', '👨‍👩‍👧 家庭'):
            assert not _RESIDUAL_EMOJI_RE.search(_clean_text(text)), text

    def test_clean_text_maps_known_emoji_with_pauses(self):
        from app import _clean_text
        assert _clean_text('你好😊😃再见') == '你好，微笑，大笑，再见'

    def test_clean_text_collapses_punctuation_runs(self):
        """Emoji substitution inserts '，' that can abut existing punctuation."""
        from app import _clean_text
        assert _clean_text('好的😊。') == '好的，微笑。'
        assert _clean_text('真的吗😲？！') == '真的吗，震惊！'
        assert _clean_text('什么？？？') == '什么？'
        assert _clean_text('好的，，，谢谢') == '好的，谢谢'

    def test_clean_text_preserves_ellipsis(self):
        """An ellipsis is a pause cue; collapsing it to '.' would lose that."""
        from app import _clean_text
        assert _clean_text('ok...') == 'ok…'
        assert _clean_text('是吗。。。') == '是吗…'
        assert _clean_text('嗯...好的。') == '嗯…好的。'
        assert _clean_text('不……') == '不……'

    def test_clean_text_preserves_clean_input(self):
        from app import _clean_text
        for text in ('正常的句子。没有表情。', 'a.b.c', 'U.S.A.', '版本 2.0.1'):
            assert _clean_text(text) == text, text


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

    def test_readyz_returns_ready(self, tmp_path):
        import app as app_module
        orig = app_module.CONFIG_FILE
        app_module.CONFIG_FILE = str(tmp_path / 'config.json')
        try:
            r = self.client.get('/readyz')
            assert r.status_code == 200
            data = r.get_json()
            assert data['ready'] is True
        finally:
            app_module.CONFIG_FILE = orig


class TestEdgeCases:
    """Test boundary conditions and edge cases."""

    @pytest.fixture(autouse=True)
    def setup(self, monkeypatch):
        import app as app_module
        app_module._cache_clear()
        monkeypatch.setattr(
            app_module, 'synthesize_edge',
            lambda *args, **kwargs: (b'edge-test-audio', None),
        )
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


class TestAPIKeys:
    """Test API key authentication."""

    @pytest.fixture(autouse=True)
    def setup(self):
        import app as app_module
        self.app = app_module.app
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()

    def test_no_key_required_by_default(self):
        """API keys should not be required by default."""
        import app as app_module
        assert app_module.API_KEYS_REQUIRED is False

    def test_rate_limit_whitelist_bypass(self):
        """Whitelisted IPs should bypass rate limiting."""
        import app as app_module
        assert '127.0.0.1' in app_module.RATE_LIMIT_WHITELIST


class TestVoiceFavorites:
    """Test voice favorites API."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        import app as app_module
        self.app = app_module.app
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()
        self.orig_config = app_module.CONFIG_FILE
        app_module.CONFIG_FILE = str(tmp_path / 'config.json')
        yield
        app_module.CONFIG_FILE = self.orig_config

    def test_get_favorites(self):
        r = self.client.get('/api/favorites')
        assert r.status_code == 200
        data = r.get_json()
        assert 'favorites' in data
        assert isinstance(data['favorites'], list)

    def test_add_favorite(self):
        before = self.client.get('/api/favorites').get_json()['count']
        r = self.client.post('/api/favorites', json={'voice': 'zh-CN-XiaoxiaoNeural'})
        assert r.status_code == 200
        after = r.get_json()['count']
        assert after >= before + 1

    def test_remove_favorite(self):
        self.client.post('/api/favorites', json={'voice': 'test-remove-voice'})
        before = self.client.get('/api/favorites').get_json()['count']
        self.client.delete('/api/favorites', json={'voice': 'test-remove-voice'})
        after = self.client.get('/api/favorites').get_json()['count']
        assert after <= before

    def test_no_duplicate_favorites(self):
        self.client.post('/api/favorites', json={'voice': 'zh-CN-YunxiNeural'})
        count1 = self.client.get('/api/favorites').get_json()['count']
        self.client.post('/api/favorites', json={'voice': 'zh-CN-YunxiNeural'})
        count2 = self.client.get('/api/favorites').get_json()['count']
        assert count1 == count2


class _FakeDoubaoResponse:
    def __init__(self, events, status_code=200):
        self.events = events
        self.status_code = status_code
        self.headers = {'X-Tt-Logid': 'test-logid'}
        self.text = ''
        self.reason = 'OK'
        self.closed = False

    def iter_lines(self, decode_unicode=False):
        for event in self.events:
            if isinstance(event, (str, bytes)):
                yield event
            else:
                yield json.dumps(event, ensure_ascii=False)

    def close(self):
        self.closed = True


class TestDoubaoStreaming:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        import app as app_module
        self.app_module = app_module
        self.orig_config = app_module.CONFIG_FILE
        self.orig_stats = app_module.STATS_FILE
        self.orig_fallback = app_module.FALLBACK_TO_EDGE
        app_module.CONFIG_FILE = str(tmp_path / 'config.json')
        app_module.STATS_FILE = str(tmp_path / 'stats.json')
        app_module.FALLBACK_TO_EDGE = False
        app_module._cache_clear()
        config = app_module.DEFAULT_CONFIG.copy()
        config.update({
            'provider': 'doubao',
            'doubao_api_key': 'single-test-key',
            'default_voice': 'zh_female_cancan_uranus_bigtts',
        })
        app_module.save_config(config)
        self.client = app_module.app.test_client()
        yield
        app_module._cache_clear()
        app_module.CONFIG_FILE = self.orig_config
        app_module.STATS_FILE = self.orig_stats
        app_module.FALLBACK_TO_EDGE = self.orig_fallback

    @staticmethod
    def _success_response():
        return _FakeDoubaoResponse([
            {'code': 0, 'message': 'OK',
             'data': base64.b64encode(b'audio-1').decode()},
            {'code': 0, 'message': 'OK', 'sentence': {'text': '测试'}},
            {'code': 0, 'message': 'OK',
             'data': base64.b64encode(b'audio-2').decode()},
            {'code': 20000000, 'message': 'ok', 'data': None},
        ])

    def test_v3_single_key_protocol_and_audio_chunks(self, monkeypatch):
        captured = {}
        response = self._success_response()

        def fake_post(url, **kwargs):
            captured['url'] = url
            captured.update(kwargs)
            return response

        monkeypatch.setattr(self.app_module._http_session, 'post', fake_post)
        chunks = list(self.app_module.stream_doubao(
            '测试文本',
            'zh_female_cancan_uranus_bigtts',
            speech_rate=20,
            section_id='section-1',
        ))

        assert chunks == [b'audio-1', b'audio-2']
        assert captured['url'] == (
            'https://openspeech.bytedance.com/api/v3/tts/unidirectional'
        )
        assert self.app_module.DOUBAO_TTS_URL == captured['url']
        assert captured['stream'] is True
        headers = captured['headers']
        assert headers['X-Api-Key'] == 'single-test-key'
        assert headers['X-Api-Resource-Id'] == 'seed-tts-2.0'
        assert self.app_module.DOUBAO_RESOURCE_ID == 'seed-tts-2.0'
        uuid.UUID(headers['X-Api-Request-Id'])
        assert 'Authorization' not in headers
        payload = captured['json']
        assert set(payload) == {'req_params'}
        assert payload['req_params']['text'] == '测试文本'
        assert payload['req_params']['speaker'] == 'zh_female_cancan_uranus_bigtts'
        assert payload['req_params']['section_id'] == 'section-1'
        assert payload['req_params']['audio_params']['speech_rate'] == 20
        assert 'app' not in payload
        assert response.closed is True

    def test_provider_error_closes_upstream(self, monkeypatch):
        response = _FakeDoubaoResponse([
            {'code': 45000000, 'message': 'resource mismatch'},
        ])
        monkeypatch.setattr(
            self.app_module._http_session, 'post',
            lambda *args, **kwargs: response,
        )
        with pytest.raises(self.app_module.ProviderStreamError, match='45000000'):
            list(self.app_module.stream_doubao(
                '测试', 'zh_female_cancan_uranus_bigtts'
            ))
        assert response.closed is True

    def test_config_test_does_not_hide_doubao_error_with_edge_fallback(self, monkeypatch):
        response = _FakeDoubaoResponse([
            {'code': 45000000, 'message': 'resource mismatch'},
        ])
        monkeypatch.setattr(
            self.app_module._http_session, 'post',
            lambda *args, **kwargs: response,
        )
        monkeypatch.setattr(self.app_module, 'FALLBACK_TO_EDGE', True)

        def unexpected_edge(*args, **kwargs):
            pytest.fail('configuration test must not use Edge fallback')

        monkeypatch.setattr(self.app_module, 'synthesize_edge', unexpected_edge)
        result = self.client.post('/api/config/test')
        data = result.get_json()
        assert result.status_code == 200
        assert data['ok'] is False
        assert '45000000' in data['error']

    def test_legado_endpoint_streams_without_content_length(self, monkeypatch):
        response = self._success_response()
        monkeypatch.setattr(
            self.app_module._http_session, 'post',
            lambda *args, **kwargs: response,
        )
        result = self.client.post(
            '/speech/stream',
            json={
                'text': '测试文本',
                'voice': 'zh_female_cancan_uranus_bigtts',
                'rate': '+20%',
            },
            buffered=False,
        )
        assert result.status_code == 200
        assert result.is_streamed
        assert result.headers['Content-Type'] == 'audio/mpeg'
        assert result.headers['X-TTS-Provider'] == 'doubao'
        assert result.headers['X-Accel-Buffering'] == 'no'
        assert 'Content-Length' not in result.headers
        assert b''.join(result.response) == b'audio-1audio-2'
        result.close()
        assert response.closed is True


class TestStreaming:
    """Test the Edge-compatible chunked streaming endpoint."""

    @pytest.fixture(autouse=True)
    def setup(self):
        import app as app_module
        self.app = app_module.app
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()

    def test_chunked_missing_text(self):
        r = self.client.post('/speech/stream/chunked', json={'voice': 'zh-CN-XiaoxiaoNeural'})
        assert r.status_code == 400

    def test_chunked_missing_voice(self):
        r = self.client.post('/speech/stream/chunked', json={'text': 'test'})
        assert r.status_code == 400

    def test_chunked_unknown_voice(self):
        r = self.client.post('/speech/stream/chunked', json={
            'text': 'test', 'voice': 'invalid-voice-xyz'
        })
        assert r.status_code == 400

    def test_chunked_edge_streams_audio(self, monkeypatch):
        import app as app_module

        class FakeCommunicate:
            def __init__(self, *args, **kwargs):
                pass

            async def stream(self):
                yield {'type': 'audio', 'data': b'edge-1'}
                yield {'type': 'WordBoundary', 'text': '测试'}
                yield {'type': 'audio', 'data': b'edge-2'}

        app_module._cache_clear()
        monkeypatch.setattr(app_module.edge_tts, 'Communicate', FakeCommunicate)
        monkeypatch.setattr(app_module, 'update_stats', lambda *args, **kwargs: None)
        r = self.client.post(
            '/speech/stream/chunked',
            json={'text': '测试', 'voice': 'zh-CN-XiaoxiaoNeural'},
            buffered=False,
        )
        assert r.status_code == 200
        assert r.is_streamed
        assert r.headers['X-Accel-Buffering'] == 'no'
        assert 'Content-Length' not in r.headers
        assert b''.join(r.response) == b'edge-1edge-2'
        r.close()

    def test_rate_limit_headers_present(self):
        r = self.client.get('/health')
        assert 'X-RateLimit-Limit' in r.headers
        assert 'X-RateLimit-Remaining' in r.headers
