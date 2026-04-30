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
| API 仕様固定なし | Mock レスポンスが誤ったキー名・フィールド名を前提に書かれており、実 API の必須パラメータ欠落・レスポンス形式ズレを素通りする | 2 |
| ライブデータ前提 | テストが「常にライブストリームあり」を前提とし、市場クローズ・週末など「ストリームなし」で起動するシナリオを未検証 | 1 |
| モード境界 saved-state 混入 | saved-state.json に含まれるライブモード用ペインがリプレイモード起動時にも読み込まれ、無効な状態で描画が実行される | 1 |
| saved-state migration 未実装 | enum バリアント追加時に、追加前の saved-state を読んだ場合の migration テストがなく、旧フォーマットでの起動時のみ再現するバグを見逃す | 1 |
| IPC 契約の片側未実装 | Rust が特殊パス（`"__all__"` 等）を送る契約が `backend.rs` に書かれているが、Python 側の対応実装とテストが追いついていない | 1 |
| バグ動作 pin | 既存のバグ挙動（空返却・None 返却）をリグレッションガードとして固定し、正しい設計（raise 等）と乖離した状態を固める | 2 |
| エラーパス副作用なし | ストリームワーカーの早期 return パスで outbox への積み忘れが発生し、Rust 側が「データが来ない」としか認識できない | 1 |
| IPC イベント → UI 状態の未配線 | IPC イベントを受信して toast を出すだけで、対応する UI コンポーネントの状態（`submitting` フラグ等）をリセットしていない | 1 |
| 初期化前データ到着 | UI コンポーネントが未初期化（`None`）の状態でデータが到着し、`if let Some(...)` でサイレントに無視される | 1 |
| エラーハンドラ早期脱出 | エラー arm が `break` するため、一時的な IO エラー（非 UTF-8 バイト等）で pipe reader が停止し、書き手の stdout バッファが詰まる | 1 |
| モード分岐漏れ | `_handle()` のような接続後フックが `mode` を考慮せず、ライブ専用の処理をリプレイモードでも実行してしまう | 2 |
| view() 分岐別オーバーレイ配線漏れ | `view()` の複数分岐のうち一部にしかモーダルオーバーレイを配線せず、他の分岐で `Some(dialog)` がサイレントに無視される | 1 |
| テストヘルパー属性ドリフト | prod `__init__` に属性を追加したとき、`__init__` をモックで迂回するテストヘルパーの属性リストを同期していない | 1 |
| 参照リソース未作成 | テストが参照する fixture / example ファイルが未コミットのまま test コードだけが先に存在する | 1 |

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

---

## 2026-04-27 — 立花 EVENT WebSocket の受信フレームが `p_cmd` ではなく `p_evt_cmd` で参照されていた

**見逃しパターン**: API 仕様固定なし（Mock が誤ったフィールド名で書かれており、実 API との乖離を素通り）

**不具合の概要**:
アプリ起動後、立花証券の Ladder（板情報）ペインが「Waiting for data...」のまま更新されない。
WebSocket 接続は確立しサーバーから FD フレームが届いているにもかかわらず、
`TachibanaEventWs._recv_loop` が FD/KP/ST を一切認識せずコールバックが呼ばれない。

**根本原因**:
Tachibana EVENT WebSocket の受信フレームはコマンド種別を `p_cmd` フィールドに格納する（例: `\x01p_cmd\x02FD`）。
しかし `TachibanaEventWs._recv_loop` が `fields.get("p_evt_cmd", "")` を参照していた。

`p_evt_cmd` は**購読 URL のリクエストパラメータ**（`p_evt_cmd=ST,KP,FD`）であり、
サーバーが返す受信フレームのフィールド名とは別物。
実サーバーは受信フレームに `p_evt_cmd` を含まないため、`evt_cmd` は常に空文字列になり、
FD/KP/ST コールバックが一度も呼ばれない状態だった。

**なぜ既存テストで発見できなかったか**:

全ての WS フレーム Mock ビルダー関数が同じ誤ったフィールド名 `p_evt_cmd` を使っていた。
実装とテストが「一貫して間違った前提」を共有していたため、すべてのテストがパスしていた。

| テスト | 見逃した理由 |
|--------|------------|
| `test_fd_frame_triggers_callback` | `_encode_fd_frame` が `\x01p_evt_cmd\x02FD` を送信 → 実装も `p_evt_cmd` を参照 → 偶然一致 |
| `test_kp_frame_triggers_callback` | `_kp_frame_bytes` が `\x01p_evt_cmd\x02KP` を送信 → 同様に偶然一致 |
| `test_st_frame_nonzero_triggers_callback` | `_st_frame_bytes` が `\x01p_evt_cmd\x02ST` を送信 → 同様に偶然一致 |
| 他全 WS テスト | すべて誤ったフィールド名の Mock を共有 |

実サーバーのフレーム内容（`p_cmd\x02FD`）を実際に受信して照合するテストが一切なかった。

**修正**:
- `python/engine/exchanges/tachibana_ws.py` 行 390:
  `fields.get("p_evt_cmd", "")` → `fields.get("p_cmd", "")`
- `python/tests/test_tachibana_ws.py` — 全 Mock ビルダーを `p_cmd` に修正
- `python/tests/test_tachibana_ws_fd_depth_recv.py`、`test_tachibana_ws_proxy.py`、
  `test_tachibana_ws_timeout.py`、`test_tachibana_depth_safety.py`、
  `test_tachibana_holiday_fallback.py`、`test_tachibana_fd_trade.py` — 同様に修正

**追加したテスト**:
- `python/tests/test_tachibana_ws.py::TestReceivedFrameFieldName::test_frame_with_p_cmd_key_triggers_fd_callback`
  — 実サーバーフォーマット（`p_cmd=FD`）でコールバックが呼ばれることを検証。
  エラーメッセージに「`fields.get('p_cmd')` を使うこと」を明記
- `python/tests/test_tachibana_ws.py::TestReceivedFrameFieldName::test_frame_with_p_evt_cmd_key_does_not_trigger_callback`
  — 誤ったキー名（`p_evt_cmd`）ではコールバックが呼ばれないことを検証（リグレッション防止）

**リグレッション確認**:
- 修正前（`p_evt_cmd` に戻した状態）: 両テストが FAIL（`2 failed in 5.23s`）
- 修正後（`p_cmd` に戻した状態）: 両テストが PASS、全テストスイート 844 passed

**教訓**:

1. **Mock フレームは「実サーバーが送るフォーマット」で書く**:
   Mock フレームのフィールド名を実装と揃えるだけでは不十分。
   「実サーバーが実際に送るフォーマット」を公式サンプルやマニュアルで確認し、Mock に反映する。
   今回は公式サンプル `e_api_websocket_receive_tel.py` で `p_cmd` を確認できた。

2. **リクエストパラメータと受信フレームフィールドは別名**:
   Tachibana の `p_evt_cmd=ST,KP,FD`（URL パラメータ）と受信フレームの `p_cmd=FD`（フィールド名）は
   役割が異なる別名。同じ名前に見えるが混同しやすい。API ドキュメントで受信フレーム仕様を明示的に確認する。

3. **「全 Mock が同じ誤りを共有」の構造的危険性**:
   共通 Mock ビルダー関数がある場合、実装の誤りを Mock が補完して全テストが通ることがある。
   「実際のサーバーが送るフレームと Mock フレームは同一か」を定期的に実機観測で照合する習慣をつける。

---

## 2026-04-28 — Ladder が市場時間外に「Waiting for data...」のまま（初期化前データ到着）

**見逃しパターン**: 初期化前データ到着（ライブデータ前提 と タイミング依存 の複合）

**不具合の概要**:
アプリ起動後（市場時間外）、TachibanaStock の Ladder（板）パネルが「Waiting for data...」のまま
REST スナップショット（bid/ask 各 10 本）が表示されない。

Python の `stream_depth` はセッションあり＋時間外の場合 `DepthSnapshot` を即座に送出するが、
Rust 側の Ladder パネルが `Content::Ladder(None)` のまま（未初期化）なため、
`ingest_depth` 内で `if let Some(panel) = panel { ... }` がサイレントに素通りしてデータが消える。

**根本原因**:
`resolve_streams` で `streams = Ready` にしてから、`ResolveContent`（次の tick で発火）が
`set_content_and_streams` → `Content::Ladder(Some(...))` を作るまでの**1 フレーム以上の窓**が存在する。
Python はローカルホスト IPC で < 1ms 以内に応答するため、`DepthReceived` がその窓の中に到着する。

```
resolve_streams() → streams=Ready → [iced が subscription 登録]
  ↓ フレームN+1
Python: Subscribe 受信 → DepthSnapshot を即送
DepthReceived 到着 → ingest_depth → Ladder(None) → サイレントドロップ
  ↓ 同フレームor次フレーム
tick() → ResolveContent → set_content_and_streams → Ladder(Some(...)) ← 遅い
```

**修正**:
`dashboard.rs::resolve_streams` で `streams = Ready` にした直後に
`!content.initialized()` を確認し、`set_content_and_streams` を即呼び出す（1 フレーム待たない）。
`ingest_depth` に `Ladder(None)` 到着時の警告ログを追加（今後のデバッグ用）。

**なぜ既存テストで発見できなかったか**:

| テスト | 見逃した理由 |
|--------|------------|
| `cargo test --workspace` | Ladder パネルの GUI 層（`pane::State`）には初期化前データ到着のシナリオを再現するテストがなかった |
| `python/tests/` | Python は `DepthSnapshot` を正しく送出しており Python 側の問題ではない |
| E2E smoke.sh | 「Ladder が空」= 正常起動と区別できない（データ表示のチェックがない） |
| 全テスト共通 | `Content::Ladder(None)` への `if let Some(panel)` サイレントドロップは警告ログすら出ず、「データが来ない」とだけ見えた |

**追加したテスト**:
- `src/screen/dashboard/pane.rs::tests::set_content_and_streams_initializes_ladder_when_content_is_none`
  — `Ladder(None)` + `streams=Ready` 状態から `set_content_and_streams` を呼ぶと `initialized()` が true になることを assert
- `src/screen/dashboard/pane.rs::tests::stream_pair_kind_returns_some_when_streams_are_ready`
  — `streams=Ready` のとき `stream_pair_kind()` が `SingleSource` を返すことを assert
    （`resolve_streams` の eager 初期化ブランチが到達可能であることを保証）

**リグレッション確認**: `set_content_and_streams` の Ladder ブランチで `Content::Ladder(None)` を返すよう変更すると test_1 が `initialized()` が false で FAIL することを確認。元に戻すと PASS。

**教訓**:

1. **「データより先に初期化を済ませる」不変条件の明示化**: GUI コンポーネントが `Option<T>` で
   未初期化状態を持つ場合、外部データ到着時には `Some` になっていることを assert すべき。
   `if let Some(...)` のサイレントドロップは「データが来ない」にしか見えず、根本原因特定が困難。

2. **1 フレーム窓の危険性**: iced の更新サイクルでは「A のメッセージ処理 → subscription 登録 →
   B のイベント到着」が 1 フレーム以内に起こりうる。特に localhost IPC は network latency が
   ほぼゼロなので、「次の tick でやればいい」は実際に window を作る。状態変更後に依存する
   初期化は即座（同一メッセージ処理内）に行う。

3. **`Content::initialized()` のテスト追加を検討**: 新しい `Content` バリアントを追加する際は
   「未初期化状態でデータが到着したとき何が起こるか」をテストで明示する。
   `Panel(None).initialized() == false` かつ `Panel(None)` へのデータ挿入がサイレントに失敗する
   設計を選ぶなら、挿入前に `initialized()` を assert する防衛コードを `ingest_depth` に置く。

---

## 2026-04-28 — Python stdout 非 UTF-8 バイトで pipe reader が停止 → asyncio deadlock → アプリ終了

**見逃しパターン**: エラーハンドラ早期脱出 / ログ検査漏れ（複合）

**不具合の概要**:
Tachibana API のレスポンスに Shift-JIS 文字が含まれる場合、Python エンジンがそれをログ出力する。
`engine-client/src/process.rs::forward_lines()` が `next_line()` からの `InvalidData` エラーを
`break` で処理していたため、pipe reader タスクが終了する。
Python の stdout pipe に reader がいなくなると、約 64KB のバッファが埋まった時点で
Python の asyncio event loop がブロックし、アプリが数分以内に応答不能→終了する。

**修正**: `forward_lines()` の `Err(e) if e.kind() == InvalidData` arm を `break` → `continue`（debug ログ付き skip）に変更。

**なぜ既存テストで発見できなかったか**:

| テスト | 見逃した理由 |
|--------|------------|
| `engine-client/tests/*.rs` | Mock サーバーを使用。実 Python subprocess が Shift-JIS 文字を stdout に出力するシナリオがない |
| `tests/e2e/smoke.sh` | `engine ws read error` は検査していたが `engine pipe read error` は未検査 |
| `src/process.rs::tests` | `forward_lines()` の非 UTF-8 入力に対する挙動のテストがなかった |

**追加したテスト**:
- `engine-client/src/process.rs::tests::forward_lines_does_not_stop_after_non_utf8_line`
  — 非 UTF-8 バイトの後もタスクが継続（`is_finished() == false`）することを検証
- `tests/e2e/smoke.sh` — `engine pipe read error` チェックを追加

**リグレッション確認**: `InvalidData` arm に `break` を追加した状態で FAIL、削除で PASS。

**教訓**:

1. **pipe reader の `break` は「対向プロセスの stdout deadlock」を引き起こす**:
   `forward_lines` のような「プロセス stdout を読む無限ループ」では、
   回復可能なエラー（`InvalidData` 等）で `break` すると reader が消えて
   書き手のバッファが詰まる。`break` は EOF や致命的 IO エラーのみに使う。

2. **`smoke.sh` のパイプ系エラー網羅**:
   `engine ws read error`（WebSocket 層）と同様に `engine pipe read error`（プロセス stdout 層）も
   smoke test でチェックする。プロセス IPC 系のエラーログは必ず smoke.sh の check 対象にする。

3. **`forward_lines` 系のテストは `JoinHandle::is_finished()` で継続性を assert する**:
   「ログの内容」を確認する代わりに「タスクがまだ動いているか」を `is_finished()` で確認するパターンが
   有効。非 UTF-8 入力後もタスクが alive であることを assert すれば、`break` → `continue` の退化を防止できる。

---

## 2026-04-27 — `OrderAccepted`/`OrderRejected` 受信後に `submitting` フラグが永久 true のままになる

**見逃しパターン**: IPC イベント → UI 状態の未配線

**不具合の概要**:
注文送信後、`OrderEntryPanel.submitting = true` にセットされるが、`OrderAccepted`/`OrderRejected`
IPC イベントを受信しても `submitting` がリセットされず、注文ボタンが永久に無効化されたままになる。

**根本原因**:
`map_engine_event_to_tachibana()` では `OrderAccepted`/`OrderRejected` を
`Message::OrderToast(...)` に変換して toast だけを表示していた。
`OrderEntryPanel.on_accepted()` / `on_rejected(reason)` を呼ぶ経路が存在しなかった。

`on_accepted()`/`on_rejected()` メソッド自体は `order_entry.rs` に実装済み・テスト済みだったが、
`main.rs` → `dashboard.rs` → `pane.rs` → `order_entry.rs` の伝播パスが全く配線されていなかった。

**なぜ既存テストで発見できなかったか**:

| テスト | 見逃した理由 |
|--------|------------|
| `order_entry.rs` の `on_accepted_clears_submitting_and_error` | `on_accepted()` を直接呼ぶ単体テスト。`main.rs` からの呼び出しパスは検証していない |
| `order_entry.rs` の `on_rejected_sets_error_and_clears_submitting` | 同上 |
| `cargo test --workspace` 全体 | IPC イベント受信 → UI 状態変化の E2E 経路をカバーするテストがない |

`on_accepted()`/`on_rejected()` が実装・テスト済みという事実が「配線も完了している」という
錯覚を生んだ。実装と配線は別のレイヤーであり、両方を検証する必要がある。

**修正**:
1. `main.rs` に `Message::OrderAccepted { client_order_id, venue_order_id }` と
   `Message::OrderRejected { client_order_id, reason }` を追加
2. `map_engine_event_to_tachibana()` で `OrderAccepted`/`OrderRejected` をこれらの新変数にマップ
3. `update()` ハンドラで `dashboard.notify_order_accepted()` / `notify_order_rejected()` を呼んでから toast を push
4. `dashboard.rs` に `notify_order_accepted()` / `notify_order_rejected()` メソッドを追加。
   `iter_all_panes_mut()` で全ペインを走査し `pending_request_id` が一致する `OrderEntry` パネルにのみ呼ぶ

**追加したテスト**: なし（既存の `on_accepted`/`on_rejected` 単体テストが PASS 継続を確認）

**教訓**:

1. **「実装済み」と「配線済み」は別のレイヤー**: メソッドが実装済み・テスト済みでも、
   呼び出し側の経路（IPC イベント → Message → update() → component）が配線されていなければ
   実行されない。新しいコンポーネントメソッドを追加したら「どこから呼ばれるか」を
   必ず確認し、end-to-end の呼び出しパスを持つ統合テストを書く。

2. **「toast を出す」≠「UI 状態をリセットする」**: IPC イベントハンドラで toast を表示する
   実装は「通知は機能している」という安心感を与えるが、UI コンポーネントの
   状態変化（`submitting`, `pending_request_id` 等）は別途配線が必要。
   IPC イベントが UI の複数レイヤーに影響する場合は、全レイヤーへの伝播をチェックリスト化する。

3. **`distribute_order_list` をモデルに**: `dashboard.rs` に `distribute_order_list()` という
   「全 OrderList パネルにデータを配信する」パターンが既にある。
   同様のパターンを必要とする新機能（`notify_order_accepted` 等）は、このパターンを参照して
   一貫した実装を行う。パターンの例が存在するときはそれに倣う。

---

## 2026-04-29 — `--mode replay` 起動時に saved-state のライブモードペインが残留し lyon_path パニック

**見逃しパターン**: モード境界 saved-state 混入（ライブデータ前提 と saved-state migration 未実装 の複合）

**不具合の概要**:
`--mode replay` で起動し、`POST /api/replay/load` で 1301.TSE を読み込むと
`thread 'main' panicked at lyon_path-1.0.16\src\path.rs:812: assertion failed: p.y.is_finite()`
で即クラッシュする。

直前ログに `Failed to request Kline(…): Request overlaps with an existing request` が出ている。

**再現手順**:
1. ライブモードで TOYOTA(7203) ペインがある状態で `saved-state.json` を保存
2. `--mode replay` で起動（Toyota ペインが残ったまま）
3. `POST /api/replay/load` で 1301.TSE を読み込む
4. `auto_generate_replay_panes` が Toyota ペインを base に分割 → Toyota ペインは描画を続ける
5. Toyota ペインがライブ接続を試みて失敗 → 重複 kline リクエスト → "Request overlaps" エラー
6. 無効状態での描画 → NaN 座標が lyon_path に渡ってパニック

**根本原因**:
`Flowsurface::new()` が `saved_state.layout_manager` を起動モードに関わらず無条件にロードしていた。
D8「in-memory 状態はモード境界を跨いで保持しない」の実装が欠落していた。

副次的 NaN 経路: `ViewState::price_to_y()` が `cell_height = NaN` を受け取ると NaN を返し、
lyon_path の `assert!(p.y.is_finite())` でパニックしていた。ガードが未実装だった。

**修正**:
1. `src/main.rs`: `Flowsurface::new()` でリプレイモードのとき `LayoutManager::new()` で fresh レイアウトに差し替え（D8 準拠）。ウィンドウジオメトリ（位置・サイズ）は引き続き saved-state から復元する
2. `src/chart.rs`: `ViewState::price_to_y()` に NaN ガードを追加。非有限値が出たとき `log::warn!` を出して `0.0` を返す（サイレントでない）
3. `src/replay_api.rs`: `Command::LoadReplayData` に N4.2 WIP で追加された `strategy_file: None, strategy_init_kwargs: None` を明示（WIP の未配線によるコンパイルエラー修正）

**なぜ既存テストで発見できなかったか**:

| テスト | 見逃した理由 |
|--------|------------|
| `cargo test --workspace` | `Flowsurface::new()` のモード境界ロジックを検証するテストがない。GUI 起動フルシーケンスを再現するユニットテストは書きにくい |
| `engine-client/tests/*.rs` | Rust クライアント層のプロトコルテストで、iced アプリの状態ロードロジックは範囲外 |
| `src/chart/kline.rs::tests` | `insert_hist_klines_updates_latest_x` は kline 挿入後の `latest_x` を検証するが、NaN 状態での描画は検証していない |
| E2E smoke.sh | GUI クラッシュは観測できるが、再現に saved-state.json の特定内容が必要で自動化されていない |
| 全テスト共通 | 「リプレイモード起動 × ライブモード saved-state.json」という組み合わせシナリオが一切テストされていなかった |

**追加したテスト**:
- `src/chart.rs::tests::price_to_y_returns_finite_for_valid_state` — 正常状態では `price_to_y` が有限値を返すことを検証
- `src/chart.rs::tests::price_to_y_guards_nan_cell_height` — `cell_height = NaN` でも `0.0` を返し lyon_path パニックを防ぐことを検証

**リグレッション確認**: `price_to_y_guards_nan_cell_height` は NaN ガードを除去すると `assert_eq!(y, 0.0)` で FAIL する（`NaN != 0.0`）。ガードを戻すと PASS。

**教訓**:

1. **D8 モード境界は「コードで強制」する**: spec に「in-memory 状態はモード境界を跨いで保持しない」と書いてあっても、`Flowsurface::new()` でコードとして強制しなければ saved-state の内容がリプレイに持ち込まれる。spec の不変条件はその実施箇所に近いコードでアサートまたは強制する。

2. **lyon_path は NaN 座標でパニックする**: iced の Canvas 描画で lyon_path を使う場合、座標が NaN になるとデバッグ困難なパニックが発生する。座標を計算するすべての関数（`price_to_y` 等）に `is_finite()` ガードを入れ、ログを残して `0.0` などの安全値にフォールバックする。

3. **「モード × saved-state の組み合わせ」は独立した test scenario**: ライブモードとリプレイモードを同一テストで扱わず、「リプレイモード起動 × ライブ saved-state」という組み合わせを独立したシナリオとしてテスト設計に含める。

4. **WIP フィールド追加はコンパイルガードを壊す**: N4.2 で `Command::LoadReplayData` に新フィールドを追加した際、既存の構築箇所が漏れてコンパイルエラーになった。enum バリアントにフィールドを追加したら `cargo check` で全呼び出し箇所を確認する習慣をつける。

---

## 2026-04-28 — `_determine_side` が曖昧 side を `"buy"` に固定 (false positive)

**見逃しパターン**: バグ動作 pin

**不具合の概要**:
`tachibana_ws.py::FdFrameProcessor._determine_side` は、quote rule（price vs ask/bid）も
tick rule（price vs prev_trade_price）も適用できない「曖昧」ケースで `return "buy"` を返していた。
これにより、midpoint で前回価格と同値の取引は無条件に buy 判定され、買い出来高が水増しされる。
live/replay 互換ロジックで side を集計する N1 フェーズで false positive になるため N1.0 として先行修正。

**修正**:
- `_determine_side` の戻り値型を `str` → `str | None` に変更し、曖昧ケースで `None` を返す
- 呼び出し側: `_side if _side is not None else "unknown"` で `trade["side"]` を設定

**なぜ既存テストが見逃したか**:
`test_tachibana_fd_trade.py::TestSideDetermination::test_tick_rule_up_gives_buy` に
「`same as prev_trade → ambiguous → default buy`」というコメントとともに `assert trade3["side"] == "buy"`
が書かれており、**バグ挙動を正の仕様として固定していた**。
テストが「現状動作を固定する」のではなく「正しい仕様を固定する」べきところで誤りを内包していた。

**追加したテスト**:
- `python/tests/test_tachibana_fd_trade.py::TestSideDetermination::test_tick_rule_up_gives_buy` — 期待値を `"buy"` → `"unknown"` に修正

**リグレッション確認**: 旧実装（`return "buy"`）に戻した状態で `trade3["side"] == "unknown"` が FAIL することを実証。

**教訓**:

1. **「動いている挙動を pin する」テストは仕様の正しさを保証しない**:
   `assert side == "buy"` は「現在の実装がそう返す」ことを固定するが、
   「そう返すべき」かどうかは別問題。曖昧ケースの戻り値を変更したとき、
   テストが FAIL せずに PASS し続けたなら、テストは誤った仕様を守り続けている。
   テストを書くときは「なぜその値が正しいか」を明文化し、コメントで根拠を示す。

2. **`None`/`unknown` の導入はリグレッションの温床**:
   `str` 返却 → `str | None` 返却 の型変更は、呼び出し側が `None` を扱わないと
   そのまま `None` が dict に入り下流で TypeError になる。
   型変更と同時に呼び出し側の全箇所を grep して `None` ハンドリングを確認すること。

3. **曖昧判定は `"unknown"` として伝搬させる**:
   `"buy"` や `"sell"` のデフォルト値で埋めると、集計ロジック（live/replay 互換）で
   false positive になる。side が不明なら `"unknown"` を明示することで、
   集計側がフィルタリングまたは別処理できるようにする。

---

## 2026-04-30 — replay モードで `_startup_tachibana()` が実行され IPC 接続が切断

**見逃しパターン**: モード分岐漏れ（ライブデータ前提 と Mock 置換漏れ の複合）

**不具合の概要**:
`--mode replay` で起動して `POST /api/replay/start` を呼ぶと HTTP 502 が返る。
ログに `engine ws read error: Reserved bits are not zero` が出て IPC 接続が切断されている。

**根本原因**:
`DataEngineServer._handle()` のハンドシェイク後の処理が `mode` に関係なく
`asyncio.create_task(self._startup_tachibana())` を実行していた。

dev 環境では `DEV_TACHIBANA_*` env var が設定されているため、リプレイモードでも
Tachibana 自動ログインが成功 → `VenueReady` → `FetchTickerStats("__all__")` IPC
→ Python が 278 KB の JSON フレームを一括送信 → fastwebsockets が
「Reserved bits are not zero」エラーで接続を切断 → `/api/replay/start` が 502 になる。

**修正**: `server.py::_handle()` に `if self._mode != "replay":` ガードを追加。

**なぜ既存テストで発見できなかったか**:

| テスト | 見逃した理由 |
|--------|------------|
| `test_server_ws_compat.py` | ハンドシェイクで `mode` フィールドを渡しておらず（デフォルト `"live"`）、リプレイモードのシナリオがなかった |
| `test_server_dispatch.py` | `_startup_tachibana` を mock.patch.object でモックしていたが、テスト自体は `mode="live"` 相当で動作していた |
| `tests/e2e/smoke.sh` | ライブモードのみを検証しており、リプレイモードでの起動シナリオがない |
| 全テスト共通 | 「リプレイモード × Tachibana dev 環境変数あり」という組み合わせを明示的にテストしていなかった |

**追加したテスト**:
- `python/tests/test_server_ws_compat.py::test_tachibana_startup_skipped_in_replay_mode`
  — `mode="replay"` でハンドシェイクした後、`_startup_tachibana` が呼ばれないことを
  `patch.object` + `assert_not_called()` で検証。ガードを除去すると FAIL することを実証済み

**リグレッション確認**: ガード（`if self._mode != "replay":`）を削除した状態で FAIL、
追加した状態で PASS することを実際に確認。

**教訓**:

1. **mode 引数はテストでも明示的に渡す**: `_handshake()` ヘルパーが `mode` を渡さないと
   デフォルト（`"live"`）になり、リプレイ固有のバグを再現できない。
   ハンドシェイクを再利用するテストでは `"mode": "replay"` を渡すケースを追加する。

2. **「背景タスクがモードを見ているか」を起動フロー設計時に確認**:
   `_handle()` のような接続後フックは「全モードで同じことをすべきか」を明示的に問う。
   `live` 専用の初期化（Tachibana ログイン、取引所 ready チェック等）は `if mode != "replay":` で囲む。

3. **env var の組み合わせが作るシナリオ**: `DEV_TACHIBANA_*` が設定された dev 環境でのみ
   Tachibana 自動ログインが成功するため、CI（env var なし）では再現しなかった。
   dev 環境固有の挙動（自動ログイン等）が他モードに影響する可能性を、env var 設定時のシナリオとして
   テストに含める。

**追補（2026-04-30 レビュー指摘）**: 同一根本原因に対する「第 2 経路」の取りこぼしを追加修正:

- `_handle()` の post-handshake 自動起動ガードだけでは不十分で、
  ユーザー明示の `RequestVenueLogin`（`server.py::_do_request_venue_login`）も replay モードで
  同じ `_startup_tachibana()` を spawn する経路だった。`replay_api.rs` の
  `POST /api/sidebar/tachibana/request-login` HTTP 経由で UI / 自動化スクリプトが
  踏むと、replay でも 278 KB → RSV ビット切断を再現する
- 修正: `_do_request_venue_login()` 冒頭にも `if self._mode == "replay":` ガードを追加し、
  `VenueError{code:"mode_mismatch"}` で拒否する
- 追加テスト: `test_server_engine_dispatch.py::TestRequestVenueLoginModeGuard`
  （reject in replay + allow in live の対照 2 件）
- **教訓 4 (新規)**: 「同じ起動関数を呼ぶ経路がコード内に複数あるか」を必ず grep で確認する。
  `grep -n 'asyncio.create_task(self\._startup_tachibana' python/engine/server.py`
  のような全 spawn 箇所列挙を bug-fix チェックリストに含める。1 箇所だけ修正して
  「mode 分岐漏れ」パターンに対応した気になる罠を避ける。

**追補 (2026-04-30, 大型フレーム RSV bit 経路の pin)**:

278 KB → RSV ビット切断の根本（fastwebsockets が permessage-deflate を実装せず
RSV1=1 フレームを拒否する性質）に対するリグレッションを 2 層で保護:

1. `python/tests/test_server_ws_compat.py::test_large_frame_payload_does_not_set_rsv1`
   — outbox に 320 KB の合成フレームを直接 inject し、`compression=None` クライアントが
   ProtocolError なく受信できることを assert（IPC pipeline contract）
2. `tests/e2e/smoke.sh` に `Reserved bits are not zero` を独立 fail trigger として追加
   （既存 `engine ws read error` の prefix に依存しないシグネチャ pin）

将来 venue が増えて bulk stats response が 1 MB 級になっても、live モード単体で
回帰検出できる。

---

## 2026-04-30 — HONDA 注文ボタンを押しても確認ダイアログが画面に出ない

**見逃しパターン**: 「実装済み≠配線済み」の view() 版（分岐別オーバーレイ配線漏れ）

**不具合の概要**:
`flowsurface --mode live` で HONDA(7267) を OrderEntry パネルで選択し「注文」を押すと、
`panel.update(SubmitClicked)` は正しく `Action::RequestConfirm` を返し、
`main.rs::update()` が `self.confirm_dialog = Some(dialog)` をセットするが、
次フレームの `view()` で確認ダイアログが画面に現れない。WAL にも書き込まれない。

**根本原因**:
`main.rs::view()` が 2 つのパスを持つ：

```rust
if let Some(menu) = self.sidebar.active_menu() {
    self.view_with_modal(base.into(), dashboard, menu)  // confirm_dialog あり
} else {
    base.into()  // ← confirm_dialog オーバーレイなし（バグ）
}
```

`view_with_modal()` 内の `Settings` / `Network` / `Order` ブランチでのみ
`confirm_dialog` を `main_dialog_modal` でオーバーレイしており、
通常ダッシュボード（サイドバーメニュー非アクティブ）の `else` ブランチでは
オーバーレイ描画が実装されていなかった。

注文フローの `Action::RequestConfirm` → `ConfirmDialog` のセットは完成していたが、
描画パスの配線が一部分岐にしかなかった（MISSES.md 2026-04-27「実装済み≠配線済み」の view() 版）。

**なぜ既存テストで発見できなかったか**:

| テスト | 見逃した理由 |
|--------|------------|
| `order_entry.rs::tests` | `panel.update()` → `Action::RequestConfirm` 戻り値のみ検証。`view()` の描画ツリーは範囲外 |
| `cargo test --workspace` | iced の `Element` ツリー構造を単体テストで検証する標準手段がない |
| `python/tests/` | Rust の描画層は Python から確認不可 |
| `tests/e2e/smoke.sh` | 「モーダルが画面に出たか」を grep で検出できない |

**修正**:
1. `apply_confirm_dialog_overlay<'a>()` フリー関数を追加し `content: Element` + `dialog: Option<&ConfirmDialog>` を受け取って純粋に変換する（`second_password_modal` と同じパターン）
2. `view()` 内で `raw_content` → `apply_confirm_dialog_overlay` → toast → `second_password_modal` の順で適用（全分岐を単一出口でラップ）
3. `view_with_modal()` の各ブランチから個別のオーバーレイ呼び出しを除去

**追加したテスト**:
- `src/main.rs::confirm_dialog_overlay_tests::helper_apply_confirm_dialog_overlay_exists`
  — `fn apply_confirm_dialog_overlay` がソースに存在することを `include_str!` + `contains` で検証
- `src/main.rs::confirm_dialog_overlay_tests::view_calls_confirm_dialog_overlay_helper`
  — `Flowsurface::view()` 本体内で `apply_confirm_dialog_overlay(` を呼ぶことを検証（`\n    fn ` 境界で view ボディを分割）
- `src/main.rs::confirm_dialog_overlay_tests::view_with_modal_branches_no_longer_redraw_overlay`
  — production コードで `confirm_dialog_container(` の呼び出し箇所が厳密に 1 箇所であることを検証（test モジュールを `split_once` で除外して自己参照を回避、`expect()` で marker 消失を検出）

**リグレッション確認**:
- `view()` を元の `else { base.into() }` に戻すと test 2 が FAIL する
- `view_with_modal()` に per-branch overlay を復元すると test 3 が `count > 1` で FAIL する

**教訓**:

1. **view() 分岐の一部だけにオーバーレイを配線するパターンは将来に向けた爆弾**:
   `view()` や `view_with_modal()` のような複数分岐を持つ描画関数でモーダルを一部分岐のみに書くと、
   他の分岐で `state = Some(...)` がサイレントに無視される。
   新しいモーダル（`second_password_modal` / `confirm_dialog` 等）を追加するときは
   **単一出口（`view()` の最終段）でラップ**することを設計原則にする。

2. **iced の view() は直接ユニットテストしにくい — ソース文字列テストで代替**:
   `Element` ツリーの構造を `cargo test` で検証する汎用手段は現時点で存在しない。
   `include_str!("./main.rs")` + 文字列マッチング・境界分割でオーバーレイの配線を
   構造的に保護するアプローチが有効。テスト sentinel の `expect()` で marker 消失も検出する。

3. **同じ状態変更 (`Some(dialog)` のセット) を複数パスの描画関数が独立して処理するなら単一責任に統一**:
   「state を Some にする → どの分岐でも描画」という流れは、描画コードが 1 箇所に集約されているときにのみ保証される。
   複数分岐が独立してオーバーレイを呼ぶ設計は、新しい分岐の追加時に必ず漏れが出る。

---

## 2026-04-30 — streaming replay の fill IPC emit パスがテスト未検証のまま dead code になっていた

**見逃しパターン**: No-op fixture 戦略 → 発注パス未到達 → IPC emit 経路が dead code

**不具合の概要**:
`POST /api/replay/start` 後、`ExecutionMarker` / `ReplayBuyingPower` が Rust 側に
一切届かない。UI の OrderList・BuyingPower ペインが空のままになる。

**根本原因**:
`NautilusRunner.start_backtest_replay_streaming()` が nautilus の `OrderFilled`
イベントを購読していなかった。`NarrativeHook._emit_execution_marker()` と
`PortfolioView` はコード上は存在していたが、streaming バックテスト経路では
一度も呼ばれていなかった（dead code）。

**なぜ既存テストで発見できなかったか**:

| テスト | 見逃した理由 |
|--------|------------|
| `test_engine_runner_replay.py`（全 25 件） | fixture 戦略として `NoOpTestStrategy`（`on_start` も `on_bar` も実装なし）を使用。1 件も発注しないため fill 経路に到達しない |
| 全 streaming 系テスト共通 | 「発注する戦略」でのシナリオがゼロ。`ExecutionMarker` / `ReplayBuyingPower` が emit されることを assert するテストが存在しなかった |
| pydantic スキーマテスト | スキーマ型は定義済みだが、実際に emit されるかどうかの integration テストがなかった |

**修正**:
1. `engine_runner.py::start_backtest_replay_streaming()` で `engine.kernel.msgbus` に
   `f"events.fills.{instrument_id}"` トピックを subscribe し、`OrderFilled` ごとに
   `ExecutionMarker` → `ReplayBuyingPower` を `on_event` コールバックに push する
2. `python/tests/fixtures/fill_strategy.py` — bar 1 で BUY、bar 2 で SELL する
   決定論的テスト戦略（2-bar fixture データと対応）
3. `python/tests/test_engine_runner_streaming_fills.py` — 15 件の integration テスト
   （emit 件数・フィールド完全一致・残高変動・決定論性・pydantic スキーマ検証）

**追加したテスト**:
- `TestStreamingFillsEmitExecutionMarker` (6 件): 1:1 発火・side 大文字・余分フィールドなし・
  instrument_id・price が decimal str・ts_event_ms が int
- `TestStreamingFillsEmitReplayBuyingPower` (7 件): fill ごとに 1 件・スキーマ完全一致・
  BUY で cash 減少・SELL で cash 増加・strategy_id 一致・2 run で ts_event_ms が同値（決定論性）・
  ExecutionMarker と ReplayBuyingPower が同じ ts_event_ms を共有
- `TestMsgbusTopic` (1 件): `f"events.fills.{str}"` と `f"events.fills.{InstrumentId}"` が
  一致することを pin（nautilus の `InstrumentId.__str__` 形式変更の早期検知）
- `TestStreamingFillsPassPydanticSchema` (1 件): emit された全イベントが pydantic `model_validate` を通過

**リグレッション確認**: `msgbus.subscribe()` 行を削除した状態で
`test_emits_one_execution_marker_per_fill` が FAIL（markers == 0）、
追加した状態で PASS することを実際に確認。

**教訓**:

1. **「発注する fixture 戦略」を常に用意する**: replay / backtest 系テストで
   「戦略が動いた」ことを検証するためには、実際に発注・約定する fixture 戦略が必要。
   `NoOpTestStrategy`（発注しない）は「エンジンが起動する」「ストリームが流れる」の
   テストには有効だが、fill 経路・IPC emit 経路の検証には役立たない。
   新たな fill 系イベントを実装したら、必ず **発注する戦略** でのシナリオを追加すること。

2. **dead code の温床: 「コードはある」≠「経路が通る」**: `NarrativeHook` や
   `PortfolioView` のように実装が存在しても、呼び出し経路が繋がっていなければ
   全て dead code になる。新機能実装後は「このコードへ到達する経路はどこか」を
   integration テストで必ず確認する。

3. **IPC emit の contract テストは emit されることを assert する**: pydantic スキーマが
   定義されていても「実際に emit されるか」を保証するテストは別途必要。スキーマ型の
   定義テストと emit 件数テストは直交する。両方書く。

4. **ts_event はデータ由来にする（`time.time()` ではなく）**: `ReplayBuyingPower.ts_event_ms`
   を `time.time()` で付与すると、同一入力でも実行タイミングで値が変わる。
   `OrderFilled.ts_event` （nanosec → `// 1_000_000` で ms）を使えば決定論的になり、
   テストで「2 回 run して同じ値が出る」ことを assert できる。

---

## 2026-04-30 — 注文確定・起動時に注文一覧・買余力が自動更新されない（UX 改善）

**見逃しパターン**: モード分岐漏れ（モード境界ガードの認識不足）

**不具合の概要**:
`OrderAccepted` 受信後に注文一覧・買余力の手動「更新」ボタンが必要だった（UX 課題）。
また起動時（`VenueReady` 受信）に買余力は自動取得されていたが、注文一覧は手動更新が必要だった。

**根本原因**: UX 機能の不足（バグではない）。以下 2 箇所に IPC 自動発行を追加:
1. `Message::OrderAccepted` ハンドラ: `GetOrderList` + `GetBuyingPower` を自動発行
2. `Message::VenueReady` ハンドラ: `GetOrderList` を自動発行（`GetBuyingPower` は既存）

**最重要の安全ガード**:
Python の replay バックテストも `OrderAccepted` を emit するため、`OrderAccepted` ハンドラの
自動 IPC 発行には `tachibana_state.is_ready()` ガードが必須。このガードを外すと
replay モードでも `GetOrderList`/`GetBuyingPower` が Tachibana API に向けて送信される。

**なぜユニットテストを追加しなかったか**:

| 理由 | 説明 |
|------|------|
| Iced Task 検査不可 | `update()` の戻り値（`Task<Message>` ツリー）を外部から検査する API が存在しない。副作用として何が spawn されたかを単体テストで assert する手段がない |
| ガードの明確さ | `if !self.tachibana_state.is_ready() { return Task::none(); }` は読んで意図が明確 |
| E2E で代替可能 | replay モードの smoke テストが「GetOrderList IPC が出ないこと」を間接的に保証する |

**教訓**:

1. **live 専用 IPC 自動発行には必ず `tachibana_state.is_ready()` ガードを書く**:
   replay バックテストは `OrderAccepted` を含む多くの live 系イベントを emit する。
   `update()` ハンドラで live 専用 IPC（`GetOrderList`, `GetBuyingPower` 等）を追加するときは、
   **必ず** `tachibana_state.is_ready()` または `self.mode == AppMode::Live` で早期 return すること。

2. **`has_*_pane()` ガードの設計指針**:
   - `VenueReady` 起動時の自動取得: ペインが表示中のときのみ発行（`has_order_list_pane()` ガードあり）
   - `OrderAccepted` 後の自動更新: ペインの有無によらず発行（後からペインを追加しても即反映するため）
   この非対称性は意図的な設計判断。

3. **Iced の update() 戻り値テスト戦略**:
   `Task<Message>` の副作用は直接テストできない。代替として:
   - ソース文字列テスト（`include_str!` + `contains`）で「コードが存在するか」を pin する
   - E2E ログ確認（debug ビルドで `[ipc] → GetOrderList` が出るか目視）
   - `tachibana_state.is_ready()` のような「ガードが存在するか」を文字列テストで pin する

4. **pane-added catch-up は別タスク**:
   `VenueReady` 後に `OrderList` ペインを後から追加した場合の catch-up 経路は今回スコープ外。
   `BuyingPower` には `main.rs:2761` 付近に pane-added catch-up 経路が存在するが、
   `OrderList` は起動時 auto-fetch のみ（ペイン追加時は手動「更新」ボタンが引き続き必要）。

---

## 2026-04-30 — streaming replay 注文一覧が常に空（注文なし）

**見逃しパターン**: 同一言語テスト + イベント経路盲点（WAL 前提の実装が streaming には無効）

**不具合の概要**:
`replay` モードで sma_cross.py を実行すると買余力パネルには残高変動が表示されるが、
注文一覧（OrderList）には「注文なし」と表示され、約定履歴が一切反映されなかった。

**根本原因（3 層）**:

1. **Rust: venue 固定バグ**
   `Action::RequestOrderList` ハンドラが venue を `"tachibana"` に固定していた。
   Python の `_do_get_order_list("tachibana")` は replay セッションが存在しないため
   常に空を返す。`APP_MODE` を参照して replay 時は venue `"replay"` を送るべきだった。

2. **Python: WAL 前提の設計**
   `_do_get_order_list_replay()` は WAL ファイル `tachibana_orders_replay.jsonl` を読んでいたが、
   streaming replay の約定は nautilus BacktestEngine の内部で完結するため WAL には書かれない。
   「約定があれば WAL に記録される」という前提が streaming 経路では成立しない。

3. **Python: auto-refresh トリガー不在**
   `EngineStopped` イベントが Rust の `Message` に対応していなかった。
   replay 完了後に自動で `GetOrderList` IPC を送る仕組みがなかった。

**修正内容**:
- `engine_runner.py`: `ExecutionMarker` emit に `qty` フィールドを追加
- `server.py`: `_on_event_tracked` closure で `ExecutionMarker` を `_replay_streaming_fills` に蓄積。
  `EngineStarted` 受信時に `clear()` して前回 fills を除去
- `server.py`: `_do_get_order_list_replay` で `_replay_streaming_fills` が非空なら WAL を読まずそれを返す
- `engine-client/src/dto.rs`: `ExecutionMarker` に `qty: Option<String>` を追加（後方互換）
- `src/main.rs`: `Action::RequestOrderList` で `APP_MODE` 参照、replay 時は venue `"replay"`
- `src/main.rs`: `Message::ReplayFinished` を追加し `EngineStopped` をマップ。
  `ReplayFinished` ハンドラで `GetOrderList{venue:"replay"}` を自動発行

**追加したテスト**:
- `python/tests/test_replay_streaming_order_list.py`（7 テスト）:
  - `TestStreamingFillsViaGetOrderList`: fills が OrderListUpdated に含まれる
  - streaming fills が空なら WAL fallback、非空なら WAL を無視
  - `TestStreamingFillsAccumulatedViaOnEventTracked`: `_on_event_tracked` の蓄積・クリア動作
- `python/tests/test_engine_runner_streaming_fills.py`: `qty` フィールド追加に伴い
  `test_execution_marker_has_no_extra_fields` → `test_execution_marker_has_required_fields` +
  `test_execution_marker_qty_is_positive_decimal_string` に更新

**教訓**:

1. **streaming 約定は WAL に書かれない**:
   nautilus `BacktestEngine` の内部約定は IPC 経由で submit されていないため WAL に残らない。
   streaming replay の「注文履歴」は IPC イベント（`ExecutionMarker`）をリアルタイムで蓄積する
   インメモリリストが唯一の信頼できるソースである。
   
2. **replay モードの venue 分岐は漏れやすい**:
   `GetOrderList` のような IPC コマンドに venue 文字列が入るとき、replay 固有の venue（`"replay"`）
   への分岐を忘れると live 専用経路に落ちて常に空が返る。
   `APP_MODE.get()` を参照して replay 時は venue を切り替えること。

3. **EngineStopped を Rust Message に対応させる**:
   replay 完了後の UI 更新（注文一覧の自動更新など）は `EngineStopped` → `Message::ReplayFinished`
   のマッピングが必要。新しい replay 後アクションを追加するときはここを起点にすること。

4. **Python-only テストは Rust 側の venue 固定を検出できない**:
   Python の `_do_get_order_list_replay` をテストしても、Rust が `venue="tachibana"` を送って
   いることは検出できない。言語境界を跨ぐ integration 確認（ログ目視または E2E）が必要。

---

## 2026-04-30 — テストヘルパー `_make_server()` の属性リストが `__init__` と乖離し AttributeError

**見逃しパターン**: テストヘルパー属性ドリフト（prod `__init__` 追加 → test helper 未同期）

**不具合の概要**:
`test_server_engine_dispatch.py` の `_make_server()` ヘルパーは `DataEngineServer.__init__`
をモックで迂回し、属性を `_REQUIRED_ATTRS` + `defaults` から手動で設定する。
`server.py` に `self._replay_streaming_fills: list[dict] = []` を追加したとき、
`_REQUIRED_ATTRS` と `defaults` に対応エントリを追加しなかった。
`fake_streaming` → `_on_event_tracked` → `self._replay_streaming_fills.clear()` を呼ぶと
`AttributeError: 'DataEngineServer' object has no attribute '_replay_streaming_fills'` で
4 テストが FAIL した。

**修正内容**:
- `test_server_engine_dispatch.py`: `_REQUIRED_ATTRS` に `"_replay_streaming_fills": None` を追加
- 同 `defaults` に `"_replay_streaming_fills": []` を追加

**既存テストが見逃した理由**:
`__init__` のモック迂回は意図的な設計（M-9 コメントに同期要件を記載）だが、
`_replay_streaming_fills` は `_on_event_tracked` closure の実行パス上にあり、
closure を呼ぶテスト追加まで `AttributeError` が露顕しなかった。
新属性が「テストで実際に使われる経路上にない」ときは沈黙のまま不一致が蓄積する。

**追加したテスト**:
既存の `test_replay_mode_calls_streaming` が今回の修正リグレッションガードとなる
（`_replay_streaming_fills` を `_REQUIRED_ATTRS` から外すと即 AttributeError で FAIL）。
追加テストは不要と判断。

**教訓**:
- `__init__` をモックで迂回するテストヘルパーを持つクラスに新属性を追加するときは、
  必ずヘルパー内の属性リスト（`_REQUIRED_ATTRS` 等）を同時に更新すること。
- M-9 パターン（`_REQUIRED_ATTRS` の集中管理）は「新属性が既存テストの実行パスに乗るまで
  検出されない」という遅延検出の弱点を持つ。属性を追加したコミット時点で ヘルパーの
  `_REQUIRED_ATTRS` を diff 確認する習慣を持つこと。

---

## 2026-04-30 — `docs/example/buy_and_hold.py` が存在せず 12 テストが FileNotFoundError

**見逃しパターン**: 参照リソース未作成（test が fixture/example ファイルの存在を前提とするが未コミット）

**不具合の概要**:
`test_strategy_live_replay_smoke.py`・`test_nautilus_determinism.py`・
`test_strategy_compat_lint.py` が `docs/example/buy_and_hold.py` を
`strategy_file` として参照していたが、ファイルが存在しなかった。
`FileNotFoundError: strategy file not found` で 12 テストが FAIL した。

**修正内容**:
- `docs/example/buy_and_hold.py` を新規作成（BuyAndHoldStrategy: 最初の bar で成行買い・保有継続）

**既存テストが見逃した理由**:
テストコードと対応するリソースファイルが同一コミットで追加されなかった。
テストは先に書かれ（または過去バージョンで存在した後に削除され）、
ファイルが未作成のまま TEST スイートに残った。
`pytest` は import エラーではなく実行時 `FileNotFoundError` で落ちるため、
CI の「コレクション」フェーズでは発見されず個別テスト実行時まで気づかない。

**追加したテスト**:
`test_strategy_compat_lint.py::test_example_buy_and_hold_replay_compat_lint` が
存在確認を兼ねたリグレッションガードになっている（ファイルを削除すると FAIL）。

**教訓**:
- `strategy_file` など外部リソースを参照するテストは、対応ファイルと **同一 PR/コミット** で
  追加すること。
- テストが参照するファイルパスを `git grep` で検索し、実ファイルの存在を PR マージ前に確認する。
- `docs/example/` のような「例示コード」ディレクトリは、テストが参照するファイルが揃っているか
  定期的に棚卸しする。
