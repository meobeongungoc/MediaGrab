"""Edge native messaging bridge. Cookies travel only in memory, over local IPC."""
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import queue
import secrets
import struct
import sys
import threading
import time
from multiprocessing.connection import Client, Listener

from yt_dlp.cookies import YoutubeDLCookieJar
from .edge_identity import EXTENSION_ID, HOST_NAME
from .errors import MediaGrabError

LIMIT = 512 * 1024
ORIGIN = f'chrome-extension://{EXTENSION_ID}/'
CONNECT_HELP = 'Open Edge and click Connect in the MediaGrab Edge Bridge extension. Keep that Edge profile open.'


def state_dir() -> Path:
    return Path(os.environ['LOCALAPPDATA']) / 'MediaGrab' / 'EdgeBridge'


def write_json(path: Path, data: dict) -> None:
    temp = path.with_suffix('.' + secrets.token_hex(6) + '.tmp')
    temp.write_text(json.dumps(data), encoding='utf-8')
    os.replace(temp, path)


def read_message(stream):
    header = stream.read(4)
    if not header:
        return None
    if len(header) != 4:
        raise ValueError('Invalid frame')
    size = struct.unpack('<I', header)[0]
    if not 0 < size <= LIMIT:
        raise ValueError('Invalid frame size')
    payload = stream.read(size)
    if len(payload) != size:
        raise ValueError('Truncated frame')
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ValueError('Invalid message')
    return value


def write_message(stream, value: dict) -> None:
    payload = json.dumps(value, separators=(',', ':')).encode('utf-8')
    if len(payload) > LIMIT:
        raise ValueError('Message too large')
    stream.write(struct.pack('<I', len(payload)) + payload)
    stream.flush()


def cookie_jar(rows) -> YoutubeDLCookieJar:
    if not isinstance(rows, list) or len(rows) > 1000:
        raise ValueError('Invalid cookies')
    jar = YoutubeDLCookieJar()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Invalid cookie')
        domain = row.get('domain', '')
        host = domain.lstrip('.').lower()
        if host not in ('youtube.com', 'google.com') and not host.endswith(('.youtube.com', '.google.com')):
            continue
        if row.get('partitionKey'):
            continue
        name, value, path = row.get('name'), row.get('value'), row.get('path', '/')
        if not all(isinstance(v, str) for v in (name, value, path)) or not name:
            raise ValueError('Invalid cookie fields')
        if any(c in name + value + domain + path for c in '\r\n\x00'):
            raise ValueError('Invalid cookie characters')
        expires = None if row.get('session') else int(row.get('expirationDate', 0))
        if expires is not None and expires <= time.time():
            continue
        jar.set_cookie(http.cookiejar.Cookie(0, name, value, None, False, domain,
            not row.get('hostOnly', False), domain.startswith('.'), path, True,
            bool(row.get('secure')), expires, expires is None, None, None,
            {'HttpOnly': None} if row.get('httpOnly') else {}))
    return jar


def fetch_cookies() -> YoutubeDLCookieJar:
    """Never deserialize pickle or expose raw IPC errors/credentials."""
    try:
        data = json.loads((state_dir() / 'endpoint.json').read_text('utf-8'))
        address = data['address']
        if not isinstance(address, str) or not address.startswith('\\\\.\\pipe\\MediaGrab-'):
            raise ValueError('Invalid endpoint')
        key = bytes.fromhex(data['key'])
        if len(key) != 32:
            raise ValueError('Invalid key')
        with Client(address, family='AF_PIPE', authkey=key) as connection:
            connection.send_bytes(b'{"command":"cookies"}')
            if not connection.poll(20):
                raise TimeoutError
            result = json.loads(connection.recv_bytes(LIMIT))
        if result.get('error'):
            raise MediaGrabError('Edge could not provide the session. ' + CONNECT_HELP)
        return cookie_jar(result['cookies'])
    except MediaGrabError:
        raise
    except Exception:
        raise MediaGrabError('Edge extension is not connected. ' + CONNECT_HELP) from None


def native_main() -> int:
    """Invoked only by the allowlisted Edge extension, not the GUI."""
    if ORIGIN not in sys.argv or os.name != 'nt':
        return 1
    import msvcrt
    msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
    msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    source, target = sys.stdin.buffer, sys.stdout.buffer
    state = state_dir()
    state.mkdir(parents=True, exist_ok=True)
    token = secrets.token_bytes(32)
    replies = queue.Queue(maxsize=2)
    stopped = threading.Event()
    endpoint = state / 'endpoint.json'
    listener = None
    try:
        hello = read_message(source)
        if not hello or hello.get('type') != 'hello':
            return 1
        profile = hello.get('profile')
        if not isinstance(profile, str) or len(profile) != 36:
            return 1
        preferred = state / 'profile.json'
        if preferred.exists() and not hello.get('select'):
            if json.loads(preferred.read_text('utf-8')).get('profile') != profile:
                write_message(target, {'type': 'status', 'ok': False, 'message': 'Another Edge profile is selected. Click Connect to select this profile.'})
                return 0
        write_json(preferred, {'profile': profile})
        address = '\\\\.\\pipe\\MediaGrab-' + secrets.token_hex(16)
        listener = Listener(address, family='AF_PIPE', authkey=token)
        write_json(endpoint, {'address': address, 'key': token.hex(), 'profile': profile})

        def serve():
            while not stopped.is_set():
                try:
                    with listener.accept() as client:
                        if not client.poll(5):
                            continue
                        request = json.loads(client.recv_bytes(1024))
                        if request != {'command': 'cookies'}:
                            client.send_bytes(b'{"error":"invalid_request"}')
                            continue
                        if json.loads(endpoint.read_text('utf-8')).get('key') != token.hex():
                            client.send_bytes(b'{"error":"profile_changed"}')
                            continue
                        request_id = secrets.token_hex(16)
                        write_message(target, {'type': 'cookies', 'id': request_id})
                        deadline = time.monotonic() + 15
                        result = {'error': 'timeout'}
                        while time.monotonic() < deadline:
                            reply = replies.get(timeout=max(.01, deadline - time.monotonic()))
                            if reply.get('id') == request_id:
                                result = {'cookies': reply.get('cookies', [])} if not reply.get('error') else {'error': 'browser_error'}
                                break
                        client.send_bytes(json.dumps(result).encode('utf-8'))
                        result = None
                except Exception:
                    # Never log cookie payloads or raw IPC errors.
                    if stopped.is_set():
                        break

        write_message(target, {'type': 'status', 'ok': True, 'message': 'Connected. MediaGrab can request your current YouTube session.'})
        threading.Thread(target=serve, daemon=True).start()
        while (message := read_message(source)) is not None:
            if message.get('type') == 'cookies':
                try:
                    replies.put_nowait(message)
                except queue.Full:
                    pass
        return 0
    except Exception:
        return 1
    finally:
        stopped.set()
        if listener is not None:
            listener.close()
        try:
            if json.loads(endpoint.read_text('utf-8')).get('key') == token.hex():
                endpoint.unlink()
        except (OSError, ValueError):
            pass


def install_host(executable: Path) -> None:
    import winreg
    state = state_dir()
    state.mkdir(parents=True, exist_ok=True)
    manifest = state / 'host.json'
    write_json(manifest, {'name': HOST_NAME, 'description': 'MediaGrab Edge session bridge',
        'path': str(executable.resolve()), 'type': 'stdio', 'allowed_origins': [ORIGIN]})
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, 'Software\\Microsoft\\Edge\\NativeMessagingHosts\\' + HOST_NAME) as key:
        winreg.SetValueEx(key, '', 0, winreg.REG_SZ, str(manifest))
