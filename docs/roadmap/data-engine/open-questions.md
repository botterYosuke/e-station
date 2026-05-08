---
title: "data-engine 未決事項 / 要相談"
status: migrated
migrated_from:
  - docs/✅python-data-engine/open-questions.md
source_commit: 9f8b91b
---

# 未決事項 / 要相談

実装着手前にユーザーと合意したい論点。

## A. プロセス・配布

1. **Python ランタイムの配布形態**
   - (a) PyInstaller 等で同梱バイナリ化（ユーザーは Python 不要、配布物は数十 MB 増）
   - (b) ユーザーに Python 3.x のインストールを要求し `uv` で依存管理
   - (c) ハイブリッド（dev は b、リリースは a）

2. ~~**Python プロセスのライフサイクル**~~
   - **決定済み（Phase 8 着手前, 2026-05-01）**: helper は `attach mode` / `in-process mode` を自動判定し、GUI 起動中は attach、engine 不在時は helper 内で `NautilusRunner` を直接起動する。詳細は python-helper-direct-api.md §0.1。

## B. IPC

3. **トランスポート選定**
   - 推奨は WebSocket + JSON（既存資産流用しやすい）。
   - 高頻度 depth で詰まる場合は MessagePack か Unix Domain Socket / Named Pipe へ移行。最初から後者にするか？

4. **エンコーディング**
   - JSON で開始するか、最初から MessagePack / FlatBuffers にするか。

## C. 機能スコープ

5. ~~**WS 直結のオプション残置**~~
   - **決定済み・実施済み（Phase 5 完了, 2026-04-25）**: 案 A（撤去）を採用。`exchange/src/adapter/hub/` を全削除し、`reqwest` / `fastwebsockets` / `tokio-rustls` / `tokio-socks` / `sonic-rs` 等の native 依存も `exchange/Cargo.toml` から除去済み。
   - `--data-engine-url` フラグは Phase 5 で必須化、Phase 6 で `--engine-cmd` によるオーバーライドへ進化。Phase 8 で固定ポート 19876 自動 attach + spawn フォールバックに到達（[spec.md §3.1](../../specs/data-engine.md#31-起動フロー外部エンジン自動-attach--spawn-フォールバック)）。
   - 案 C（optional feature）の復活余地は理論上残るが、現時点で要望なし。

6. **マルチプロセス構成**
   - フェーズ 1 は asyncio 単一プロセスで確定（[spec.md §6.1](../../specs/data-engine.md#61-プロセスモデルフェーズ-1-時点)）。
   - 将来分割できるよう `ExchangeWorker` 抽象を先に入れる方針。GIL / CPU がボトルネックと判明した時点で分割。
   - **残論点**: 分割時に使うのが `multiprocessing` か subprocess + IPC か。フェーズ 3 以降で決定でも可。

## D. 開発フロー

7. **言語境界のスキーマ管理（最優先で合意したい）**
   - 本計画の中心論点。手書きで Rust / Python 両側に型を書くとドリフトしやすいため、**実装着手前に決める**。
   - 候補:
     - (a) JSON Schema を single source of truth にし、`quicktype` で Rust / Python 両方を生成。
     - (b) Rust 側 `serde` 定義 + `schemars` で JSON Schema をエクスポート → Python は `datamodel-code-generator` で pydantic を生成。
     - (c) `.proto` で定義して `prost` + `betterproto` を使う（将来の gRPC 切替にも繋がる）。
   - 決定後、[spec.md §4.3](../../specs/data-engine.md#43-メッセージスキーマ) と [implementation-plan.md](./implementation-plan.md) フェーズ 0 の生成手順に反映する。

8. **テスト戦略**
   - 取引所 API の VCR / モック方針。Live テストはどこまで CI に載せるか。

## E. 品質・運用

9. **障害時の UX**
   - Python 落ち = チャート無表示。リトライ中の UI 表現（バナー、ステータスインジケータ）。

10. **既存ユーザーの移行**
    - 既存リリースから引き継ぐ設定 (`config.json`, レイアウト) は維持予定だが、互換性を破る変更が出た場合のマイグレーションをどこまで自動化するか。

## F. 雑多な確認

11. ~~**keyring の他用途**~~
    - **決定済み・実施済み（Phase 5 完了, 2026-04-25）**: プロキシ資格情報の Rust 側保持のみで利用継続。Phase 5 では `exchange/` 配下から `keyring` 依存を除去するスコープではなく、`src/` 側で `Proxy` 設定を保持するパターンを維持した。Python 側は `SetProxy` IPC 経由で受け渡し（spec.md §5.4）。

12. ~~**E2E テスト自動化の運用方針**~~ (Phase 7 T3 で発生)
    - **決定済み・実施済み（Phase 8.2 完了, 2026-05-03）**: HTTP 依存の bash E2E（s56〜s83, s90, tachibana_* 11 ファイル）を削除。`smoke.sh` のみ起動監視用として維持。
    - `scripts/replay_dev_load.sh` 削除済み。`scripts/run-replay-debug.sh` は DEPRECATED コメントを追記済み（HTTP API ポート 9876 依存のため機能しない）。
    - 新たな E2E は `pytest + python -m engine.replay_session run` で代替。詳細は python-helper-direct-api.md。

13. ~~**Phase 8 helper API 設計の未決 Q (Q2/Q3b/Q8/Q10/Q11)**~~
    - **すべて決定済み・実装済み（Phase 8 完了, 2026-05-03）**:
      - Q2 (Python プロセス LCM): attach mode / in-process mode を自動判定して解決済み
      - Q3b (GUI フォームのデフォルト値記憶): Phase 8.1c でフォーム実装、記憶方針は前回値保持なし（シンプル設計）
      - Q8 (session ファイルパス): `data::data_path("engine-session.json")` を Rust が書き、Python が `platformdirs` で同パスを解決。実装済み（`engine-client/src/session_file.rs`, `python/engine/replay_session.py`）
      - Q10 (state guard 範囲): `ReplayState`/`LiveState` 2 つの直交 state machine で実装済み
      - Q11 (session ファイル書き込み判断): 常に書く（handshake 成立後に atomic write）。実装済み
