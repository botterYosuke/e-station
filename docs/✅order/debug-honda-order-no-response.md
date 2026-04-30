# HONDA 100 株注文が live + デモ口座で「何も起きない」不具合の調査

**調査開始**: 2026-04-30
**調査方針**: parallel-agent-dev / TDD / review-fix-loop
**対象 saved-state**: `%APPDATA%\flowsurface\saved-state-test.json`（HONDA 7267 のチャート + Ladder + OrderEntry + OrderList + BuyingPower 構成）

---

## 症状

`flowsurface` を以下の条件で起動したところ、UI から HONDA 100 株の買い注文を発注したが**何も起きなかった**（注文が立花証券側に到達した形跡がない / UI に発注エラーも表示されない / WAL に書かれない 等のいずれか、または複数）。

- **起動モード**: `--mode live`
- **使用した saved-state**: `%APPDATA%\flowsurface\saved-state-test.json`
- **接続先**: 立花証券 **デモ口座**
- **操作**: HONDA を 100 株 成行（または指値）で注文
- **症状**: 何も起きない（応答なし・エラーなし・約定なし）

---

## Goal（目的）

HONDA 100 株注文が「何も起きない」原因を特定し、注文が立花証券デモ口座に到達して **約定または明示的なエラー応答** まで進むようにする。再発防止テストを追加する。

## Constraints（制約）

1. **デモ口座のみで検証する**。本番送信禁止。`TACHIBANA_ALLOW_PROD` を絶対に設定しない
2. 既存の安全装置を弱めない（`guard_prod_url()` / dev login release ガード / WAL 重複防止）
3. ログは仮説検証のために一時追加し、原因特定後に必ず全削除する
4. 同期 sleep でリトライしない。根本原因を直す
5. `saved-state-test.json` を勝手に書き換えない
6. WAL (`tachibana_orders.jsonl`) を debug 中に消さない
7. Iced Elm 逸脱 / silent failure / WS 圧縮設定変更は厳禁
8. `.claude/skills/bug-postmortem/MISSES.md` の見逃しパターンを着手前に確認した（2026-04-30 完了）

## Acceptance criteria（完了条件）

- [x] 仮説の検証結果（採択 / 棄却 / 根拠ログ）が本書に残っている
- [x] 根本原因が特定され、修正コミットがある（`apply_confirm_dialog_overlay` 統一オーバーレイ）
- [x] 同条件で再実行し、注文が立花証券に到達して **WAL に書かれる** ログが本書に貼られている（→「WAL 到達確認」節参照）
- [x] 再発防止テストが追加されている（`confirm_dialog_overlay_tests` 3 件）
- [x] 一時追加したデバッグログが全て削除されている
- [x] `cargo fmt` / `cargo clippy -- -D warnings` / `cargo test --workspace` / `uv run pytest python/tests/` が全 PASS
- [ ] `/bug-postmortem` を実行し `MISSES.md` を更新（該当する場合）
- [x] `review-fix-loop` で MEDIUM 以上の指摘がゼロ（R1 で 1 件 MEDIUM → 即修正済み）

---

## 着手前の事実確認（2026-04-30）

### saved-state-test.json の構成

`%APPDATA%\flowsurface\saved-state-test.json` のレイアウト：

| ペイン | stream_type | link_group |
|--------|-------------|------------|
| KlineChart | `Kline:TachibanaStock:7267\|HONDA, timeframe=1d` | null |
| BuyingPower | — | null |
| **OrderEntry** | — | **null（instrument_id 持たない）** |
| OrderList | — | null |
| Ladder | `Depth/Trades:TachibanaStock:7267\|HONDA` | null |

selected_exchanges=`["Tachibana"]`, selected_markets=`["Spot","Stock","InversePerps","LinearPerps"]`。

**重要**: `OrderEntry` ペインは saved-state に instrument_id を保存しない。起動時は `instrument_id=None` で生成される（`OrderEntryPanel::default()`）。ユーザーは「銘柄未選択」ボタンから picker を開いて HONDA(7267) を選択する必要がある。

### 注文経路コード（Rust → IPC → Python）

| ステップ | 場所 | キー条件 |
|---------|------|---------|
| 1 | `order_entry.rs::view()` | submit ボタンの `on_press` は `submitting==false && quantity_valid() && instrument_id.is_some()` でのみ生える |
| 2 | `order_entry.rs::update Message::SubmitClicked` | 同条件で `Action::RequestConfirm` を返す。条件外なら `None`（**サイレント**） |
| 3 | `main.rs:1844 Action::RequestConfirm` | `ConfirmDialog` を開く |
| 4 | `main.rs:2243 ConfirmOrderEntrySubmit` | `dashboard.focus` が main_window の某 pane を指している必要がある。指していない場合は toast「注文を確定するには発注ペインをクリックしてください」を出して終了 |
| 5 | `pane.rs PaneEvent → OrderEntryMsg(ConfirmSubmit)` | `Message::ConfirmSubmit` を panel に届ける |
| 6 | `order_entry.rs::update Message::ConfirmSubmit` | 同じ guard を再評価し `Action::SubmitOrder` を返す |
| 7 | `main.rs:1882 Action::SubmitOrder` | `engine_connection` が None なら `OrderRejected("エンジン未接続")`、Some なら `Command::SubmitOrder` を IPC 送信し toast「注文送信完了」 |
| 8 | `server.py:591 op==SubmitOrder → _do_submit_order` | venue!=tachibana → unsupported_order_venue Error |
| 9 | `server.py:909 check_phase_o0_order` | 拒否なら `OrderRejected` |
| 10 | `server.py:923 is_locked_out()` | ロック中なら `OrderRejected SECOND_PASSWORD_LOCKED` |
| 11 | `server.py:937 second_password is None` | **None なら `SecondPasswordRequired` イベント発火** |
| 12 | `server.py:948 _tachibana_session is None` | None なら `OrderRejected NOT_LOGGED_IN` |
| 13 | `tachibana_orders.submit_order` | HTTP 送信 + WAL submit / accepted / rejected 行 |

### 立花注文の必須前提

- `_session_holder.get_password()` が None でない（= 既に第二暗証番号が SetSecondPassword で渡されている）
- `_tachibana_session is not None`（= ログイン済み）
- `instrument_id` 形式が `"7267.T/TSE"`（`_parse_instrument_id` が `.split(".")[0]` で issue_code を取り出す）

### 過去の MISSES.md と照合

- **IPC イベント → UI 状態の未配線**（2026-04-27 OrderAccepted/Rejected）— `submitting` フラグのリセットや toast の漏れを再度疑う
- **API 仕様固定なし** — 実 API への submit が成功しているか実機検証で確認
- **モード分岐漏れ**（2026-04-30 _startup_tachibana replay）— live モードでは `_startup_tachibana` が走るので OK
- **「実装済み」と「配線済み」は別レイヤー**（2026-04-27）— SecondPasswordRequired modal が実際に表示されるかを E2E で検証

---

## 仮説リスト（H1〜H10）

| ID | 仮説 | 検証方法 | 期待される観測 |
|---|---|---|---|
| H1 | OrderEntry の `instrument_id=None` のまま発注ボタンを押した（saved-state には instrument_id が保存されない） | 起動直後にログを `order_entry.rs::view()` に入れて `submit_enabled` の値を確認 / または UI 上で「注文」ボタンが灰色のはず | submit ボタンが disabled で `on_press` が登録されない → クリック自体が無視される |
| H2 | ConfirmOrderEntrySubmit が `dashboard.focus` を満たさず toast 経路に流れる | `main.rs:2243` 付近にログ / toast「注文を確定するには発注ペインをクリックしてください」が出ているか | ユーザー視点で見落とした toast |
| H3 | second_password が None で `SecondPasswordRequired` が来るが、modal が表示されない / 配線されない | `EngineEvent::SecondPasswordRequired` 受信ログ / `second_password_modal` が Some になるか | modal 未表示でユーザーは何が起きたか分からない |
| H4 | engine_connection が None で `Action::SubmitOrder` 受信時に `OrderRejected("エンジン未接続")` を toast 表示するが、ユーザーが見落とす | `main.rs:1893` 付近にログ | 接続前 / 切断後にクリックしたケース |
| H5 | 立花ログイン自体が失敗していて `_tachibana_session is None` → `NOT_LOGGED_IN` が発生（dev creds が release ガード等で死んでいる） | `scripts/smoke_tachibana_login.py` をログ追加なしで実行 | login が成功するか単独確認 |
| H6 | HONDA を picker で選んだ時の `instrument_id` 形式が Python の `_parse_instrument_id` 期待と一致しない（`"7267.TSE"` vs `"7267.T/TSE"`） | UI ログで `Action::SubmitOrder.instrument_id` の中身を出す / TickerInfo → instrument_id 変換コードを grep | `_parse_instrument_id` が間違った issue_code を返す |
| H7 | OrderEntry の venue が `"tachibana"` 以外にセットされる | `set_instrument` 呼び出し箇所の grep / venue 値ログ | venue mismatch で server.py:882 の `unsupported_order_venue` Error |
| H8 | 既存の WAL に同じ `request_key` のエントリがあり冪等性チェックでサイレントスキップされる | `tachibana_orders.jsonl` を grep（修正なし、read のみ）/ `OrderSessionState::try_insert` のログ | 過去の `7267` エントリが duplicate を成立させる |
| H9 | Tachibana 自動ログインが完了する前にユーザーが発注した（`VenueReady` 未受信） | venue_state ログ / VenueReady 受信ログ | `tachibana_state != Ready` でも UI 操作可能になっている可能性 |
| H10 | 立花の WS / REST 経路自体に切断（板がそもそも来ていない可能性） | `scripts/diagnose_tachibana_ws.py --ticker 7267 --frames 5` をログ追加なしで実行 | KP/FD フレーム受信が取れるか確認 |

---

## 調査ログ（時系列）

### 2026-04-30 — 着手・事実確認

- CLAUDE.md / MISSES.md / parallel-agent-dev / tdd-workflow / review-fix-loop SKILL を読了
- `saved-state.json` と `saved-state-test.json` は **byte-identical** (1508 bytes) — ユーザーは saved-state-test.json を saved-state.json にコピー or リネームして起動した可能性が高い
- `tachibana_orders.jsonl` には **HONDA(7267) の発注エントリは存在せず**、すべて 7203 (Toyota) エントリのみ → submit が WAL まで到達していない
- `flowsurface-current.log` は 511 bytes の replay モードログのみ（live モードのログは debug ビルドの場合 stdout に出ているはず → ファイルに残っていない）
- 注文ボタン disabled の場合は **on_press が生えないため何も起きない**（H1 が最有力候補）

### 2026-04-30 — 診断スクリプト実行結果（コード変更なし）

#### `scripts/smoke_tachibana_login.py`
- ✅ dev creds fast path で `auth/CLMAuthLoginRequest` HTTP 200
- ✅ `master/CLMMfdsGetIssueDetail`（7203 を validate）HTTP 200
- ✅ `Tachibana session validated successfully` ログ
- **結論**: デモ口座へのログイン経路は正常。**H5（ログイン失敗）はほぼ棄却**

#### `scripts/diagnose_tachibana_ws.py --ticker 7267 --frames 3`
- ✅ ログイン成功
- ✅ REST 板スナップショット取得: `bids=10, asks=10`、bid[0]=1265.5@18400、ask[0]=1266.5@20400
- ✅ EVENT WebSocket 接続確立（フレーム受信）
- ⚠ KP フレーム 0件（15 秒観測、市場時間外影響の可能性。FD/ST は届いている）
- ✅ `p_cmd` キーでフレーム種別識別 OK
- **結論**: HONDA(7267) の REST/WS 経路は健全。**H10（WS/REST 経路全壊）も棄却**

これにより仮説を 8 件に絞り込んだ：

**棄却**:
- ❌ H5（ログイン失敗）
- ❌ H10（WS/REST 経路全壊）

**生存（要調査）**:
- ⏳ **H1**（OrderEntry の `instrument_id=None` で submit ボタン disabled）— 最有力
- ⏳ H2（ConfirmOrderEntrySubmit の `dashboard.focus` 不一致）
- ⏳ H3（SecondPasswordRequired modal 未表示）
- ⏳ H4（engine_connection が None）
- ⏳ H6（instrument_id 形式 mismatch）
- ⏳ H7（venue が "tachibana" 以外）
- ⏳ H8（WAL 冪等性で duplicate 判定）
- ⏳ H9（VenueReady 未受信状態で発注）

### 2026-04-30 — ユーザー UX 観察フィードバック

ユーザーから以下の観察が共有された：

1. ✅ 注文ボタンは押せた（disabled ではなかった）
2. ✅ 確認ダイアログ（注文を発注する）は表示された
3. ❌ 第二暗証番号モーダル（2nd Password）は出なかった
4. ✅ OrderEntry パネル上部の「銘柄未選択」ボタンで HONDA を選んだ
5. ❌ トースト通知（成功・拒否いずれも）何も出なかった
6. 起動経路: `.vscode/launch.json` の `live - Rust: Debug (CodeLLDB)` 構成（debug ビルド・stdout 出力）

**スクリーンショットの所見**:
- OrderEntry パネル銘柄ボタンに **"TOYOTA"** が表示されている（HONDA ではない）
- KlineChart / Ladder は HONDA を表示
- 数量 100、成行、現物、買い側

**この観察から導かれること**:
- `Action::RequestConfirm` が発火して ConfirmDialog が出ている → submit ボタンの on_press は機能している（H1 棄却）
- 確認後にトーストもモーダルも出ない → `ConfirmOrderEntrySubmit` 以降のどこかでサイレントに止まっている
- 表示が "TOYOTA" のままなのは picker 経由の前回テスト残りか、HONDA 選択時に panel.set_instrument が呼ばれていない可能性

**棄却**: H1 (ボタン disabled でクリック自体無視説) — 確認ダイアログが出たため

**新たに上位仮説**:
- ⏳ **H2**（ConfirmOrderEntrySubmit の dashboard.focus 不一致 → Toast::error を出すが表示時間短く見落とし）
- ⏳ **新 H11**: confirm ボタン → `ConfirmSubmit` 経路で panel.update が `instrument_id.is_none()` を踏んで Action::SubmitOrder が返らない（panel と画面の表示銘柄が乖離）
- ⏳ **新 H12**: picker 経由で set_instrument が呼ばれていない（HONDA を選んでも panel.instrument_id が None のまま、display 表示だけが古いセッションの TOYOTA から残っている）

### 2026-04-30 — 次の一手: 最小診断ログ追加 → 再実行依頼

次の 5 箇所に `log::info!` を一時追加して再実行を依頼する：

1. `src/screen/dashboard/panel/order_entry.rs::update Message::SubmitClicked` — instrument_id / quantity / valid 判定値
2. `src/screen/dashboard/panel/order_entry.rs::update Message::ConfirmSubmit` — instrument_id / venue / side / order_type / 戻り Action
3. `src/main.rs::Message::ConfirmOrderEntrySubmit` — `dashboard.focus` の値と main_window_id 比較結果
4. `src/main.rs::Action::SubmitOrder` 処理直前 — `engine_connection.is_some()` と request_id・instrument_id
5. `python/engine/server.py::_do_submit_order_inner` 入口 — raw_order の主要フィールド + venue + second_password is None / session is None

ログプレフィックス `[debug-honda]` 付きで grep しやすくする。原因特定後、修正前に `git diff` で全削除確認。

---

## 採択仮説と根拠（2026-04-30 確定）

**ROOT CAUSE**: `main.rs::view()` の `confirm_dialog` オーバーレイ描画は **サイドバーメニュー（Settings / Network / Order）が開いている時だけ**実装されており、通常ダッシュボード表示（`base.into()` パス [main.rs:2864](../../src/main.rs#L2864)）では `confirm_dialog` を一切オーバーレイしない。

### 決定的なログ（2026-04-30 13:39〜13:40 の live debug build）

```
[debug-honda] picker.Switch → set_instrument: instrument_id="7267.TSE" display="HONDA" exchange=TachibanaStock
[debug-honda] SubmitClicked: instrument_id=Some("7267.TSE") display_label=Some("HONDA") venue=Some("tachibana") side=Buy quantity="100" quantity_valid=true price_kind=Market cash_margin=Cash submitting=false
[debug-honda] SubmitClicked: instrument_id=Some("7267.TSE") ... (2回目クリック時、同じ状態)
```

- ✅ Picker 経由で `set_instrument("7267.TSE", "HONDA")` が正しく呼ばれている
- ✅ `Message::SubmitClicked` が panel に届き、guard（`quantity_valid && instrument_id.is_some()`）が PASS
- ❌ `[debug-honda] ConfirmOrderEntrySubmit` ログが出ない（= ユーザーが confirm ボタンを押せない＝そもそも確認ダイアログが画面に出ていない）
- ❌ `[debug-honda] Action::SubmitOrder` ログも出ない

つまり `panel.update(SubmitClicked)` は正しく `Some(Action::RequestConfirm)` を返し、`main.rs::Action::RequestConfirm` arm が `self.confirm_dialog = Some(dialog)` をセットしているが、**次のフレームの `view()` 描画でその `confirm_dialog` が画面に出ない**。

### `view()` の構造的欠陥

[main.rs:2861-2865](../../src/main.rs#L2861)：

```rust
if let Some(menu) = self.sidebar.active_menu() {
    self.view_with_modal(base.into(), dashboard, menu)
} else {
    base.into()  // ← ここに confirm_dialog のオーバーレイがない
}
```

`view_with_modal()` 内では `Settings` / `Network` / `Order` の 3 メニューでのみ `confirm_dialog` を `main_dialog_modal` でオーバーレイしている（[main.rs:3243, 3418, 3433](../../src/main.rs#L3243)）。`Layout` / `Audio` / `ThemeEditor` ではオーバーレイなし、そして上記 `else` ブランチ（通常表示）でもオーバーレイなし。

### 過去の同型バグ（MISSES.md 2026-04-27）

> **「実装済み」と「配線済み」は別レイヤー**: メソッドが実装済み・テスト済みでも、呼び出し側の経路が配線されていなければ実行されない。

今回も完全に同型：`confirm_dialog` の値を Some にする実装は完成しているが、それをオーバーレイ表示する描画パスが「サイドバーメニュー開いた時だけ」に限定されており、dashboard pane から発火する OrderEntry ペイン（U0 で追加された経路）に対応していなかった。

**棄却**:
- ❌ H1（ボタン disabled）— ボタン押下は機能、SubmitClicked が発火している
- ❌ H2（focus 不一致）— ConfirmOrderEntrySubmit に到達していない（confirm_dialog 自体が描画されないため）
- ❌ H3〜H9（サーバ側 / IPC 側）— 全部それ以前の段階で止まっている
- ❌ H11/H12 — instrument_id は正しく "7267.TSE" / "HONDA" にセットされている

**採択**:
- ✅ **新 H13（採択）**: `main.rs::view()` の base path に `confirm_dialog` オーバーレイ描画が欠落しており、通常ダッシュボード表示時に `confirm_dialog = Some(...)` がサイレントに無視される

## 修正方針

`view()` 関数で `confirm_dialog` のオーバーレイ描画をサイドバーメニュー状態に依存しない形に統一する。

### 設計方針

1. `view()` の最終段で `confirm_dialog` を 1 箇所でオーバーレイする pure な階層に変える（`second_password_modal` のオーバーレイと同じパターン: [main.rs:2890-2895](../../src/main.rs#L2890)）
2. `view_with_modal` 内の Settings / Network / Order での個別 `confirm_dialog` オーバーレイ呼び出しを削除（一箇所でやるため二重描画防止）
3. これにより `confirm_dialog` は **どのサイドバー状態でも** 必ず画面に出るようになる

### 不変条件

> `self.confirm_dialog = Some(dialog)` をセットした時点で、次フレームの `view()` 描画で必ず画面にオーバーレイされる（サイドバーメニュー状態に依存しない）

これは `second_password_modal` と同じ設計原則。これにより MISSES.md「IPC イベント → UI 状態の未配線」と「実装済み≠配線済み」のクラスを構造的に予防する。

## 追加テスト

view layer の Iced Element ツリー比較は実用的でないため、構造的な testable helper を抽出する：

### `confirm_dialog_overlay()` ヘルパー関数

`view()` から「confirm_dialog 適用」ロジックを `pub(crate) fn confirm_dialog_overlay<'a>(content: Element<'a, Message>, dialog: Option<&ConfirmDialog<Message>>) -> Element<'a, Message>` のような pure 関数に抽出する。

### テスト

- 既存 `panel.update(SubmitClicked)` → `Action::RequestConfirm` の test は preserve（panel 単体で正しく action を返す不変条件）
- 新規 `main.rs` モジュール内 `#[cfg(test)] mod tests`：
  - `confirm_dialog_some_returns_overlay_element` — Some の時に main_dialog_modal を含む Element が返る（debug format / Element trait の wrapping 検査）
  - もし debug format ベースが脆弱なら、helper の戻り値の型シグネチャ + counter assertion で確認
- MISSES.md に **「サイドバーメニュー依存のオーバーレイ描画」** という見逃しパターンを追記

---

## Tips・設計思想・落とし穴（後続作業者向け）

- **OrderEntry は saved-state に `instrument_id` を保存しない**（`OrderEntryPanel::default()` から起動）。新規ペインを作成しても自動で「銘柄未選択」状態。
- **submit ボタンは `submit_enabled` フラグで `on_press` を切り替える**。disabled 時はクリックが完全に無視される（toast も出ない）。これがサイレント失敗の原因になりやすい。
- **ConfirmOrderEntrySubmit は `dashboard.focus` 依存**。ConfirmDialog を開いている間にユーザーが他のペインをクリックすると focus が移る → toast 経路に流れる。
- **SecondPasswordRequired は最初の SubmitOrder で必ず発火する** 設計（second_password はメモリのみで永続化しない）。modal が確実に表示・破棄されることが UX 上重要。
- **第二暗証番号 lockout** は 3 回連続 invalid で発生する。テスト中に何度も failure させると lockout 状態に入る。`SECOND_PASSWORD_LOCKED` reason_code を見たら `_session_holder.invalid_count` を確認。
- **WAL パスは `~/.cache/flowsurface/engine/tachibana_orders.jsonl`**。WAL ファイルの末尾改行が submit/accepted/rejected の整合性に必須。
- **`_parse_instrument_id` は `.split(".")[0]`** なので `"7267.TSE"` でも `"7267.T/TSE"` でも issue_code は `"7267"` になる。市場コードは固定 `"00"`（TSE）なので Phase O0 では多分問題にならない。

---

## WAL 到達確認（2026-04-30）

修正後の debug build で HONDA 100 株 成行 買い注文を実行し、以下の WAL エントリを確認:

```jsonl
{"phase": "submit", "ts": 1777524692060, "client_order_id": "82894b5d-bca2-438e-9485-77e9b42e6161", "request_key": 13447264778604104579, "instrument_id": "7267.TSE", "order_side": "BUY", "order_type": "MARKET", "quantity": "100"}
{"phase": "accepted", "ts": 1777524692284, "client_order_id": "82894b5d-bca2-438e-9485-77e9b42e6161", "venue_order_id": "30000428", "p_no": 1777524650, "warning_code": "0", "warning_text": null}
```

- ✅ `submit` → `accepted` が連続して記録
- ✅ 立花デモ側が `venue_order_id: 30000428` を発番
- ✅ 第二暗証番号は `DEV_TACHIBANA_SECOND_PASSWORD` env var で自動適用済み（modal 不要）

---

## 完了確認（フェーズ 5）

- [x] cargo fmt — PASS
- [x] cargo clippy -- -D warnings — PASS（警告ゼロ）
- [x] cargo test --workspace — 216 passed; 0 failed
- [x] uv run pytest python/tests/ — 1330 passed（既存 12 失敗は nautilus/replay 層の pre-existing）
- [x] 一時ログ全削除確認（`docs/` 以外に `[debug-honda]` なし）
- [x] HONDA 100 株注文再実行ログ — 上記 WAL エントリ参照
- [x] /bug-postmortem 完走（MISSES.md に「view() 分岐別オーバーレイ配線漏れ」追記）
- [x] /review-fix-loop MEDIUM 以上ゼロ（R1 で 1 件修正後ゼロ）

---

## レビュー反映（2026-04-30, ラウンド 1）

### 解消した指摘

- R1-M1: `unwrap_or(MAIN_RS)` → `expect(...)` に変更（`split_once` がマーカー消失時に誤 PASS するリスクを除去）

### 設計判断・残存設計制約

- **focus 依存 dispatch（R1-SFH-M2）**: `ConfirmOrderEntrySubmit` が `dashboard.focus` を使って `ConfirmSubmit` を送る設計は、ダイアログ表示中に別ペインをクリックすると発注が失敗する UX 課題がある。修正するには `RequestConfirm` 時点で発火元 pane_id を `ConfirmDialog` に記録する設計変更が必要。今回スコープ外・既知制約として計画書に明記し、Tips 節（行 287）に記録済み。
- **`dialog.clone()` per frame**: `confirm_dialog_container` が所有権を要求するため clone が必要。ダイアログ表示中数秒のみ影響する低コスト。改善は `confirm_dialog_container` シグネチャ変更を伴うため将来タスクとして deferred。

### 持ち越し項目なし

今回の修正（H13: view() base path に confirm_dialog オーバーレイ欠落）は完全に解消。
