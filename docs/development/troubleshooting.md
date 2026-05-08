---
title: 開発時トラブルシューティング
status: draft
authored: 2026-05-08
sources:
  - AGENTS.md
  - engine-client/src/process.rs
  - engine-client/src/connection.rs
  - python/engine/server_grpc.py
  - scripts/check_schema_parity.py
---

# 開発時トラブルシューティング

エンドユーザー向け runbook ではなく **開発時のハマりどころ** を集約する。

## ログの位置

ビルド種別で異なる:

- **release** (`cargo build --release`): `~/AppData/Roaming/flowsurface/flowsurface-current.log`（Windows）/ プラットフォーム別 data dir
- **debug** (`cargo build`): ターミナル stdout

## 起動ハンドシェイクが失敗する

| 症状 | 原因候補 | 確認 |
|---|---|---|
| `schema_major mismatch` で接続拒否 | Rust と Python のスキーマバージョン不一致 | `scripts/check_schema_parity.py` を実行 |
| `Ready` が来ず 15s タイムアウト | Python engine の起動失敗・別ポート占有 | engine ログ（または `--port` 指定で手動起動して stdout 確認）|
| `EngineBusy` が連発 | 複数クライアント同時接続の FCFS 制御 | `python/engine/server.py` の multi-client broadcast 仕様を確認 |
| `engine_session_id` 切替で板が消える | Python 再起動の正常検知 | 仕様どおり。`DepthSnapshot` を再要求すれば復帰 |

## 不具合修正フロー（AGENTS.md より）

1. 仮説を 2〜8 個立て、検証ログをコードに追加
2. ログを確認して原因特定
3. 修正後、追加ログをすべて削除
4. **必ず `/bug-postmortem` スキルを実行** — 既存テストで発見できなかった理由を分析、テスト追加判断、修正前 FAIL / 修正後 PASS を確認
5. 過去の見逃しパターン（Mock 置換漏れ・同一言語テスト・ログ検査漏れ・再接続隠蔽など）と照合

## silent failure の禁止

過去のレビュー（`docs/decisions/00xx-review-fixes-*.md` 群）で繰り返し指摘された
パターン:

- handshake タイムアウトのエラーを握りつぶす → 15s timeout + sticky error 必須
- `EngineBusy` を全クライアントに broadcast してしまう → `request_id` フィルタで unicast
- 全断時に内部 state がリセットされない → 接続断時に明示リセット
- `EngineStopped` が 1 度しか送られない → 補完 + 二重送出ガード

修正コードは必ず「失敗経路で何が表示されるか」を E2E / integration テストで assert する。

## IPC スキーマ変更時

1. `engine-client/src/lib.rs` の `SCHEMA_MINOR` を上げ、履歴コメントに 1 行追記
2. `python/engine/schemas.py` を同期（major / minor 両方）
3. `engine-client/src/dto.rs` と `proto/engine.proto` を更新
4. `scripts/check_schema_parity.py` で 3 者一致を確認
5. `docs/roadmap/changelog.md` に年表追記

詳細は [../architecture/ipc-schema.md](../architecture/ipc-schema.md)。
