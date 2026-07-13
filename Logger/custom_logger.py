import inspect
import logging
import os
import sys
from pathlib import Path
from typing import List, Tuple, Union

from run_settings import LOG_DIR


def setup_global_logger(
    script_name: str = None,
    log_level: Union[int, str] = "INFO",
    headers: Union[List[str], Tuple[str, ...]] = None,
    logger_name: str = "pipeline",
) -> logging.Logger:
    if isinstance(log_level, str):
        log_level = getattr(logging, log_level.upper(), logging.INFO)

    if script_name is None:
        frame = inspect.stack()[1]
        script_name = os.path.basename(frame.filename)

    if headers is None or not isinstance(headers, (list, tuple)) or len(headers) < 3:
        raise ValueError("headers must include at least Date, Level, Msg/Message")

    first_three = [str(h).strip().lower() for h in headers[:3]]
    if not (
        first_three[0] == "date"
        and first_three[1] == "level"
        and first_three[2] in ("msg", "message")
    ):
        raise ValueError("headers must start with ['Date','Level','Msg'|'Message']")

    # RAG_LOG_DIR env var overrides run_settings.LOG_DIR for portability.
    log_dir = os.getenv("RAG_LOG_DIR") or LOG_DIR
    log_path = Path(log_dir) / f"a_{os.path.splitext(script_name)[0]}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    class CSVFormatter(logging.Formatter):
        def __init__(self, columns, datefmt=None):
            super().__init__()
            self.columns = [str(c) for c in columns]
            self.datefmt = datefmt or "%m-%d %H:%M"

        @staticmethod
        def _escape(v) -> str:
            if v is None:
                return '""'
            s = str(v).replace('"', '""')
            return f'"{s}"'

        def format(self, record):
            message = record.getMessage()
            row = []
            for idx, col in enumerate(self.columns):
                if idx == 0:
                    row.append(self._escape(self.formatTime(record, self.datefmt)))
                elif idx == 1:
                    row.append(self._escape(record.levelname))
                elif idx == 2:
                    row.append(self._escape(message))
                else:
                    row.append(self._escape(record.__dict__.get(col, "")))
            return ",".join(row)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))

    file_handler = logging.FileHandler(log_path, mode="w", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(CSVFormatter(headers))
    file_handler.stream.write(",".join(str(h) for h in headers) + "\n")
    file_handler.stream.flush()

    logger = logging.getLogger(logger_name)
    logger.setLevel(log_level)
    logger.propagate = False
    # Close existing handlers before dropping them: clearing alone leaks the
    # open file descriptors (and locks the log file on Windows) when this is
    # called more than once per process.
    for handler in list(logger.handlers):
        try:
            handler.close()
        except Exception:
            pass
    logger.handlers.clear()
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    logger.info(f"Logger initialized: {log_path}")
    return logger
