<a id="p7"></a>
# Mode メニュー新設実装計画（live ↔ replay 切替）

**作成日**: 2026-05-04
**作成者**: Claude Opus 4.7（botterYosuke）
**ステータス**: 部分完了（Agent A: T1/T2/T3 Rust コアインフラ ✅ / Agent B: Python WAL + StopReplay ハンドラ ✅ / Agent C: `Action::SwitchMode` 完全実装 ✅）
**起点課題**: [fix-save-menu.md](./fix-save-menu.md) P7（モード切替メニュー導線がない）
**前提**: [./P8-widget-menu-bar-linux.md](./P8-widget-menu-bar-linux.md)（Linux で `モード（Mode）` メニューを表示するには iced 自前メニューバーが必要）
**関連 F\***: [fix-save-menu.md](./fix-save-menu.md) F4（未保存変更 confirm）/ F7（本書 = `モード（Mode）` メニュー新設）

> 凡例について: 本書で参照する F\* / P\* / Phase 8.x の対応関係は [fix-save-menu.md](./fix-save-menu.md) 冒頭の凡例で固定する。本書からは F\* で参照する（H3 等の独自表記は廃止）。

---

<a id="やること"></a>
## やること

メニューバーに `モード（Mode）` サブメニューを追加し、`ライブ（Live）` / `リプレイ（Replay）` をラジオ選択で切り替えられるようにする。

```text
File   モード（Mode）
       ├─ ON  ライブ（Live）
       └─ OFF リプレイ（Replay）
```

- 現在のモードに `ON` テキストおよびチェックマークを付けて視認可能にする（muda 側は `CheckMenuItem`、iced 自前側は `ON` / `OFF` プレフィックス）
- 別モードの項目を選ぶと **アプリを再起動** して新しい `--mode` で立ち上げ直す
  （in-place 切替は engine state の作り直しが伴うため初期実装ではやらない）
- 再起動前に live モードで未保存変更があれば確認ダイアログ（F4 で導入する `confirm_dialog_overlay` を共有）
- メニュー項目のラベルは [fix-save-menu.md](./fix-save-menu.md) の統一規約に従い `開く…（Open）` / `上書き保存（Save）` / `名前を付けて保存…（Save As）` / `Replay を開始…` / `Replay を停止` と日本語（英名）併記。`モード（Mode）` も同形式

---

<a id="behavior"></a>
## 切替時の挙動

### 基本フロー

| 現在 → 次 | 動作 |
|----------|------|
| live → replay | **live モードのまま** `saved-state.json` への自動保存を flush → engine プロセス停止（Drop で `engine-session.json` 削除）→ `--mode replay` で `restart_with_mode(Replay)`。**replay モード起動後は CLAUDE.md D9 通り `saved-state.json` を read/write しない**ため live 設定は保全される |
| replay → live | engine に `Command::StopReplay` 送信 → `Event::ReplayStopped` を最大 5 秒待つ → engine プロセス停止（Drop で `engine-session.json` 削除）→ `--mode live` で `restart_with_mode(Live)` → 起動後 engine 再起動 → `engine-session.json` 再生成 |

`restart_with_mode(mode)` は既存の `Flowsurface::restart()` を流用するが、`APP_MODE` static の上書きと `Cli::mode` 引数の更新を同時に行う必要がある。具体的な実装ポイントは [Q2](#q2) を参照。

engine プロセスは **モード切替時に常に再起動** する（live engine と replay engine は内部状態が大きく異なるため、再利用しない）。`engine-session.json` は engine プロセスの Drop で削除され、再起動後の bootstrap で再生成される（[`Phase 8.1b B2` 参照](./fix-save-menu.md)）。

### 5 軸 matrix（不変条件）

`(現モード, 切替先, In-flight order, EngineBusy, submit_in_flight)` の 5 軸で挙動を完全に定義する（**統一決定 61** で 4 軸 → 5 軸に拡張。第 5 軸 `submit_in_flight` は P9 W&B run buffer の active submit を表す。詳細は [./P9-wandb-submit-menu.md#run-buffer-spec](./P9-wandb-submit-menu.md#run-buffer-spec) を参照）。

**不変条件 68**: active な W&B submit (`submit_in_flight = Some(_)`) 中は SwitchMode を reject する（`EngineBusy` 相当の扱い）。詳細仕様は [./P9-wandb-submit-menu.md#run-buffer-spec](./P9-wandb-submit-menu.md#run-buffer-spec) を参照。

| 現モード | 切替先 | In-flight order | EngineBusy | submit_in_flight | 期待挙動 |
|---------|-------|-----------------|------------|------------------|---------|
| live | replay | -（対象外） | -（対象外） | `Some(_)` | restart 中止 + 「W&B 送信中です」dialog（[統一決定 61, 68](#q4) / [P9 run-buffer-spec](./P9-wandb-submit-menu.md#run-buffer-spec)） |
| replay | live | -（対象外） | -（対象外） | `Some(_)` | restart 中止 + 「W&B 送信中です」dialog（[統一決定 61, 68](#q4) / [P9 run-buffer-spec](./P9-wandb-submit-menu.md#run-buffer-spec)） |
| live | replay | あり | -（対象外） | `None` | restart 中止 + 「未約定注文があります」エラー dialog（WAL は read-only 参照のみ、書き換えない） |
| live | replay | なし | あり | `None` | restart 中止 + 「engine がビジー状態です」エラー dialog（`BusyError` 由来） |
| live | replay | なし | なし | `None` | dirty なら F4 confirm → 自動保存 flush → engine 停止 → restart |
| replay | live | -（対象外） | あり | `None` | restart 中止 + エラー dialog |
| replay | live | -（対象外） | なし | `None` | `Command::StopReplay` → `Event::ReplayStopped` 待ち（5s） → engine 停止 → restart。タイムアウト時は `Command::ForceStopReplay` を送って強制停止 fallback を発火 → 再度 `Event::ReplayStopped`（最大 2s）→ engine 停止 → restart。`ForceStopReplay` も失敗したケースのみ restart 中止 + エラー dialog（[統一決定 35](#q4)）|
| live | live | -（対象外） | -（対象外） | -（対象外） | no-op（メニューは disable） |
| replay | replay | -（対象外） | -（対象外） | -（対象外） | no-op（メニューは disable） |

- **submit_in_flight**: P9 W&B run buffer の `submit_in_flight: Option<SubmitHandle>` フィールド。`Some(_)` の場合 active な W&B submit が進行中であり、SwitchMode は reject される（**統一決定 61, 68**）。`tests/mode_switch_blocks_during_submit.rs` で保護。
- **In-flight order**: `tachibana_orders.jsonl` WAL に未約定エントリがある場合（live → replay 時のみ判定）。WAL 整合性を破壊しないため restart を中止する。**SwitchMode ハンドラは WAL を書き換えない。live→replay 切替直前に read-only で参照するのは許容**（[統一決定 37](#q4)）
- **WAL in-flight 検知アルゴリズム**: `tachibana_orders.jsonl` を **tail から逆順スキャン**し、order_id ごとに最新行の `status` を集計する。最新 status が `submitted` または `partial`（部分約定）であるものを in-flight とみなす。`filled` / `cancelled` / `rejected` で完結している order_id は in-flight 判定から除外する。プロセスクラッシュで `submitted` のまま残留したエントリも in-flight として扱う（[統一決定 34](#q4)）
- **EngineBusy**: engine 側 state guard（`ReplayState` / `LiveState`）が busy を返すケース。`EngineBusy` event → `BusyError` 例外に変換され、メッセージは dialog にそのまま表示する
- **保存失敗 / confirm cancel**: live → replay の `saved-state.json` flush 失敗、または F4 confirm で「キャンセル」選択時は restart 中止
- **停止失敗**: replay → live で `Event::ReplayStopped` が 5s タイムアウトしたら **`Command::ForceStopReplay` fallback** を発火し、追加 2s で `Event::ReplayStopped` を待つ（[統一決定 35](#q4)）。`ForceStopReplay` も失敗 or `EngineBusy` の場合のみ restart 中止 + エラー dialog

---

<a id="cargo-platform"></a>
## Cargo / プラットフォーム

- muda（Win/Mac）と iced 自前メニュー（Linux、[./P8-widget-menu-bar-linux.md](./P8-widget-menu-bar-linux.md)）双方に同じ `モード（Mode）` サブメニューを実装
- `native_menu::Action` に `SwitchMode(AppMode)` バリアントを追加
- ハンドラは `Message::SwitchAppMode(AppMode)` で受けて `restart_with_mode(mode)` を呼ぶ
- アクセラレータは **muda 正規ルート** を使う（`MenuItem::new(label, true, Some(accelerator))`）。iced の `keyboard::on_key` 経由のフォールバックは `cfg(target_os="linux")` 限定 にとどめ、Win/Mac での二重発火を防ぐ

✅ **T3 完了 (Agent A, 2026-05-04)**: `Action::SwitchMode(AppMode)` 追加、`MenuIds` に `switch_live`/`switch_replay` 追加、`attach()` に `CheckMenuItem` ベースの「モード（Mode）」サブメニューを追加、`event_stream()` で変換。`actions_for_mode()` を 6-tuple に拡張。`linux_keyboard_subscription()` に `MODE_SWITCHING` チェック（統一決定 64）追加。`tests/accelerator_bind.rs` に T3 用テスト 5 件追加。

---

<a id="design-questions"></a>
## 設計上の論点

<a id="q1"></a>
### Q1. ラジオ表示の muda 側対応

- muda 0.15 の `MenuItem` には `set_checked` を持つ `CheckMenuItem` があるため、これを 2 つ並べて排他制御する（select 時に他方を uncheck）
- iced 自前メニュー側はテキスト前に `ON` / `OFF` を付ける単純実装でよい

<a id="q2"></a>
### Q2. `--mode` の動的書き換え

`Cli::mode` は起動時にパースされた `clap` 構造体に固定されている。再起動経路は 2 候補：

- **a. プロセスを exec し直す**（`std::process::Command::new(env::current_exe())` → `argv` を組み直して spawn → `std::process::exit(0)`）
- **b. `APP_MODE` static を書き換えて `Flowsurface::new()` を呼び直す**

→ 推奨: **b**。既存 `restart()` の延長で済み、起動時間も短い。

`APP_MODE` は `OnceLock` から `static APP_MODE: std::sync::Mutex<Option<AppMode>>` への変更が必要（`current_path` 引継ぎ用 static `static CURRENT_PATH: std::sync::Mutex<Option<PathBuf>>` と同形式・同方針）。

**既存 `APP_MODE` 参照箇所**（`grep -rn "APP_MODE" src/` で確認すべき箇所、実装時に必ず網羅すること）:

- `src/main.rs`（`Flowsurface::new()` 内 read / `Cli` パース直後の write）
- `src/main.rs`（`Flowsurface::restart()` 内 read）
- `src/native_menu.rs`（メニュー初期化時の現モード判定 read）

**Mutex poisoning 戦略**: `lock().unwrap_or_else(|e| e.into_inner())` で poison から復旧する（panic 中の partial write は許容）。`OnceLock` から `Mutex` への移行で API が変わるため、ユーティリティ `app_mode()` / `set_app_mode(mode)` を導入し読み書き経路を 1 箇所に集約する。

✅ **T1 完了 (Agent A, 2026-05-04)**: `APP_MODE` を `OnceLock` → `Mutex<Option<AppMode>>` に移行。`app_mode()` / `set_app_mode()` ユーティリティ追加。全 `APP_MODE.get()...` 参照を `app_mode()` に一括置換。

<a id="q3"></a>
### Q3. 再入禁止と連打防止

`static MODE_SWITCHING: AtomicBool = AtomicBool::new(false)` を導入する。**素手で `compare_exchange` を呼んで finally で false に戻すのではなく、必ず `struct ModeSwitchGuard` の RAII で包む**（[統一決定 33](#q4)）。これにより panic 経路でも Drop が走り、stuck で「永遠に切替不可」になる事故を構造的に防ぐ。

```rust
struct ModeSwitchGuard;

impl ModeSwitchGuard {
    /// 取得成功で Some、既に true なら None を返す
    fn try_acquire() -> Option<Self> {
        MODE_SWITCHING
            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .ok()
            .map(|_| ModeSwitchGuard)
    }
}

impl Drop for ModeSwitchGuard {
    fn drop(&mut self) {
        MODE_SWITCHING.store(false, Ordering::Release);
    }
}
```

`restart_with_mode(mode)` の冒頭で `let _guard = ModeSwitchGuard::try_acquire().ok_or(ModeSwitchError::AlreadySwitching)?;` を呼び、関数スコープ内で生きている間だけ true、抜ける（成功 / `?` 早期 return / panic unwind）と必ず false へ戻る。

✅ **T2 完了 (Agent A, 2026-05-04)**: `MODE_SWITCHING: AtomicBool`・`ModeSwitchGuard`・`ModeSwitchError` を `src/main.rs` に実装。`pub` 可視性で統合テストからも参照可能。`tests/mode_switch_panic_recovery.rs`・`tests/mode_switch_reentry.rs` を新規作成（ソースインスペクション方式）。

- 取得失敗時（`AlreadySwitching`）はメニュークリックを no-op にする
- メニュー側でも `モード（Mode）` サブメニュー全体を `MODE_SWITCHING == true` の間は disable
- `Flowsurface::new()` の APP_MODE read は **lock 取得 → 値 clone → drop** の順で行い、`MODE_SWITCHING` は確認しない（new() は restart 末尾で呼ばれるため、その時点では既に切替処理が write フェーズに入っている）。lock 取得順は常に **`MODE_SWITCHING` → `submit_in_flight` → `APP_MODE` → `CURRENT_PATH`** の固定順序にしてデッドロックを防ぐ（**統一決定 58**: P9 W&B run buffer の `submit_in_flight` を第 2 段に挿入。P7・P9 双方に明記）。詳細は [./P9-wandb-submit-menu.md#run-buffer-spec](./P9-wandb-submit-menu.md#run-buffer-spec) を参照。`tests/wandb_modeswitch_lock_order.rs` で順序リグレッションを保護する
- 順序違反は debug build で `debug_assert!` により panic、release build では `tracing::warn!` ログのみ出力する（運用安全性のため release では落とさない。**統一決定 R6-82**）

<a id="q4"></a>
### Q4. replay セッション中の切替制限

replay 実行中（`ReplayState::Running`）に live へ切り替えると、engine 内の戦略実行が中断される。挙動候補：

- **a. 確認ダイアログを出す**（「実行中の replay を停止して live モードに切り替えますか？」）
- **b. 切替メニューを disable にする**（実行中は選べない）

→ 推奨: **a**。ユーザー意図を尊重しつつ事故防止になる。停止コマンド・タイムアウト処理は[切替時の挙動](#behavior) matrix を参照。

---

<a id="non-scope"></a>
## 非スコープ

- -（対象外） in-place モード切替（engine state を壊さずに切り替える）
- -（対象外） モード切替時のレイアウト引き継ぎ（D9 通り live のレイアウトのみが永続化対象）
- -（対象外） 第 3 のモード（paper trading 等）の追加

---

<a id="related-tasks"></a>
## 関連タスクとの関係

- **F4（未保存変更の confirm）** とロジックを共有する。先に F4 の `confirm_dialog_overlay` API を入れると流用が効く
- **[./P8-widget-menu-bar-linux.md](./P8-widget-menu-bar-linux.md)（Linux 自前メニュー）** が前提。Linux でも `モード（Mode）` メニューを出すには iced 自前メニューバーが必要

---

<a id="acceptance"></a>
## 受け入れ基準

1. live ↔ replay の切替が `モード（Mode）` メニューから可能で、現モード側に `ON` 表示 / `CheckMenuItem` チェックが入っている
2. live → replay 切替で `saved-state.json` に live 設定が flush され、replay 起動後にそのファイルが read/write されない（D9 整合）
3. replay → live 切替で `Command::StopReplay` → `Event::ReplayStopped` の往復が完了し、`engine ws read error` ログが出ない（CLAUDE.md E2E 整合）
4. 5 軸 matrix の各分岐（in-flight order あり / EngineBusy / submit_in_flight / 保存失敗 / 停止失敗 / confirm cancel）で restart が中止され、対応するエラー dialog が表示される（**統一決定 61** で 4 軸→5 軸に拡張）
5. メニュー連打で `restart_with_mode` が二重起動しない（`MODE_SWITCHING` AtomicBool で保護）
6. モード切替で engine プロセスが必ず再起動し、`engine-session.json` が一度削除→再生成される
7. `tachibana_orders.jsonl` WAL に未約定エントリがある状態で live → replay を選ぶと restart が中止される
8. アクセラレータが Win/Mac で muda 経由のみ発火し、Linux で iced kbd 経由でのみ発火する（二重発火しない）
9. `restart_with_mode` 内 panic 後に次の SwitchMode が `AlreadySwitching` で stuck しないこと（`ModeSwitchGuard` Drop による RAII 解放。`tests/mode_switch_panic_recovery.rs` で保護）
10. replay → live で `Event::ReplayStopped` が 5s タイムアウトしたときに `Command::ForceStopReplay` fallback で停止 → 後続 SwitchMode が成功すること（`tests/mode_switch_stop_timeout.rs` で保護）
11. 2 並行 SwitchMode は片方が `Err(AlreadySwitching)` / skip となり、guard 解放後の再試行で成功すること（`tests/mode_switch_reentry.rs` で保護）。さらに **accelerator 経路（Linux iced kbd `keyboard::on_key`）でも `MODE_SWITCHING.load()` を確認して dispatch を抑止する**契約を保証する（**統一決定 64**: メニュー disable と独立に発火する accelerator 経路に対応。`tests/mode_switch_accelerator_disabled.rs` で保護）
12. WAL in-flight 検知が tail 逆順スキャンで実装され、部分約定→全約定 / プロセスクラッシュ残留の両 fixture で正しく判定されること（`python/tests/test_wal_in_flight_detection.py` で保護）
13. active な W&B submit (`submit_in_flight = Some(_)`) 中の SwitchMode が reject され「W&B 送信中です」dialog が表示されること（**統一決定 61, 68**。`tests/mode_switch_blocks_during_submit.rs` で保護。詳細仕様は [./P9-wandb-submit-menu.md#run-buffer-spec](./P9-wandb-submit-menu.md#run-buffer-spec)）
14. lock 取得順序が tracing で観測でき、固定順序 `MODE_SWITCHING → submit_in_flight → APP_MODE → CURRENT_PATH` を満たすこと。逆順取得を試みた場合、debug build では `debug_assert!` により panic し、release build では `tracing::warn!` 警告ログが出力されること（**統一決定 58, R6-82**。`tests/wandb_modeswitch_lock_order.rs` で保護）

---

<a id="test-strategy"></a>
## テスト方針

### Rust integration test

| ファイル | 期待 | 観測ポイント |
|---------|------|------------|
| `tests/mode_switch_restart.rs` | `restart_with_mode(Replay)` 後に `app_mode() == AppMode::Replay` かつ `Cli::mode == Replay` を assert | `APP_MODE` lock 取得 → 値比較 |
| `tests/mode_switch_reentry.rs` | **2 並行 SwitchMode** を同時 spawn → 片方が `Err(ModeSwitchError::AlreadySwitching)`（or skip）、もう片方は処理に入る。先行処理の guard 解放後に再度 SwitchMode が成功すること（[統一決定 36](#q4)） | `MODE_SWITCHING.load(Ordering::SeqCst)` の遷移 / `restart_with_mode` 戻り値 `Result<(), ModeSwitchError::AlreadySwitching>` |
| `tests/mode_switch_panic_recovery.rs` | `restart_with_mode` 内部で意図的に panic を起こしたあと、次の SwitchMode 呼び出しが `AlreadySwitching` ではなく成功すること（`ModeSwitchGuard` の Drop が走る証跡。[統一決定 33](#q4)） | `std::panic::catch_unwind` で panic を捕捉 → `MODE_SWITCHING.load() == false` を assert → 再度 `try_acquire().is_some()` |
| `tests/mode_switch_stop_timeout.rs` | replay → live で engine が `Event::ReplayStopped` を返さない mock を使い 5s タイムアウト → `Command::ForceStopReplay` fallback が発火して停止 → 続けて発火する SwitchMode が `EngineBusy` ではなく成功すること（[統一決定 35](#q4)） | mock engine 観測 / `ForceStopReplay` の送信ログ / 後続 SwitchMode 戻り値 `Ok` |
| `tests/mode_switch_in_flight_order.rs` | live → replay 時、WAL に open order があると `Err(ModeSwitchError::InFlightOrder)` を返し restart されないこと。**WAL は read-only 参照のみ**で fixture が変化しないことも assert（[統一決定 37](#q4)） | `tachibana_orders.jsonl` を fixture で用意 / `app_mode()` が変化しないこと / WAL ファイルの mtime / sha が不変 |
| `tests/mode_switch_engine_busy.rs` | engine が `EngineBusy` を返すケースで `Err(ModeSwitchError::Busy)` に変換されること | mock engine で `EngineBusy` event を返す / dialog メッセージ assert |
| `tests/mode_switch_blocks_during_submit.rs` | active な W&B submit (`submit_in_flight = Some(_)`) 中に SwitchMode が reject され `Err(ModeSwitchError::SubmitInFlight)` を返し、dialog 文言が「W&B 送信中です」であること（**統一決定 61, 68**） | P9 run buffer に submit handle を inject / SwitchMode 戻り値 / dialog message assert |
| `tests/mode_switch_accelerator_disabled.rs` | Linux iced kbd accelerator 経路 (`keyboard::on_key`) で `MODE_SWITCHING.load() == true` の間 dispatch が抑止されること（**統一決定 64**: メニュー disable と独立した経路の保護） | accelerator key event を simulate / `Message::SwitchAppMode` が dispatch されないこと assert |
| `tests/wandb_modeswitch_lock_order.rs` | lock 取得順序が `MODE_SWITCHING → submit_in_flight → APP_MODE → CURRENT_PATH` の固定順序であること。Mutex helper で `tracing::info!(target: "lock_order", "acquire {name}")` を記録し、`tracing-test` で span/event 列を取り出して順序を assert する。逆順を試みる fixture 関数で `debug_assert!(prev_index < next_index)` 違反による panic を `#[should_panic]` テストで保護する（`parking_lot::deadlock::check_deadlock()` は `std::sync::Mutex` に効かないため不採用。**統一決定 58, R6-82**） | tracing-test による span/event 列取得 / 固定順序 assert / 逆順試行時の `debug_assert!` panic（`#[should_panic]`） |

### Python テスト

| ファイル | 期待 | 観測ポイント |
|---------|------|------------|
| ✅ `python/tests/test_wal_in_flight_detection.py` | `tachibana_orders.jsonl` の tail 逆順スキャンで in-flight 判定が正しく行われること（[統一決定 34](#q4)）。fixture 6 種：(1) 部分約定→全約定（`partial` の後 `filled`）→ in-flight=空、(2) プロセスクラッシュで `submitted` のみ残留 → in-flight=該当 order_id を含む、(3) 複数注文混在、(4) ファイル不在、(5) partial 残留、(6) 末尾 truncated 行スキップ | `detect_in_flight_orders(path)` の戻り値の集合比較 |
| ✅ `python/tests/test_server_engine_dispatch.py::TestStopReplayDispatch` | `StopReplay` IPC が RUNNING 状態のみ受理され ReplayStopped を broadcast すること。IDLE / STOPPED では EngineBusy を返し、live モードでは mode_mismatch EngineError を返すこと | `_handle_stop_replay` ハンドラの outbox 内容検査 |
| ✅ `python/tests/test_server_engine_dispatch.py::TestForceStopReplayDispatch` | `ForceStopReplay` IPC が state guard なしで全ランナーを強制停止し ReplayStopped を broadcast すること | `_handle_force_stop_replay` ハンドラの outbox 内容検査 |

### E2E（bash + uv）

```bash
# replay → live 切替（手動操作 simulator は持たないため env で APP_MODE 切替を simulate）
OBSERVE_S=60 bash tests/e2e/smoke.sh
# 期待ログ:
#   "mode switch: replay -> live"
#   "engine restarted (mode=live)"
#   "engine-session.json regenerated"
# NG ログ:
#   "engine ws read error"
```

### 観測コマンド

```bash
# モード切替時の engine ライフサイクル確認
ls -la "$APPDATA/flowsurface/engine-session.json"   # 切替直後に新しい mtime
cat ~/AppData/Roaming/flowsurface/flowsurface-current.log | grep "mode switch"
```

---

<a id="agent-b-progress"></a>
## Agent B 完了記録（2026-05-04）

### 実装ファイル

| ファイル | 内容 |
|---------|------|
| ✅ `python/engine/wal_in_flight.py` | P1: WAL in-flight 検知ユーティリティ。`detect_in_flight_orders(path)` が tail から逆順スキャンして submitted/partial な order_id の frozenset を返す。読み取り専用。 |
| ✅ `python/engine/server.py` | P2: `_dispatch()` に `StopReplay` / `ForceStopReplay` ブランチ追加。`_handle_stop_replay()` / `_handle_force_stop_replay()` メソッド追加。 |
| ✅ `python/tests/test_wal_in_flight_detection.py` | P3: WAL in-flight 検知の 12 テストケース（6 fixture + 追加 4 ケース）。全緑確認済み。 |
| ✅ `python/tests/test_server_engine_dispatch.py` | P2 テスト: TestStopReplayDispatch（8 テスト）+ TestForceStopReplayDispatch（5 テスト）追加。全緑確認済み。 |

---

<a id="agent-c-progress"></a>
## Agent C 完了記録（2026-05-04）

### 実装ファイル

| ファイル | 内容 |
|---------|------|
| ✅ `src/main.rs` | Step 2: `Flowsurface` 構造体に `pending_mode_switch` / `_mode_switch_guard` フィールド追加。`Message` enum に F7 バリアント 7 件（`DiscardAndSwitchMode` / `SaveAndSwitchMode` / `SwitchModeWithSpecs` / `ModeSwitchStopAcked` / `ModeSwitchStopTimeout` / `ModeSwitchForceStopTimeout`）追加。`has_wal_in_flight_orders()` 関数追加。`EngineEvent::ReplayStopped` arm を `map_engine_event_to_tachibana` に追加。`restart_with_mode()` メソッド追加。`Action::SwitchMode` スタブを完全実装に置き換え。F7 Message ハンドラ 6 件追加。 |
| ✅ `tests/mode_switch_restart.rs` | T4: `restart_with_mode` 構造テスト 4 件。全緑確認済み。 |
| ✅ `tests/mode_switch_in_flight_order.rs` | T5: WAL in-flight 検知 構造テスト 4 件。全緑確認済み。 |
| ✅ `tests/mode_switch_accelerator_disabled.rs` | T6: accelerator 経路 MODE_SWITCHING チェック構造テスト 3 件。全緑確認済み。 |

### 設計判断

1. **WAL パス解決**: `dirs_next` クレートは `Cargo.toml` に直接依存していないため、`HOME` / `USERPROFILE` 環境変数で代替した。Windows では `USERPROFILE` が `C:\Users\{user}` を指す。
2. **dummy message 選択**: Agent C は `Message::ReplayFinished` を仮の dummy に使ったが、レビューで問題が発覚（FindingR1）。`Message::Noop` を新設してレビュー修正で差し替え済み。
3. **`SaveAndSwitchMode` の window 収集**: Agent C は `SwitchModeWithSpecs` に re-route する方式を採用したが、`is_dirty` の再チェックで無限ループが発生するバグが発覚（FindingR4）。`Message::SwitchModeSaveComplete` を新設して専用の保存+再起動パスとした。

### 追加の設計判断（review-fix-loop で発覚）

1. **live モードでの StopReplay**: `StopReplay` は `ReplayOnlyCommand` に分類されるため `EngineBusy` と live state を組み合わせると pydantic ValidationError になる。そのため live モードでの受信は `EngineError{code=mode_mismatch}` で返す（EngineBusy ではない）。
2. **ForceStopReplay の state guard なし**: 設計通り state guard を持たない。STOPPING・IDLE・LOADED 状態でも強制実行し、全ランナーを停止して ReplayStopped を broadcast する。
3. **ReplayStopped の final_equity**: 停止が完了する前（runner の EngineStopped を待たずに）送出するため `None` を設定する。runner の最終 equity は EngineStopped で別途届く。

---

## Review-fix 完了記録（2026-05-04）

`/review-fix-loop` 実行によるレビュー後、以下 HIGH バグを修正した。

| Finding | 場所 | 問題 | 修正 |
|---------|------|------|------|
| R1 | `src/main.rs` StopReplay/ForceStopReplay send_task | `Message::ReplayFinished` を dummy に使用 → spurious `GetOrderList` IPC 発火 | `Message::Noop` を新設して差し替え |
| R2 | `src/main.rs` `GoBack` ハンドラ | `pending_mode_switch` / `_mode_switch_guard` を未クリア → Escape で dirty-confirm 閉じると `MODE_SWITCHING` が永続 true になるバグ | 両フィールドを `None` にクリア |
| R3 | `src/main.rs` `ToggleDialogModal(None)` ハンドラ | 同上 → backdrop クリックで dirty-confirm 閉じた場合も `MODE_SWITCHING` が永続 true | 両フィールドを `None` にクリア |
| R4 | `src/main.rs` `SaveAndSwitchMode` ハンドラ | `SwitchModeWithSpecs` に re-route → `is_dirty` 再チェックで保存前なので true → 無限ダイアログループ | `SwitchModeSaveComplete` メッセージを新設して直接保存+restart |

修正後: `cargo test --workspace` 全緑 / `cargo clippy -- -D warnings` クリーン

---

## レビュー反映 (2026-05-05, ラウンド 2)

`/review-fix-loop` ラウンド 2 で検出された 16 件（M3 は先行コミット済み）を
Path A で TDD 修正した。

### Phase 1: 型基盤

| Finding | 修正内容 | 主な変更箇所 |
|---------|---------|------------|
| **M1** | `ModeSwitchError::SubmitInFlight` バリアントを追加（W&B submit 進行中の SwitchMode 拒否を型レベルで表現） | `src/main.rs` enum 定義 / `tests/mode_switch_blocks_during_submit.rs::mode_switch_error_has_submit_in_flight_variant` |
| **L1** | `ModeSwitchError::ConfirmCancelled` バリアントを追加（F4 confirm cancel を型レベルで表現） | `src/main.rs` enum 定義 / `tests/mode_switch_panic_recovery.rs::mode_switch_error_has_confirm_cancelled_variant` |
| **M13** | `pending_mode_switch` + `_mode_switch_guard` ペアを `mode_switch_state: Option<(AppMode, ModeSwitchGuard)>` に統合。31 サイト網羅更新でペア drift（target だけ消えて guard が永遠に true）を構造的に不可能にする。`SaveAndSwitchMode` のみ guard を温存するため `as_ref()` で読み取り、他は `take()` で guard ごと消費 | `src/main.rs` struct field + 17 サイトの read/write 経路 / 既存 18 件のテストをフィールド名追従更新 |

### Phase 2: HIGH

| Finding | 修正内容 | 主な変更箇所 |
|---------|---------|------------|
| **H1** | `DiscardAndSwitchMode` / `SaveAndSwitchMode` の stale early-return 経路で guard を構造的に解放（M13 の `take()` 統合により実現） | `src/main.rs` 該当ハンドラ / `tests/mode_switch_timeout_abort.rs::discard_switch_mode_stale_releases_guard` `save_switch_mode_stale_releases_guard` |
| **H2** | `src/native_menu.rs` Win/Mac `event_stream()` で `Action::SwitchMode(_)` 発火前に `MODE_SWITCHING.load()` を確認し、`true` のとき `log::debug!` を出して `continue` する。muda accelerator はメニュー disable と独立に発火するため、Linux 側 `linux_keyboard_subscription` と同等の保護を入れる（統一決定 64） | `src/native_menu.rs::event_stream` / `tests/mode_switch_accelerator_disabled.rs::win_mac_event_stream_checks_mode_switching` |
| **H3** | `_handle_force_stop_replay` 完了時に `_replay_state = ReplayState.IDLE` を設定。STOPPING 残留で後続 `LoadReplayData` / `StartEngine` が永遠に弾かれるバグを修正 | `python/engine/server.py::_handle_force_stop_replay` / `python/tests/test_server_engine_dispatch.py::test_force_stop_replay_resets_state_to_idle` |
| **H4** | live モードでの `StopReplay` 受信時、`mode_mismatch` `EngineError` を呼び出し元のみに unicast する（broadcast すると他クライアントが偽 error 表示する）。`_send_unicast` helper を新設 | `python/engine/server.py::_send_unicast` `_handle_stop_replay` / `python/tests/test_server_engine_dispatch.py::TestStopReplayUnicast` |

### Phase 3: MEDIUM 観測性 / 仕様整合

| Finding | 修正内容 | 主な変更箇所 |
|---------|---------|------------|
| **M2 (軽量版)** | thread-local `LOCK_ORDER_INDEX` + `lock_order_acquire(name)` helper を導入。`MODE_SWITCHING(0) → SUBMIT_IN_FLIGHT(1) → APP_MODE(2) → CURRENT_PATH(3)` の固定順序を `debug_assert!` で保護し、release では `tracing::warn!` にフォールバック（統一決定 R6-82）。`Action::SwitchMode` と `restart_with_mode` で helper を呼ぶ | `src/main.rs::lock_order_acquire` `LOCK_ORDER_INDEX` / `src/main.rs::lock_order_tests` (#[should_panic] 含む 3 件) / `tests/wandb_modeswitch_lock_order.rs` 構造ガード 4 件 |
| **M4** | `Message::ModeSwitchStopTimeout` 入口に `log::warn!("[F7] StopReplay timed out — sending ForceStopReplay fallback")`、`ModeSwitchForceStopTimeout` 入口に `log::warn!("[F7] ForceStopReplay also timed out — aborting mode switch")` を追加 | `src/main.rs` 該当ハンドラ / `tests/mode_switch_timeout_abort.rs::stop_timeout_emits_warn_log` `force_stop_timeout_emits_warn_log` |
| **M5** | 保存失敗時に `Toast::error` に加えて modal `ConfirmDialog` も表示（toast の auto-dismiss で失敗が見落とされない） | `src/main.rs::SwitchModeSaveComplete` ハンドラ |

### Phase 4: MEDIUM Python WAL / IPC 契約

| Finding | 修正内容 | 主な変更箇所 |
|---------|---------|------------|
| **M6** | `wal_in_flight.py` IO エラー時 `log.warning`、Rust `has_wal_in_flight_orders` も `log::warn!` 出力。silent fallback で診断不能だった経路を観測可能化 | 両ファイル + `tests/mode_switch_in_flight_order.rs::wal_fn_logs_io_error` / `python/tests/test_wal_in_flight_detection.py::TestIoErrorLogging` |
| **M8** | `TERMINAL_STATUSES = frozenset({filled, cancelled, rejected})` 定数を導入し、その補集合で in-flight 判定する（`submitted` / `partial` 列挙ではなく）。未知ステータスは保守的に in-flight 扱い | `python/engine/wal_in_flight.py` + Rust 同等更新 / `python/tests/test_wal_in_flight_detection.py::TestUnknownStatus` / `tests/mode_switch_in_flight_order.rs::wal_fn_excludes_terminal_statuses` |
| **M9** | `wal_in_flight.py` を `_iter_lines_reverse(path, chunk_size)` で末尾から chunk 単位で逆順読み出しに切り替え。改行バイト境界で安全に分割し、長い行（chunk_size 超）でも正しく動作する。大きな WAL でも先頭を全読みしないメモリ効率版 | `python/engine/wal_in_flight.py::_iter_lines_reverse` / `TestLargeWal::test_large_wal_does_not_load_full_file` / `TestLargeWal::test_iter_lines_reverse_handles_chunk_boundary` |
| **M10** | `_handle_stop_replay` / `_handle_force_stop_replay` の `request_id` 空文字 fallback を排除。None or "" なら `EngineError(code="malformed_json")` を unicast 返却で早期 return | `python/engine/server.py` 両ハンドラ / `test_stop_replay_missing_request_id_emits_error` `test_force_stop_replay_missing_request_id_emits_error` |
| **M11** | `tachibana_orders.py` writer は `client_order_id` のみ書き出すため、`wal_in_flight.py` / Rust `has_wal_in_flight_orders` の `order_id` フィールド名 fallback を削除。テストの `TestClientOrderId` クラスも削除（単一フィールド名で fallback 不要） | 両言語 wal 検知 + `python/tests/test_wal_in_flight_detection.py` 既存 12 fixture を `client_order_id` 名に追従 |
| **M12** | `_handle_stop_replay` / `_handle_force_stop_replay` の `ws=None` 既定値を排除し必須引数化。テスト 13 件で `ws=None` を明示する | `python/engine/server.py` シグネチャ / `python/tests/test_server_engine_dispatch.py` の全コール更新 |

### Phase 5: 残 LOW + 計画書

| Finding | 修正内容 | 主な変更箇所 |
|---------|---------|------------|
| **L2** | stale `SwitchModeWithSpecs` の `mode_switch_state.is_none()` 早期 return で `log::debug!` を追加（観測性） | `src/main.rs::SwitchModeWithSpecs` ハンドラ |
| **L3** | Win/Mac `event_stream()` の役割と H2 ガードの存在を doc コメントで明記（読み手が `MODE_SWITCHING.load()` の意図をすぐ理解できるように） | `src/native_menu.rs::event_stream` ドックコメント |
| **M7** | 計画書ファイル名整合更新は本ブロック追記で完了 | 本ファイル末尾 |

### コミット履歴

```text
e1a8c79 feat(F7): ModeSwitchError 拡張と mode_switch_state tuple 統合 (M1/L1/M13)
d9ac83b fix(F7): HIGH 4 件（stale guard / Win-Mac MODE_SWITCHING / ForceStop IDLE / live unicast）
71f17bd feat(F7): MEDIUM 観測性 + WAL 契約強化 (M2/M6/M8/M9/M10/M11/M12)
d255df5 feat(F7): LOW 観測性とラウンド 2 計画書反映 (L2/L3/M7)
```

---

## レビュー反映 (2026-05-05, ラウンド 3)

`/review-fix-loop` ラウンド 3 で検出された **CRITICAL 1 + HIGH 4 + MEDIUM 10 + LOW 5 = 20 件** を
Path A（全件）の TDD で解消した。Phase 1/2/3/4+5 の 4 コミットに分割。

### 解消した指摘

| ID | 重要度 | 場所 | 内容 | 修正 |
|----|--------|------|------|------|
| **C1** | CRITICAL | `wal_in_flight.py` + `tachibana_orders.py` | writer は `phase`（`submit`/`accepted`/`rejected`）を書いていたが reader は `status` を読んでいたため、in-flight 検知が常に false negative。live→replay 切替時の WAL 安全装置が空転 | reader を `phase` に書き換え、終端 phase = `rejected` のみ。`submit`/`accepted`/未知 は in-flight 扱い。writer 経由 contract test を Python と Rust 両言語に追加 |
| **H1** | HIGH | `src/main.rs::ModeSwitchGuard::drop` | 早期 return で guard が drop される際 `LOCK_ORDER_INDEX` が高位に残る | `Drop::drop` で `lock_order_reset()` を呼ぶ RAII 化。`mode_switch_guard_drop_resets_lock_order_index` で pin |
| **H2** | HIGH | `_handle_stop_replay` 末尾 | broadcast 後に state リセットがなく STOPPING 残留 race | `_replay_state = IDLE` を broadcast の **前** に追加。`test_stop_replay_state_idle_before_broadcast` で順序を pin |
| **H3** | HIGH | `_iter_lines_reverse` chunk boundary | parametrize テストが 2 ケースしかなかった | chunk_size 11 通り × trailing_nl 2 通り × ファイルサイズ 3 種で総当たり |
| **H4** | HIGH | M10/M12 unicast テスト 13 件 | `ws=None` で unicast/broadcast の区別が pin できていなかった | `_ListOutbox` を `(ws, payload)` タプル記録に拡張、テスト 13 件を `ws=MagicMock()` 化、両ハンドラ冒頭に `assert ws is not None` 追加 |
| **M-1** | MEDIUM | 両ハンドラ冒頭 | `ws=None` silent fallback 防止 | `assert ws is not None` を追加。`test_handle_*_rejects_none_ws` で AssertionError を pin |
| **M-2** | MEDIUM | writer ↔ reader contract | C1 の根本原因テスト不在 | C1 と統合 — Python `TestWalContract` 3 件 + Rust contract test 3 件 |
| **M-3** | MEDIUM | `lock_order_acquire` 観測性 | E2E test 経路が薄い | helper 内で `log::info!(target: "lock_order", ...)` を必ず emit する軽量版 |
| **M-4** | MEDIUM | M5 modal + toast 二重表示 | save 失敗時に表示面が分散 | Toast を撤去し modal のみで通知 |
| **M-5** | MEDIUM | `_handle_force_stop_replay` の state リセット | broadcast の後で IDLE にしていた | reset → broadcast の順序に変更。`test_force_stop_replay_state_idle_before_broadcast` で hook 時点 state を assert |
| **M-rust1** | MEDIUM | `lock_order_tests` setup | 既存テストはすべて reset 済み（OK） | 新規追加テストも reset 後に開始 |
| **M-rust2** | MEDIUM | `SwitchModeWithSpecs` save 失敗 | `SwitchModeSaveComplete` と非対称で modal なし | modal alert を追加し M5 と対称化 |
| **M-rust3** | MEDIUM | `ToggleDialogModal(None)` の `mode_switch_state = None` | mode-switch 以外でも reset していた | 副作用が起きないこと（解放は Guard::drop 経由）を明示するコメント追加 |
| **M-rust4** | MEDIUM | F9d unbounded channel | スコープ外（F9 系は触らない指示）でスキップ | — |
| **M-rust5** | MEDIUM | `ModeSwitchGuard::try_acquire` runtime test | 不在 | `mode_switch_guard_try_acquire_is_exclusive_and_recoverable` を追加 |
| **M-silent4** | MEDIUM | `_send_unicast` が無駄に async | sync で書ける | `async def` → `def` 化、caller 3 箇所から `await` を除去 |
| **L1-rust** | LOW | `_lock_order_index_for` rename | leading underscore は不要 | `lock_order_index_for` に変更 |
| **L2-rust** | LOW | `ModeSwitchError::ConfirmCancelled` 未配線 | dead variant | `#[allow(dead_code)]` + 将来配線方針の docstring |
| **L3-general** | LOW | M10 エラーメッセージ | code ベース判定で十分 | 既存実装で OK |
| **L4-general** | LOW | 「### 設計判断」見出し重複 | 同一見出し 2 連続 | 後者を「### 追加の設計判断（review-fix-loop で発覚）」にリネーム |
| **L5-silent** | LOW | `wal_in_flight.py` docstring | writer schema 変更時の更新ガイドが弱い | WARNING 追記、3 箇所同時更新の注意、writer 経由 contract test を先に書く方針 |

### 設計判断 / 新たな知見

- **WAL writer/reader contract は言語境界で別々に管理されるため、writer 経由 fixture を必ず追加する**。同一言語 mock のみだと writer の wire schema 変更で reader が silent regression する。F7 ラウンド 3 の C1 は writer (`phase`) と reader (`status`) のキー食い違いを fixture テスト（reader 単独）の hard-coded fixture が隠蔽していた典型例。`MISSES.md` の「同一言語テスト」見逃しパターンに該当。
- **`ModeSwitchGuard::drop` の RAII 範囲は `MODE_SWITCHING` atomic だけでは不十分**。`LOCK_ORDER_INDEX` thread-local も同時に reset すべき。RAII オブジェクトが管理する状態は「全部」明示すること。
- **state machine の reset は broadcast の前**。`ReplayStopped` を受け取ったクライアントが「state は既に IDLE」前提で次の Command を投げる race を防ぐため、reset → broadcast の順序を invariants として pin。
- **assert は contract のドキュメンテーション**。`assert ws is not None, "ws is mandatory ..."` は型では表現できない不変条件を実行時に明示できる。サイレントに broadcast にフォールバックされるよりは AssertionError で停止する方が診断しやすい。

### 次回 MISSES.md 追記候補

- writer/reader wire schema の言語境界 contract test の徹底化（writer 経由 fixture を必ず 1 件以上追加する規約）
- RAII オブジェクトが thread-local / global state を持つ場合、Drop で全部 reset することの強制（コードレビュー時の checklist 化）
- async helper を sync で書ける場合は sync を選好する（無駄な await を caller に強要しないため）

### コミット履歴（ラウンド 3）

```text
17acacf fix(F7): C1 — WAL writer/reader schema 不整合を修正し contract test を追加する
fec2764 fix(F7): HIGH 4 件（lock_order RAII / StopReplay state / chunk boundary / unicast pin）
4e3b330 fix(F7): MEDIUM 10 件（dialog 対称化 / lock-order tracing / state race / runtime test）
(本コミット): fix(F7): LOW + ラウンド 3 計画書反映
```
