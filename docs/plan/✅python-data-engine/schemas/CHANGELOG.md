# IPC Schema Changelog

Schema versioning follows the policy in [spec.md §4.5.1](../spec.md#451-スキーマバージョニング運用).

- **major** bump: breaking changes (field removal, rename, enum variant removal)
- **minor** bump: backwards-compatible additions (new fields, new variants, new commands)

## v1.1 (2026-04-25) — TradesFetched documented (minor)

- **minor bump 1.0→1.1**: added `TradesFetched` event definition with `is_last: bool`
  to `events.json` and pydantic `schemas.py`. The wire format was already chunked in
  Phase 4 (server.py / engine-client), but the JSON Schema and pydantic model lagged
  behind; this commit reconciles them. Backwards compatible (field was already sent
  and Rust side defaults missing `is_last` to `true`).

## v1.0 (2026-04-24) — Phase 2 baseline (breaking)

- **major bump 0→1**: Rust `engine-client` crate shipped with `SCHEMA_MAJOR = 1`.
  Python `schemas.py` updated to match (`SCHEMA_MAJOR = 1, SCHEMA_MINOR = 0`).
  The v0.1 schema was development-only and never reached a stable release, so bumping
  major is cleaner than accumulating minor deltas on an unreleased version.
- No message shape changes; bump reflects the first real handshake-enforced version.

## v0.1 (2026-04-24) — initial

- First schema definition for Phase 0.
- All command (`op`) and event (`event`) message types defined.
- Timestamps: UNIX ms as `integer`.
- Prices/quantities: `string` to avoid floating-point precision loss.
