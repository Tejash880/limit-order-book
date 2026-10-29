"""Event logging, serialization, hashing, and deterministic replay engine."""

from __future__ import annotations
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

from lob.engine import MatchingEngine
from lob.models import Event, OrderType, Side, event_from_dict


def canonical_event_json(event: Event) -> str:
    """Return a deterministic canonical JSON string for an event."""
    return json.dumps(event.to_dict(), sort_keys=True, separators=(",", ":"))


def hash_events(events: Iterable[Event]) -> str:
    """Compute an SHA256 digest across a sequence of events.

    Guarantees that identical event streams yield the exact same hexadecimal digest.
    """
    hasher = hashlib.sha256()
    for event in events:
        hasher.update(canonical_event_json(event).encode("utf-8"))
    return hasher.hexdigest()


class EventLogger:
    """Logs client commands and engine output events to JSONL files."""

    def __init__(self, filepath: Union[str, Path]) -> None:
        self.filepath = Path(filepath)
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self.filepath, "w", encoding="utf-8")

    def log_command(self, cmd_dict: Dict[str, Any]) -> None:
        """Write a command line to JSONL."""
        self._file.write(json.dumps(cmd_dict, sort_keys=True) + "\n")
        self._file.flush()

    def close(self) -> None:
        if not self._file.closed:
            self._file.close()

    def __enter__(self) -> EventLogger:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


class EventReplayer:
    """Replays commands from a JSONL log file into a fresh MatchingEngine."""

    def __init__(self, filepath: Union[str, Path]) -> None:
        self.filepath = Path(filepath)

    def replay(self, engine: Optional[MatchingEngine] = None) -> Tuple[MatchingEngine, List[Event], str]:
        """Replay all commands in the file.

        Returns:
            Tuple of (engine, all_emitted_events, sha256_hash_of_events)
        """
        if engine is None:
            engine = MatchingEngine()

        all_events: List[Event] = []
        with open(self.filepath, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                cmd = json.loads(line)
                action = cmd["cmd"]

                if action == "NEW_ORDER":
                    side = Side(cmd["side"])
                    order_type = OrderType(cmd["order_type"])
                    events = engine.process_order(
                        order_id=cmd["order_id"],
                        side=side,
                        order_type=order_type,
                        quantity=cmd["quantity"],
                        price=cmd.get("price"),
                    )
                elif action == "CANCEL":
                    events = engine.cancel_order(order_id=cmd["order_id"])
                elif action == "AMEND":
                    events = engine.amend_order(
                        order_id=cmd["order_id"],
                        new_price=cmd["new_price"],
                        new_qty=cmd["new_qty"],
                    )
                else:
                    raise ValueError(f"Unknown command '{action}' at line {line_num}")

                all_events.extend(events)

        digest = hash_events(all_events)
        return engine, all_events, digest
