"""Translate failures without exposing raw URLs, tokens or server responses."""

class MediaGrabError(Exception):
    """A message safe to show to the user."""


class SessionRejected(MediaGrabError):
    """The source explicitly rejected the login session."""


class MembershipRequired(MediaGrabError):
    """YouTube explicitly requires a channel membership or a higher tier."""


def membership_required(error: BaseException | str) -> bool:
    text = str(error).lower()
    return any(phrase in text for phrase in (
        'join this channel to get access', 'members-only content',
        'only available to members', 'available to this channel\'s members',
        'requires a channel membership', 'members at level',
    ))


MEMBERSHIP_MESSAGE = 'Channel membership required. Join this channel at the required tier with this YouTube account, then retry. No video was unlocked.'


def explain_error(error: BaseException | str) -> str:
    message = str(error).lower()
    if membership_required(error):
        return MEMBERSHIP_MESSAGE
    if any(word in message for word in ("members-only", "members only", "sign in", "login", "private video")):
        return "This content requires account access. Sign in with an authorized YouTube account and select Edge and the correct profile. Membership access requires a membership."
    if any(word in message for word in ("not available in your country", "geo-restricted", "geographic")):
        return "This content is region-restricted. The app does not bypass region restrictions."
    if any(word in message for word in ("removed", "unavailable", "not found", "404")):
        return "This content is no longer accessible. Check the URL in your browser."
    if "requested format" in message:
        return "The selected format is unavailable. For video, select Best quality (MKV) to preserve source quality."
    if any(word in message for word in ("ffmpeg", "ffprobe")):
        return "FFmpeg/FFprobe could not process the file. Check the tools and available disk space."
    if any(word in message for word in ("timed out", "timeout", "connection", "network", "403", "429")):
        return "Connection failed or the source is limiting access. Retry later; update the downloader if the issue persists."
    if any(word in message for word in ("permission", "access is denied", "no space", "disk full")):
        return "Cannot write the file. Check folder permissions and available disk space."
    return "Download failed. Check the public URL and connection; the downloader may need an update if the platform changed."
