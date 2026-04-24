# IPC Schema Changelog

Schema versioning follows the policy in [spec.md §4.5.1](../spec.md#451-スキーマバージョニング運用).

- **major** bump: breaking changes (field removal, rename, enum variant removal)
- **minor** bump: backwards-compatible additions (new fields, new variants, new commands)

## v0.1 (2026-04-24) — initial

- First schema definition for Phase 0.
- All command (`op`) and event (`event`) message types defined.
- Timestamps: UNIX ms as `integer`.
- Prices/quantities: `string` to avoid floating-point precision loss.
