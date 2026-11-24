"""Regression coverage for client-only Legado engine credentials."""

import copy
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest

import app as server


@pytest.mark.parametrize('required', [False, True])
def test_initial_state_exposes_auth_requirement_without_secrets(monkeypatch, required):
    monkeypatch.setattr(server, 'API_KEYS_REQUIRED', required)
    monkeypatch.setattr(server, 'API_KEYS', {'never-embed-client-secret'})
    monkeypatch.setattr(server, 'ADMIN_TOKEN', 'never-embed-admin-secret')
    monkeypatch.setattr(server, 'load_config',
                        lambda: copy.deepcopy(server.DEFAULT_CONFIG))
    page = server.app.test_client().get('/').get_data(as_text=True)
    match = re.search(r'window\.__TTS_INITIAL__ = (.*?);</script>', page)
    assert json.loads(match.group(1))['apiKeysRequired'] is required
    assert 'never-embed-client-secret' not in page
    assert 'never-embed-admin-secret' not in page
    assert 'id="legado-api-key" type="password"' in page
    assert 'id="legado-api-key"' in page


def test_subscription_refuses_unusable_unauthenticated_engine(monkeypatch):
    monkeypatch.setattr(server, 'API_KEYS_REQUIRED', True)
    client = server.app.test_client()
    for path in ('/api/legado/subscribe', '/api/legado/subscribe?auto=true'):
        response = client.get(path)
        assert response.status_code == 400
        assert 'Subscription URLs do not carry credentials' in response.get_json()['error']['message']


def run_legado_js(body, required=True):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is needed for browser credential regression tests')
    script = Path(__file__).with_name('static').joinpath('app.js').read_text()
    functions = script[
        script.index('    function getLegadoClientKey()'):
        script.index('    async function synthesize(')
    ]
    harness = r'''
const assert = require('node:assert/strict');
const INITIAL = {apiKeysRequired: REQUIRED};
const state = {provider: 'edge', configRequestId: 0, legadoConfigText: ''};
const elements = {};
const $ = id => elements[id] ||= {
    value: '', disabled: true, textContent: '', hasAttribute: () => false,
};
const window = {location: {origin: 'https://tts.example'}};
const requests = [], copied = [], feedback = [];
const currentVoiceId = () => 'zh-CN-XiaoxiaoNeural';
const hasUsableCurrentVoice = () => true;
const getToken = () => { throw Error('Must not read admin token for Legado'); };
const setFeedback = (...args) => feedback.push(args);
const setButtonBusy = () => {};
const notify = () => {};
const copyText = async text => copied.push(text);
const invalidateLegadoConfig = message => {
    state.configRequestId++;
    state.legadoConfigText = '';
    $('copy-config-button').disabled = true;
    $('copy-subscribe-button').disabled = true;
    $('legado-config').textContent = message;
};
const template = {
    name: 'Edge', url: 'https://tts.example/speech/stream,' + JSON.stringify({
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: {text: '{{speakText}}', voice: currentVoiceId()},
    }),
};
const jsonRequest = async path => {
    requests.push(path);
    if (path.startsWith('/api/legado/subscribe')) return {url: 'https://tts.example/subscription'};
    return JSON.parse(JSON.stringify(template));
};
const readOptions = config => JSON.parse(config.url.slice(config.url.indexOf(',') + 1));
'''.replace('REQUIRED', json.dumps(required))
    result = subprocess.run(
        [node, '-e', harness + functions + '\n(async () => {\n' + body
         + '\n})().catch(error => { console.error(error); process.exitCode = 1; });'],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_key_is_only_added_to_exported_request_header():
    run_legado_js(r'''
$('legado-api-key').value = 'client-only-secret';
await updateLegadoConfig();
const config = JSON.parse(state.legadoConfigText);
assert.equal(readOptions(config).headers.Authorization, 'Bearer client-only-secret');
assert.equal(readOptions(config).headers['Content-Type'], 'application/json');
assert.equal(readOptions(config).body.text, '{{speakText}}');
assert.equal(readOptions(template).headers.Authorization, undefined);
assert.equal($('copy-config-button').disabled, false);
assert.equal($('copy-subscribe-button').disabled, true);
assert.ok(requests.every(url => !url.includes('client-only-secret')));
assert.ok(!$('subscribe-url-display').value.includes('client-only-secret'));
await copySubscribeUrl();
assert.equal(copied.length, 0);
assert.equal(requests.length, 1);
''')


def test_required_auth_without_client_key_disables_export():
    run_legado_js(r'''
await updateLegadoConfig();
assert.equal(state.legadoConfigText, '');
assert.equal($('copy-config-button').disabled, true);
assert.equal($('copy-subscribe-button').disabled, true);
assert.equal(requests.length, 0);
await copySubscribeUrl();
assert.equal(copied.length, 0);
''')


def test_optional_auth_without_key_keeps_subscription_working():
    run_legado_js(r'''
await updateLegadoConfig();
assert.equal(readOptions(JSON.parse(state.legadoConfigText)).headers.Authorization, undefined);
assert.equal($('copy-subscribe-button').disabled, false);
await copySubscribeUrl();
assert.deepEqual(copied, ['https://tts.example/subscription']);
''', required=False)


def test_explicit_key_disables_subscription_even_when_auth_optional():
    run_legado_js(r'''
$('legado-api-key').value = 'client-only-secret';
await updateLegadoConfig();
assert.equal($('copy-subscribe-button').disabled, true);
await copySubscribeUrl();
assert.equal(copied.length, 0);
assert.equal(requests.length, 1);
''', required=False)


def test_removing_required_key_invalidates_previously_generated_secret():
    run_legado_js(r'''
$('legado-api-key').value = 'client-only-secret';
await updateLegadoConfig();
assert.ok(state.legadoConfigText.includes('client-only-secret'));
$('legado-api-key').value = '';
await updateLegadoConfig();
assert.equal(state.legadoConfigText, '');
assert.ok(!$('legado-config').textContent.includes('client-only-secret'));
assert.equal($('copy-config-button').disabled, true);
''')


def test_control_characters_cannot_enter_exported_header():
    run_legado_js(r'''
$('legado-api-key').value = 'client\r\nInjected: value';
await updateLegadoConfig();
assert.equal(state.legadoConfigText, '');
assert.equal($('copy-config-button').disabled, true);
assert.equal($('copy-subscribe-button').disabled, true);
''')
