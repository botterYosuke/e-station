# 未決事項 / 要相談

実装着手前にユーザーと合意したい論点。

## A. プロセス・配布

1. **Python ランタイムの配布形態**
   - (a) PyInstaller 等で同梱バイナリ化（ユーザーは Python 不要、配布物は数十 MB 増）
   - (b) ユーザーに Python 3.x のインストールを要求し `uv` で依存管理
   - (c) ハイブリッド（dev は b、リリースは a）

2. **Python プロセスのライフサイクル**
   - Rust が常に spawn / 監視 / kill するか、ユーザーが別途起動するスタンドアロン運用も公式サポートするか。

## B. IPC

3. **トランスポート選定**
   - 推奨は WebSocket + JSON（既存資産流用しやすい）。
   - 高頻度 depth で詰まる場合は MessagePack か Unix Domain Socket / Named Pipe へ移行。最初から後者にするか？

4. **エンコーディング**
   - JSON で開始するか、最初から MessagePack / FlatBuffers にするか。

## C. 機能スコープ

5. **WS 直結のオプション残置（計画射程に直結、フェーズ 2 完了時に最終確定）**
   - フェーズ 5 で完全に Rust 側取引所コードを消す前提だったが、[spec.md §7.1](./spec.md#71-rust-直結モードの長期方針要決定) で案 A（撤去） / 案 B（恒久残置） / 案 C（optional feature）を列挙。
   - 暫定は **案 A**。フェーズ 2 のレイテンシ計測結果で最終確定する。
   - **Phase 0.5 で `VenueBackend` trait を実装済み**。trait は `NativeBackend`（既存 Rust 直結）と将来の `EngineClientBackend`（Python IPC）の両方を実装できる設計になっており、案 A でも C でも対応可能。最終決定はフェーズ 2 計測後。

6. **マルチプロセス構成**
   - フェーズ 1 は asyncio 単一プロセスで確定（[spec.md §6.1](./spec.md#61-プロセスモデルフェーズ-1-時点)）。
   - 将来分割できるよう `ExchangeWorker` 抽象を先に入れる方針。GIL / CPU がボトルネックと判明した時点で分割。
   - **残論点**: 分割時に使うのが `multiprocessing` か subprocess + IPC か。フェーズ 3 以降で決定でも可。

## D. 開発フロー

7. **言語境界のスキーマ管理（最優先で合意したい）**
   - 本計画の中心論点。手書きで Rust / Python 両側に型を書くとドリフトしやすいため、**実装着手前に決める**。
   - 候補:
     - (a) JSON Schema を single source of truth にし、`quicktype` で Rust / Python 両方を生成。
     - (b) Rust 側 `serde` 定義 + `schemars` で JSON Schema をエクスポート → Python は `datamodel-code-generator` で pydantic を生成。
     - (c) `.proto` で定義して `prost` + `betterproto` を使う（将来の gRPC 切替にも繋がる）。
   - 決定後、[spec.md §4.3](./spec.md#43-メッセージスキーマ) と [implementation-plan.md](./implementation-plan.md) フェーズ 0 の生成手順に反映する。

8. **テスト戦略**
   - 取引所 API の VCR / モック方針。Live テストはどこまで CI に載せるか。

## E. 品質・運用

9. **障害時の UX**
   - Python 落ち = チャート無表示。リトライ中の UI 表現（バナー、ステータスインジケータ）。

10. **既存ユーザーの移行**
    - 既存リリースから引き継ぐ設定 (`config.json`, レイアウト) は維持予定だが、互換性を破る変更が出た場合のマイグレーションをどこまで自動化するか。

## F. 雑多な確認

11. **keyring の他用途**
    - プロキシ資格情報以外に `keyring` crate を使っている機能が無いか。無ければフェーズ 5 で依存削除候補、ある場合は Rust 側に残す。着手前に要 grep 確認。

12. **E2E テスト自動化の運用方針** (Phase 7 T3 で発生)
    - 現状は [`tests/e2e/smoke.sh`](../../../tests/e2e/smoke.sh) を手動 / CI step で実行する素朴な bash スクリプト。手動 GUI シナリオ（チャート描画・kill -9 復旧・ストリーム持続）はまだ人手。
    - 選択肢:
      - (a) bash + ログ grep のまま育てる（現状）。CI 統合は GitHub Actions 上で `cargo build --release && bash tests/e2e/smoke.sh` を流すだけ。venue API への live 依存があるためスケジュール run 限定。
      - (b) Rust 側に `cargo xtask e2e` を新設し、ProcessManager + EngineConnection を直接駆動して assertion を Rust で書く。GUI に触れない範囲で十分なカバレッジが取れる。
      - (c) `.claude/skills/agent-experience-verification` のように HTTP API を flowsurface に追加してエージェント駆動 E2E を可能にする。GUI シナリオまでカバーできるが、HTTP API のメンテコストが発生。
    - 決定タイミング: Phase 8 着手前。短期的には (a) を継続。
    - 影響箇所: [`tests/e2e/`](../../../tests/e2e/), [`phase-7-ui-regression-remediation.md`](./phase-7-ui-regression-remediation.md) の T3 セクション。
