#!/usr/bin/env python3
"""Verify that proto/engine.proto is consistent with the JSON schemas.

Checks:
  1. Every Command name in commands.json has a corresponding oneof field in Command.
  2. Every Event name in events.json has a corresponding oneof field in Event.
  3. No proto Command/Event oneof field is missing from the JSON schemas
     (one-sided additions are flagged).
  4. [Field-level] proto oneof field numbers are unique (no duplicates).
  5. [Field-level] `reserved` keyword is present in Command/Event messages (proto
     deletion hygiene guard).
  6. [Field-level] HelloRequest must be field 1 in Command oneof.
  7. [Field-level] ReadyResponse must be field 1 in Event oneof.

Run:
    python scripts/check_schema_parity.py

Exit code 0 = parity OK; non-zero = drift detected.

This script will be deleted in G3 (WebSocket transport removal) when JSON schemas
are retired and the proto becomes the sole source of truth.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
COMMANDS_JSON = ROOT / "docs" / "specs/data-engine" / "schemas" / "commands.json"
EVENTS_JSON = ROOT / "docs" / "specs/data-engine" / "schemas" / "events.json"
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


def extract_message_block(proto_text: str, message_name: str) -> str | None:
    """Extract the full message block for a named proto message.

    R2-M7: Uses a brace-depth counter to correctly handle nested `}` braces,
    replacing the fragile regex that matched the first closing `}`.
    """
    start = re.search(rf'message\s+{re.escape(message_name)}\s*\{{', proto_text)
    if not start:
        return None
    depth = 1
    i = start.end()
    while i < len(proto_text) and depth > 0:
        if proto_text[i] == '{':
            depth += 1
        elif proto_text[i] == '}':
            depth -= 1
        i += 1
    return proto_text[start.start():i]


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


def extract_oneof_field_numbers_from_proto(proto_text: str, message_name: str) -> dict[str, int]:
    """Extract {field_name: field_number} from the oneof payload block in a given message."""
    pattern = rf"message\s+{re.escape(message_name)}\s*\{{([^}}]*oneof\s+payload\s*\{{[^}}]*\}}[^}}]*)\}}"
    msg_match = re.search(pattern, proto_text, re.DOTALL)
    if not msg_match:
        return {}

    oneof_pattern = r"oneof\s+payload\s*\{([^}]*)\}"
    oneof_match = re.search(oneof_pattern, msg_match.group(1), re.DOTALL)
    if not oneof_match:
        return {}

    field_pattern = r"(\w+)\s+(\w+)\s*=\s*(\d+)"
    fields: dict[str, int] = {}
    for line in oneof_match.group(1).splitlines():
        line = line.strip()
        if line.startswith("//") or not line:
            continue
        m = re.match(field_pattern, line)
        if m:
            fields[m.group(2)] = int(m.group(3))
    return fields


def check_field_level(proto_text: str, message_name: str, errors: list[str]) -> None:
    """Field-level checks for a proto message's oneof payload block.

    Checks:
    1. No duplicate field numbers.
    2. `reserved` keyword exists in the message (deletion hygiene).
    3. Field numbers are contiguous starting from 1 (gaps may indicate accidental deletion
       without `reserved`).
    """
    field_map = extract_oneof_field_numbers_from_proto(proto_text, message_name)
    if not field_map:
        return

    # 1. Duplicate field numbers
    number_to_names: dict[int, list[str]] = {}
    for name, num in field_map.items():
        number_to_names.setdefault(num, []).append(name)
    for num, names in sorted(number_to_names.items()):
        if len(names) > 1:
            errors.append(
                f"FIELD NUMBER DUPLICATE in {message_name} oneof: "
                f"field number {num} used by {names}"
            )

    # 2. `reserved` keyword presence (deletion hygiene guard).
    # R2-M7: use extract_message_block() (brace-depth counter) instead of the
    # fragile regex that could match the first inner `}` in a long message block.
    # We accept that in early stages there may be no deletions yet — this is a
    # WARNING-level notice only (does not fail the check).
    msg_block_text = extract_message_block(proto_text, message_name)
    if msg_block_text is not None and "reserved" not in msg_block_text:
        # Not an error — just a note. Print it as an informational warning.
        print(
            f"  NOTE: {message_name} has no `reserved` fields yet "
            f"(add `reserved N;` when deleting fields to prevent number reuse)."
        )

    # 3. Check for gaps in field numbers (may indicate missing `reserved` after deletion)
    numbers = sorted(field_map.values())
    if numbers:
        expected = set(range(1, numbers[-1] + 1))
        actual = set(numbers)
        gaps = expected - actual
        if gaps:
            errors.append(
                f"FIELD NUMBER GAP in {message_name} oneof: "
                f"missing numbers {sorted(gaps)} "
                f"(use `reserved` when removing fields to prevent silent number reuse)"
            )


def extract_repeated_fields_from_message_block(message_block: str) -> set[str]:
    """Extract `repeated` field names from a proto message block.

    R2-H3a: Finds all `repeated <type> <name>` declarations inside the block.
    """
    return set(re.findall(r'repeated\s+\w+\s+(\w+)', message_block))


def check_repeated_vs_json(
    proto_text: str,
    message_name: str,
    schema_path: Path,
    errors: list[str],
) -> None:
    """R2-H3a: Verify that proto `repeated` fields map to JSON `"type": "array"`.

    For each `repeated` field in nested message definitions inside the proto
    message block, check that the corresponding JSON $defs entry has a
    `properties[field]` with `"type": "array"`.
    """
    if not schema_path.exists():
        return

    msg_block = extract_message_block(proto_text, message_name)
    if msg_block is None:
        return

    data = json.loads(schema_path.read_text(encoding="utf-8"))
    defs = data.get("$defs", {})

    # For each sub-message inside the block, check its repeated fields.
    # We look for nested message blocks (e.g. FetchKlinesCommand { repeated string symbols = 1; })
    # by scanning for `repeated` inside the block and cross-referencing JSON defs.
    for sub_msg_match in re.finditer(r'message\s+(\w+)\s*\{', msg_block):
        sub_name = sub_msg_match.group(1)
        # Get the sub-message's block from the full proto text
        sub_block = extract_message_block(proto_text, sub_name)
        if sub_block is None:
            continue
        repeated_fields = extract_repeated_fields_from_message_block(sub_block)
        if not repeated_fields:
            continue

        # Look for the JSON def — try both the sub_name and pascal-cased variants.
        json_def = defs.get(sub_name)
        if json_def is None:
            continue  # Not in JSON schema — skip (may be proto-only)

        json_props = json_def.get("properties", {})
        for field_name in repeated_fields:
            if field_name not in json_props:
                continue  # Not in JSON properties — skip
            field_schema = json_props[field_name]
            if field_schema.get("type") != "array":
                errors.append(
                    f"REPEATED FIELD MISMATCH in {sub_name}.{field_name}: "
                    f"proto says `repeated` but JSON schema type={field_schema.get('type')!r} "
                    f"(expected \"array\")"
                )


def check_oneof_existence_in_json(
    proto_text: str,
    message_name: str,
    schema_path: Path,
) -> None:
    """R2-H3b: NOTE-only check that oneof blocks in proto have corresponding JSON fields.

    Per plan: oneof フィールド順序の厳密確認は G3 後のフォーマット変更次第なので
    「検出のみ、エラーは出さない（NOTE として出力）」とする。
    """
    if not schema_path.exists():
        return

    msg_block = extract_message_block(proto_text, message_name)
    if msg_block is None:
        return

    data = json.loads(schema_path.read_text(encoding="utf-8"))
    defs = data.get("$defs", {})

    # Find oneof block names in the message
    for oneof_match in re.finditer(r'oneof\s+(\w+)\s*\{([^}]*)\}', msg_block, re.DOTALL):
        oneof_name = oneof_match.group(1)
        oneof_body = oneof_match.group(2)
        # Extract field names inside the oneof
        oneof_fields = re.findall(r'\w+\s+(\w+)\s*=\s*\d+', oneof_body)
        missing_in_json = []
        for field in oneof_fields:
            pascal_name = snake_to_pascal(field)
            if pascal_name not in defs and field not in defs:
                missing_in_json.append(field)
        if missing_in_json:
            print(
                f"  NOTE: oneof {oneof_name!r} in {message_name} has fields not in JSON schema "
                f"(proto-only or not yet added): {missing_in_json}"
            )


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

    # ── Field-level checks ────────────────────────────────────────────────────
    check_field_level(proto_text, "Command", errors)
    check_field_level(proto_text, "Event", errors)

    # ── R2-H3a: repeated field ↔ JSON array symmetry ──────────────────────────
    check_repeated_vs_json(proto_text, "Command", COMMANDS_JSON, errors)
    if EVENTS_JSON.exists():
        check_repeated_vs_json(proto_text, "Event", EVENTS_JSON, errors)

    # ── R2-H3b: oneof existence NOTE (no errors, informational only) ──────────
    check_oneof_existence_in_json(proto_text, "Command", COMMANDS_JSON)
    if EVENTS_JSON.exists():
        check_oneof_existence_in_json(proto_text, "Event", EVENTS_JSON)

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
