# IPC Schema Changelog

Schema versioning follows the policy in [spec.md §4.5.1](../spec.md#451-スキーマバージョニング運用).

- **major** bump: breaking changes (field removal, rename, enum variant removal)
- **minor** bump: backwards-compatible additions (new fields, new variants, new commands)

## v1.2 (2026-04-25) — Tachibana venue scaffolding (minor)

- **minor bump 1.1→1.2**: added Tachibana 立花証券 venue support at the IPC
  level. Backwards compatible: every new shape is additive.
  - **New commands**:
    - `SetVenueCredentials { request_id, payload: VenueCredentialsPayload }` —
      tagged enum (today only `tachibana`) carrying user_id / password /
      optional second_password / `is_demo` / optional virtual-URL session.
      Sent at startup (keyring restore) and after managed-mode restart.
    - `RequestVenueLogin { request_id, venue }` — Rust UI asks the engine
      to drive the venue-specific login flow (Tachibana spawns a tkinter
      helper subprocess).
  - **New events**:
    - `VenueReady { venue, request_id? }` — idempotent; emitted after every
      successful `SetVenueCredentials`. Resubscribe is ProcessManager-owned;
      consumers must not re-subscribe on receipt (architecture.md §3, F8).
    - `VenueError { venue, request_id?, code, message }` — `code` enum
      includes `session_expired` / `unread_notices` / `phone_auth_required` /
      `login_failed` / `ticker_not_found` / `transport_error`. `message` is
      Python-authored user-facing text (F-Banner1) — the Rust UI renders it
      verbatim.
    - `VenueCredentialsRefreshed { venue, session }` — startup re-login
      produced fresh virtual URLs; Rust uses this to update keyring.
    - `VenueLoginStarted { venue, request_id? }` — tkinter helper spawned.
    - `VenueLoginCancelled { venue, request_id? }` — user closed the dialog.
  - **`request_id` shape**: pinned via the new `$defs/RequestId`
    (`pattern`: lowercase hyphenated UUIDv4, `maxLength`: 36) per LOW-1 /
    F-L7. Applied to `SetVenueCredentials` / `RequestVenueLogin` and to
    `Venue*` events' nullable `request_id` fields.
  - **`Disconnected.reason` convention**: `"market_closed"` is reserved for
    Tachibana JST session boundaries; not a new field.
  - **Capabilities path**: `Ready.capabilities.venue_capabilities[<venue>]`
    is left untyped on the Rust side (F-M8) — Python is the schema source
    of truth. Rust-side reads go through `engine_client::capabilities::
    venue_capability` which returns `Result<Option<T>, _>` so missing /
    malformed entries cannot silently default to `false`.

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
