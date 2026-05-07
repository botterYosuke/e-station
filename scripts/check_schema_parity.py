#!/usr/bin/env python3
"""Verify that proto/engine.proto is consistent with the JSON schemas.

Checks:
  1. Every Command name in commands.json has a corresponding oneof field in Command.
  2. Every Event name in events.json has a corresponding oneof field in Event.
  3. No proto Command/Event oneof field is missing from the JSON schemas
     (one-sided additions are flagged).

Run:
    python scripts/check_schema_parity.py

Exit code 0 = parity OK; non-zero = drift detected.

Note: This script is intentionally simple — it checks names only, not field-level
types (that requires a proto descriptor which depends on protoc being installed).
Field-level drift is caught by the generated code failing to compile.

This script will be deleted in G3 (WebSocket transport removal) when JSON schemas
are retired and the proto becomes the sole source of truth.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
COMMANDS_JSON = ROOT / "docs" / "✅python-data-engine" / "schemas" / "commands.json"
EVENTS_JSON = ROOT / "docs" / "✅python-data-engine" / "schemas" / "events.json"
PROTO_FILE = ROOT / "proto" / "engine.proto"

# Commands that exist in dto.rs but are NOT yet in commands.json (newer additions).
# Remove from this list once commands.json is updated.
PROTO_ONLY_COMMANDS: set[str] = {
    "StartEngine",
    "StopEngine",
    "LoadReplayData",
    "SetReplaySpeed",
    "StopReplay",
    "ForceStopReplay",
    "PauseReplay",
    "ResumeReplay",
    "StepReplay",
    "StepBackward",
    "LoadStrategyScenario",
    "SaveStrategyScenario",
}

# Events that exist in dto.rs but not in events.json.
PROTO_ONLY_EVENTS: set[str] = {
    "EngineStarted",
    "EngineStopped",
    "ReplayDataLoaded",
    "PositionOpened",
    "PositionClosed",
    "ExecutionMarker",
    "StrategySignal",
    "ReplayBuyingPower",
    "DateChangeMarker",
    "RestoreSnapshot",
    "ReplayHistoryChanged",
    "ReplayStopped",
    "EngineBusy",
    "ClientConnected",
    "ClientDisconnected",
    "StrategyScenarioLoaded",
    "StrategyScenarioLoadFailed",
    "StrategyScenarioSaved",
    "LiveBuyingPower",
    # Synthetic — Rust-only, not in proto nor JSON schemas
    # "ConnectionDropped",  # excluded by design
}


def extract_oneof_names_from_proto(proto_text: str, message_name: str) -> set[str]:
    """Extract field names from the oneof payload block in a given message."""
    # Find the message block
    pattern = rf"message\s+{re.escape(message_name)}\s*\{{([^}}]*oneof\s+payload\s*\{{[^}}]*\}}[^}}]*)\}}"
    msg_match = re.search(pattern, proto_text, re.DOTALL)
    if not msg_match:
        raise ValueError(f"Could not find message {message_name} with oneof payload in proto")

    oneof_pattern = r"oneof\s+payload\s*\{([^}]*)\}"
    oneof_match = re.search(oneof_pattern, msg_match.group(1), re.DOTALL)
    if not oneof_match:
        raise ValueError(f"Could not find oneof payload in {message_name}")

    # Extract field names (type field_name = N;)
    field_pattern = r"(\w+)\s+(\w+)\s*=\s*\d+"
    fields: set[str] = set()
    for line in oneof_match.group(1).splitlines():
        line = line.strip()
        if line.startswith("//") or not line:
            continue
        m = re.match(field_pattern, line)
        if m:
            # The message type is the first capture, field name is second
            # e.g. "HelloRequest hello = 1;" → type=HelloRequest, name=hello
            fields.add(m.group(2))
    return fields


def snake_to_pascal(name: str) -> str:
    return "".join(w.capitalize() for w in name.split("_"))


def extract_json_schema_names(schema_path: Path) -> set[str]:
    """Extract command/event names from a JSON schema oneOf list."""
    if not schema_path.exists():
        return set()
    data = json.loads(schema_path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for ref in data.get("oneOf", []):
        ref_str = ref.get("$ref", "")
        if ref_str.startswith("#/$defs/"):
            names.add(ref_str[len("#/$defs/"):])
    return names


def main() -> int:
    proto_text = PROTO_FILE.read_text(encoding="utf-8")
    errors: list[str] = []

    # ── Commands ──────────────────────────────────────────────────────────────
    proto_command_fields = extract_oneof_names_from_proto(proto_text, "Command")
    # Convert snake_case field names → PascalCase command names
    proto_command_names = {snake_to_pascal(f) for f in proto_command_fields}

    json_command_names = extract_json_schema_names(COMMANDS_JSON)

    json_only = json_command_names - proto_command_names - PROTO_ONLY_COMMANDS
    for name in sorted(json_only):
        errors.append(f"COMMAND in JSON schema but missing from proto oneof: {name}")

    proto_only = proto_command_names - json_command_names - PROTO_ONLY_COMMANDS
    for name in sorted(proto_only):
        errors.append(f"COMMAND in proto oneof but missing from JSON schema (add to PROTO_ONLY_COMMANDS or update JSON): {name}")

    # ── Events ────────────────────────────────────────────────────────────────
    if EVENTS_JSON.exists():
        proto_event_fields = extract_oneof_names_from_proto(proto_text, "Event")
        proto_event_names = {snake_to_pascal(f) for f in proto_event_fields}

        json_event_names = extract_json_schema_names(EVENTS_JSON)

        json_only_ev = json_event_names - proto_event_names - PROTO_ONLY_EVENTS
        for name in sorted(json_only_ev):
            errors.append(f"EVENT in JSON schema but missing from proto oneof: {name}")

        proto_only_ev = proto_event_names - json_event_names - PROTO_ONLY_EVENTS
        for name in sorted(proto_only_ev):
            errors.append(f"EVENT in proto oneof but missing from JSON schema (add to PROTO_ONLY_EVENTS or update JSON): {name}")

    # ── Handshake contract validation ─────────────────────────────────────────
    # HelloRequest must be field 1 in Command.oneof.payload
    hello_match = re.search(r"HelloRequest\s+hello\s*=\s*(\d+)", proto_text)
    if not hello_match or hello_match.group(1) != "1":
        errors.append("PROTO CONTRACT VIOLATION: HelloRequest must be field 1 in Command.oneof.payload")

    # ReadyResponse must be field 1 in Event.oneof.payload
    ready_match = re.search(r"ReadyResponse\s+ready\s*=\s*(\d+)", proto_text)
    if not ready_match or ready_match.group(1) != "1":
        errors.append("PROTO CONTRACT VIOLATION: ReadyResponse must be field 1 in Event.oneof.payload")

    # ── Report ────────────────────────────────────────────────────────────────
    if errors:
        print(f"Schema parity FAILED ({len(errors)} issue(s)):")
        for e in errors:
            print(f"  ✗ {e}")
        return 1

    n_events = len(proto_event_names) if EVENTS_JSON.exists() else "?"
    print(f"Schema parity OK - {len(proto_command_names)} commands, {n_events} events checked.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
