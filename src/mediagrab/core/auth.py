"""Read an explicitly chosen login session into memory, never export it."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import http.cookiejar
import warnings
import functools
import inspect

from yt_dlp.cookies import YoutubeDLCookieJar, extract_cookies_from_browser
from .errors import MediaGrabError, SessionRejected


class CookieLogger:
    def debug(self, *args: Any, **kwargs: Any) -> None:
        pass
    info = debug
    warning = debug
    error = debug


@dataclass(repr=False)
class AuthSession:
    jar: http.cookiejar.CookieJar = field(repr=False)
    source: str = 'edge'

    def refresh(self) -> None:
        if self.source == 'edge-extension':
            from .edge_bridge import fetch_cookies
            self.jar = validate_session_jar(fetch_cookies())

    def __repr__(self) -> str:
        return 'AuthSession(<credentials hidden>)'

    def attach(self, ydl: Any) -> None:
        ydl.cookiejar = self.jar


def load_session(project: Path, browser: str | None = None,
                 profile: str | None = None, cookie_file: Path | None = None) -> AuthSession | None:
    if browser and cookie_file:
        raise MediaGrabError('Choose one login source: a browser or a cookie file.')
    if profile and not browser:
        raise MediaGrabError('Select a browser before specifying a profile.')
    if not browser and cookie_file is None:
        return None
    if browser and browser not in ('edge', 'chrome', 'firefox', 'edge-extension'):
        raise MediaGrabError('Supported browsers: Edge, Chrome and Firefox.')
    if browser == 'edge-extension':
        if profile:
            raise MediaGrabError('Choose the Edge profile using Connect in the extension, not the profile text field.')
        from .edge_bridge import fetch_cookies
        jar = fetch_cookies()
    elif cookie_file is not None:
        path = cookie_file.expanduser().resolve()
        if path.is_relative_to(project.resolve()):
            raise MediaGrabError('The cookie file must be outside the project/app folder. Never paste cookies into chat.')
        if not path.is_file():
            raise MediaGrabError('Cookie file not found. Choose the file again on this computer.')
        try:
            jar = http.cookiejar.MozillaCookieJar(str(path))
            with warnings.catch_warnings():
                warnings.simplefilter('ignore')
                jar.load(ignore_discard=True, ignore_expires=True)
            for loaded_cookie in jar:
                if loaded_cookie.expires == 0:
                    loaded_cookie.expires = None
                    loaded_cookie.discard = True
            jar.filename = None
        except Exception:
            raise MediaGrabError('Cannot read the cookie file or its Netscape format is invalid. Choose a new file locally; never send its contents in chat.') from None
    else:
        try:
            jar = extract_cookies_from_browser(browser, profile, CookieLogger())
        except Exception:
            raise MediaGrabError('Cannot read browser cookies. Close Edge/Chrome and retry. If protection still prevents access, use Firefox or a Netscape cookie file outside the app. Browser security is not disabled.') from None
    return AuthSession(validate_session_jar(jar), browser or 'file')


def validate_session_jar(jar):
    # Retain only domains required for YouTube authentication.
    filtered = YoutubeDLCookieJar()
    for cookie in jar:
        domain = cookie.domain.lstrip('.').lower()
        if domain in ('youtube.com', 'google.com') or domain.endswith(('.youtube.com', '.google.com')):
            filtered.set_cookie(cookie)
    if not any(cookie.name in ('SAPISID', '__Secure-1PAPISID', '__Secure-3PAPISID', 'SID', '__Secure-1PSID', '__Secure-3PSID') and not cookie.is_expired() for cookie in filtered):
        raise MediaGrabError('No usable YouTube session found. Sign in to YouTube and choose the correct browser profile. Login is not confirmed.')
    return filtered


def session_rejected(error) -> bool:
    message = str(error).lower()
    return any(word in message for word in ('sign in', 'sign-in', 'login required',
        'cookies are no longer valid', 'cookies have expired', 'session has expired', 'http error 401'))


def fresh_edge_session(operation):
    signature = inspect.signature(operation)
    @functools.wraps(operation)
    def wrapped(*args, **kwargs):
        session = signature.bind_partial(*args, **kwargs).arguments.get('session')
        if session is None or session.source != 'edge-extension':
            return operation(*args, **kwargs)
        session.refresh()
        try:
            return operation(*args, **kwargs)
        except SessionRejected:
            session.refresh()
            try:
                return operation(*args, **kwargs)
            except SessionRejected:
                raise MediaGrabError('YouTube rejected the refreshed session. Sign in again in the connected Edge profile, then retry. Membership still requires account access.') from None
    return wrapped


def apply_auth_limits(options: dict[str, Any], session: AuthSession | None) -> None:
    if session:
        options.update(concurrent_fragment_downloads=1, sleep_interval=5,
                       max_sleep_interval=10, sleep_interval_requests=1)
