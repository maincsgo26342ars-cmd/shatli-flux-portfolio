"""Rotating local application log. Never log credentials or authentication tokens."""
import logging
from logging.handlers import RotatingFileHandler

from app.config import Config


def setup_logging(config: Config) -> None:
    directory = config.user_data()
    directory.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        directory / config.values["paths"]["log"],
        maxBytes=1_000_000, backupCount=2, encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger = logging.getLogger("shatli_flux")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        logger.addHandler(handler)
