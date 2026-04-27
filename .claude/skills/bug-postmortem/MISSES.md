# Bug Postmortem — テスト見逃し記録

このファイルは `/bug-postmortem` スキルが自動的に更新する。
新しい見逃しは末尾に追記する。

---

## 見逃しパターン集

| パターン名 | 説明 | 発生回数 |
|-----------|------|---------|
| Mock 置換漏れ | テストがモック実装を使い、実ライブラリの挙動差異を再現できない | 2 |
| 同一言語テスト | Python→Python または Rust→Rust で完結し、言語境界の挙動が未検証 | 1 |
| ログ検査漏れ | smoke.sh の grep パターンが実際の障害ログと不一致 | 1 |
| 再接続隠蔽 | 自動リカバリが成功するため初回失敗が観測ウィンドウに残らない | 1 |
| 冪等性未検証 | 「変更なし」の入力パスがテストされておらず、副作用の有無が見逃される | 1 |
| 共有状態の独立構築 | 同じ論理状態を複数オブジェクトが「デフォルト引数で各自構築」してしまい、`is` 同一性が崩れて値の重複・順序逆転が起こる | 1 |
| API 仕様固定なし | Mock レスポンスが誤ったキー名・フィールド名を前提に書かれており、実 API の必須パラメータ欠落・レスポンス形式ズレを素通りする | 1 |
| saved-state migration 未実装 | enum バリアント追加時に、追加前の saved-state を読んだ場合の migration テストがなく、旧フォーマットでの起動時のみ再現するバグを見逃す | 1 |
| IPC 契約の片側未実装 | Rust が特殊パス（`"__all__"` 等）を送る契約が `backend.rs` に書かれているが、Python 側の対応実装とテストが追いついていない | 1 |

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

---

## 2026-04-27 — SetProxy(None) が起動時フェッチを全キャンセル

**見逃しパターン**: 冪等性未検証

**根本原因**:
`_handle_set_proxy()` はプロキシ URL が変わっていなくても常に `_cancel_all_streams()` を呼んでいた。
`Message::EngineConnected` ハンドラが接続のたびに `SetProxy(None)` を送信する（プロキシ未設定でも）。
`TickersTable::new()` で起動時に生成されたフェッチタスクが Python 側で実行中に `SetProxy(None)` が届くと全タスクがキャンセルされ、
全取引所に `cancelled: request interrupted (proxy change or disconnect)` エラーが表示された。

既存テスト `test_server_proxy.py` は「プロキシ URL が変わる」ケース（None → "http://..."）しかカバーしておらず、
「URL が変わらない」（None → None）ケースの副作用が検証されていなかった。

**追加したテスト**:
- `python/tests/test_server_proxy.py::test_set_proxy_none_when_already_none_does_not_cancel_streams`
- `python/tests/test_server_proxy.py::test_set_proxy_same_url_twice_does_not_double_restart`

**修正内容**:
- `DataEngineServer.__init__` に `self._proxy_url: str | None = None` を追加
- `_handle_set_proxy` 冒頭に `if proxy_url == self._proxy_url: return` を追加

**教訓**:
状態を更新するハンドラには必ず「変更なし」の入力パスをテストする。

---

## 2026-04-27 — CLMMfdsGetMarketPrice に sTargetColumn 必須パラメータ欠落（4 バグ複合）

**見逻しパターン**: API 仕様固定なし / Mock の補完不足（複合）

**症状**: 立花ログイン後の `fetch_ticker_stats` / `fetch_depth_snapshot` で
`Fetch error: Tachibana: -1: 引数（sTargetColumn:[NULL]）エラー。` が UI に表示される。

**根本原因（4 バグ複合）**:

| # | バグ | 影響 |
|---|------|------|
| 1 | `sTargetColumn` がリクエストペイロードから欠落 | 直接の `-1` エラー原因 |
| 2 | スキーマキー `aCLMMfdsMarketPriceData` → 正しくは `aCLMMfdsMarketPrice` | エラー解消後にデータが空になる |
| 3 | `fetch_ticker_stats` フィールド名 `sCurrentPrice` 等 → 正しくは `pDPP` 等 FD コード | 値がすべて空文字列になる |
| 4 | `fetch_depth_snapshot` フィールド名 `sGBP_{i}` 等 → 正しくは `pGBP1`..`pGBP10` | bid/ask が空になる |

`CLMMfdsGetMarketPrice` の `sTargetColumn` は「取得したい FD 情報コードをカンマ区切りで列挙する」
**必須パラメータ**（マニュアル § CLMMfdsGetMarketPrice / サンプル e_api_sample_v4r8.py L342）。
レスポンスのキー名（`aCLMMfdsMarketPrice`）と各アイテムのフィールド名（`pDPP` 等 FD コード）は、
`sTargetColumn` で指定した情報コードと 1 対 1 で対応する。

**なぜ既存テストが見逃したか**:

既存 `test_fetch_ticker_stats_returns_dict` の Mock が
`"aCLMMfdsMarketPriceData": [..., "sCurrentPrice": "2880" ...]`
という**誤ったレスポンスを前提**に固定されており、実 API の必須パラメータ検証も
正しいフィールド名検証も行っていなかった。Mock テストは「クライアント側のペイロード組立」を
検証せず、リクエスト URL の中身を一切 assert しない構造だった。

**修正**:
- `fetch_ticker_stats` に `"sTargetColumn": "pDPP,pDOP,pDHP,pDLP,pDV,tDPP:T"` を追加
- `fetch_depth_snapshot` に `"sTargetColumn"` を気配 10 本分のコードで追加
- `MarketPriceResponse.aCLMMfdsMarketPriceData` → `aCLMMfdsMarketPrice` に修正
- 両関数のレスポンスパースを FD コード (`pDPP` 等) に修正

**追加したテスト**:
- `python/tests/test_tachibana_market_price_payload.py::test_fetch_ticker_stats_includes_sTargetColumn_in_request`
  — URL に `sTargetColumn` が含まれることを assert
- `python/tests/test_tachibana_market_price_payload.py::test_fetch_ticker_stats_sTargetColumn_contains_required_fd_codes`
  — `pDPP,pDOP,pDHP,pDLP,pDV` の 5 コードが含まれることを assert
- `python/tests/test_tachibana_market_price_payload.py::test_fetch_ticker_stats_parses_fd_code_fields_correctly`
  — FD コードフィールドが `last_price`/`open`/`high`/`low`/`volume` に正しく写像されることを assert
- `python/tests/test_tachibana_market_price_payload.py::test_fetch_depth_snapshot_includes_sTargetColumn_in_request`
  — depth snapshot リクエストにも `sTargetColumn` が含まれることを assert
- `python/tests/test_tachibana_market_price_payload.py::test_fetch_depth_snapshot_parses_fd_code_bid_ask_fields`
  — `pGBP1`/`pGAP1` 等の FD コードから bid/ask が正しく取得されることを assert

**リグレッション確認**: `sTargetColumn` 削除 + 旧キー名に戻した状態で 5 テストが FAIL することを実証済み。

**教訓**:

1. **リクエスト URL を assert するテストが必要**: `_http_get` を Mock するとき、
   引数 URL の内容（特に必須パラメータの有無）を assert しないと、欠落を見逃す。
   「どんな URL で呼ばれたか」を `captured_urls` で検査するパターンを標準化する。

2. **Mock レスポンスのキー名は公式サンプルで照合する**: Mock レスポンスを書くとき、
   フィールド名・配列キー名を公式サンプルコード（`e_api_*.py`）またはマニュアルの応答例と
   照合する。「書いた人が想像した名前」と「実 API の名前」が乖離した場合、
   Mock テストはパスし実機はエラーになる。

3. **API 必須パラメータのリグレッションガード**: 実 API が `-1` 等のエラーを返す原因となる
   必須パラメータ（`sTargetColumn` 等）は、「URL に含まれること」を assert するテストを書く。
   特にパラメータを**動的に組み立てる**コード（FD コードのカンマ区切り列挙）では、
   コードリファクタで欠落するリスクがある。
特に「コネクション復旧時に毎回送られるコマンド」（SetProxy, SetVenueCredentials 等）は
起動・再接続シーケンスとの競合を想定した冪等性テストを書く習慣をつける。

---

## 2026-04-27 — TachibanaWorker と server で PNoCounter が独立構築され p_no 衝突

**見逃しパターン**: 共有状態の独立構築 / デフォルト値前提 / タイミング依存（複合）

**症状**: 立花ログイン後の最初のフェッチで UI に `Fetch error: Tachibana: 6:
引数（p_no:[X] <= 前要求p_no:[X]）エラー。` が表示される。両 p_no が完全に同一値。

**根本原因**:
[server.py](python/engine/server.py) で `TachibanaWorker(...)` を構築する際、
`p_no_counter=` 引数を渡していなかった。`TachibanaWorker.__init__` の
`self._p_no_counter = p_no_counter or PNoCounter()` フォールバックにより
worker は独自の `PNoCounter` を生成。

`PNoCounter.__init__` は `self._value = int(time.time())` で初期化されるため、
`server._tachibana_p_no_counter` と `worker._p_no_counter` が同じ Unix 秒で
構築されると、両者の `next()` は完全に同一の値列を返す。

呼び出し経路：
- `validate_session_on_startup` → server カウンター → `next()` = T+1
- `_ensure_master_loaded` / `fetch_klines` / `fetch_ticker_stats` → worker カウンター → `next()` = T+1（**衝突**）

立花 API の R4 単調増加 invariant 違反として error 6 で拒否される。

**修正**:
- `TachibanaWorker(...)` に `p_no_counter=self._tachibana_p_no_counter` を渡す
- `self._tachibana_p_no_counter = PNoCounter()` の構築を `self._workers` 辞書より前に移動
- 旧位置にはポインタコメントを残してリファクタリング時の事故を防止

**既存テストが見逃した理由**:

| テスト | 見逃した理由 |
|--------|------------|
| `test_tachibana_apply_session_sync.py` | `_apply_tachibana_session` の同期だけを検証し、p_no カウンターの共有は範囲外 |
| `test_server_dispatch.py` 等 | フェッチ系の単体ユニットでは server と worker のカウンターが衝突するシナリオを再現していない |
| Tachibana ワーカー単体テスト | テスト用に独自の `PNoCounter` を渡すため、本番の「2 つ作ってしまう」構造を踏まない |

**追加したテスト**:
- `python/tests/test_tachibana_p_no_counter_shared.py::test_server_and_tachibana_worker_share_same_p_no_counter`
  — `worker._p_no_counter is server._tachibana_p_no_counter` を `is` 比較で検証
- `python/tests/test_tachibana_p_no_counter_shared.py::test_shared_counter_produces_monotonic_p_no_across_server_and_worker`
  — 交互 `next()` で重複・逆転がないことを検証（実値ベースの fail-safe）

**リグレッション確認**: `p_no_counter=` 引数を削除した状態で両テストが FAIL することを実証
（実際の重複値 `1777276609, 1777276609, ...` を観測）。引数を戻すと PASS。

**教訓**:

1. **`is` 同一性アサーション**: 同じ論理状態を複数のコンポーネントが共有すべきとき、
   値比較ではなくインスタンス同一性 (`is`) を assert する。値比較は「同じ初期化ロジックで
   揃ってしまう」ケースを通してしまう（今回の `int(time.time())` 同秒衝突）。

2. **デフォルト引数フォールバックは諸刃の剣**: `p_no_counter or PNoCounter()` のような
   「無ければ自前で作る」フォールバックは API の柔軟性を上げる一方、呼び出し側が
   引数を**渡し忘れる**と silent に独立インスタンスが生まれる。共有が必須な場合は
   フォールバックを除去するか、必須引数化する設計を検討する。

3. **構築順序のリグレッションガード**: 「A の前に B を構築する必要がある」という
   制約は、A の定義位置にコメントを残すだけでなく、テストで `is` 同一性を assert すれば
   将来の並び替えリファクタで自動的に検出される。

4. **タイミング依存バグの再現**: `int(time.time())` の解像度（1 秒）依存のバグは、
   サーバー起動が高速なテスト環境では確実に再現する一方、本番ではプロセス起動の
   タイミング次第で「たまに通る」ように見える。値ベースのテストでは検出困難なため、
   構造的不変条件 (`is` 同一性) を直接 assert する。

---

## 2026-04-27 — 立花銘柄一覧がサイドバーに表示されない（saved-state migration 未実装 + IPC 契約片側未実装の複合）

**見逃しパターン**: saved-state migration 未実装 / IPC 契約の片側未実装（複合）

**不具合の概要**:
アプリ起動後、立花銘柄がサイドバーに一切表示されない。`VenueReady` は正常受信。
2 つの独立したバグが重なっていた。

**Bug 1: `MarketKind::Stock` が saved-state の `selected_markets` に含まれない**

`MarketKind::Stock` は立花連携と同時に追加された新バリアント。それ以前に保存した
`saved-state.json` には `["Spot","InversePerps","LinearPerps"]` しかなく、
デシリアライズ時に自動補完（migration）が走らないため、全立花銘柄が
`matches_market = false` でフィルタされて表示ゼロになる。

`Settings::default()` は `MarketKind::ALL`（Stock 含む）を使うため、fresh な
saved-state または default 起動では再現しない。**既存 saved-state がある環境でのみ発生**する。

**Bug 2: `TachibanaWorker.fetch_ticker_stats("__all__")` 未実装**

Rust の `fetch_ticker_stats_task`（`engine-client/src/backend.rs`）は全銘柄 stats を
一括取得するため `ticker = "__all__"` で Python に送る。Python の `TachibanaWorker`
には `__all__` 分岐がなく、`"__all__"` を銘柄コードとして API に渡して API エラー。
`ticker_rows` に stats がなければ行が追加されないため、表示ゼロになる。

**なぜ既存テストで発見できなかったか**:

| テスト | 見逃した理由 |
|--------|------------|
| `filtered_rows` テスト | `selected_markets` を `default()`（全種含む）で固定しており、旧フォーマット移行シナリオがない |
| `push_ticker_row_for_test` ヘルパー | 直接 rows を操作するため stats fetch パスを通らない |
| `test_server_dispatch.py` | `FetchTickerStats` を `"__all__"` で呼ぶケースが未カバー |
| 手動実機テスト | fresh な saved-state では `Stock` が含まれるため再現しない |

**修正**:
- `Settings::migrate()` を追加し `new_with_settings` で呼び出す（旧 saved-state を自動補完）
- `TachibanaWorker.fetch_ticker_stats` に `"__all__"` 分岐を追加（マスタからゼロ値プレースホルダーを生成）

**追加したテスト**:
- `data/src/tickers_table.rs::tests::migrate_adds_stock_to_legacy_selected_markets` — 旧フォーマットで migrate 後に Stock が追加されること
- `data/src/tickers_table.rs::tests::migrate_is_idempotent` — 2 回呼んでも重複しないこと
- `data/src/tickers_table.rs::tests::migrate_covers_all_market_kinds` — 空リストから全 MarketKind::ALL が入ること
- `python/tests/test_tachibana_fetch_all_stats.py::test_fetch_ticker_stats_all_returns_bulk_placeholders`
- `python/tests/test_tachibana_fetch_all_stats.py::test_fetch_ticker_stats_all_returns_empty_when_master_empty`
- `python/tests/test_tachibana_fetch_all_stats.py::test_fetch_ticker_stats_all_skips_empty_issue_code`

**教訓**:

1. **saved-state migration テストの必須化**: `Settings` / `State` 型の `#[derive(Deserialize)]` に新フィールドや新 enum バリアントを追加した場合、「旧フォーマット（そのフィールドが存在しない）でデシリアライズ → migration 後に新バリアントが含まれる」ことを assert するテストをセットで書く。`default()` を使う既存テストではこのシナリオを踏まない。

2. **IPC 契約テスト**: Rust `backend.rs` にある「Python に送る特殊パス」（`"__all__"` 等）は、Python 側の実装と 1:1 で対応するテストを書く。Rust 側の `if ticker == "__all__"` という分岐を追加したタイミングで、Python 側に「`fetch_ticker_stats("__all__", ...)` が dict を返すこと」のテストを追加することをルールにする。

3. **「fresh 環境でのみ確認」の罠**: migration 系バグは開発者の環境が常に最新（fresh）であるため手動テストで発見できない。「旧フォーマット saved-state でのみ再現」するバグは自動テストでしか網羅できない。

---

## 2026-04-27 — `_startup_tachibana` 未モックで VenueReady/VenueError が Pong より先に届くテスト失敗

**見逃しパターン**: Mock 置換漏れ（部分モック — Worker クラスはモックしたが background task は実装が走った）

**症状**:
`test_set_proxy_with_no_streams_does_not_crash` が
`AssertionError: Expected Pong, got: {'event': 'VenueReady', 'venue': 'tachibana', ...}` で失敗。
他 3 テスト（`test_set_proxy_cancels_inflight_fetch_trades_emits_error` 等）も同様に
`VenueError` が先着することで `event == 'Error'` アサーションが失敗または不安定化。

**根本原因**:
`_handle()` 内で handshake 完了直後に `asyncio.create_task(self._startup_tachibana())` が起動する。
`_startup_tachibana` は `TachibanaWorker`（モック済み）は使わず、
`tachibana_startup_login()` を直接呼ぶため、Worker クラスをパッチしても実装が走る。
実環境では保存済みセッションがある場合 `VenueReady` を、失敗した場合 `VenueError` を outbox に積む。
これらが期待するメッセージ（`Pong`/`Error`）より先に `ws.recv()` に届き、アサーションが失敗する。

`test_server_dispatch.py` では commit 74aa481（2026-04-27）で修正済みだったが、
`test_server_proxy.py` に同じ対処が伝播していなかった。

**修正**:
`proxy_server` fixture と独自サーバーを立てる 3 テストの `ExitStack` に
```python
patch.object(DataEngineServer, "_startup_tachibana", AsyncMock(return_value=None))
```
を追加。

**追加したテスト**: なし（テスト自身が修正対象。修正後 13/13 が PASS することで回帰ガードを兼ねる）

**教訓**:

1. **「Worker をモックした」≠「背景タスクをモックした」**: `_startup_tachibana` のような
   background task はワーカーを経由せず独自の関数を呼ぶ。`DataEngineServer._handle()` が
   `create_task()` するすべての内部メソッドを列挙し、テストで outbox に意図しないイベントを
   積む可能性があるものをモックする必要がある。

2. **既存のパッチを新テストファイルに伝播するルール**: 同一 class のテストが複数ファイルに
   わたるとき、あるファイルへのパッチ追加は「同じ class を使う他のテストファイル」への
   適用を必ずセットで確認する（`grep -r "_startup_tachibana"` や
   `grep -r "DataEngineServer"` で漏れを検出）。

3. **ws.recv() 単回読みのリスク**: 非同期サーバーが複数のイベントを outbox に積みうる場合、
   `ws.recv()` を 1 回だけ呼んで特定イベントを期待するテストは順序依存で壊れる。
   「他の非同期タスクが送りうるイベントは必ずモックで抑制する」か、
   「期待イベントが来るまでループして読み捨てる」かを設計段階で選択する。
