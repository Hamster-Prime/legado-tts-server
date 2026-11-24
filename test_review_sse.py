"""Regression coverage for authenticated, bounded activity streams."""
from pathlib import Path
import http.client
import errno
import importlib.util
import os
import runpy
import shutil
import socket
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

import app as app_module


@pytest.fixture
def sse_client(monkeypatch):
    monkeypatch.setattr(app_module, 'ADMIN_TOKEN', 'review-secret')
    monkeypatch.setattr(app_module, 'SSE_MAX_SUBSCRIBERS', 1)
    monkeypatch.setattr(app_module, '_sse_subscribers', [])
    return app_module.app.test_client()


def test_activity_requires_header_not_query_token(sse_client):
    response = sse_client.get('/api/events?token=review-secret')
    assert response.status_code == 401
    assert not app_module._sse_subscribers
    response = sse_client.get(
        '/api/events', headers={'Authorization': 'Bearer review-secret'},
        buffered=False)
    assert response.status_code == 200
    response.close()
    assert not app_module._sse_subscribers


def test_full_activity_queue_closes_without_freeing_capacity_early(sse_client):
    headers = {'Authorization': 'Bearer review-secret'}
    response = sse_client.get('/api/events', headers=headers, buffered=False)
    subscriber = app_module._sse_subscribers[0]
    for index in range(101):
        app_module._sse_publish('test', {'index': index})
    assert subscriber.closed.is_set()
    assert subscriber in app_module._sse_subscribers
    assert sse_client.get('/api/events', headers=headers).status_code == 503
    # test_client has already pulled the connected event; no queued messages
    # should be emitted after the slow subscriber has been closed.
    assert b''.join(response.response) == b'data: {"type":"connected"}\n\n'
    response.close()
    assert not app_module._sse_subscribers
    next_response = sse_client.get('/api/events', headers=headers, buffered=False)
    assert next_response.status_code == 200
    next_response.close()


def test_unstarted_response_releases_subscription(monkeypatch):
    monkeypatch.setattr(app_module, 'ADMIN_TOKEN', '')
    monkeypatch.setattr(app_module, '_sse_subscribers', [])
    with app_module.app.test_request_context('/api/events'):
        response = app_module.api_events()
    assert len(app_module._sse_subscribers) == 1
    response.close()
    assert not app_module._sse_subscribers


def test_shutdown_with_full_queue_does_not_block(monkeypatch):
    subscriber = app_module._SSESubscriber()
    for index in range(100):
        subscriber.queue.put_nowait(index)
    monkeypatch.setattr(app_module, '_sse_subscribers', [subscriber])
    monkeypatch.setattr(app_module, '_shutdown_done', False)
    monkeypatch.setattr(app_module, '_shutdown_edge_executor', lambda: None)
    app_module._graceful_shutdown()
    assert subscriber.closed.is_set()


@pytest.mark.parametrize('threads, expected', [(1, 0), (2, 1), (4, 2), (8, 4), (32, 20)])
def test_gunicorn_reserves_actual_worker_capacity(monkeypatch, threads, expected):
    monkeypatch.setattr(app_module, 'SSE_MAX_SUBSCRIBERS', 20)
    config = runpy.run_path(str(Path(__file__).with_name('gunicorn.conf.py')))
    worker = type('ThreadWorker', (), {})()
    worker.cfg = SimpleNamespace(threads=threads)
    config['post_worker_init'](worker)
    assert app_module.SSE_MAX_SUBSCRIBERS == expected
    assert '%(r)s' not in config['access_log_format']
    assert '%(q)s' not in config['access_log_format']


def test_fetch_stream_parser_auth_reconnect_and_cancel():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is not installed')
    script = Path(__file__).with_name('static').joinpath('app.js').read_text()
    activity_functions = script[
        script.index('    function ensureActivityStream() {'):
        script.index('    function startMonitoring() {')]
    api_function = script[
        script.index('    async function api('):
        script.index('    async function expectOk(')]
    # Extract the real transport and parser, rather than mocking EventSource or
    # merely asserting source strings. Split a multibyte payload across reads.
    harness = r'''
const assert = require('node:assert/strict');
const state = {activitySource: null};
const INITIAL = {adminProtected: true};
const getToken = () => 'review-secret';
const received = [];
const appendActivity = data => received.push(data);
const $$ = () => [];
const setLiveState = () => {};
const notify = () => {};
const setAdminToken = () => {};
const errorText = async response => `HTTP ${response.status}`;
const scheduled = [];
const window = {
    setTimeout: fn => {scheduled.push(fn); return scheduled.length;},
    clearTimeout: () => {},
};
const requests = [];
let writer;
global.fetch = async (url, options) => {
    requests.push({url, options});
    return new Response(new ReadableStream({start(controller) {writer = controller;}}));
};
const tick = () => new Promise(resolve => setImmediate(resolve));
'''
    assertions = r'''
(async () => {
    ensureActivityStream();
    await tick();
    assert.equal(requests[0].url, '/api/events');
    assert.equal(requests[0].options.headers.get('Authorization'), 'Bearer review-secret');
    const payload = new TextEncoder().encode(
        ': heartbeat\r\n\r\ndata: {"type":"tts_request","voice":"中文"}\r\n\r\n'
            .replaceAll('\\r', '\r').replaceAll('\\n', '\n'));
    // byte-wise reads exercise UTF-8 decoding and CR/LF boundaries.
    for (const byte of payload) writer.enqueue(new Uint8Array([byte]));
    await tick();
    assert.equal(received.length, 1);
    assert.equal(received[0].voice, '中文');
    writer.close();
    await tick();
    assert.equal(scheduled.length, 1);
    scheduled.shift()();
    await tick();
    assert.equal(requests.length, 2);
    stopActivityStream();
    assert.equal(requests[1].options.signal.aborted, true);
    writer.close();
    await tick();
    assert.equal(scheduled.length, 0);
    assert.equal(state.activitySource, null);
})().catch(error => {console.error(error); process.exitCode = 1;});
'''
    result = subprocess.run(
        [node, '-e', harness + api_function + activity_functions + assertions],
        text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


def test_real_gunicorn_sse_capacity_and_redacted_access_log(tmp_path):
    """Exercise the actual worker hook, CLI thread override and access logger."""
    if importlib.util.find_spec('gunicorn') is None:
        pytest.skip('Gunicorn is not installed')
    if not hasattr(socket, 'AF_UNIX'):
        pytest.skip('Unix sockets are unavailable')
    sock_path = str(tmp_path / 'tts.sock')
    # Some CI sandboxes prohibit even local Unix-domain listening sockets.
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(sock_path)
        except PermissionError as error:
            if error.errno in (errno.EPERM, errno.EACCES):
                pytest.skip('Sandbox forbids Unix socket binding')
            raise
    os.unlink(sock_path)
    log_path = tmp_path / 'gunicorn.log'
    token = 'integration-admin-secret-not-for-logs'
    env = dict(os.environ, ADMIN_TOKEN=token, SSE_MAX_SUBSCRIBERS='100',
               CONFIG_FILE=str(tmp_path / 'config.json'),
               STATS_FILE=str(tmp_path / 'stats.json'))

    class UnixConnection(http.client.HTTPConnection):
        def connect(self):
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.sock.settimeout(self.timeout)
            self.sock.connect(sock_path)

    def get(path, headers=None):
        connection = UnixConnection('localhost', timeout=3)
        connection.request('GET', path, headers=headers or {})
        return connection, connection.getresponse()

    connections = []
    with log_path.open('w') as log_file:
        process = subprocess.Popen(
            [sys.executable, '-m', 'gunicorn', '-c', 'gunicorn.conf.py',
             '--bind', 'unix:' + sock_path, '--threads', '2', '--workers', '1',
             '--graceful-timeout', '1', 'app:app'],
            cwd=Path(__file__).parent, env=env,
            stdout=log_file, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 10
            while True:
                assert process.poll() is None, log_path.read_text()
                try:
                    connection, response = get('/livez')
                    assert response.status == 200
                    response.read()
                    connection.close()
                    break
                except (OSError, http.client.HTTPException):
                    if time.monotonic() >= deadline:
                        pytest.fail(log_path.read_text())
                    time.sleep(0.05)

            first, response = get('/api/events', {'Authorization': 'Bearer ' + token})
            connections.append(first)
            assert response.status == 200
            assert b'connected' in response.readline()

            second, rejected = get('/api/events', {'Authorization': 'Bearer ' + token})
            connections.append(second)
            assert rejected.status == 503
            assert b'max 1' in rejected.read()

            health, healthy = get('/livez?token=' + token)
            connections.append(health)
            assert healthy.status == 200
            healthy.read()

            anonymous, denied = get('/api/events?token=' + token)
            connections.append(anonymous)
            assert denied.status == 401
            denied.read()
        finally:
            for connection in connections:
                connection.close()
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    log_text = log_path.read_text()
    assert 'GET /livez HTTP/' in log_text
    assert 'GET /api/events HTTP/' in log_text
    assert token not in log_text
    assert '?token=' not in log_text
