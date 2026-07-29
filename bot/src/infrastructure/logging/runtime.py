from __future__ import annotations

import logging
from pathlib import Path


class GoldQueueLoggingManager:
    @staticmethod
    def configure(logs_dir: Path, level_name: str) -> None:
        level = getattr(logging, level_name.upper(), logging.INFO)
        logs_dir.mkdir(parents=True, exist_ok=True)
        root = logging.getLogger()
        root.setLevel(level)
        for handler in list(root.handlers):
            root.removeHandler(handler)
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        from datetime import datetime
        file_handler = logging.FileHandler(logs_dir / f"gold-bot.{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.log", encoding="utf-8")
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        console_handler = logging.StreamHandler()
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        root.addHandler(file_handler)
        root.addHandler(console_handler)
        # Keep application DEBUG visibility while reducing third-party noise.
        logging.getLogger("matplotlib").setLevel(logging.INFO)
        logging.getLogger("matplotlib.font_manager").setLevel(logging.INFO)
        logging.getLogger(__name__).info("🧭 Logging configured | requested=%s resolved=%s", level_name, logging.getLevelName(level))
