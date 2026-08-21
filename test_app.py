#!/usr/bin/env python3
"""Unit tests for legado-tts-server"""

import json
import logging
import os
import sys
import tempfile
import base64
import uuid
import threading
import time
from concurrent.futures import ThreadPoolExecutor

# Add parent dir to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest
from app import (
    resolve_provider, parse_rate, load_config, save_config,
    load_stats, _read_json, _write_json, _empty_provider_stats, ALL_PROVIDERS,
    DOUBAO_VOICES, TENCENT_VOICES, EDGE_VOICES, XIAOMI_VOICES, FISH_AUDIO_VOICES,
    OPENAI_PROVIDER_MODELS, _clean_text, _split_text_chunks, _check_rate_limit,
    _concat_mp3,
)


class TestResolveProvider:
    """Test voice-to-provider routing."""

    def test_edge_voices(self):
        for v in EDGE_VOICES:
            assert resolve_provider(v['id']) == 'edge', f"{v['id']} should route to edge"

    def test_doubao_voices(self):
        for v in DOUBAO_VOICES:
            assert resolve_provider(v['id']) == 'doubao', f"{v['id']} should route to doubao"

    @pytest.mark.parametrize('voice', [
        'en_male_tim_uranus_bigtts',
        'ICL_uranus_en_female_charlie_tob',
        'saturn_zh_female_aojiaonvyou_tob',
        'multi_female_maomao_conversation_wvae_bigtts',
    ])
    def test_official_doubao_voice_prefixes(self, voice):
        assert resolve_provider(voice) == 'doubao'

    def test_tencent_voices(self):
        for v in TENCENT_VOICES:
            assert resolve_provider(v['id']) == 'tencent', f"{v['id']} should route to tencent"

    def test_xiaomi_voices(self):
        for v in XIAOMI_VOICES:
            assert resolve_provider(v['id']) == 'xiaomi', f"{v['id']} should route to xiaomi"

    def test_fishaudio_voices(self):
        for v in FISH_AUDIO_VOICES:
            assert resolve_provider(v['id']) == 'fishaudio', f"{v['id']} should route to fishaudio"

    def test_fishaudio_reference_id(self):
        assert resolve_provider('e6882dded6cb4a96b36453914dc231db') == 'fishaudio'

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


class TestDoubaoVoiceCatalog:
    def test_official_voice_names(self):
        assert DOUBAO_VOICES == [
            {'id': 'zh_female_cancan_uranus_bigtts', 'name': '知性灿灿 2.0'},
            {'id': 'zh_female_shuangkuaisisi_uranus_bigtts', 'name': '爽快思思 2.0'},
            {'id': 'zh_female_tiexinnvsheng_uranus_bigtts', 'name': '贴心女声 2.0'},
            {'id': 'zh_female_jitangmei_uranus_bigtts', 'name': '鸡汤妹妹 2.0'},
            {'id': 'zh_female_mengyatou_uranus_bigtts', 'name': '萌丫头 2.0'},
            {'id': 'zh_male_shaonianzixin_uranus_bigtts', 'name': '少年梓辛 2.0'},
            {'id': 'zh_male_wennuanahu_uranus_bigtts', 'name': '温暖阿虎 2.0'},
            {'id': 'zh_male_cixingjieshuonan_uranus_bigtts', 'name': '磁性解说男声 2.0'},
            {'id': 'zh_male_xuanyijieshuo_uranus_bigtts', 'name': '悬疑解说 2.0'},
            {'id': 'zh_male_baqiqingshu_uranus_bigtts', 'name': '霸气青叔 2.0'},
            {'id': 'zh_female_shaoergushi_uranus_bigtts', 'name': '少儿故事 2.0'},
        ]

    @pytest.mark.parametrize(('legacy_name', 'voice_id'), [
        ('知性灿灿', 'zh_female_cancan_uranus_bigtts'),
        ('爽快思思', 'zh_female_shuangkuaisisi_uranus_bigtts'),
        ('贴心女声', 'zh_female_tiexinnvsheng_uranus_bigtts'),
        ('鸡汤妹妹', 'zh_female_jitangmei_uranus_bigtts'),
        ('萌丫头', 'zh_female_mengyatou_uranus_bigtts'),
        ('少年梓辛', 'zh_male_shaonianzixin_uranus_bigtts'),
        ('温暖阿虎', 'zh_male_wennuanahu_uranus_bigtts'),
        ('磁性解说男声', 'zh_male_cixingjieshuonan_uranus_bigtts'),
        ('悬疑解说', 'zh_male_xuanyijieshuo_uranus_bigtts'),
        ('霸气青叔', 'zh_male_baqiqingshu_uranus_bigtts'),
        ('少儿故事', 'zh_female_shaoergushi_uranus_bigtts'),
    ])
    def test_legacy_display_name_aliases(self, legacy_name, voice_id):
        import app as app_module

        assert app_module._resolve_voice_alias(legacy_name) == voice_id


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

    def test_atomic_write_keeps_file_private(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, 'config.json')
            _write_json(path, {'secret': 'first'})
            assert os.stat(path).st_mode & 0o777 == 0o600

            os.chmod(path, 0o664)
            _write_json(path, {'secret': 'second'})
            assert os.stat(path).st_mode & 0o777 == 0o600


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
                assert cfg['default_voice'] == 'zh_female_cancan_uranus_bigtts'
                assert cfg['edge_voice'] == 'zh-CN-XiaoxiaoNeural'
                assert cfg['fallback_enabled'] is True
                assert cfg['fallback_voice'] == 'zh-CN-XiaoxiaoNeural'
                assert cfg['fallback_retry_count'] == 1
                assert cfg['fallback_retry_delay_ms'] == 500
                assert cfg['fishaudio_voice'] == 'fish-default'
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

    def test_legacy_generic_default_voice_is_migrated_for_doubao(self):
        import app as app_module

        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump({
                'provider': 'edge',
                'default_voice': 'zh-CN-XiaoxiaoNeural',
            }, f)
            path = f.name
        original = app_module.CONFIG_FILE
        app_module.CONFIG_FILE = path
        try:
            config = load_config()
            assert config['provider'] == 'edge'
            assert config['edge_voice'] == 'zh-CN-XiaoxiaoNeural'
            assert config['default_voice'] == 'zh_female_cancan_uranus_bigtts'
            assert _read_json(path, {})['default_voice'] == config['default_voice']
        finally:
            app_module.CONFIG_FILE = original
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

    @pytest.mark.parametrize('legacy_voice', [
        'fish-animated',
        'fish-speech-zh',
        'fish-audio-male',
        'fish-narrator',
    ])
    def test_legacy_fishaudio_voice_is_migrated(
            self, legacy_voice, monkeypatch, tmp_path):
        import app as app_module

        path = tmp_path / 'config.json'
        _write_json(str(path), {
            'provider': 'fishaudio',
            'fishaudio_voice': legacy_voice,
            'voice_favorites': [legacy_voice],
        })
        monkeypatch.setattr(app_module, 'CONFIG_FILE', str(path))

        cfg = load_config()

        assert cfg['fishaudio_voice'] == 'fish-default'
        assert cfg['voice_favorites'] == ['fish-default']
        persisted = _read_json(str(path), {})
        assert persisted['fishaudio_voice'] == 'fish-default'
        assert persisted['voice_favorites'] == ['fish-default']

    def test_legacy_custom_fish_reference_is_migrated_to_named_library(
            self, monkeypatch, tmp_path):
        import app as app_module

        path = tmp_path / 'config.json'
        reference_id = '9a9cf47702da476aa4629e2506d4a857'
        _write_json(str(path), {
            'provider': 'fishaudio',
            'fishaudio_voice': 'custom',
            'fishaudio_reference_id': f'  {reference_id}  ',
        })
        monkeypatch.setattr(app_module, 'CONFIG_FILE', str(path))

        cfg = load_config()

        assert cfg['fishaudio_voice'] == reference_id
        assert cfg['custom_voices']['fishaudio'] == [{
            'id': reference_id,
            'name': '未命名自定义音色',
        }]
        persisted = _read_json(str(path), {})
        assert persisted['fishaudio_voice'] == reference_id
        assert persisted['custom_voices'] == cfg['custom_voices']

    def test_legacy_custom_ids_are_preserved_when_not_selected(
            self, monkeypatch, tmp_path):
        import app as app_module

        path = tmp_path / 'config.json'
        _write_json(str(path), {
            'provider': 'edge',
            'default_voice': 'S_cloned_narrator_01',
            'fishaudio_voice': 'fish-default',
            'fishaudio_reference_id': 'fish_reference_01',
        })
        monkeypatch.setattr(app_module, 'CONFIG_FILE', str(path))

        cfg = load_config()

        assert cfg['custom_voices']['doubao'] == [{
            'id': 'S_cloned_narrator_01',
            'name': '未命名自定义音色',
        }]
        assert cfg['custom_voices']['fishaudio'] == [{
            'id': 'fish_reference_01',
            'name': '未命名自定义音色',
        }]

    def test_load_config_returns_deep_copy_of_custom_voice_library(
            self, monkeypatch, tmp_path):
        import app as app_module

        path = tmp_path / 'config.json'
        monkeypatch.setattr(app_module, 'CONFIG_FILE', str(path))
        cfg = app_module.DEFAULT_CONFIG.copy()
        cfg['custom_voices'] = {
            'doubao': [{'id': 'S_voice_01', 'name': '原名称'}],
            'fishaudio': [],
        }
        save_config(cfg)

        first = load_config()
        first['custom_voices']['doubao'][0]['name'] = '被修改'
        first['custom_voices']['doubao'].append({'id': 'S_voice_02', 'name': '新增'})

        second = load_config()
        assert second['custom_voices']['doubao'] == [
            {'id': 'S_voice_01', 'name': '原名称'},
        ]


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
            'fishaudio', 'test', 'fish-default', 0
        )
        assert audio is None
        assert '未配置' in error
        assert actual_provider is None
        assert actual_voice is None

    def test_fishaudio_default_omits_reference_id(self, monkeypatch):
        import app as app_module
        captured = {}

        class Response:
            status_code = 200
            content = b'fish-audio'
            text = ''

        def fake_post(url, **kwargs):
            captured.update(kwargs)
            return Response()

        monkeypatch.setattr(app_module._http_session, 'post', fake_post)
        audio, error = app_module.synthesize_fishaudio(
            '测试', 'fish-default', config={
                'fishaudio_api_key': 'key',
                'fishaudio_reference_id': 'custom-reference',
            })

        assert error is None
        assert audio == b'fish-audio'
        assert captured['json'] == {'text': '测试', 'format': 'mp3'}
        assert captured['headers']['Authorization'] == 'Bearer key'

    def test_fishaudio_custom_voice_sends_trimmed_reference_id(self, monkeypatch):
        import app as app_module
        captured = {}

        class Response:
            status_code = 200
            content = b'custom-fish-audio'
            text = ''

        def fake_post(url, **kwargs):
            captured['url'] = url
            captured.update(kwargs)
            return Response()

        monkeypatch.setattr(app_module._http_session, 'post', fake_post)
        audio, error = app_module.synthesize_fishaudio(
            '测试', 'custom', config={
                'fishaudio_api_key': 'key',
                'fishaudio_reference_id': '  9a9cf47702da476aa4629e2506d4a857  ',
            })

        assert error is None
        assert audio == b'custom-fish-audio'
        assert captured['url'] == 'https://api.fish.audio/v1/tts'
        assert captured['json'] == {
            'text': '测试',
            'format': 'mp3',
            'reference_id': '9a9cf47702da476aa4629e2506d4a857',
        }

    def test_fishaudio_custom_voice_requires_reference_id(self, monkeypatch):
        import app as app_module

        def unexpected_post(*args, **kwargs):
            pytest.fail('missing custom reference must fail before the HTTP request')

        monkeypatch.setattr(app_module._http_session, 'post', unexpected_post)
        audio, error = app_module.synthesize_fishaudio(
            '测试', 'custom', config={
                'fishaudio_api_key': 'key',
                'fishaudio_reference_id': '',
            })

        assert audio is None
        assert 'Reference ID' in error

    def test_fishaudio_rejects_unknown_preset_before_request(self, monkeypatch):
        import app as app_module

        def unexpected_post(*args, **kwargs):
            pytest.fail('an unknown Fish preset must fail before the HTTP request')

        monkeypatch.setattr(app_module._http_session, 'post', unexpected_post)
        audio, error = app_module.synthesize_fishaudio(
            '测试', 'fish-not-a-preset', config={'fishaudio_api_key': 'key'})

        assert audio is None
        assert '不支持' in error

    def test_cache_key_changes_with_fish_reference(self, monkeypatch, tmp_path):
        import app as app_module
        monkeypatch.setattr(app_module, 'CONFIG_FILE', str(tmp_path / 'config.json'))
        config = app_module.DEFAULT_CONFIG.copy()
        config.update({
            'provider': 'fishaudio',
            'fishaudio_api_key': 'key',
            'fishaudio_voice': 'custom',
            'fishaudio_reference_id': 'reference-a',
        })
        app_module.save_config(config)
        first = app_module._cache_key('fishaudio', '测试', 'custom', 0)
        app_module._cache_put(first, b'old-reference-audio')

        config['fishaudio_reference_id'] = 'reference-b'
        app_module.save_config(config)
        second = app_module._cache_key('fishaudio', '测试', 'custom', 0)

        assert second != first
        assert app_module._cache_get(second) is None


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

    def test_speech_stream_empty_voice(self):
        r = self.client.post('/speech/stream', json={'text': 'hello', 'voice': ''})
        assert r.status_code == 400

    def test_speech_stream_null_voice_is_not_treated_as_omitted(self):
        r = self.client.post('/speech/stream', json={'text': 'hello', 'voice': None})
        assert r.status_code == 400

    @pytest.mark.parametrize(('endpoint', 'payload'), [
        ('/speech/stream', {'text': '默认目标'}),
        ('/speech/stream/chunked', {'text': '默认目标'}),
        ('/v1/audio/speech', {'model': 'tts-1', 'input': '默认目标'}),
        ('/api/tts/preview', {}),
        ('/api/speech/batch', {'texts': ['默认目标']}),
    ])
    def test_synthesis_routes_use_saved_provider_and_voice_by_default(
            self, monkeypatch, endpoint, payload):
        import app as app_module

        config = app_module.DEFAULT_CONFIG.copy()
        config.update({
            'provider': 'tencent',
            'tencent_voice': '501003',
        })
        app_module.save_config(config)
        captured = []

        def fake_dispatch(provider, text, voice, pct, **kwargs):
            captured.append((provider, voice))
            return b'default-target-audio', None, provider, voice

        monkeypatch.setattr(app_module, 'dispatch', fake_dispatch)

        response = self.client.post(endpoint, json=payload)

        assert response.status_code == 200, response.get_data(as_text=True)
        assert captured == [('tencent', '501003')]

    def test_explicit_provider_without_voice_uses_its_saved_voice(self, monkeypatch):
        import app as app_module

        config = app_module.DEFAULT_CONFIG.copy()
        config.update({
            'provider': 'edge',
            'edge_voice': 'zh-CN-YunxiNeural',
            'tencent_voice': '501001',
        })
        app_module.save_config(config)
        captured = {}

        def fake_dispatch(provider, text, voice, pct, **kwargs):
            captured.update(provider=provider, voice=voice)
            return b'provider-default-audio', None, provider, voice

        monkeypatch.setattr(app_module, 'dispatch', fake_dispatch)
        response = self.client.post('/speech/stream', json={
            'text': '指定服务商默认音色',
            'provider': 'tencent',
        })

        assert response.status_code == 200
        assert captured == {'provider': 'tencent', 'voice': '501001'}

    def test_speech_stream_unknown_voice(self):
        r = self.client.post('/speech/stream', json={'text': 'hello', 'voice': 'invalid'})
        assert r.status_code == 400

    def test_speech_stream_legacy_fish_voice_uses_default_payload(self, monkeypatch):
        import app as app_module
        captured = {}

        class Response:
            status_code = 200
            content = b'fish-default-audio'
            text = ''

        def fake_post(url, **kwargs):
            captured.update(kwargs)
            return Response()

        config = app_module.DEFAULT_CONFIG.copy()
        config['fishaudio_api_key'] = 'key'
        app_module.save_config(config)
        monkeypatch.setattr(app_module._http_session, 'post', fake_post)

        result = self.client.post('/speech/stream', json={
            'text': '旧版链接',
            'voice': 'fish-animated',
            'provider': 'fishaudio',
        })

        assert result.status_code == 200
        assert result.data == b'fish-default-audio'
        assert result.headers['X-TTS-Voice'] == 'fish-default'
        assert captured['json'] == {'text': '旧版链接', 'format': 'mp3'}

    def test_speech_stream_explicit_doubao_accepts_custom_voice(self, monkeypatch):
        import app as app_module
        captured = {}

        def fake_stream_response(text, voice, pct, **kwargs):
            captured.update({'text': text, 'voice': voice, 'pct': pct})
            return app_module.Response(b'custom-doubao-audio', mimetype='audio/mpeg')

        monkeypatch.setattr(app_module, '_doubao_stream_response', fake_stream_response)

        r = self.client.post('/speech/stream', json={
            'text': '自定义音色测试',
            'voice': 'S_custom_voice_123',
            'provider': 'doubao',
        })

        assert r.status_code == 200
        assert r.data == b'custom-doubao-audio'
        assert captured['voice'] == 'S_custom_voice_123'

    def test_explicit_doubao_custom_voice_reaches_all_synthesis_paths(self, monkeypatch):
        import app as app_module

        custom_voice = 'S_custom_voice_123'
        assert app_module.resolve_provider(custom_voice) is None
        captured = []

        def fake_stream_response(text, voice, pct, **kwargs):
            captured.append(('chunked', 'doubao', voice))
            return app_module.Response(b'custom-stream-audio', mimetype='audio/mpeg')

        def fake_dispatch(provider, text, voice, pct, **kwargs):
            captured.append(('dispatch', provider, voice))
            return b'custom-buffered-audio', None, provider, voice

        monkeypatch.setattr(app_module, '_doubao_stream_response', fake_stream_response)
        monkeypatch.setattr(app_module, 'dispatch', fake_dispatch)

        requests = [
            ('/speech/stream/chunked', {
                'text': '流式测试', 'voice': custom_voice, 'provider': 'doubao',
            }),
            ('/api/tts/preview', {
                'voice': custom_voice, 'provider': 'doubao',
            }),
            ('/v1/audio/speech', {
                'model': 'tts-1', 'input': 'OpenAI 测试',
                'voice': custom_voice, 'provider': 'doubao',
            }),
            ('/api/speech/batch', {
                'texts': ['批量测试'], 'voice': custom_voice, 'provider': 'doubao',
            }),
        ]
        for endpoint, payload in requests:
            response = self.client.post(endpoint, json=payload)
            assert response.status_code == 200, (endpoint, response.get_data(as_text=True))

        assert captured == [
            ('chunked', 'doubao', custom_voice),
            ('dispatch', 'doubao', custom_voice),
            ('dispatch', 'doubao', custom_voice),
            ('dispatch', 'doubao', custom_voice),
        ]

    @pytest.mark.parametrize('voice', [
        'zh_bad voice',
        'zh_' + ('a' * 128),
    ])
    def test_speech_stream_rejects_invalid_inferred_doubao_voice(self, monkeypatch, voice):
        import app as app_module

        def unexpected_stream(*args, **kwargs):
            pytest.fail('invalid voice must not reach the Doubao provider')

        monkeypatch.setattr(app_module, '_doubao_stream_response', unexpected_stream)

        r = self.client.post('/speech/stream', json={
            'text': '无效音色测试',
            'voice': voice,
        })

        assert r.status_code == 400
        assert 'Invalid voice ID' in r.get_data(as_text=True)

    def test_speech_stream_explicit_doubao_preserves_alias_shaped_custom_id(self, monkeypatch):
        import app as app_module
        captured = {}

        def fake_stream_response(text, voice, pct, **kwargs):
            captured.update({'text': text, 'voice': voice, 'pct': pct})
            return app_module.Response(b'alias-shaped-custom-audio', mimetype='audio/mpeg')

        monkeypatch.setattr(app_module, '_doubao_stream_response', fake_stream_response)

        r = self.client.post('/speech/stream', json={
            'text': '别名形态的自定义音色',
            'voice': 'nova',
            'provider': 'doubao',
        })

        assert r.status_code == 200
        assert r.data == b'alias-shaped-custom-audio'
        assert captured['voice'] == 'nova'

    def test_speech_stream_explicit_doubao_migrates_legacy_voice_id(self, monkeypatch):
        import app as app_module
        captured = {}

        def fake_stream_response(text, voice, pct, **kwargs):
            captured['voice'] = voice
            return app_module.Response(b'migrated-doubao-audio', mimetype='audio/mpeg')

        monkeypatch.setattr(app_module, '_doubao_stream_response', fake_stream_response)

        r = self.client.post('/speech/stream', json={
            'text': '旧音色 ID 兼容测试',
            'voice': 'zh_female_cancan_mars_bigtts',
            'provider': 'doubao',
        })

        assert r.status_code == 200
        assert captured['voice'] == 'zh_female_cancan_uranus_bigtts'

    def test_speech_stream_saved_doubao_custom_voice_without_provider(self, monkeypatch):
        import app as app_module
        captured = {}

        def fake_stream_response(text, voice, pct, **kwargs):
            captured.update({'text': text, 'voice': voice, 'pct': pct})
            return app_module.Response(b'legacy-legado-custom-audio', mimetype='audio/mpeg')

        monkeypatch.setattr(app_module, '_doubao_stream_response', fake_stream_response)
        custom_voice = 'nova'
        saved = self.client.post('/api/config', json={
            'provider': 'doubao',
            'default_voice': custom_voice,
        })
        assert saved.status_code == 200

        r = self.client.post('/speech/stream', json={
            'text': '旧版 Legado 配置测试',
            'voice': custom_voice,
        })

        assert r.status_code == 200
        assert r.data == b'legacy-legado-custom-audio'
        assert captured['voice'] == custom_voice

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
        assert r.headers['Cache-Control'] == 'no-store'
        data = r.get_json()
        assert 'provider' in data
        assert 'doubao_api_key' in data
        assert 'appid' not in data
        assert 'access_token' not in data
        assert type(data['fallback_enabled']) is bool
        assert type(data['fallback_retry_count']) is int
        assert type(data['fallback_retry_delay_ms']) is int
        assert data['fallback_voice'] == 'zh-CN-XiaoxiaoNeural'
        assert data['active_provider'] == 'edge'
        assert data['active_voice'] == 'zh-CN-XiaoxiaoNeural'

    def test_config_post_persists_fallback_policy(self):
        payload = {
            'fallback_enabled': False,
            'fallback_voice': 'zh-CN-YunxiNeural',
            'fallback_retry_count': 3,
            'fallback_retry_delay_ms': 1200,
        }

        result = self.client.post('/api/config', json=payload)

        assert result.status_code == 200
        saved = load_config()
        for key, value in payload.items():
            assert saved[key] == value
        exposed = self.client.get('/api/config').get_json()
        for key, value in payload.items():
            assert exposed[key] == value

    def test_index_hydrates_fallback_policy_and_controls(self):
        result = self.client.post('/api/config', json={
            'fallback_enabled': False,
            'fallback_voice': 'zh-CN-YunxiNeural',
            'fallback_retry_count': 2,
            'fallback_retry_delay_ms': 900,
        })
        assert result.status_code == 200

        html = self.client.get('/').get_data(as_text=True)

        assert 'id="fallback-enabled"' in html
        assert 'id="fallback-voice"' in html
        assert 'id="fallback-retry-count"' in html
        assert 'id="fallback-retry-delay-ms"' in html
        assert '"fallbackEnabled": false' in html
        assert '"fallbackVoice": "zh-CN-YunxiNeural"' in html
        assert '"fallbackRetryCount": 2' in html
        assert '"fallbackRetryDelayMs": 900' in html

    def test_config_post(self):
        r = self.client.post('/api/config', json={'provider': 'edge'})
        assert r.status_code == 200
        assert r.get_json()['status'] == 'ok'
        assert r.get_json()['active_provider'] == 'edge'
        assert r.get_json()['active_voice'] == 'zh-CN-XiaoxiaoNeural'

    def test_config_post_accepts_custom_doubao_voice(self):
        r = self.client.post('/api/config', json={
            'provider': 'doubao',
            'default_voice': 'S_custom_voice_123',
        })

        assert r.status_code == 200
        config = load_config()
        assert config['provider'] == 'doubao'
        assert config['default_voice'] == 'S_custom_voice_123'

    def test_custom_voice_crud_persists_names_and_merges_catalogs(self):
        doubao = {'provider': 'doubao', 'id': 'S_narrator_01', 'name': '温柔旁白'}
        fish = {
            'provider': 'fishaudio',
            'id': '9a9cf47702da476aa4629e2506d4a857',
            'name': '我的克隆声',
        }

        assert self.client.post('/api/custom-voices', json=doubao).status_code == 200
        assert self.client.post('/api/custom-voices', json=fish).status_code == 200

        library = self.client.get('/api/custom-voices').get_json()
        assert library == {
            'doubao': [{'id': doubao['id'], 'name': doubao['name']}],
            'fishaudio': [{'id': fish['id'], 'name': fish['name']}],
        }
        for provider, expected in [('doubao', doubao), ('fishaudio', fish)]:
            catalog = self.client.get(
                f'/api/voices?provider={provider}').get_json()
            item = next(voice for voice in catalog if voice['id'] == expected['id'])
            assert item == {
                'id': expected['id'],
                'name': expected['name'],
                'custom': True,
            }
        search = self.client.get('/api/voices/search?q=克隆声').get_json()
        assert search['count'] == 1
        assert search['results'][0] == {
            'id': fish['id'],
            'name': fish['name'],
            'provider': 'fishaudio',
            'custom': True,
        }

        renamed = {**fish, 'name': '重命名后的克隆声'}
        assert self.client.post('/api/custom-voices', json=renamed).status_code == 200
        assert self.client.get('/api/custom-voices').get_json()['fishaudio'] == [{
            'id': fish['id'], 'name': renamed['name'],
        }]

        deleted = self.client.delete('/api/custom-voices', json={
            'provider': 'doubao', 'id': doubao['id'],
        })
        assert deleted.status_code == 200
        assert deleted.get_json()['custom_voices']['doubao'] == []

    @pytest.mark.parametrize('payload', [
        None,
        {'provider': 'edge', 'id': 'voice', 'name': '名称'},
        {'provider': 'doubao', 'id': '', 'name': '名称'},
        {'provider': 'doubao', 'id': 'bad voice', 'name': '名称'},
        {'provider': 'doubao', 'id': 'S_voice', 'name': ''},
        {'provider': 'doubao', 'id': 'S_voice', 'name': 'n' * 81},
        {'provider': 'doubao', 'id': 'zh-CN-XiaoxiaoNeural', 'name': '跨服务商'},
        {'provider': 'fishaudio', 'id': 'fish-default', 'name': '内置音色'},
        {'provider': 'fishaudio', 'id': 'reference', 'name': '名称', 'extra': True},
    ])
    def test_custom_voice_post_rejects_invalid_entries(self, payload):
        response = self.client.post('/api/custom-voices', json=payload)
        assert response.status_code == 400
        assert self.client.get('/api/custom-voices').get_json() == {
            'doubao': [], 'fishaudio': [],
        }

    def test_custom_voice_delete_rejects_saved_active_voice(self):
        entry = {'provider': 'doubao', 'id': 'S_active_voice', 'name': '当前音色'}
        assert self.client.post('/api/custom-voices', json=entry).status_code == 200
        saved = self.client.post('/api/config', json={
            'provider': 'doubao',
            'default_voice': entry['id'],
        })
        assert saved.status_code == 200

        deleted = self.client.delete('/api/custom-voices', json={
            'provider': 'doubao', 'id': entry['id'],
        })

        assert deleted.status_code == 409
        assert self.client.get('/api/custom-voices').get_json()['doubao'] == [{
            'id': entry['id'], 'name': entry['name'],
        }]

    def test_delete_migrated_fish_voice_does_not_resurrect(self):
        import app as app_module
        reference_id = 'legacy_reference_delete_01'
        config = app_module.DEFAULT_CONFIG.copy()
        config.update({
            'fishaudio_voice': 'fish-default',
            'fishaudio_reference_id': reference_id,
        })
        app_module.save_config(config)
        assert app_module.load_config()['custom_voices']['fishaudio'] == [{
            'id': reference_id, 'name': '未命名自定义音色',
        }]

        deleted = self.client.delete('/api/custom-voices', json={
            'provider': 'fishaudio', 'id': reference_id,
        })

        assert deleted.status_code == 200
        assert deleted.get_json()['custom_voices']['fishaudio'] == []
        reloaded = app_module.load_config()
        assert reloaded['custom_voices']['fishaudio'] == []
        assert reloaded['fishaudio_reference_id'] == ''

    def test_cross_site_catalog_does_not_expose_custom_voices_without_token(self):
        voice = {
            'provider': 'fishaudio',
            'id': 'private_cross_site_reference',
            'name': '仅管理页可见',
        }
        assert self.client.post('/api/custom-voices', json=voice).status_code == 200

        direct = self.client.get('/api/voices?provider=fishaudio').get_json()
        cross_site = self.client.get('/api/voices?provider=fishaudio', headers={
            'Origin': 'https://example.invalid',
            'Sec-Fetch-Site': 'cross-site',
        }).get_json()

        assert voice['id'] in {item['id'] for item in direct}
        assert voice['id'] not in {item['id'] for item in cross_site}

    def test_saved_fish_custom_voice_sends_its_own_reference_id(self, monkeypatch):
        import app as app_module
        captured = {}
        reference_id = '9a9cf47702da476aa4629e2506d4a857'

        class Response:
            status_code = 200
            content = b'custom-fish-audio'
            text = ''

        def fake_post(url, **kwargs):
            captured.update(kwargs)
            return Response()

        monkeypatch.setattr(app_module._http_session, 'post', fake_post)
        assert self.client.post('/api/custom-voices', json={
            'provider': 'fishaudio', 'id': reference_id, 'name': 'Fish 旁白',
        }).status_code == 200
        assert self.client.post('/api/config', json={
            'provider': 'fishaudio',
            'fishaudio_api_key': 'fish-secret',
            'fishaudio_voice': reference_id,
        }).status_code == 200

        result = self.client.post('/speech/stream', json={
            'text': '实际 Reference ID 测试',
            'voice': reference_id,
            'provider': 'fishaudio',
        })

        assert result.status_code == 200
        assert result.data == b'custom-fish-audio'
        assert result.headers['X-TTS-Voice'] == reference_id
        assert captured['json']['reference_id'] == reference_id

    def test_config_get_masks_key_but_returns_custom_voice_metadata(self):
        voice = {'provider': 'fishaudio', 'id': 'reference_01', 'name': '书架旁白'}
        assert self.client.post('/api/custom-voices', json=voice).status_code == 200
        assert self.client.post('/api/config', json={
            'fishaudio_api_key': 'private-key',
        }).status_code == 200

        data = self.client.get('/api/config').get_json()

        assert data['fishaudio_api_key'] == '***'
        assert data['configured_fields']['fishaudio_api_key'] is True
        assert data['custom_voices']['fishaudio'] == [{
            'id': voice['id'], 'name': voice['name'],
        }]

    def test_index_places_custom_voice_fields_in_voice_picker(self):
        html = self.client.get('/').get_data(as_text=True)

        assert 'id="custom-voice-name"' in html
        assert 'id="custom-voice-id"' in html
        assert 'id="custom-voice-save"' in html
        assert 'id="fishaudio-reference-id"' not in html

    def test_config_post_rejects_edge_voice_for_doubao(self):
        r = self.client.post('/api/config', json={
            'provider': 'doubao',
            'default_voice': 'zh-CN-XiaoxiaoNeural',
        })

        assert r.status_code == 400
        assert load_config()['provider'] == 'edge'

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
        assert b'href="/static/app.css"' in r.data
        assert b'src="/static/app.js"' in r.data
        assert self.client.get('/static/app.css').status_code == 200
        assert self.client.get('/static/app.js').status_code == 200

    def test_provider_draft_is_unsaved_until_switching_back(self):
        """The redesigned console keeps provider, log, and navigation controls working."""
        try:
            from playwright.sync_api import (
                Error as PlaywrightError,
                expect,
                sync_playwright,
            )
        except ImportError:
            pytest.skip('Playwright is not installed')

        import shutil
        chrome = shutil.which('google-chrome') or shutil.which('chromium')
        if not chrome:
            pytest.skip('No Chromium browser is installed')

        html = self.client.get('/').get_data(as_text=True)
        html = html.replace(
            '<script defer src="/static/app.js"></script>', '')
        javascript = self.client.get('/static/app.js').get_data(as_text=True)

        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch(
                    executable_path=chrome, headless=True,
                    args=['--no-sandbox'])
            except PlaywrightError as error:
                pytest.skip(f'Chromium cannot start in this environment: {error}')
            try:
                page = browser.new_page()
                page_errors = []
                page.on('pageerror', lambda error: page_errors.append(str(error)))
                page.set_content(html)
                page.evaluate("""
                    () => {
                        window.__testEventSources = [];
                        window.EventSource = class {
                            constructor(url) {
                                this.url = url;
                                this.closed = false;
                                window.__testEventSources.push(this);
                                queueMicrotask(() => this.onopen?.());
                            }

                            close() {
                                this.closed = true;
                            }
                        };
                    }
                """)
                page.add_script_tag(content=javascript)

                page.wait_for_function(
                    '() => window.__testEventSources.length === 1')
                expect(page.locator('#save-chip-text')).to_have_text('已保存')
                page.locator('[data-provider="doubao"]').click()
                expect(page.locator('#save-chip-text')).to_have_text('有未保存更改')
                assert page.locator('#save-chip').evaluate(
                    "element => element.classList.contains('is-dirty')")
                expect(page.locator(
                    '[data-settings-provider="doubao"]')).to_be_visible()
                expect(page.locator(
                    '[data-settings-provider="edge"]')).to_be_hidden()
                expect(page.locator('#custom-voice-manager')).to_be_visible()

                page.locator('[data-provider="edge"]').click()
                expect(page.locator('#save-chip-text')).to_have_text('已保存')
                assert page.locator('#save-chip').evaluate(
                    "element => element.classList.contains('is-saved')")
                expect(page.locator(
                    '[data-settings-provider="edge"]')).to_be_visible()
                expect(page.locator(
                    '[data-settings-provider="doubao"]')).to_be_hidden()

                edge_button = page.locator('[data-provider="edge"]')
                edge_button.focus()
                edge_button.press('ArrowRight')
                expect(page.locator(
                    '[data-provider="doubao"]')).to_have_attribute(
                        'aria-checked', 'true')
                expect(page.locator('#save-chip-text')).to_have_text('有未保存更改')
                page.locator('[data-provider="doubao"]').press('Home')
                expect(edge_button).to_have_attribute('aria-checked', 'true')
                expect(page.locator('#save-chip-text')).to_have_text('已保存')

                page.locator('#btn-sub-logs').click()
                page.locator('#clear-logs-btn').click()
                expect(page.locator('#activity-empty')).to_have_text(
                    '日志已清空，等待新的合成请求…')
                page.evaluate("""
                    eventData => window.__testEventSources.at(-1).onmessage({
                        data: JSON.stringify(eventData),
                    })
                """, {
                    'type': 'tts_request',
                    'request_id': 'browser-regression-1',
                    'ts': '2026-08-19T12:00:00',
                    'provider': 'edge',
                    'voice': 'zh-CN-XiaoxiaoNeural',
                    'chars': 4,
                    'status': 200,
                    'outcome': 'success',
                    'ms': 12,
                })
                expect(page.locator('#activity-feed .activity-line')).to_have_count(1)
                expect(page.locator('#activity-feed .activity-line')).to_contain_text(
                    'edge')

                page.locator('#console-maintenance-tab').click()
                assert page.evaluate(
                    '() => window.__testEventSources[0].closed') is True
                page.locator('#console-main-tab').click()
                page.wait_for_function(
                    '() => window.__testEventSources.length === 2')
                assert page.evaluate(
                    '() => window.__testEventSources[1].closed') is False
                assert page_errors == []
            finally:
                browser.close()

    def test_config_test_endpoint(self):
        r = self.client.post('/api/config/test')
        assert r.status_code == 200
        data = r.get_json()
        assert 'provider' in data
        assert 'ok' in data

    def test_config_post_unknown_provider(self):
        import app as app_module

        self.client.post('/api/config', json={
            'provider': 'edge',
            'edge_voice': 'zh-CN-YunxiNeural',
        })
        before = load_config()
        with open(app_module.CONFIG_FILE, 'rb') as f:
            before_bytes = f.read()
        before_stat = os.stat(app_module.CONFIG_FILE)

        r = self.client.post('/api/config', json={'provider': 'nonexistent'})
        assert r.status_code == 400
        assert load_config() == before
        with open(app_module.CONFIG_FILE, 'rb') as f:
            assert f.read() == before_bytes
        after_stat = os.stat(app_module.CONFIG_FILE)
        assert after_stat.st_ino == before_stat.st_ino
        assert after_stat.st_mtime_ns == before_stat.st_mtime_ns

    @pytest.mark.parametrize('payload', [
        {'provider': ['edge']},
        {'provider': 'fishaudio', 'fishaudio_voice': 'custom'},
        {'provider': 'fishaudio', 'fishaudio_voice': 'fish-not-a-preset'},
        {'edge_voice': 'not-a-real-voice'},
        {'edge_voice': {'id': 'zh-CN-XiaoxiaoNeural'}},
        {'xiaomi_api_key': 12345},
        {'fallback_enabled': 'false'},
        {'fallback_retry_count': True},
        {'fallback_retry_count': -1},
        {'fallback_retry_count': 4},
        {'fallback_retry_count': 1.5},
        {'fallback_retry_delay_ms': False},
        {'fallback_retry_delay_ms': -1},
        {'fallback_retry_delay_ms': 5001},
        {'fallback_retry_delay_ms': 0.5},
        {'fallback_voice': 'zh_male_shaonianzixin_uranus_bigtts'},
    ], ids=['provider-list', 'custom-fish-without-reference',
            'unknown-fish-preset', 'unknown-voice', 'voice-object', 'credential-number',
            'fallback-enabled-string', 'retry-count-bool', 'retry-count-negative',
            'retry-count-too-large', 'retry-count-float', 'retry-delay-bool',
            'retry-delay-negative', 'retry-delay-too-large', 'retry-delay-float',
            'fallback-non-edge-voice'])
    def test_config_post_invalid_values_do_not_write(self, payload):
        import app as app_module

        self.client.post('/api/config', json={
            'provider': 'edge',
            'edge_voice': 'zh-CN-XiaoxiaoNeural',
        })
        before = load_config()
        with open(app_module.CONFIG_FILE, 'rb') as f:
            before_bytes = f.read()
        before_stat = os.stat(app_module.CONFIG_FILE)

        r = self.client.post('/api/config', json=payload)

        assert r.status_code == 400
        assert load_config() == before
        with open(app_module.CONFIG_FILE, 'rb') as f:
            assert f.read() == before_bytes
        after_stat = os.stat(app_module.CONFIG_FILE)
        assert after_stat.st_ino == before_stat.st_ino
        assert after_stat.st_mtime_ns == before_stat.st_mtime_ns

    def test_concurrent_config_patches_preserve_both_updates(self, monkeypatch):
        import app as app_module
        first_inside = threading.Event()
        second_started = threading.Event()
        release_first = threading.Event()
        original_apply = app_module._apply_config_payload

        def delayed_apply(config, data):
            if data.get('doubao_api_key') == 'doubao-concurrent-key':
                first_inside.set()
                assert release_first.wait(timeout=2)
            return original_apply(config, data)

        monkeypatch.setattr(app_module, '_apply_config_payload', delayed_apply)

        def post(payload):
            if payload.get('xiaomi_api_key'):
                second_started.set()
            with self.app.test_client() as client:
                return client.post('/api/config', json=payload).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(post, {'doubao_api_key': 'doubao-concurrent-key'})
            assert first_inside.wait(timeout=2)
            second = pool.submit(post, {'xiaomi_api_key': 'xiaomi-concurrent-key'})
            assert second_started.wait(timeout=2)
            time.sleep(0.05)
            release_first.set()
            assert first.result(timeout=2) == 200
            assert second.result(timeout=2) == 200

        config = app_module.load_config()
        assert config['doubao_api_key'] == 'doubao-concurrent-key'
        assert config['xiaomi_api_key'] == 'xiaomi-concurrent-key'

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
        assert r.status_code == 200
        assert r.content_type == 'audio/mpeg'

    def test_openai_speech_missing_input(self):
        r = self.client.post('/v1/audio/speech', json={
            'model': 'tts-1',
            'voice': 'zh-CN-XiaoxiaoNeural',
        })
        assert r.status_code == 400
        data = r.get_json()
        assert 'error' in data

    def test_openai_speech_empty_voice(self):
        r = self.client.post('/v1/audio/speech', json={
            'model': 'tts-1',
            'input': 'hello',
            'voice': '',
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
        assert r.status_code == 200

    @pytest.mark.parametrize('model', ['tts-1', 'tts-1-hd'])
    def test_openai_sse_rejects_legacy_tts_models(self, model):
        r = self.client.post('/v1/audio/speech', json={
            'model': model,
            'input': '测试',
            'voice': 'alloy',
            'stream_format': 'sse',
        })
        assert r.status_code == 400
        assert 'not supported' in r.get_json()['error']['message']

    def test_openai_rejects_unknown_stream_format(self):
        r = self.client.post('/v1/audio/speech', json={
            'model': 'gpt-4o-mini-tts',
            'input': '测试',
            'voice': 'alloy',
            'stream_format': 'invalid',
        })
        assert r.status_code == 400

    def test_openai_models(self):
        r = self.client.get('/v1/models')
        assert r.status_code == 200
        data = r.get_json()
        assert data['object'] == 'list'
        assert {model['id'] for model in data['data']} == {
            'gpt-4o-mini-tts',
            'gpt-4o-mini-tts-2025-12-15',
            'tts-1',
            'tts-1-hd',
            *OPENAI_PROVIDER_MODELS,
        }
        assert all(isinstance(model['created'], int) for model in data['data'])
        provider_models = {
            model['id']: model['provider']
            for model in data['data'] if 'provider' in model
        }
        assert provider_models == OPENAI_PROVIDER_MODELS

    def test_openai_model_retrieve_supports_provider_alias(self):
        response = self.client.get('/v1/models/fishaudio-tts')

        assert response.status_code == 200
        assert response.get_json()['id'] == 'fishaudio-tts'
        assert response.get_json()['provider'] == 'fishaudio'

    def test_openai_model_retrieve_unknown_returns_404(self):
        response = self.client.get('/v1/models/not-a-model')
        assert response.status_code == 404

    @pytest.mark.parametrize('voice', [
        'alloy', 'ash', 'ballad', 'coral', 'echo', 'fable', 'onyx',
        'nova', 'sage', 'shimmer', 'verse', 'marin', 'cedar',
    ])
    def test_openai_current_builtin_voice_aliases(self, voice):
        r = self.client.post('/v1/audio/speech', json={
            'model': 'gpt-4o-mini-tts',
            'input': '测试',
            'voice': voice,
        })
        assert r.status_code == 200
        assert r.headers['X-TTS-Provider'] == 'edge'
        assert r.headers['X-TTS-Voice'] in {
            'zh-CN-XiaoxiaoNeural',
            'zh-CN-XiaoyiNeural',
            'zh-CN-YunjianNeural',
            'zh-CN-YunxiNeural',
            'zh-CN-YunxiaNeural',
            'zh-CN-YunyangNeural',
        }

    def test_openai_speech_voice_by_name(self):
        """Test voice resolution by display name."""
        r = self.client.post('/v1/audio/speech', json={
            'model': 'tts-1',
            'input': '测试',
            'voice': '晓晓 - 女声',  # display name
        })
        assert r.status_code == 200

    @pytest.mark.parametrize(('model', 'voice', 'expected_provider'), [
        ('legado-edge-tts', 'zh-CN-XiaoxiaoNeural', 'edge'),
        ('legado-volcengine-tts',
         'zh_female_cancan_uranus_bigtts', 'doubao'),
        ('legado-tencent-tts', '501002', 'tencent'),
        ('legado-xiaomi-mimo-tts', 'mimo_default', 'xiaomi'),
        ('legado-fish-audio-tts', 'fish-default', 'fishaudio'),
        # A short alias is accepted for manually configured clients too.
        ('mimo-v2-tts', 'default_zh', 'xiaomi'),
    ])
    def test_openai_provider_model_routes_without_extension_field(
            self, monkeypatch, model, voice, expected_provider):
        import app as app_module

        captured = {}

        def fake_dispatch(provider, text, requested_voice, pct, **kwargs):
            captured.update(provider=provider, voice=requested_voice)
            return b'provider-model-audio', None, provider, requested_voice

        monkeypatch.setattr(app_module, 'dispatch', fake_dispatch)
        response = self.client.post('/v1/audio/speech', json={
            'model': model,
            'input': '模型路由测试',
            'voice': voice,
        })

        assert response.status_code == 200
        assert captured == {'provider': expected_provider, 'voice': voice}
        assert response.headers['X-TTS-Provider'] == expected_provider

    def test_openai_provider_model_uses_saved_voice_when_omitted(self, monkeypatch):
        import app as app_module

        config = app_module.DEFAULT_CONFIG.copy()
        config.update({
            'provider': 'xiaomi',
            'xiaomi_api_key': 'xiaomi-key',
            'xiaomi_voice': 'default_eh',
        })
        app_module.save_config(config)
        captured = {}

        def fake_dispatch(provider, text, voice, pct, **kwargs):
            captured.update(provider=provider, voice=voice)
            return b'saved-provider-audio', None, provider, voice

        monkeypatch.setattr(app_module, 'dispatch', fake_dispatch)
        response = self.client.post('/v1/audio/speech', json={
            'model': 'legado-xiaomi-mimo-tts',
            'input': '使用已保存音色',
        })

        assert response.status_code == 200
        assert captured == {'provider': 'xiaomi', 'voice': 'default_eh'}

    @pytest.mark.parametrize(('model', 'expected_provider'), [
        ('e6882dded6cb4a96b36453914dc231db', 'fishaudio'),
        ('mimo_default', 'xiaomi'),
        ('501002', 'tencent'),
        ('zh-CN-XiaoxiaoNeural', 'edge'),
    ])
    def test_openai_model_value_can_be_provider_voice(
            self, monkeypatch, model, expected_provider):
        """Clients with only a model field can use it as the voice ID."""
        import app as app_module

        captured = {}

        def fake_dispatch(provider, text, voice, pct, **kwargs):
            captured.update(provider=provider, voice=voice)
            return b'model-voice-audio', None, provider, voice

        monkeypatch.setattr(app_module, 'dispatch', fake_dispatch)
        response = self.client.post('/v1/audio/speech', json={
            'model': model,
            'input': 'Model 音色测试',
        })

        assert response.status_code == 200
        assert captured == {'provider': expected_provider, 'voice': model}
        assert response.headers['X-TTS-Provider'] == expected_provider
        assert response.headers['X-TTS-Voice'] == model

    def test_openai_fish_reference_id_also_works_in_voice_field(self, monkeypatch):
        import app as app_module

        reference_id = 'e6882dded6cb4a96b36453914dc231db'
        captured = {}

        def fake_dispatch(provider, text, voice, pct, **kwargs):
            captured.update(provider=provider, voice=voice)
            return b'fish-reference-audio', None, provider, voice

        monkeypatch.setattr(app_module, 'dispatch', fake_dispatch)
        response = self.client.post('/v1/audio/speech', json={
            'model': 'tts-1',
            'input': 'Fish Reference 音色测试',
            'voice': reference_id,
        })

        assert response.status_code == 200
        assert captured == {'provider': 'fishaudio', 'voice': reference_id}

    def test_openai_provider_model_accepts_saved_fish_reference_id(
            self, monkeypatch):
        import app as app_module

        reference_id = 'reference_custom_01'
        config = app_module.DEFAULT_CONFIG.copy()
        config.update({
            'provider': 'fishaudio',
            'fishaudio_api_key': 'fish-key',
            'fishaudio_voice': reference_id,
            'custom_voices': {
                'doubao': [],
                'fishaudio': [{'id': reference_id, 'name': '自定义旁白'}],
            },
        })
        app_module.save_config(config)
        captured = {}

        def fake_dispatch(provider, text, voice, pct, **kwargs):
            captured.update(provider=provider, voice=voice)
            return b'fish-custom-audio', None, provider, voice

        monkeypatch.setattr(app_module, 'dispatch', fake_dispatch)
        response = self.client.post('/v1/audio/speech', json={
            'model': 'legado-fish-audio-tts',
            'input': '自定义鱼声',
            'voice': reference_id,
        })

        assert response.status_code == 200
        assert captured == {'provider': 'fishaudio', 'voice': reference_id}

    def test_openai_voice_namespace_selects_provider(self, monkeypatch):
        import app as app_module

        reference_id = 'reference_namespace_01'
        config = app_module.DEFAULT_CONFIG.copy()
        config['custom_voices'] = {
            'doubao': [],
            'fishaudio': [{'id': reference_id, 'name': '命名空间旁白'}],
        }
        app_module.save_config(config)
        captured = {}

        def fake_dispatch(provider, text, voice, pct, **kwargs):
            captured.update(provider=provider, voice=voice)
            return b'namespaced-audio', None, provider, voice

        monkeypatch.setattr(app_module, 'dispatch', fake_dispatch)
        response = self.client.post('/v1/audio/speech', json={
            'model': 'gpt-4o-mini-tts',
            'input': '命名空间测试',
            'voice': f'fishaudio:{reference_id}',
        })

        assert response.status_code == 200
        assert captured == {'provider': 'fishaudio', 'voice': reference_id}

    def test_openai_custom_voice_object_selects_provider(self, monkeypatch):
        import app as app_module

        reference_id = 'reference_object_01'
        config = app_module.DEFAULT_CONFIG.copy()
        config['custom_voices'] = {
            'doubao': [],
            'fishaudio': [{'id': reference_id, 'name': '对象旁白'}],
        }
        app_module.save_config(config)
        captured = {}

        def fake_dispatch(provider, text, voice, pct, **kwargs):
            captured.update(provider=provider, voice=voice)
            return b'object-voice-audio', None, provider, voice

        monkeypatch.setattr(app_module, 'dispatch', fake_dispatch)
        response = self.client.post('/v1/audio/speech', json={
            'model': 'legado-fish-audio-tts',
            'input': '对象音色测试',
            'voice': {'id': reference_id},
        })

        assert response.status_code == 200
        assert captured == {'provider': 'fishaudio', 'voice': reference_id}

    def test_openai_custom_voice_object_requires_id(self):
        response = self.client.post('/v1/audio/speech', json={
            'model': 'legado-fish-audio-tts',
            'input': '缺少对象 ID',
            'voice': {},
        })

        assert response.status_code == 400
        assert 'voice object' in response.get_json()['error']['message']

    def test_openai_provider_model_rejects_conflicting_provider(self):
        response = self.client.post('/v1/audio/speech', json={
            'model': 'legado-volcengine-tts',
            'provider': 'xiaomi',
            'voice': 'mimo_default',
            'input': '冲突测试',
        })

        assert response.status_code == 400
        assert 'selects provider doubao' in response.get_json()['error']['message']

    def test_openai_provider_model_does_not_fallback_to_edge(self, monkeypatch):
        import app as app_module

        calls = []

        def failed_dispatch_impl(provider, *args, **kwargs):
            calls.append(provider)
            return None, 'provider unavailable'

        monkeypatch.setattr(app_module, '_dispatch_impl', failed_dispatch_impl)
        response = self.client.post('/v1/audio/speech', json={
            'model': 'legado-volcengine-tts',
            'voice': 'zh_female_cancan_uranus_bigtts',
            'input': '严格服务商测试',
        })

        assert response.status_code == 500
        assert calls == ['doubao']
        assert 'provider unavailable' in response.get_json()['error']['message']

    def test_openai_voices_catalog_uses_provider_namespaces(self):
        response = self.client.get('/v1/voices?provider=xiaomi')

        assert response.status_code == 200
        data = response.get_json()
        assert data['object'] == 'list'
        assert {item['provider'] for item in data['data']} == {'xiaomi'}
        assert 'xiaomi:mimo_default' in {item['id'] for item in data['data']}
        assert all(item['voice_id'] for item in data['data'])

    def test_openai_voices_catalog_rejects_unknown_provider(self):
        response = self.client.get('/v1/voices?provider=unknown')
        assert response.status_code == 400

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
        secrets = {
            'doubao_api_key': 'D@1!',
            'tencent_secret_id': 'T@2!',
            'tencent_secret_key': 'K@3!',
            'xiaomi_api_key': 'X@4!',
            'fishaudio_api_key': 'F@5!',
        }
        saved = self.client.post('/api/config', json=secrets)
        assert saved.status_code == 200

        r = self.client.get('/api/config')
        assert r.status_code == 200
        data = r.get_json()
        for key, secret in secrets.items():
            assert data[key] == '***'
            assert data['configured_fields'][key] is True
            assert secret not in r.get_data(as_text=True)

        index = self.client.get('/')
        assert index.status_code == 200
        page = index.get_data(as_text=True)
        for secret in secrets.values():
            assert secret not in page

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
        assert [voice['id'] for voice in data] == ['fish-default']
        assert all(voice['id'] != 'custom' for voice in data)

    def test_config_get_has_fishaudio_status(self):
        r = self.client.get('/api/config')
        data = r.get_json()
        assert 'fishaudio' in data['provider_status']

    def test_config_post_migrates_legacy_fishaudio_voice(self):
        import app as app_module

        result = self.client.post('/api/config', json={
            'provider': 'fishaudio',
            'fishaudio_voice': 'fish-narrator',
        })

        assert result.status_code == 200
        assert app_module.load_config()['fishaudio_voice'] == 'fish-default'

    def test_openai_speech_speed_clamped(self):
        """Speed should be clamped to 0.25-4.0 range."""
        for speed in [0.0, -1.0, 5.0, 999.0]:
            r = self.client.post('/v1/audio/speech', json={
                'model': 'tts-1', 'input': '测试',
                'voice': 'zh-CN-XiaoxiaoNeural', 'speed': speed,
            })
            assert r.status_code == 200, f"speed={speed} should be clamped, got {r.status_code}"

    def test_openai_speech_speed_invalid_string(self):
        """Non-numeric speed should default to 1.0."""
        r = self.client.post('/v1/audio/speech', json={
            'model': 'tts-1', 'input': '测试',
            'voice': 'zh-CN-XiaoxiaoNeural', 'speed': 'not_a_number',
        })
        assert r.status_code == 200

    def test_openai_speech_response_format_default(self):
        """Default response_format should be mp3."""
        r = self.client.post('/v1/audio/speech', json={
            'model': 'tts-1', 'input': '测试', 'voice': 'zh-CN-XiaoxiaoNeural',
        })
        assert r.status_code == 200
        assert r.content_type.startswith('audio/mpeg')

    def test_openai_speech_response_format_flac(self):
        """A supported non-mp3 format is honoured when ffmpeg is available,
        and falls back to mp3 (advertised as such) when it is not."""
        from app import _FORMAT_MIME
        r = self.client.post('/v1/audio/speech', json={
            'model': 'tts-1', 'input': '测试', 'voice': 'zh-CN-XiaoxiaoNeural',
            'response_format': 'flac',
        })
        assert r.status_code == 200
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
        assert r.status_code == 200
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

    def test_query_token_is_not_accepted_on_config_routes(self):
        r = self.client.get('/api/config?token=test-secret-token')
        assert r.status_code == 401

    def test_query_token_is_limited_to_event_source(self):
        import app as app_module
        with self.app.test_request_context('/api/events?token=test-secret-token'):
            assert app_module._extract_bearer_token() == 'test-secret-token'

    def test_custom_voice_api_and_catalog_require_admin_for_private_entries(self):
        import app as app_module
        headers = self._auth_headers()
        reference_id = 'private-reference-01'

        assert self.client.post('/api/custom-voices', json={
            'provider': 'fishaudio', 'id': reference_id, 'name': '私有音色',
        }).status_code == 401
        created = self.client.post('/api/custom-voices', headers=headers, json={
            'provider': 'fishaudio', 'id': reference_id, 'name': '私有音色',
        })
        assert created.status_code == 200

        anonymous_ids = {
            voice['id'] for voice in
            self.client.get('/api/voices?provider=fishaudio').get_json()
        }
        authenticated_ids = {
            voice['id'] for voice in self.client.get(
                '/api/voices?provider=fishaudio', headers=headers).get_json()
        }
        assert reference_id not in anonymous_ids
        assert reference_id in authenticated_ids
        assert self.client.get('/api/custom-voices').status_code == 401
        assert self.client.get(
            '/api/custom-voices', headers=headers).get_json()['fishaudio'] == [{
                'id': reference_id, 'name': '私有音色',
            }]

        config = app_module.load_config()
        config.update({
            'provider': 'fishaudio',
            'fishaudio_voice': reference_id,
            'fishaudio_api_key': 'private-key',
        })
        app_module.save_config(config)
        page = self.client.get('/').get_data(as_text=True)
        assert reference_id not in page
        assert 'private-key' not in page

    def test_anonymous_legado_default_hides_active_custom_voice(self):
        import app as app_module
        headers = self._auth_headers()
        reference_id = 'private-legado-reference'
        assert self.client.post('/api/custom-voices', headers=headers, json={
            'provider': 'fishaudio', 'id': reference_id, 'name': '私有订阅音色',
        }).status_code == 200
        config = app_module.load_config()
        config.update({
            'provider': 'fishaudio',
            'fishaudio_voice': reference_id,
        })
        app_module.save_config(config)

        for suffix in ('', '?provider=fishaudio'):
            anonymous = self.client.get(
                '/api/legado/config' + suffix).get_json()
            authenticated = self.client.get(
                '/api/legado/config' + suffix,
                headers=headers,
            ).get_json()

            assert reference_id not in anonymous['url']
            assert anonymous['name'] == 'Fish Audio - 默认音色'
            assert reference_id in authenticated['url']
            assert authenticated['name'] == 'Fish Audio - 私有订阅音色'

    def test_anonymous_stats_hide_custom_voice_ids_but_keep_totals(self):
        import app as app_module
        headers = self._auth_headers()
        reference_id = 'private-stats-reference'
        assert self.client.post('/api/custom-voices', headers=headers, json={
            'provider': 'fishaudio', 'id': reference_id, 'name': '私有统计音色',
        }).status_code == 200
        app_module.update_stats(12, 'fishaudio', voice=reference_id)

        anonymous = self.client.get('/api/stats').get_json()['fishaudio']
        authenticated = self.client.get(
            '/api/stats', headers=headers).get_json()['fishaudio']

        assert anonymous['total_chars'] == 12
        assert reference_id not in anonymous.get('voice_stats', {})
        assert authenticated['voice_stats'][reference_id]['chars'] == 12

    def test_protected_home_does_not_embed_fish_reference(self):
        import app as app_module
        config = app_module.DEFAULT_CONFIG.copy()
        config.update({
            'provider': 'fishaudio',
            'fishaudio_api_key': 'fish-secret',
            'fishaudio_voice': 'custom',
            'fishaudio_reference_id': 'private-reference-id',
        })
        app_module.save_config(config)

        page = self.client.get('/')

        assert page.status_code == 200
        assert b'private-reference-id' not in page.data
        protected = self.client.get('/api/config', headers=self._auth_headers())
        assert protected.get_json()['fishaudio_reference_id'] == 'private-reference-id'


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
        assert data['name'] == 'Edge TTS - 晓晓 - 女声'
        assert 'url' in data
        assert 'speech/stream' in data['url']
        assert 'audio/mpeg' in data['contentType']

    def test_legado_config_default_tracks_active_provider_voice(self):
        import app as app_module

        config = app_module.DEFAULT_CONFIG.copy()
        config.update({
            'provider': 'xiaomi',
            'default_voice': 'zh_female_cancan_uranus_bigtts',
            'xiaomi_voice': 'default_zh',
        })
        app_module.save_config(config)

        response = self.client.get('/api/legado/config')

        assert response.status_code == 200
        exported = response.get_json()
        request_config = json.loads(exported['url'].split(',', 1)[1])
        assert exported['name'] == '小米 MiMo - 中文女声'
        assert request_config['body']['provider'] == 'xiaomi'
        assert request_config['body']['voice'] == 'default_zh'

    def test_legado_config_custom_voice(self):
        r = self.client.get('/api/legado/config?voice=zh-CN-YunxiNeural')
        assert r.status_code == 200
        data = r.get_json()
        assert data['name'] == 'Edge TTS - 云希 - 男声'
        assert 'zh-CN-YunxiNeural' in data['url']

    @pytest.mark.parametrize(('provider', 'voice', 'expected_name'), [
        ('doubao', 'zh_male_shaonianzixin_uranus_bigtts',
         '火山引擎 - 少年梓辛 2.0'),
        ('tencent', '501002', '腾讯云 TTS - 智菊 - 阅读女声'),
        ('edge', 'zh-CN-XiaoxiaoNeural', 'Edge TTS - 晓晓 - 女声'),
        ('xiaomi', 'default_zh', '小米 MiMo - 中文女声'),
        # Copied links from releases with placeholder Fish presets still work.
        ('fishaudio', 'fish-animated', 'Fish Audio - 默认音色'),
    ])
    def test_legado_config_uses_provider_and_voice_display_name(
            self, provider, voice, expected_name):
        r = self.client.get(
            f'/api/legado/config?provider={provider}&voice={voice}'
        )

        assert r.status_code == 200
        assert r.get_json()['name'] == expected_name

    def test_legado_config_uses_voice_id_for_uncatalogued_custom_voice(self):
        custom_voice = 'S_custom_voice_123'
        r = self.client.get(
            f'/api/legado/config?provider=doubao&voice={custom_voice}'
        )

        assert r.status_code == 200
        assert r.get_json()['name'] == f'火山引擎 - {custom_voice}'

    @pytest.mark.parametrize(('provider', 'voice_id', 'voice_name'), [
        ('doubao', 'S_saved_narrator', '收藏的火山旁白'),
        ('fishaudio', 'fish_reference_saved_01', '收藏的 Fish 旁白'),
    ])
    def test_legado_config_uses_saved_custom_voice_name(
            self, provider, voice_id, voice_name):
        created = self.client.post('/api/custom-voices', json={
            'provider': provider, 'id': voice_id, 'name': voice_name,
        })
        assert created.status_code == 200

        response = self.client.get(
            f'/api/legado/config?provider={provider}&voice={voice_id}')

        assert response.status_code == 200
        config = response.get_json()
        assert config['name'] == (
            ('火山引擎' if provider == 'doubao' else 'Fish Audio')
            + f' - {voice_name}'
        )
        request_config = json.loads(config['url'].split(',', 1)[1])
        assert request_config['body']['voice'] == voice_id
        assert request_config['body']['provider'] == provider

    def test_legado_config_is_pretty_utf8_json(self):
        r = self.client.get(
            '/api/legado/config'
            '?provider=doubao'
            '&voice=zh_male_shaonianzixin_uranus_bigtts'
        )

        assert r.status_code == 200
        assert r.headers['Content-Type'] == 'application/json; charset=utf-8'
        raw = r.data.decode('utf-8')
        assert raw.startswith('{\n  "concurrentRate": "0",')
        assert '火山引擎 - 少年梓辛 2.0' in raw
        assert json.loads(raw) == r.get_json()

    def test_legado_config_includes_provider_in_request_body(self):
        r = self.client.get('/api/legado/config?provider=doubao')

        assert r.status_code == 200
        data = r.get_json()
        request_config = json.loads(data['url'].split(',', 1)[1])
        assert request_config['body']['provider'] == 'doubao'

    def test_legado_exports_explicit_doubao_custom_voice(self):
        custom_voice = 'S_custom_voice_123'
        query = f'?voice={custom_voice}&provider=doubao'

        config_response = self.client.get('/api/legado/config' + query)
        assert config_response.status_code == 200
        config = config_response.get_json()
        assert config['name'] == f'火山引擎 - {custom_voice}'
        request_config = json.loads(config['url'].split(',', 1)[1])
        assert request_config['body']['voice'] == custom_voice
        assert request_config['body']['provider'] == 'doubao'

        subscribe_response = self.client.get('/api/legado/subscribe' + query)
        assert subscribe_response.status_code == 200
        subscription = subscribe_response.get_json()
        subscribed_request = json.loads(subscription['config']['url'].split(',', 1)[1])
        assert subscribed_request['body']['voice'] == custom_voice
        assert subscribed_request['body']['provider'] == 'doubao'

    def test_legado_subscribe_encoded(self):
        r = self.client.get(
            '/api/legado/subscribe'
            '?provider=doubao'
            '&voice=zh_male_shaonianzixin_uranus_bigtts'
            '&auto=true'
        )
        assert r.status_code == 200
        import base64
        decoded_text = base64.b64decode(r.data).decode('utf-8')
        assert decoded_text.startswith('{\n  "concurrentRate": "0",')
        decoded = json.loads(decoded_text)
        assert decoded['name'] == '火山引擎 - 少年梓辛 2.0'
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
        assert r.status_code == 200
        data = r.get_json()
        assert len(data['results']) == 2
        assert data['retry_count'] == 0
        assert data['fallback_count'] == 0
        assert r.headers['X-TTS-Retry-Count'] == '0'
        for result in data['results']:
            assert result['retry_count'] == 0
            assert result['fallback'] is False
            assert result['requested_provider'] == 'edge'

    def test_preview_reports_synthesis_metadata(self):
        r = self.client.post('/api/tts/preview', json={
            'voice': 'zh-CN-XiaoxiaoNeural',
            'provider': 'edge',
        })

        assert r.status_code == 200
        data = r.get_json()
        assert data['provider'] == 'edge'
        assert data['requested_provider'] == 'edge'
        assert data['retry_count'] == 0
        assert data['fallback'] is False
        assert r.headers['X-TTS-Provider'] == 'edge'
        assert r.headers['X-TTS-Retry-Count'] == '0'

    def test_batch_reports_per_item_fallback_and_retries(self, monkeypatch):
        import app as app_module

        def fallback_dispatch(*args, **kwargs):
            app_module.request._tts_retry_count += 1
            return b'edge-audio', None, 'edge', 'zh-CN-YunxiNeural'

        monkeypatch.setattr(app_module, 'dispatch', fallback_dispatch)
        r = self.client.post('/api/speech/batch', json={
            'voice': 'zh_female_cancan_uranus_bigtts',
            'provider': 'doubao',
            'texts': ['第一段', '第二段'],
        })

        assert r.status_code == 200
        data = r.get_json()
        assert data['retry_count'] == 2
        assert data['fallback_count'] == 2
        assert r.headers['X-TTS-Retry-Count'] == '2'
        assert r.headers['X-TTS-Fallback'] == 'true'
        assert [item['retry_count'] for item in data['results']] == [1, 1]
        assert all(item['fallback'] for item in data['results'])
        assert all(item['provider'] == 'edge' for item in data['results'])


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
        assert r.headers['Cache-Control'] == 'no-store'
        assert r.headers['Pragma'] == 'no-cache'

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

    def test_import_migrates_legacy_fishaudio_voice(self):
        import app as app_module

        result = self.client.post('/api/config/import', json={
            '_version': '1.9.0',
            'provider': 'fishaudio',
            'fishaudio_api_key': 'key',
            'fishaudio_voice': 'fish-animated',
            'voice_favorites': ['fish-animated'],
        })

        assert result.status_code == 200
        config = app_module.load_config()
        assert config['fishaudio_voice'] == 'fish-default'
        assert config['voice_favorites'] == ['fish-default']

    def test_import_empty_body(self):
        r = self.client.post('/api/config/import',
            data='', content_type='application/json')
        assert r.status_code == 400

    def test_cross_site_admin_requests_are_rejected_without_token(self):
        headers = {
            'Origin': 'https://malicious.example',
            'Sec-Fetch-Site': 'cross-site',
        }
        exported = self.client.get('/api/config/export', headers=headers)
        changed = self.client.post('/api/config', json={'provider': 'edge'}, headers=headers)

        assert exported.status_code == 403
        assert changed.status_code == 403

    def test_same_origin_admin_request_still_works_without_token(self):
        r = self.client.get('/api/config/export', headers={
            'Origin': 'http://localhost',
            'Sec-Fetch-Site': 'same-origin',
        })
        assert r.status_code == 200

    def test_import_rejects_masked_credentials_without_overwriting(self):
        import app as app_module
        saved = self.client.post('/api/config', json={'doubao_api_key': 'real-secret'})
        assert saved.status_code == 200
        masked = self.client.get('/api/config').get_json()

        result = self.client.post('/api/config/import', json=masked)

        assert result.status_code == 400
        assert '掩码凭证' in result.get_data(as_text=True)
        assert app_module.load_config()['doubao_api_key'] == 'real-secret'

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

    def test_custom_voice_library_roundtrips_through_export_import(self):
        import app as app_module
        expected = {
            'doubao': [
                {'id': 'S_export_01', 'name': '导出火山一'},
                {'id': 'S_export_02', 'name': '导出火山二'},
            ],
            'fishaudio': [
                {'id': 'reference_export_01', 'name': '导出 Fish'},
            ],
        }
        for provider, voices in expected.items():
            for voice in voices:
                result = self.client.post('/api/custom-voices', json={
                    'provider': provider, **voice,
                })
                assert result.status_code == 200
        self.client.post('/api/config', json={'doubao_api_key': 'secret-key'})

        exported = self.client.get('/api/config/export').get_json()
        assert exported['custom_voices'] == expected
        assert exported['doubao_api_key'] == 'secret-key'

        app_module.save_config(app_module.DEFAULT_CONFIG)
        imported = self.client.post('/api/config/import', json=exported)

        assert imported.status_code == 200
        restored = load_config()
        assert restored['custom_voices'] == expected
        assert restored['doubao_api_key'] == 'secret-key'

    @pytest.mark.parametrize('custom_voices', [
        [],
        {'doubao': 'not-a-list', 'fishaudio': []},
        {'doubao': [{'id': 'S_one', 'name': '一'}, {'id': 'S_one', 'name': '二'}],
         'fishaudio': []},
        {'doubao': [{'id': 'S_one', 'name': ''}], 'fishaudio': []},
        {'doubao': [], 'fishaudio': [], 'edge': []},
    ])
    def test_import_rejects_invalid_custom_voice_library(self, custom_voices):
        response = self.client.post('/api/config/import', json={
            'provider': 'edge', 'custom_voices': custom_voices,
        })
        assert response.status_code == 400


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

    def test_empty_voice_returns_400(self):
        r = self.client.post('/speech/stream', json={
            'text': '测试', 'voice': ''
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
        for path in ('/speech/stream', '/v1/audio/speech'):
            schema = data['paths'][path]['post']['requestBody']['content'][
                'application/json']['schema']
            assert schema['properties']['provider']['type'] == 'string'
            assert set(schema['properties']['provider']['enum']) == set(ALL_PROVIDERS)
            assert 'provider' not in schema['required']
            assert 'voice' not in schema['required']


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

    def _save_fallback_policy(self, **overrides):
        config = self.app_module.load_config()
        config.update({
            'fallback_retry_count': 1,
            'fallback_retry_delay_ms': 0,
            **overrides,
        })
        self.app_module.save_config(config)

    def test_transient_http_failure_retries_before_first_audio(self, monkeypatch):
        failed = _FakeDoubaoResponse([], status_code=503)
        succeeded = self._success_response()
        responses = iter((failed, succeeded))
        request_ids = []

        def fake_post(*args, **kwargs):
            request_ids.append(kwargs['headers']['X-Api-Request-Id'])
            return next(responses)

        self._save_fallback_policy(fallback_retry_count=1)
        monkeypatch.setattr(self.app_module._http_session, 'post', fake_post)

        result = self.client.post(
            '/speech/stream',
            json={
                'text': '重试测试',
                'voice': 'zh_female_cancan_uranus_bigtts',
            },
            buffered=False,
        )

        assert result.status_code == 200
        assert result.headers['X-TTS-Provider'] == 'doubao'
        assert result.headers['X-TTS-Retry-Count'] == '1'
        assert b''.join(result.response) == b'audio-1audio-2'
        result.close()
        assert len(request_ids) == 2
        assert len(set(request_ids)) == 2
        assert failed.closed is True
        assert succeeded.closed is True

    def test_connection_timeout_retries_before_first_audio(self, monkeypatch):
        succeeded = self._success_response()
        calls = []

        def fake_post(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise self.app_module.requests.Timeout('upstream timed out')
            return succeeded

        self._save_fallback_policy(fallback_retry_count=1)
        monkeypatch.setattr(self.app_module._http_session, 'post', fake_post)

        result = self.client.post(
            '/speech/stream',
            json={
                'text': '超时重试测试',
                'voice': 'zh_female_cancan_uranus_bigtts',
            },
            buffered=False,
        )

        assert result.status_code == 200
        assert result.headers['X-TTS-Retry-Count'] == '1'
        assert b''.join(result.response) == b'audio-1audio-2'
        result.close()
        assert len(calls) == 2

    def test_retry_uses_capped_exponential_backoff(self):
        delay = self.app_module._retry_delay_seconds

        assert delay(500, 1) == 0.5
        assert delay(500, 2) == 1.0
        assert delay(500, 3) == 2.0
        assert delay(5000, 2) == 5.0
        assert delay(0, 3) == 0.0

    def test_retry_after_extends_local_backoff_and_is_capped(self):
        response = self._success_response()
        response.headers['Retry-After'] = '3'

        retry_after = self.app_module._retry_after_seconds(response)

        assert retry_after == 3.0
        assert self.app_module._retry_wait_seconds(500, 1, retry_after) == 3.0
        response.headers['Retry-After'] = '120'
        assert self.app_module._retry_after_seconds(response) == 60.0

    def test_retry_exhaustion_uses_configured_edge_voice(self, monkeypatch):
        failures = iter((
            _FakeDoubaoResponse([], status_code=503),
            _FakeDoubaoResponse([], status_code=503),
        ))
        calls = []

        def fake_post(*args, **kwargs):
            calls.append(1)
            return next(failures)

        self._save_fallback_policy(
            fallback_enabled=True,
            fallback_voice='zh-CN-YunxiNeural',
            fallback_retry_count=1,
        )
        monkeypatch.setattr(self.app_module, 'FALLBACK_TO_EDGE', True)
        monkeypatch.setattr(self.app_module._http_session, 'post', fake_post)
        monkeypatch.setattr(
            self.app_module,
            'synthesize_edge',
            lambda *args, **kwargs: (b'configured-edge-fallback', None),
        )

        result = self.client.post('/speech/stream', json={
            'text': '回退测试',
            'voice': 'zh_female_cancan_uranus_bigtts',
        })

        assert result.status_code == 200
        assert result.data == b'configured-edge-fallback'
        assert result.headers['X-TTS-Provider'] == 'edge'
        assert result.headers['X-TTS-Voice'] == 'zh-CN-YunxiNeural'
        assert result.headers['X-TTS-Fallback'] == 'true'
        assert result.headers['X-TTS-Requested-Provider'] == 'doubao'
        assert result.headers['X-TTS-Retry-Count'] == '1'
        assert len(calls) == 2

        request_id = result.headers['X-Request-ID']
        audit = next(
            record for record in reversed(self.app_module._audit_log)
            if record['request_id'] == request_id
        )
        assert audit['provider'] == 'edge'
        assert audit['voice'] == 'zh-CN-YunxiNeural'
        assert audit['retries'] == 1
        assert audit['fallback'] is True

    def test_retry_exhaustion_without_fallback_reports_count(self, monkeypatch):
        failures = iter((
            _FakeDoubaoResponse([], status_code=503),
            _FakeDoubaoResponse([], status_code=503),
        ))
        calls = []

        def fake_post(*args, **kwargs):
            calls.append(1)
            return next(failures)

        self._save_fallback_policy(
            fallback_enabled=False,
            fallback_retry_count=1,
        )
        monkeypatch.setattr(self.app_module._http_session, 'post', fake_post)

        result = self.client.post('/speech/stream', json={
            'text': '仅重试不回退',
            'voice': 'zh_female_cancan_uranus_bigtts',
        })

        assert result.status_code == 500
        assert result.headers['X-TTS-Retry-Count'] == '1'
        assert 'X-TTS-Fallback' not in result.headers
        assert len(calls) == 2

    def test_nonretryable_provider_error_does_not_consume_retry_budget(self, monkeypatch):
        response = _FakeDoubaoResponse([
            {'code': 45000000, 'message': 'resource mismatch'},
        ])
        calls = []

        def fake_post(*args, **kwargs):
            calls.append(1)
            return response

        self._save_fallback_policy(fallback_retry_count=3)
        monkeypatch.setattr(self.app_module._http_session, 'post', fake_post)

        result = self.client.post('/speech/stream', json={
            'text': '确定性错误测试',
            'voice': 'zh_female_cancan_uranus_bigtts',
        })

        assert result.status_code == 500
        assert '45000000' in result.get_data(as_text=True)
        assert len(calls) == 1

    def test_failure_after_first_audio_never_retries_or_falls_back(self, monkeypatch):
        response = _FakeDoubaoResponse([
            {'code': 0, 'message': 'OK',
             'data': base64.b64encode(b'partial-audio').decode()},
        ])
        calls = []

        def fake_post(*args, **kwargs):
            calls.append(1)
            return response

        self._save_fallback_policy(
            fallback_enabled=True,
            fallback_retry_count=3,
        )
        monkeypatch.setattr(self.app_module, 'FALLBACK_TO_EDGE', True)
        monkeypatch.setattr(self.app_module._http_session, 'post', fake_post)

        def unexpected_edge(*args, **kwargs):
            pytest.fail('a stream that already emitted audio must not fallback')

        monkeypatch.setattr(self.app_module, 'synthesize_edge', unexpected_edge)

        with self.app_module._metrics_lock:
            success_before = self.app_module._metrics['requests_success']
            failed_before = self.app_module._metrics['requests_failed']

        result = self.client.post(
            '/speech/stream',
            json={
                'text': '首块后断流测试',
                'voice': 'zh_female_cancan_uranus_bigtts',
            },
            buffered=False,
        )

        assert result.status_code == 200
        assert result.headers['X-TTS-Provider'] == 'doubao'
        with pytest.raises(self.app_module.ProviderStreamError,
                           match='流式响应未正常结束'):
            b''.join(result.response)
        result.close()
        assert len(calls) == 1
        with self.app_module._metrics_lock:
            assert self.app_module._metrics['requests_success'] == success_before
            assert self.app_module._metrics['requests_failed'] == failed_before + 1
        request_id = result.headers['X-Request-ID']
        audit = next(
            record for record in reversed(self.app_module._audit_log)
            if record['request_id'] == request_id
        )
        assert audit['status'] == 200
        assert audit['outcome'] == 'stream_failed'
        assert '流式响应未正常结束' in audit['stream_error']

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

    def test_config_test_uses_draft_without_persisting_and_bypasses_cache(self, monkeypatch):
        saved = self.app_module.DEFAULT_CONFIG.copy()
        saved.update({
            'provider': 'edge',
            'doubao_api_key': 'saved-key',
            'default_voice': 'zh_female_cancan_uranus_bigtts',
        })
        self.app_module.save_config(saved)
        with open(self.app_module.CONFIG_FILE, 'rb') as f:
            before_bytes = f.read()
        before_stat = os.stat(self.app_module.CONFIG_FILE)

        sample = self.app_module._clean_text('你好，配置测试。')
        voice = 'zh_female_cancan_uranus_bigtts'
        self.app_module._cache_put(
            self.app_module._cache_key('doubao', sample, voice, 0),
            b'cached-success-that-must-not-be-used',
        )
        captured = {}

        def reject_draft(text, selected_voice, speech_rate=0, config=None):
            captured.update({
                'text': text,
                'voice': selected_voice,
                'speech_rate': speech_rate,
                'config': config,
            })
            return None, 'draft credential rejected'

        def unexpected_edge(*args, **kwargs):
            pytest.fail('configuration test must not fallback to Edge')

        monkeypatch.setattr(self.app_module, 'synthesize_doubao', reject_draft)
        monkeypatch.setattr(self.app_module, 'synthesize_edge', unexpected_edge)
        monkeypatch.setattr(self.app_module, 'FALLBACK_TO_EDGE', True)

        result = self.client.post('/api/config/test', json={
            'provider': 'doubao',
            'default_voice': voice,
            'doubao_api_key': 'draft-only-key',
        })
        data = result.get_json()

        assert result.status_code == 200
        assert data['provider'] == 'doubao'
        assert data['ok'] is False
        assert data['error'] == 'draft credential rejected'
        assert captured['text'] == sample
        assert captured['voice'] == voice
        assert captured['speech_rate'] == 0
        assert captured['config']['provider'] == 'doubao'
        assert captured['config']['doubao_api_key'] == 'draft-only-key'

        persisted = self.app_module.load_config()
        assert persisted['provider'] == 'edge'
        assert persisted['doubao_api_key'] == 'saved-key'
        with open(self.app_module.CONFIG_FILE, 'rb') as f:
            assert f.read() == before_bytes
        after_stat = os.stat(self.app_module.CONFIG_FILE)
        assert after_stat.st_ino == before_stat.st_ino
        assert after_stat.st_mtime_ns == before_stat.st_mtime_ns

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

    def test_chunked_empty_voice(self):
        r = self.client.post('/speech/stream/chunked', json={
            'text': 'test', 'voice': '',
        })
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

    def test_openai_instructions_do_not_pass_unsupported_edge_style(self, monkeypatch):
        import app as app_module

        class FakeCommunicate:
            def __init__(self, *args, **kwargs):
                assert 'style' not in kwargs

            async def stream(self):
                yield {'type': 'audio', 'data': b'openai-edge-audio'}

        app_module._cache_clear()
        monkeypatch.setattr(app_module.edge_tts, 'Communicate', FakeCommunicate)
        monkeypatch.setattr(app_module, 'update_stats', lambda *args, **kwargs: None)

        result = self.client.post('/v1/audio/speech', json={
            'model': 'gpt-4o-mini-tts',
            'input': '测试',
            'voice': 'coral',
            'instructions': 'Speak clearly and warmly.',
        })

        assert result.status_code == 200
        assert result.data == b'openai-edge-audio'
        assert result.headers['X-TTS-Provider'] == 'edge'

    def test_openai_sse_uses_official_event_schema(self, monkeypatch):
        import app as app_module

        monkeypatch.setattr(
            app_module, '_edge_stream_iter',
            lambda *args, **kwargs: iter((b'edge-1', b'edge-2')),
        )
        monkeypatch.setattr(app_module, 'update_stats', lambda *args, **kwargs: None)

        result = self.client.post('/v1/audio/speech', json={
            'model': 'gpt-4o-mini-tts',
            'input': '测试',
            'voice': 'alloy',
            'stream_format': 'sse',
        }, buffered=False)
        body = b''.join(result.response).decode()
        result.close()
        events = [
            json.loads(line.removeprefix('data: '))
            for line in body.splitlines()
            if line.startswith('data: ')
        ]

        assert result.status_code == 200
        assert result.content_type.startswith('text/event-stream')
        assert events == [
            {
                'type': 'speech.audio.delta',
                'audio': base64.b64encode(b'edge-1').decode(),
            },
            {
                'type': 'speech.audio.delta',
                'audio': base64.b64encode(b'edge-2').decode(),
            },
            {
                'type': 'speech.audio.done',
                'usage': {
                    'input_tokens': 0,
                    'output_tokens': 0,
                    'total_tokens': 0,
                },
            },
        ]

    def test_openai_sse_wraps_buffered_provider_audio(self, monkeypatch):
        import app as app_module

        monkeypatch.setattr(
            app_module, 'dispatch',
            lambda provider, text, voice, pct, **kwargs: (
                b'tencent-audio', None, provider, voice),
        )
        monkeypatch.setattr(app_module, 'update_stats', lambda *args, **kwargs: None)

        result = self.client.post('/v1/audio/speech', json={
            'model': 'gpt-4o-mini-tts',
            'input': '测试',
            'provider': 'tencent',
            'voice': '501000',
            'stream_format': 'sse',
        })
        events = [
            json.loads(line.removeprefix('data: '))
            for line in result.get_data(as_text=True).splitlines()
            if line.startswith('data: ')
        ]

        assert result.status_code == 200
        assert result.content_type.startswith('text/event-stream')
        assert result.headers['X-TTS-Provider'] == 'tencent'
        assert events[0] == {
            'type': 'speech.audio.delta',
            'audio': base64.b64encode(b'tencent-audio').decode(),
        }
        assert events[-1]['type'] == 'speech.audio.done'

    def test_chunked_edge_failure_after_audio_is_reclassified(self, monkeypatch):
        import app as app_module

        def interrupted(*args, **kwargs):
            yield b'partial-edge'
            raise RuntimeError('edge stream interrupted')

        monkeypatch.setattr(app_module, '_edge_stream_iter', interrupted)
        with app_module._metrics_lock:
            success_before = app_module._metrics['requests_success']
            failed_before = app_module._metrics['requests_failed']

        result = self.client.post(
            '/speech/stream/chunked',
            json={'text': '测试', 'voice': 'zh-CN-XiaoxiaoNeural'},
            buffered=False,
        )

        with pytest.raises(RuntimeError, match='edge stream interrupted'):
            b''.join(result.response)
        result.close()
        with app_module._metrics_lock:
            assert app_module._metrics['requests_success'] == success_before
            assert app_module._metrics['requests_failed'] == failed_before + 1
        request_id = result.headers['X-Request-ID']
        audit = next(record for record in reversed(app_module._audit_log)
                     if record['request_id'] == request_id)
        assert audit['outcome'] == 'stream_failed'

    def test_openai_sse_failure_after_audio_is_reclassified(self, monkeypatch):
        import app as app_module

        def interrupted(*args, **kwargs):
            yield b'partial-edge'
            raise RuntimeError('openai sse interrupted')

        monkeypatch.setattr(app_module, '_edge_stream_iter', interrupted)
        with app_module._metrics_lock:
            success_before = app_module._metrics['requests_success']
            failed_before = app_module._metrics['requests_failed']

        result = self.client.post('/v1/audio/speech', json={
            'model': 'gpt-4o-mini-tts',
            'input': '测试',
            'voice': 'zh-CN-XiaoxiaoNeural',
            'stream_format': 'sse',
        }, buffered=False)

        assert result.status_code == 200
        with pytest.raises(RuntimeError, match='openai sse interrupted'):
            b''.join(result.response)
        result.close()
        with app_module._metrics_lock:
            assert app_module._metrics['requests_success'] == success_before
            assert app_module._metrics['requests_failed'] == failed_before + 1
        request_id = result.headers['X-Request-ID']
        audit = next(record for record in reversed(app_module._audit_log)
                     if record['request_id'] == request_id)
        assert audit['outcome'] == 'stream_failed'

    def test_openai_sse_failure_before_audio_returns_http_error(self, monkeypatch):
        import app as app_module

        def failed_before_audio(*args, **kwargs):
            raise RuntimeError('edge unavailable before audio')
            yield  # pragma: no cover

        monkeypatch.setattr(app_module, '_edge_stream_iter', failed_before_audio)

        result = self.client.post('/v1/audio/speech', json={
            'model': 'gpt-4o-mini-tts',
            'input': '测试',
            'voice': 'alloy',
            'stream_format': 'sse',
        })

        assert result.status_code == 500
        assert result.is_json
        assert 'edge unavailable before audio' in result.get_json()['error']['message']

    def test_rate_limit_headers_present(self):
        r = self.client.get('/health')
        assert 'X-RateLimit-Limit' in r.headers
        assert 'X-RateLimit-Remaining' in r.headers
