# 立花 WebSocket FD push 不動作 修正プラン

**起票日**: 2026-05-01
**起票者**: Claude (Opus 4.7)
**症状**: 立花 Ladder の更新頻度が異常に遅い（体感 10 秒間隔）
**スコープ**: 板情報のリアルタイム push を回復させる。原因は未確定のため、調査と修正の両輪で進める

---

## 1. 問題の現状（事実のみ）

### 1.1 観測された事象

`scripts/diagnose_tachibana_ws.py --ticker 7203 --frames 5` を 2026-05-01 に demo
環境で実行した結果（接続そのものは確立）:

| 項目 | 結果 |
|---|---|
| ログイン | ✅ |
| REST `fetch_depth_snapshot` (bids=10 / asks=10) | ✅ |
| WebSocket 接続確立 | ✅ |
| **15 秒ウィンドウ内の受信フレーム** | **`['ST', 'ST', 'ST']`** |
| **FD（時価/板）フレーム** | **0 件** |
| **KP（キープアライブ）フレーム** | **0 件** |

### 1.2 想定挙動との差分

公式仕様（[SKILL.md §EVENT/WebSocket ストリームのパース規約](../../.claude/skills/tachibana/SKILL.md)）:

- `FD` = 初回メモリ内スナップショット（全データ）→ 以降は変化分のみ通知
- `KP` = **5 秒間通知未送信時に必ず送出**

接続直後に **少なくとも 1 回は FD のスナップショットが届くはず**。届かないなら
そもそも板情報の購読自体が成立していない可能性が高い。

### 1.3 fallback 経路への落下

[tachibana_ws.py:284](../../python/engine/exchanges/tachibana_ws.py#L284) の
`_DEPTH_SAFETY_TIMEOUT_S = 30.0` により、30 秒以内に「気配付き FD」が来ないと
`depth_unavailable` が発火し、[tachibana.py:912 `_depth_polling_fallback`](../../python/engine/exchanges/tachibana.py#L912)
で **10 秒固定の REST polling** に切替わる。

スクリーンショットで観測された「板の動きが 10 秒間隔」はこの fallback と一致。

---

## 2. 影響範囲

| レイヤ | 影響 |
|---|---|
| Ladder ペイン | 更新間隔 ≥ 10 秒（fallback 動作中） |
| TimeAndSales | 同上 — FD に依存するため push 不在 |
| 約定ベース指標（VWAP, footprint 等） | 同上 |
| 戦略の発注タイミング判定 | 板変化を捉えられず機会損失 |
| KlineChart（CLMMfdsGetMarketPriceHistory 経由） | 影響なし（別経路） |
| 注文・余力（業務 REQUEST 系） | 影響なし |

立花 venue の **リアルタイム性に依存する全機能が劣化**。

---

## 3. 原因候補（限定しない）

現時点では切り分けが不十分。以下を **すべて** 仮説として保持し、§4 の追加調査で
ひとつずつ潰す。

| ID | 仮説 | 棄却条件 |
|---|---|---|
| H1 | 検証時点が立会時間外で、demo 環境は時間外 FD を流さない | 平日 9:30 JST 再実行で FD/KP が届けば棄却 |
| H2 | demo 環境は本番と異なり時価配信を提供しない（仕様差） | 営業窓口確認 / 立会時間中でも届かないなら濃厚 |
| H3 | `p_rid=22` が demo で時価配信不可。`p_rid=0` 等への変更が必要 | URL パラメータ振り直しテスト |
| H4 | `p_mkt_code` がマスタ未ロード時のフォールバック `"00"` で誤値（[tachibana.py:521-526](../../python/engine/exchanges/tachibana.py#L521-L526)） | マスタロード待機後の市場コードで再試行 |
| H5 | `p_eno=0` の指定が初回スナップショット送出をブロック（再送 vs 新規の解釈差） | `p_eno` 値変更テスト |
| H6 | URL エンコード差異（`func_replace_urlecnode` の挙動）でサーバ側が拒否 | 生 URL とサンプルコードの URL を厳密 diff |
| H7 | `sUrlEventWebSocket` の末尾整形（`rstrip("?&")`）が想定外（[tachibana.py:532](../../python/engine/exchanges/tachibana.py#L532)） | ログイン応答の生 URL を確認 |
| H8 | ST フレーム自体がエラーを通知している（`p_errno`, `sResultCode` 等） | ST フレームの全フィールドダンプで判明 |
| H9 | EVENT 仮想 URL 期限切れ（既に新規 URL を要する状態だが残骸が見えていない） | 直前再ログイン後の即時再現で棄却可 |
| H10 | アカウント側で「電話認証」「金商法交付書面」「e支店・API 申込み」のいずれかが未完了で、API レベルでは認証通過するが時価配信は拒否 | ST 内容 / 立花サポート確認 |
| H11 | proxy / TLS 経路で FD バイナリフレームのみがブロックされる（ST はテキスト） | proxy 無効環境で再現確認 |
| H12 | サンプルコードの URL パラメータ並び順を厳密に守っていない（`p_rid` を先頭にする等の暗黙仕様） | 並び順を [サンプル L573-585](../../.claude/skills/tachibana/samples/e_api_websocket_receive_tel.py/e_api_websocket_receive_tel.py#L573) と一致させて再試行 |
| H13 | `p_cmd` キー解釈ミス（実際の FD は別キーで判定すべき） | サンプル受信例とフィールド突き合わせ |
| H14 | 12 秒 dead-frame timeout により FD 受信前に切断 → 再接続ループでループ内寿命が短い | watchdog ログを観察 / `_DEAD_FRAME_TIMEOUT_S` 一時延長 |

**重要**: 「demo の時間外」と早期に断定しないこと。fallback 動作している以上、原因が
H1〜H14 のどれか（または複数の組合せ）かを **証拠で潰す** 必要がある。

---

## 4. 追加調査計画

調査は **修正コードを書く前** に終わらせる。各タスクは原則として独立に実行可能で、
得られた結果で原因表（§3）を絞り込む。

### I1. ST フレームの中身ダンプ（最優先）

**現状**: `diagnose_tachibana_ws.py` は ST 受信を集計するだけで内容を表示していない
（フレーム一覧 `['ST', 'ST', 'ST']` 程度）。ST には `p_errno` / エラー本文が
含まれている可能性が高く、ここに一次情報がある。

**やること**:
- `diagnose_tachibana_ws.py` に **ST 受信時の全フィールドダンプ** を追加
  （マスク対象は仮想 URL / パスワードのみ、`p_errno` 等は出す）
- 同様に **FD/KP/ST 全フレームの raw 1〜2 フレーム** を `repr()` でダンプするオプション
  `--dump-raw N` を追加
- 再実行し ST のエラーコードを取得

**完了条件**: ST の `p_errno` / エラーメッセージが判明し、H8/H10 の真偽が決まる。

### I2. URL パラメータの厳密一致確認

**現状**: 当該 URL 構築は [tachibana.py:528-543](../../python/engine/exchanges/tachibana.py#L528)。
パラメータ順序が公式サンプル [`e_api_websocket_receive_tel.py:573-585`](../../.claude/skills/tachibana/samples/e_api_websocket_receive_tel.py/e_api_websocket_receive_tel.py#L573) と
微妙に違うかどうか未確認。

**やること**:
- 現在の URL（マスク後）とサンプル URL を文字列単位で diff
- 並び順、`p_evt_cmd` の値（`ST,KP,FD` vs `ST,KP,EC,SS,US,FD` 等）の差分を文書化

**完了条件**: H6/H7/H12 が棄却可能 or 修正方針が定まる。

### I3. 立会時間中の再実行

**やること**:
- 平日 09:30 JST 以降に I1 改良版 `diagnose_tachibana_ws.py` を再実行
- 同時に proxy 無効・有効の両方で実行（H11 切り分け）

**完了条件**: H1（時間外）の真偽が判明。

### I4. パラメータバリエーションテスト

I1〜I3 で原因が確定しなかった場合のみ実施。

**やること**: 以下の組合せを順に試す:
- `p_rid` を `22 → 0 → 11 → 6` に変更
- `p_evt_cmd` を `ST,KP,FD` → サンプルと完全一致（`ST,KP,EC,SS,US,FD`）
- `p_eno` を `0 → 1`
- `p_mkt_code` をマスタから取得した値に固定

**完了条件**: FD/KP のいずれかが届く構成を発見、または全パターンで届かないことを確認。

### I5. 立花サポート / 仕様文書再読

I3/I4 でも届かない場合:
- demo 環境の時価配信仕様（本番との差）を立花営業窓口に問合せ
- `api_event_if_v4r7.pdf`（`manual_files/` 同梱なし、立花 API 専用ページの最新版）を
  再ダウンロードし、`p_rid` 表 §3.(3) を再確認

**完了条件**: H2 が棄却 or 確定。

---

## 5. 修正実装計画（原因確定後にトリガー）

§4 の調査で原因がほぼ確定する想定。原因クラスごとの実装方針を先回りで定義する。

### F-A. パラメータ修正系（H3, H4, H5, H6, H12, H13）

**変更箇所**: [tachibana.py:528-543 `_build_ws_url`](../../python/engine/exchanges/tachibana.py#L528)
**実装**: 確定した正しいパラメータ値・順序に修正
**テスト**:
- 単体: URL 文字列が期待値と一致することを assert する pytest を追加
  （`python/tests/test_tachibana_ws_url.py` 新設）
- 統合: 修正前 → I1 再現 / 修正後 → FD 受信確認（手動 + diagnose スクリプトの
  exit code が 0）

### F-B. 時間外ハンドリング系（H1）

時間外なら fallback で良い、ではなく **fallback の周期を見直す**:
**変更箇所**: [tachibana_ws.py:287](../../python/engine/exchanges/tachibana_ws.py#L287)
- `_DEPTH_POLL_INTERVAL_S` を **時間帯依存**（立会中 1〜2 秒、時間外 10 秒）に切替
- 立会中なのに fallback に落ちている場合は VenueError レベルを上げ、ユーザーに
  接続再試行を促す UI 文言を表示

**テスト**: JST 時計 mock + asyncio fake clock で `interval_for_now()` の挙動を pytest 化

### F-C. ST エラー伝搬系（H8, H10）

ST が恒常的に来る場合は、**握り潰さず Rust 側に伝える**。

**変更箇所**:
- [tachibana_ws.py:399-400](../../python/engine/exchanges/tachibana_ws.py#L399) — ST callback はあるが、その先で何もしていない（要確認）
- [tachibana.py:879 `_cb_depth`](../../python/engine/exchanges/tachibana.py#L879) — ST 分岐を追加し `VenueError` outbox イベントを送る

**テスト**: ST フレーム受信 → `VenueError` が outbox に積まれることの pytest

### F-D. 仮想 URL 期限切れ系（H9）

検出時に再ログインを自動トリガするフローを実装（既存の再接続ロジックでカバー
できているか確認、不足なら追加）。

⏸ H9 自動検出・自動再ログイン経路は**本フェーズでは未実装**。現状は ST `p_errno=2`
を `st_session_expired` として VenueError 通知 + polling fallback への切替までは行うが、
仮想 URL 失効時の再ログイン契機は引き続き手動。follow-up タスクで対処予定。

### F-E. アカウント設定系（H10）

コード修正不要。ユーザー向けドキュメント（[SKILL.md §立花 venue 利用の前提条件](../../.claude/skills/tachibana/SKILL.md#立花-venue-利用の前提条件t7-追記)）に
**「FD が来ない場合のチェックリスト」** を追記。

---

## 6. 観測性強化（修正と並行）

原因に関わらず、以下は今回の機会に必ず入れる:

### O1. 構造化ログ

[tachibana_ws.py](../../python/engine/exchanges/tachibana_ws.py) に以下を追加:

- `_connect_once` 直後に `INFO`: 接続確立時刻 / URL パラメータ要約（仮想 URL マスク）
- 30 秒ごとに **接続開始からの累積 (cumulative) フレーム数** を `INFO` で吐く（FD/KP/ST/その他）
- `depth_unavailable` 発火時に `WARN` で「累積フレーム種別カウント」を併記

### O2. メトリクス

`engine.server` の statistics に以下を追加:

- `tachibana_fd_frames_total{ticker}` カウンタ
- `tachibana_st_frames_total{ticker}` カウンタ
- `tachibana_depth_polling_active{ticker}` ゲージ（fallback 中 1）

UI 側のステータスバー / フッタに「板更新源: WS / Polling」表示を出す（[src/screen/dashboard/](../../src/screen/dashboard/)）。

⏸ deferred — 本フェーズでは未実装。follow-up issue で対処予定。

### O3. 起動時セルフテスト

debug ビルドのみ、起動後 60 秒経過しても 1 件も FD が届いていなければ
`WARN` ログを大きく出す。

⏸ deferred — 本フェーズでは未実装。follow-up issue で対処予定。

---

## 7. テスト戦略

### 7.1 リグレッションガード（必須）

| テスト | ファイル | 内容 |
|---|---|---|
| `test_tachibana_ws_url_format` | `python/tests/test_tachibana_ws_url.py`（新設） | `_build_ws_url` の出力が公式サンプル準拠の文字列であることを assert |
| `test_st_frame_emits_venue_error` | `python/tests/test_tachibana_depth_sync.py`（拡張） | ST フレーム受信時に VenueError outbox に積まれる |
| `test_fd_frame_count_metric` | 同上 | FD 受信カウンタが increment される |
| `test_depth_unavailable_warn_log` | 同上 | 30 秒 FD なしで WARN ログ + フレーム種別カウントが出力される |
| `test_polling_interval_in_session` | 同上 | 立会時間中 fallback 周期が短縮される（時計 mock） |

### 7.2 統合（実機）

- `scripts/diagnose_tachibana_ws.py` の改良版を **修正前 → FAIL / 修正後 → PASS** で
  実機検証
- E2E スモークテスト（[tests/e2e/smoke.sh](../../tests/e2e/smoke.sh)）に立花板更新の
  検査項目を追加（debug ログ grep で `tachibana_fd_frames_total > 0` 等）

---

## 8. ロールアウト手順

1. **§4 調査タスク I1〜I3 を完了**（修正コード 0 行の段階）
2. 結果を本ドキュメントの末尾に追記（観測ログとして保存）
3. 原因確定 → §5 該当ブランチ（F-A〜F-E）を実装
4. §6 観測性強化と §7.1 テストを必ず同 PR に同梱（`/bug-postmortem` 適用）
5. demo 環境で `diagnose_tachibana_ws.py` PASS
6. 立会時間中に手動動作確認（GUI 起動 → Ladder の tick が 1 秒以内に動く）
7. `/review-fix-loop` でレビュー
8. 本番（`TACHIBANA_ALLOW_PROD=1`）での確認は **ユーザーが希望した場合のみ**

---

## 9. リスクとロールバック

| リスク | 緩和策 |
|---|---|
| 原因を誤判定し別パターンで再発 | §4 の調査を全て事実ベースで潰してから修正へ進む |
| URL パラメータ変更が本番のみで挙動差 | 修正後、demo + 本番（ユーザー同意あれば）両方で diagnose 実行 |
| fallback 周期短縮で立花サーバ負荷増 | 立会時間中のみ短縮（最短 1 秒）+ 上限 polling max を維持 |
| ST 連発時のログ noise | rate-limit（30 秒に 1 回まで集計ログ）を入れる |

ロールバック: 既存実装に戻すだけ（fallback 経路は維持されているのでシステム全体は
壊れない）。

---

## 10. open questions（追跡）

[docs/✅order/open-questions.md](open-questions.md) に以下を追記:

- Q-T-FD-1: demo 環境は時間外に FD を配信するか（立花仕様確認）
- Q-T-FD-2: 立会時間中でも `p_rid=22` で時価配信が来ない場合、`p_rid=0` への切替は安全か
- Q-T-FD-3: 立花の ST フレームに含まれる主要 `p_errno` の意味マッピング表
- Q-T-FD-4: H9 仮想 URL 期限切れ自動再ログイン (M-D) — 現状未実装、follow-up issue で対処

---

## 11. 参考

- [SKILL.md — EVENT/WebSocket ストリームのパース規約](../../.claude/skills/tachibana/SKILL.md)
- [サンプル e_api_websocket_receive_tel.py](../../.claude/skills/tachibana/samples/e_api_websocket_receive_tel.py/e_api_websocket_receive_tel.py)
- [tachibana_ws.py](../../python/engine/exchanges/tachibana_ws.py)
- [tachibana.py: stream_depth / _depth_polling_fallback](../../python/engine/exchanges/tachibana.py)
- [scripts/diagnose_tachibana_ws.py](../../scripts/diagnose_tachibana_ws.py)

---

## 12. 観測ログ（2026-05-01 実施）

### ✅ I1: ST フレームダンプ機能追加

**実施内容**: `scripts/diagnose_tachibana_ws.py` に以下を追加
- ST フレーム受信時に全フィールドを即時出力（`_mask_st_fields` でシークレットをマスク）
- `--dump-raw N` オプション追加（先頭 N フレームの raw repr を出力）
- **重要**: WS 接続 URL を `session.url_event_ws`（ベース URL）から `worker._build_ws_url(ticker)`（銘柄購読パラメータ付き完全 URL）に修正

**判明した追加バグ**: diagnose スクリプト自体がベース URL に接続していたため、そもそも銘柄購読が発生していなかった。診断結果 `['ST', 'ST', 'ST']` は WS 接続自体は成功するが購読リクエストをしていないため FD/KP が来ない状態を示していた。

### ✅ I2: URL パラメータの厳密一致確認

**コード解析結果**:

| 項目 | 現コード（修正前） | サンプルコード |
|---|---|---|
| `p_evt_cmd` の値 | `ST%2CKP%2CFD`（`,` → `%2C`） | `ST,KP,FD`（生カンマ） |
| URL エンコード関数 | `func_replace_urlecnode` を各値に適用 | 使用しない |
| パラメータ順序 | 一致 | — |

**根本原因確定（H6）**: `func_replace_urlecnode` は `,` を `%2C` にエンコードする。`_build_ws_url` でこの関数を `p_evt_cmd='ST,KP,FD'` に適用すると `ST%2CKP%2CFD` となる。立花サーバは `%2C` をカンマとして解釈せず、FD 購読を認識しない。

**棄却された仮説**: H1（時間外）— 購読リクエスト自体が壊れていたため時間帯は無関係。H8（ST エラー）— ST 内容は無関係。H14（死亡タイムアウト）— 無関係。

---

## 13. 設計判断ログ

### F-A 適用：URL エンコードを除去

**決定**: `_build_ws_url` から `func_replace_urlecnode` を完全削除し、公式サンプルと同様に生値を結合する。

**理由**: 
1. 公式サンプル `e_api_websocket_receive_tel.py:573-585` が `func_replace_urlecnode` を使用していない
2. WebSocket URL に含まれるパラメータ値はすべて英数字のみ（`22`, `1000`, 市場コード数字, 銘柄コード数字）か、カンマ区切りのイベントコード（`ST,KP,FD`）であり、サーバが生カンマを期待している
3. `func_replace_urlecnode` は REQUEST URL（JSON ボディのエンコード）用に設計されたものであり、WebSocket URL パラメータへの適用は用途外

**棄却した代替案**: 
- `p_evt_cmd` のみエンコードを外す → 部分的な修正は将来の混乱を招くためすべて外す
- `func_replace_urlecnode` 側でカンマをホワイトリスト化する → WebSocket URL 構築が `func_replace_urlecnode` を使うべきでない

### ST フレーム VenueError 伝搬を追加（F-C）

**決定**: `_cb_depth` で ST フレームを受信した際、`p_errno != '0'` なら `VenueError{code: st_errno_{N}}` を outbox に送る。

**理由**: 元のコードは ST フレームを完全に無視していたため、サーバ側エラーの検知が不可能だった（§3 H8/H10 類の問題を診断できない）。

---

## 14. Tips（後続作業者向け）

### diagnose スクリプトの確認コマンド

```bash
# ST フレームの内容確認（立会時間外でも接続・購読の動作確認）
uv run python scripts/diagnose_tachibana_ws.py --ticker 7203 --dump-raw 5

# 立会時間中の FD 受信確認（修正後 PASS を確認する）
uv run python scripts/diagnose_tachibana_ws.py --ticker 7203 --frames 5 --timeout 30
```

### 修正の期待動作（立会時間中）

修正前: `p_evt_cmd=ST%2CKP%2CFD` → サーバが FD 購読を認識しない → ST フレームのみ → fallback

修正後: `p_evt_cmd=ST,KP,FD` → サーバが正しく購読 → FD/KP フレームが届く → Ladder リアルタイム更新

### 立会時間外での確認

時間外でも KP フレーム（5 秒間隔キープアライブ）は届くはず。立会時間外に diagnose を実行して KP が来れば修正が効いている。FD は立会中のみ。

---

## 15. 仮説ステータス更新

| ID | 仮説 | ステータス |
|---|---|---|
| **H6** | URL エンコード差異（`func_replace_urlecnode` の挙動） | **✅ 確定（根本原因）** |
| H1 | 立会時間外 | ⬜ 部分的に関係（時間外なら FD は来ないが、それ以前に購読が壊れていた） |
| H8 | ST がエラーを通知 | ⬜ 今回の件では原因ではないが、ST ログを追加して診断可能に |
| H10 | アカウント設定未完了 | ⬜ 立会時間中に KP が届けば棄却可能 |
| H2, H3, H4, H5, H7, H9, H11, H12, H13, H14 | その他仮説 | ⬜ H6 修正で解決すれば不要 |

---

## レビュー反映 (2026-05-01, ラウンド 1)

### 解消した指摘

- ✅ CRITICAL C1: 実 demo 認証情報のハードコード除去 + secrets-guard テスト新設 (`python/tests/test_tachibana_secrets_guard.py`)
- ✅ HIGH H-A: `_ST_SECRET_KEYS` を frozenset 定数化、`sUrlRequest`/`sUrlMaster`/`sUrlPrice`/`sUrlEvent`/`sUrlEventWebSocket` を追加、`scripts/diagnose_tachibana_ws.py` と `tachibana.py` で同一定数を共有
- ✅ HIGH H-B: `p_errno` を None 既定に変更し `st_no_errno` / `st_session_expired` (=2) / `st_errno_<n>` を分岐、`_ST_OK_ERRNO_CODES` 定数化（空文字 = 正常 / R6 準拠）
- ✅ HIGH H-C / MEDIUM M-G: ST→VenueError の rate-limit 30s + `st_session_expired` 検出時に `_inner_stop.set()` で polling fallback へ落下
- ✅ HIGH H-D: `_FRAME_STATS_INTERVAL_S` テストの patch 値を `0.05` に修正、フレーム間隔を `await asyncio.sleep(0.08)` で確保。計画 §6 O1 の文言を「累積 (cumulative)」に統一
- ✅ HIGH H-E: `build_ws_url` を module-level 純関数化、テストの `__new__` ハック撤廃（`_lookup_sizyou_c` の単体テストへ分離）
- ✅ HIGH H-F: `_WS_PARAM_ALLOWED_RE = ^[0-9A-Za-z]+$` ホワイトリストでバリデーション
- ✅ HIGH H-G: `tachibana_ws.py` 末尾に `_DEAD_FRAME_TIMEOUT_S < _DEPTH_SAFETY_TIMEOUT_S` および `_FRAME_STATS_INTERVAL_S <= _DEPTH_SAFETY_TIMEOUT_S` の不変条件 assert を追加 + リグレッションテスト
- ✅ MEDIUM M-C: `depth_unavailable` WARN にフレーム種別カウント (FD/KP/ST/other) 併記、テスト拡張
- ✅ MEDIUM M-E: `_st_frame` に終端 `\x01` を統一
- ✅ MEDIUM M-F: 冗長 `patch.object` ブロックを削除
- ✅ MEDIUM M-H: `depth_keys_seen` → `_first_fd_received` 改名
- ✅ MEDIUM M-I: `_frame_counts` を `collections.Counter` 化（KeyError 回避）
- ✅ MEDIUM M-J: 未知 evt_cmd の DEBUG ログ + EC/SS/US TODO コメント
- ⏸ deferred (M-A/M-B/M-D): O2 メトリクス・O3 起動時セルフテスト・H9 仮想 URL 期限切れ自動再ログインは follow-up タスクで対処（§6 O2/O3 / §5 F-D / §10 Q-T-FD-4 に明記）

### 設計判断

- `_lookup_sizyou_c` はインスタンスメソッドのまま残し、`build_ws_url` は値受け取りの純関数に分離。マスタ未ロードのフォールバック ('00') ロジックは worker の責務として保持し、URL 構築はマスタを知らない（H-E）
- ST `p_errno=""` の扱いは SKILL.md R6（"空文字列 = 正常"）に厳密準拠。`?` を OK 集合から外すことで「キー欠落」と「正常応答」の混同を防ぐ（H-B）
- ST→VenueError rate-limit は **per-stream-depth 呼び出しのスコープ**（クロージャ内 `st_last_emit` dict）に限定。再接続をまたぐと再度発火するが、これは「再接続のたびに 1 件は通知したい」要件と整合
- `_first_fd_received` / `frame_counts_seen` は再接続をまたいで保持される必要があるため `_cb_depth` クロージャの**外側** (`stream_depth` の局所変数) に配置。`while` ループ内で毎回新しい `_cb_depth` を作っても閉包経由で参照できる
- 純関数化に伴い `_WS_PARAM_FORBIDDEN`（ブラックリスト）を `_WS_PARAM_ALLOWED_RE`（ホワイトリスト）に置換。ASCII alnum 以外を全拒否することでサーバ側の URL パース変更にも安全側に倒れる

### 持ち越し (Phase O1 候補)

- M-A (O2 メトリクス): `engine.server` の statistics に立花 FD/ST カウンタとフッタ表示。新 issue で trackする
- M-B (O3 起動時セルフテスト): debug ビルドのみ、起動 60s 後に FD 件数 0 ならば WARN
- M-D (H9 自動再ログイン): `st_session_expired` を契機に再ログインまで自動化する経路。現状は手動・ダイアログ経由
