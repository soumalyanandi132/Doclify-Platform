"""Logging configured explicitly by the CLI or application host."""
import logging
from datetime import datetime
from pathlib import Path

def get_logger(name=__name__):
    return logging.getLogger(name)

def configure_cli_logging():
    logger = logging.getLogger("doclify")
    if logger.handlers:
        return
    directory = Path(".doclify/logs")
    directory.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(directory / f"{datetime.now():%Y%m%d-%H%M%S}.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
