# Bug Postmortem — テスト見逃し記録

このファイルは `/bug-postmortem` スキルが自動的に更新する。
新しい見逃しは末尾に追記する。

---

## 見逃しパターン集

| パターン名 | 説明 | 発生回数 |
|-----------|------|---------|
| Mock 置換漏れ | テストがモック実装を使い、実ライブラリの挙動差異を再現できない | 1 |
| 同一言語テスト | Python→Python または Rust→Rust で完結し、言語境界の挙動が未検証 | 1 |
| ログ検査漏れ | smoke.sh の grep パターンが実際の障害ログと不一致 | 1 |
| 再接続隠蔽 | 自動リカバリが成功するため初回失敗が観測ウィンドウに残らない | 1 |

---

## 2026-04-25 — Python websockets デフォルト圧縮が fastwebsockets と非互換

**見逃しパターン**: Mock 置換漏れ / 同一言語テスト / ログ検査漏れ（複合）

**不具合の概要**:
アプリ起動直後に全取引所で "Fetch error: … Data engine restarting. Please retry." が
表示される。原因は `engine ws read error: Reserved bits are not zero`（Rust ログ）。

Python の `websockets.serve()` がデフォルト設定（`compression="deflate"`）で
permessage-deflate 拡張をネゴシエートし、RSV1=1 の圧縮フレームを送信する。
Rust 側の `fastwebsockets` 0.9.0 はこれを拒否して接続を切断 → `EngineRestarting`
エラーが全フェッチに伝播する。

**修正**: `websockets.serve(..., compression=None)` を `server.py` に追加。

**既存テストが見逃した理由**:

| テスト | 見逃した理由 |
|--------|------------|
| `engine-client/tests/handshake.rs` | Mock サーバーに `tokio-tungstenite` を使用。デフォルトで圧縮を有効化しないため、RSV1=1 フレームが発生しない |
| `python/tests/test_server_dispatch.py` | Python `websockets` クライアント→Python `websockets` サーバー。両者が同じ圧縮機能を持つため、圧縮が正常にネゴシエートされてエラーにならない |
| `tests/e2e/smoke.sh` | ① `engine ws read error` を grep していなかった。② `engine handshake complete` の出現回数を数えていなかったため、切断→再接続ループを見逃した |

**追加したテスト**:
- `python/tests/test_server_ws_compat.py::test_server_refuses_permessage_deflate`
  — Python クライアントが圧縮を希望しても、サーバーが拒否することを検証
- `python/tests/test_server_ws_compat.py::test_ping_pong_survives_without_client_compression`
  — `compression=None` クライアントでも Ping/Pong が完走することを検証
- `tests/e2e/smoke.sh` — `engine ws read error` チェックと再接続カウントチェックを追加

**リグレッション確認**: `compression=None` を除去した状態で
`test_server_refuses_permessage_deflate` が FAIL することを実際に確認済み。

**教訓**:

1. **言語境界テストの必要性**: Rust クライアント × Python サーバーの組み合わせは、
   同一言語でのテストでは再現できない挙動差異を持つ。
   `fastwebsockets` のような薄いクライアントを使う場合は、実際の Python サーバーと
   組み合わせたテストが必要。

2. **ライブラリデフォルト値の危険性**: 外部ライブラリのデフォルト設定（今回は
   `compression="deflate"`）が将来変更される可能性を考慮し、
   明示的に `compression=None` のような設定を assert するテストを書く。

3. **smoke.sh の盲点チェックリスト**:
   - エラーパターンを grep しているか
   - 再接続ループを「接続成功回数」で検出しているか
   - 自動リカバリが成功した場合でも初回エラーが残るか

4. **Mock を使うテストの補完**: Mock ベースのテストは fast だが言語境界は確認できない。
   統合テスト（実サーバー起動）で補完する設計を標準とする。

---

## 2026-04-25: 立花 `_do_validate` のパラメータ名誤り (sIssueCode → sTargetIssueCode)

**症状**: T3 で `validate_session_on_startup` を実機 demo 環境に向けて呼び出したところ、
立花 API が `code=-1, message=（sTargetIssueCode:[NULL]）エラー` を返却。session validation
が常に失敗し、起動時の自動 keyring 復元が動作しない。

**根本原因**: T2 で `_do_validate` の `CLMMfdsGetIssueDetail` リクエスト payload を
`{"sIssueCode": "7203", "sSizyouC": "00"}` で組み立てていたが、マニュアル
`mfds_json_api_ref_text.html#CLMMfdsGetIssueDetail` の正しいパラメータ名は
`sTargetIssueCode`（カンマ区切り銘柄コードリスト、`sSizyouC` は不要）。

**修正**: `python/engine/exchanges/tachibana_auth.py::_do_validate` を
`{"sTargetIssueCode": "7203"}` に修正し、HIGH-D2 pinned テスト
(`test_validate_session_uses_get_issue_detail_with_pinned_payload`) を更新。

**なぜ既存テストで発見できなかったか**:
* HIGH-D2 pinned テストは **私たちが書いた誤った payload を pin していた** —
  サーバー応答を `httpx_mock` で固定していたため、誤ったパラメータ名でも
  「`sCLMID` / `sIssueCode` / `sSizyouC` / `sJsonOfmt` の 4 点が揃っている」を
  assert してしまっていた。**実際の API がそのパラメータを受け付けるかは
  検証していない**（同一言語テスト・Mock の補完不足）。
* T2 受け入れ条件 `pytest -m demo_tachibana` は「実 demo 環境ログイン」を
  指定していたが、電話認証済アカウント前提の手動レーンに置かれており
  CI で実走しなかった。Phase 2 (B) 案（manual lane only）を採用していたため。

**教訓**:

1. **Mock 応答を pin したテストは「クライアント側の payload 構築」しか検証しない**:
   サーバーが本当にそのパラメータを認識するかは別レイヤーのテストで補完する必要がある。
   公式マニュアルのサンプル例（`mfds_json_api_ref_text.html` の `<td>` 内 JSON 例）と
   照合する snapshot テストを追加することで誤りを早期検知できる。

2. **手動 demo レーンの CI 統合タイミングを早める**: T2 段階で実機 smoke を
   走らせていれば即発見できた。`scripts/smoke_tachibana_login.py` のような
   一発実行可能なスクリプトを T1/T2 段階から保守する習慣をつける。

3. **公式マニュアルの sample 例と pinned test は二重に揃える**: マニュアル
   側の sample 例セクション (`<td>{ "sCLMID":"...", "sTargetIssueCode":"..." }</td>`)
   からパラメータ名を抽出して pinned test の expected と比較する static 検査を
   T7 lint phase に追加検討（現状 `tools/secret_scan*` 系と同列で実装可能）。
