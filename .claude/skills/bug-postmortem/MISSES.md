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
| ライブデータ前提 | テストが「常にライブストリームあり」を前提とし、市場クローズ・週末など「ストリームなし」で起動するシナリオを未検証 | 1 |
| saved-state migration 未実装 | enum バリアント追加時に、追加前の saved-state を読んだ場合の migration テストがなく、旧フォーマットでの起動時のみ再現するバグを見逃す | 1 |
| IPC 契約の片側未実装 | Rust が特殊パス（`"__all__"` 等）を送る契約が `backend.rs` に書かれているが、Python 側の対応実装とテストが追いついていない | 1 |
| バグ動作 pin | 既存のバグ挙動（空返却・None 返却）をリグレッションガードとして固定し、正しい設計（raise 等）と乖離した状態を固める | 1 |
| エラーパス副作用なし | ストリームワーカーの早期 return パスで outbox への積み忘れが発生し、Rust 側が「データが来ない」としか認識できない | 1 |

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

---

## 2026-04-27 — `fetch_depth_snapshot` が session=None で `{}` を無音返却（バグ動作を pin）

**見逃しパターン**: 「バグ動作を正として pin した」テスト（API 仕様固定なし の変形）

**不具合の概要**:
`fetch_depth_snapshot` は `session is None` のとき `{}` を返していた。
呼び出し元 `_do_request_depth_snapshot` は `snap["last_update_id"]` を参照するため
KeyError が発生し、`_spawn_fetch` の `except Exception` で汎用 Error が返るだけで
「セッションがない」という原因は一切伝わらなかった。

同じクラスの `fetch_klines` / `fetch_ticker_stats` / `_download_master` はいずれも
`TachibanaError(code="no_session")` を raise する設計で統一されていた。

**根本原因**:
既存テスト `test_fetch_depth_snapshot_returns_empty_dict_when_session_is_none` が
`assert result == {}` として**バグ動作そのものをリグレッションガードとして固定していた**。
`fetch_klines` との一貫性（`no_session` raise）を比較・検証するテストがなかった。

**修正**:
- `fetch_depth_snapshot` の `session is None` 分岐を `TachibanaError(code="no_session", ...)` raise に変更
- 既存テスト `test_fetch_depth_snapshot_returns_empty_dict_when_session_is_none` を
  `test_fetch_depth_snapshot_raises_no_session_when_session_is_none` に更新

**追加・変更したテスト**:
- `python/tests/test_tachibana_worker_basic.py::test_fetch_depth_snapshot_raises_no_session_when_session_is_none`

**リグレッション確認**: `git stash` で修正を戻した状態で旧テスト（`result == {}`）が PASS、
新テスト（`pytest.raises(TachibanaError)`）が収集失敗することを確認。`git stash pop` で PASS。

**教訓**:
1. **「バグ動作を pin したテスト」の検出**: テスト名に `returns_empty` / `returns_none` が含まれる
   `session=None` 分岐テストは「その返却値が意図か・バグか」を必ず隣接メソッドと比較する。
   同クラスの他メソッドが同じ条件で `raise TachibanaError` するなら、
   `return {}` を pin したテストはバグ動作を固定している可能性が高い。

2. **「兄弟メソッドとの一貫性」アサーション**: `session is None` ガードを持つメソッド群は、
   同じ挙動（raise / log+return / return only）を統一し、一方を変えたら他方も変えるルールを持つ。
   テストでは「fetch_klines と fetch_depth_snapshot が同じコードを raise すること」を
   1 つのファイルで比較テストとして書くのが理想。

---

## 2026-04-27 — `stream_trades` / `stream_depth` が session=None でログなし・Disconnected なしに終了

**見逃しパターン**: エラーパス副作用なし

**不具合の概要**:
`stream_trades` / `stream_depth` は `session is None` のとき `return` するだけで、
outbox への `Disconnected` イベント積みも `log.warning` も行わなかった。
Rust 側は「購読成功したつもりでデータが来ない」状態になる。

**根本原因**:
`session is None` の分岐にテストが一切なかった。市場クローズ時の同一パス
（`_tachibana_ws.is_market_open` が False → `Disconnected` を積んで return）との
対称性を検証するテストもなかった。

**修正**:
- `stream_trades` の `session is None` 分岐に `log.warning` + `Disconnected` outbox append を追加
- `stream_depth` も同様

**追加したテスト**:
- `python/tests/test_tachibana_worker_basic.py::test_stream_trades_session_none_appends_disconnected_event`
- `python/tests/test_tachibana_worker_basic.py::test_stream_depth_session_none_appends_disconnected_event`

**リグレッション確認**: `git stash` で修正を戻した状態で両テストが
`assert 0 == 1 (len(outbox))` で FAIL することを確認。`git stash pop` で PASS。

**教訓**:
1. **「副作用なし return」は outbox を期待するテストでしか検出できない**:
   `return` だけの早期脱出は呼び出し元の視点では「データが来ない」ことしか分からない。
   ストリームワーカーの全早期 return パスに「outbox へ何かを積んでから return する」という
   設計ルールを持つ。積むべきイベント（`Disconnected` / `VenueError`）をテストで assert する。

2. **「市場クローズ」と「セッションなし」の対称性**:
   `is_market_open` が False の場合に `Disconnected` を積む実装があるなら、
   `session is None` の場合も同様に積む必要がある。対称パスが存在するときは
   「両方をテストしているか」をコードレビューで確認する。

---

## 2026-04-27 — 立花チャートが空白（市場クローズ時に latest_x が 0 のまま）

**見逃しパターン**: ライブデータ前提（市場クローズ・週末などストリームなしシナリオを未検証）

**不具合の概要**:
アプリ起動後、立花証券（日本株）の日足チャートが Y 軸に 1, 0, -0, -1 しか表示されず
ローソク足が一切描画されない。他の値もすべて空白。

ログ確認では klines の取得自体は成功（450 件、1500 件など）しており、
Python エンジンも HTTP 200 を返していた。

**根本原因**:
`KlineChart::new(&[], ...)` で空のチャートを初期化すると `chart.latest_x = 0`（デフォルト値）になる。
その後 `insert_hist_klines(Some(req_id), klines)` で履歴 klines を挿入するが、
`chart.latest_x` が更新されなかった。

`FitToVisible` 自動スケールは `visible_region(bounds)` → `interval_range` → `x_to_interval` の流れで
「チャートの可視時間範囲」を計算する。この計算は `self.latest_x` を基準にするため、
`latest_x = 0` のままでは可視範囲が Unix エポック付近（1970 年）になり、
実際の klines（1990〜2026 年）は範囲外に存在し続ける。
→ `visible_price_range(0, epsilon)` は `None` を返し、Y 軸スケールが更新されない。

**なぜ Binance では起きないか**:
Binance は市場が常時オープンで、ライブ kline ストリームからの更新
（`on_kline_update` → `chart.latest_x = kline.time`）が `insert_hist_klines` の前に必ず届く。
`latest_x` が実タイムスタンプに更新された状態で履歴が挿入されるため、表示が正しく機能する。
立花は週末（日本株市場クローズ）に「ライブストリームなし」で起動した場合のみ再現する。

**修正**:
`src/chart/kline.rs::insert_hist_klines` に、挿入された klines の中で最新のタイムスタンプを
`chart.latest_x` に反映するブロックを追加（`on_kline_update` の同パターンを踏襲）:
```rust
if let Some(latest_t) = klines_raw.iter().map(|k| k.time).max() {
    let chart = self.mut_state();
    if latest_t > chart.latest_x {
        chart.latest_x = latest_t;
    }
}
```

**なぜ既存テストで発見できなかったか**:

| テスト | 見逃した理由 |
|--------|------------|
| `cargo test --workspace` | `KlineChart` の描画・状態の単体テストが存在しなかった |
| Python tests | Rust チャート層のレンダリング状態は Python 側から検証できない |
| `tests/e2e/smoke.sh` | 「チャートが描画されたか」を確認する手段がなく、空白チャートは正常起動と区別できない |
| 全テスト共通 | 「市場クローズ時（週末）の起動」というシナリオが一切テストされていない。常にライブストリームありの前提で設計されていた |

**追加したテスト**:
- `src/chart/kline.rs::tests::insert_hist_klines_updates_latest_x`
  — `KlineChart::new(&[])` で空チャートを作成し、`insert_hist_klines` 後に `latest_x` が
    最新 kline タイムスタンプに更新されることを assert

**リグレッション確認**: 修正前（`latest_x` 更新コード削除）で FAIL、修正後で PASS を実証。

**教訓**:

1. **「ライブストリームなし」シナリオのテスト必須化**:
   `latest_x` のような「基準タイムスタンプ」を持つチャートは、
   ストリームなし（市場クローズ、週末、接続エラー）の状態で履歴データを挿入した場合に
   正しく表示されることをテストする。「常に接続中」を前提としたテストだけでは、
   この種のバグを発見できない。

2. **状態更新の対称性チェック**:
   `latest_x` を更新するパスが複数ある場合（`KlineChart::new`、`on_kline_update`）、
   `insert_hist_klines` も同じ更新をすべきかどうかを設計時に確認する。
   「同じフィールドを更新する他のパスがあるか」を設計レビューで列挙する習慣をつける。

3. **`FitToVisible` 自動スケールの依存変数を把握する**:
   `FitToVisible` は `latest_x` を基準に可視範囲を計算する。
   チャート初期化後に `latest_x` が正しく設定されていないと、
   挿入されたデータが正しく表示されない。
   `latest_x` を変更・リセットする操作は必ず `FitToVisible` が期待する不変条件との整合を確認する。
