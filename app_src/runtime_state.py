from __future__ import annotations

import json
import os
import random
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def project_root_from(file_path: str) -> Path:
    return Path(file_path).resolve().parent.parent if file_path.endswith('.py') else Path(file_path).resolve()


def atomic_write_json(path: Path, payload: dict[str, Any], retries: int = 30, base_delay: float = 0.05) -> None:
    """Windows-safe atomic-ish JSON write.

    Writes to a unique temp file in the same directory and then replaces the target.
    Retries os.replace() because Windows can briefly deny the rename if another process
    is reading the destination file at that exact moment.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    safe = dict(payload)
    safe.setdefault('updated_at', datetime.now(timezone.utc).isoformat())
    content = json.dumps(safe, indent=2, ensure_ascii=False)

    last_exc: Exception | None = None
    for attempt in range(retries):
        temp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        try:
            temp.write_text(content, encoding='utf-8')
            os.replace(temp, path)
            return
        except PermissionError as exc:
            last_exc = exc
            try:
                if temp.exists():
                    temp.unlink()
            except Exception:
                pass
            # Small jitter helps when multiple processes are touching the same file.
            delay = min(base_delay * (1.35 ** attempt), 1.0) + random.uniform(0.0, 0.02)
            time.sleep(delay)
        except Exception:
            try:
                if temp.exists():
                    temp.unlink()
            except Exception:
                pass
            raise

    if last_exc is not None:
        raise last_exc


def read_json(path: Path, fallback: dict[str, Any] | None = None, retries: int = 3, delay: float = 0.03) -> dict[str, Any]:
    fallback = fallback or {}
    if not path.exists():
        return dict(fallback)
    for attempt in range(retries):
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            if isinstance(data, dict):
                out = dict(fallback)
                out.update(data)
                return out
            return dict(fallback)
        except Exception:
            if attempt == retries - 1:
                return dict(fallback)
            time.sleep(delay)
    return dict(fallback)
