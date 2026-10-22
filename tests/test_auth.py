from pathlib import Path
import http.cookiejar
from unittest.mock import Mock
import pytest
from yt_dlp.cookies import YoutubeDLCookieJar
from mediagrab.core.auth import load_session, AuthSession, CookieLogger, apply_auth_limits
from mediagrab.core.errors import MediaGrabError


def cookie(domain, name='SAPISID'):
    return http.cookiejar.Cookie(0, name, 'SECRET_MUST_NOT_LEAK', None, False, domain, domain.startswith('.'), domain.startswith('.'), '/', True, True, None, True, None, None, {})


def test_browser_memory_only_and_domain_filter(monkeypatch, tmp_path):
    from mediagrab.core import auth
    jar = YoutubeDLCookieJar()
    jar.set_cookie(cookie('.youtube.com'))
    jar.set_cookie(cookie('.unrelated.example'))
    monkeypatch.setattr(auth, 'extract_cookies_from_browser', lambda *a: jar)
    session = load_session(tmp_path, 'edge')
    assert len(session.jar) == 1 and session.jar.filename is None
    assert 'SECRET' not in repr(session)
    fake = Mock()
    session.attach(fake)
    assert fake.cookiejar is session.jar
    options = {}
    apply_auth_limits(options, session)
    assert options['concurrent_fragment_downloads'] == 1 and options['sleep_interval'] >= 5


def test_cookie_logger_and_error_never_leak(monkeypatch, tmp_path, capsys, caplog):
    from mediagrab.core import auth
    def fail(*args):
        raise RuntimeError('SECRET_MUST_NOT_LEAK')
    monkeypatch.setattr(auth, 'extract_cookies_from_browser', fail)
    for method in ('debug', 'info', 'warning', 'error'):
        getattr(CookieLogger(), method)('SECRET_MUST_NOT_LEAK')
    with pytest.raises(MediaGrabError) as e:
        load_session(tmp_path, 'edge')
    assert 'SECRET' not in str(e.value) + capsys.readouterr().out + caplog.text


def test_reject_cookie_inside_project(tmp_path):
    file = tmp_path / 'cookies.txt'
    file.write_text('DO NOT READ')
    with pytest.raises(MediaGrabError, match='outside'):
        load_session(tmp_path, cookie_file=file)


def test_cookie_file_not_modified(tmp_path):
    project = tmp_path / 'project'
    project.mkdir()
    path = tmp_path / 'cookies-test.txt'
    jar = YoutubeDLCookieJar(str(path))
    test_cookie = cookie('.youtube.com')
    test_cookie.expires = 4102444800
    jar.set_cookie(test_cookie)
    jar.save(ignore_discard=True, ignore_expires=True)
    before = path.read_bytes()
    session = load_session(project, cookie_file=path)
    assert session.jar.filename is None and path.read_bytes() == before


def test_empty_jar_not_claimed_logged_in(monkeypatch, tmp_path):
    from mediagrab.core import auth
    monkeypatch.setattr(auth, 'extract_cookies_from_browser', lambda *a: YoutubeDLCookieJar())
    with pytest.raises(MediaGrabError, match='No usable YouTube session'):
        load_session(tmp_path, 'edge')



def test_local_log_redacts_private_urls_and_credentials():
    from mediagrab.core.logging_utils import redact
    text = 'cookie=SECRET authorization=SECRET token=SECRET https://example.com/rss?token=SECRET Bearer SECRET'
    assert 'SECRET' not in redact(text)


def test_malformed_cookie_file_never_prints_content(tmp_path, capsys, caplog):
    project = tmp_path / 'project'
    project.mkdir()
    path = tmp_path / 'cookies-bad.txt'
    path.write_text('# Netscape HTTP Cookie File\n.youtube.com\tFALSE\t/\tFALSE\t4102444800\tSAPISID\tSECRET_MUST_NOT_LEAK\textra\n', encoding='utf-8')
    with pytest.raises(MediaGrabError) as error:
        load_session(project, cookie_file=path)
    captured = capsys.readouterr()
    assert 'SECRET_MUST_NOT_LEAK' not in str(error.value) + captured.out + captured.err + caplog.text
