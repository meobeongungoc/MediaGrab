import base64
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest

from mediagrab.core import edge_bridge as bridge
from mediagrab.core.auth import fresh_edge_session, load_session
from mediagrab.core.errors import MediaGrabError, SessionRejected

ROOT = Path(__file__).resolve().parents[1]


def host_command(origin):
    packaged = os.environ.get('MEDIAGRAB_QA_EXE')
    return [packaged, origin] if packaged else [sys.executable, str(ROOT / 'launch_gui.py'), origin]


def cookie(**updates):
    result = dict(domain='.youtube.com', name='SAPISID', value='SYNTHETIC_QA_SECRET',
        path='/', secure=True, session=True, httpOnly=True)
    result.update(updates)
    return result


def test_cookie_scope_and_flags():
    jar = bridge.cookie_jar([cookie(), cookie(domain='.google.com'), cookie(domain='.evil.com'),
        cookie(domain='.youtube.com.evil.com'), cookie(partitionKey={'topLevelSite': 'https://test.invalid'})])
    assert len(jar) == 2 and jar.filename is None
    assert all(c.secure and c.has_nonstandard_attr('HttpOnly') for c in jar)
    assert len(bridge.cookie_jar([cookie(session=False, expirationDate=1)])) == 0
    with pytest.raises(ValueError):
        bridge.cookie_jar([cookie(value='x\r\nInjected: yes')])


@pytest.mark.parametrize('data', [b'\x01', struct.pack('<I', bridge.LIMIT + 1),
    struct.pack('<I', 2) + b'{', struct.pack('<I', 2) + b'[]'])
def test_reject_invalid_native_messages(data):
    with pytest.raises(ValueError):
        bridge.read_message(io.BytesIO(data))


def test_extension_uses_no_database_decryption(monkeypatch, tmp_path):
    from mediagrab.core import auth
    monkeypatch.setattr(bridge, 'fetch_cookies', lambda: bridge.cookie_jar([cookie()]))
    monkeypatch.setattr(auth, 'extract_cookies_from_browser', lambda *a: pytest.fail('No decryption'))
    session = load_session(tmp_path, 'edge-extension')
    assert session.source == 'edge-extension' and 'SYNTHETIC_QA' not in repr(session)
    with pytest.raises(MediaGrabError, match='profile'):
        load_session(tmp_path, 'edge-extension', 'Default')
    monkeypatch.setattr(bridge, 'fetch_cookies', lambda: bridge.cookie_jar([]))
    with pytest.raises(MediaGrabError, match='No usable YouTube session'):
        load_session(tmp_path, 'edge-extension')


def test_session_refresh_is_bounded():
    session = Mock(source='edge-extension')
    calls = []
    @fresh_edge_session
    def operation(session=None):
        calls.append(True)
        raise SessionRejected('Expired')
    with pytest.raises(MediaGrabError, match='Sign in again'):
        operation(session)
    assert len(calls) == session.refresh.call_count == 2


def test_network_error_does_not_trigger_refresh_retry():
    session = Mock(source='edge-extension')
    @fresh_edge_session
    def operation(session=None):
        raise MediaGrabError('Network failed')
    with pytest.raises(MediaGrabError, match='Network'):
        operation(session)
    assert session.refresh.call_count == 1


def test_extension_identity_and_permissions():
    manifest = json.loads((ROOT / 'edge-extension/manifest.json').read_text('utf-8'))
    digest = hashlib.sha256(base64.b64decode(manifest['key'])).hexdigest()[:32]
    assert ''.join(chr(97 + int(c, 16)) for c in digest) == bridge.EXTENSION_ID
    assert set(manifest['host_permissions']) == {'https://*.youtube.com/*', 'https://*.google.com/*'}
    assert 'externally_connectable' not in manifest and 'content_scripts' not in manifest


@pytest.mark.skipif(os.name != 'nt', reason='Windows named pipes')
def test_native_host_actual_pipe_roundtrip_no_cookie_files(tmp_path, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    process = subprocess.Popen(host_command(bridge.ORIGIN),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW)
    pool = ThreadPoolExecutor(2)
    try:
        bridge.write_message(process.stdin, {'type': 'hello', 'profile': 'a' * 36, 'select': True})
        assert pool.submit(bridge.read_message, process.stdout).result(timeout=15)['ok']
        result = pool.submit(bridge.fetch_cookies)
        request = pool.submit(bridge.read_message, process.stdout).result(timeout=10)
        assert request['type'] == 'cookies'
        bridge.write_message(process.stdin, {'type': 'cookies', 'id': request['id'], 'cookies': [cookie()]})
        assert next(iter(result.result(timeout=10))).value == 'SYNTHETIC_QA_SECRET'
        assert all('SYNTHETIC_QA_SECRET' not in path.read_text('utf-8') for path in tmp_path.rglob('*.json'))
        process.stdin.close()
        assert process.wait(timeout=10) == 0
        assert not (bridge.state_dir() / 'endpoint.json').exists()
        assert process.stderr.read() == b''
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)
        pool.shutdown(wait=False, cancel_futures=True)


@pytest.mark.skipif(os.name != 'nt', reason='Windows native host')
def test_other_extension_origin_rejected(tmp_path):
    result = subprocess.run(host_command('chrome-extension://other/'),
        capture_output=True, timeout=15, env=dict(os.environ, LOCALAPPDATA=str(tmp_path)),
        creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 1
    assert not list(tmp_path.rglob('endpoint.json'))
