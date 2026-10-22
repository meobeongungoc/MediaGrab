"""Local diagnostic logs never retain URLs or raw credential fields."""
import re
from pathlib import Path
from logging.handlers import RotatingFileHandler
import logging


def redact(text: str) -> str:
    text = re.sub(r'https?://[^\s<>]+', '[URL hidden]', text, flags=re.I)
    text = re.sub(r'(?i)\b(cookie|authorization|token|password|sapisid|sid|__Secure-[\w-]+)\b\s*[:=]\s*[^\s;,]+', r'\1=[hidden]', text)
    text = re.sub(r'(?i)Bearer\s+\S+', 'Bearer [hidden]', text)
    return text


def local_logger(root: Path) -> logging.Logger:
    logger = logging.getLogger('mediagrab.gui.safe')
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        folder = root / 'logs'
        folder.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(folder / 'mediagrab.log', maxBytes=1_000_000, backupCount=2, encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
        logger.addHandler(handler)
    return logger
