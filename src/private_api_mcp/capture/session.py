"""In-memory capture session, ring buffer, and action marker tracking."""

from __future__ import annotations

from collections import deque
import json
from pathlib import Path
import threading
import time
from typing import Any, Optional, Union
from urllib.parse import urlparse

from private_api_mcp.models import CapturedExchange


class CaptureSession:
    """Stores captured HTTP exchanges with a configurable maximum buffer size and action checkpoints."""

    def __init__(self, max_buffer: int = 1000) -> None:
        self.max_buffer = max_buffer
        self._exchanges: deque[CapturedExchange] = deque(maxlen=max_buffer)
        self._action_markers: list[dict[str, Any]] = []
        self._current_action_label: Optional[str] = None
        self._lock = threading.Lock()

    @property
    def current_action_label(self) -> Optional[str]:
        return self._current_action_label

    @current_action_label.setter
    def current_action_label(self, label: Optional[str]) -> None:
        self._current_action_label = label

    @property
    def action_markers(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._action_markers)

    def add_exchange(self, exchange: CapturedExchange) -> None:
        """Add a captured exchange to the ring buffer.

        If the exchange does not have an action_label and an action is currently active,
        assign the active action_label.
        """
        if exchange.action_label is None and self._current_action_label:
            exchange.action_label = self._current_action_label

        with self._lock:
            self._exchanges.append(exchange)

    def mark_action(self, label: str) -> dict[str, Any]:
        """Record an action marker checkpoint with a timestamp.

        Subsequent captured requests will be associated with this label.
        """
        if not label:
            raise ValueError("Action label cannot be empty")

        checkpoint = {
            "label": label,
            "timestamp": time.time(),
        }
        with self._lock:
            self._action_markers.append(checkpoint)
            self._current_action_label = label

        return checkpoint

    def get_exchanges(
        self,
        since_mark: Optional[str] = None,
        domain: Optional[str] = None,
        method: Optional[str] = None,
        limit: int = 100,
    ) -> list[CapturedExchange]:
        """Retrieve captured exchanges filtered by checkpoint, domain, and HTTP method."""
        with self._lock:
            items = list(self._exchanges)
            markers = list(self._action_markers)

        # 1. Filter by action marker timestamp if since_mark is provided
        if since_mark:
            marker_ts: Optional[float] = None
            # Search for the latest occurrence of since_mark
            for m in reversed(markers):
                if m["label"] == since_mark:
                    marker_ts = m["timestamp"]
                    break

            if marker_ts is not None:
                items = [ex for ex in items if ex.timestamp >= marker_ts]
            else:
                # Fallback: match by label string if timestamp marker not found
                items = [ex for ex in items if ex.action_label == since_mark]

        # 2. Filter by domain
        if domain:
            target_domain = domain.lower().strip()
            filtered = []
            for ex in items:
                try:
                    host = (urlparse(ex.url).hostname or "").lower()
                    if host == target_domain or host.endswith("." + target_domain):
                        filtered.append(ex)
                except Exception:
                    continue
            items = filtered

        # 3. Filter by method
        if method:
            target_method = method.upper().strip()
            items = [ex for ex in items if ex.method.upper() == target_method]

        # Return up to limit items
        if limit is not None and limit > 0:
            return items[-limit:] if len(items) > limit else items
        return items

    def dump_to_jsonl(self, file_path: Union[str, Path]) -> int:
        """Serialize all captured exchanges to a JSON Lines (.jsonl) file."""
        p = Path(file_path)
        p.parent.mkdir(parents=True, exist_ok=True)

        with self._lock:
            exchanges = list(self._exchanges)

        with open(p, "w", encoding="utf-8") as f:
            for ex in exchanges:
                f.write(ex.model_dump_json() + "\n")

        return len(exchanges)

    def load_from_jsonl(self, file_path: Union[str, Path]) -> int:
        """Load exchanges from a JSON Lines (.jsonl) file and append them to the session."""
        p = Path(file_path)
        if not p.is_file():
            raise FileNotFoundError(f"JSONL file not found: {file_path}")

        loaded_count = 0
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line_str = line.strip()
                if not line_str:
                    continue
                try:
                    ex = CapturedExchange.model_validate_json(line_str)
                    self.add_exchange(ex)
                    loaded_count += 1
                except Exception:
                    continue

        return loaded_count

    def clear(self) -> None:
        """Clear all stored exchanges, markers, and reset active action."""
        with self._lock:
            self._exchanges.clear()
            self._action_markers.clear()
            self._current_action_label = None

    def __len__(self) -> int:
        with self._lock:
            return len(self._exchanges)
