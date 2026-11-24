import asyncio
import errno
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import app


def test_stats_concurrent_updates_are_atomic(tmp_path, monkeypatch):
    path = tmp_path / 'stats.json'
    monkeypatch.setattr(app, 'STATS_FILE', str(path))
    monkeypatch.setattr(app, '_stats_readonly', False)
    with ThreadPoolExecutor(max_workers=16) as executor:
        list(executor.map(lambda _: app.update_stats(1, 'edge'), range(250)))
    result = json.loads(path.read_text())['edge']
    assert result['total_chars'] == 250
    assert result['total_requests'] == 250


def test_stats_reset_serializes_with_update(tmp_path, monkeypatch):
    path = tmp_path / 'stats.json'
    monkeypatch.setattr(app, 'STATS_FILE', str(path))
    monkeypatch.setattr(app, '_stats_readonly', False)
    monkeypatch.setattr(app, 'ADMIN_TOKEN', '')
    entered = threading.Event()
    release = threading.Event()
    original_write = app._write_json

    def blocking_write(filename, data):
        if threading.current_thread().name == 'stats-update':
            entered.set()
            assert release.wait(5)
        original_write(filename, data)

    monkeypatch.setattr(app, '_write_json', blocking_write)
    update = threading.Thread(target=app.update_stats, args=(1, 'edge'), name='stats-update')
    update.start()
    assert entered.wait(5)
    result = []

    def reset():
        with app.app.test_client() as client:
            result.append(client.delete('/api/stats').status_code)

    reset_thread = threading.Thread(target=reset)
    reset_thread.start()
    release.set()
    update.join(5)
    reset_thread.join(5)
    assert not update.is_alive() and not reset_thread.is_alive()
    assert result == [200]
    assert json.loads(path.read_text())['edge']['total_requests'] == 0


def test_edge_sync_timeout_cancels_stream(monkeypatch):
    cancelled = []

    class Communicate:
        async def stream(self):
            try:
                await asyncio.sleep(10)
                yield {'type': 'audio', 'data': b'audio'}
            finally:
                cancelled.append(True)

    monkeypatch.setattr(app, 'REQUEST_TIMEOUT', 0.01)
    monkeypatch.setattr(app, '_edge_communicate', lambda *a, **kw: Communicate())
    audio, error = app.synthesize_edge('hello', 'voice')
    assert audio is None
    assert 'timed out' in error
    assert cancelled == [True]


def test_stats_readonly_filesystem_latches(monkeypatch, tmp_path):
    monkeypatch.setattr(app, 'STATS_FILE', str(tmp_path / 'stats.json'))
    monkeypatch.setattr(app, '_stats_readonly', False)
    attempts = []

    def readonly(*args):
        attempts.append(True)
        raise OSError(errno.EROFS, 'Read-only filesystem')

    monkeypatch.setattr(app, '_write_json', readonly)
    app.update_stats(1, 'edge')
    app.update_stats(1, 'edge')
    assert attempts == [True]
    assert app._stats_readonly
