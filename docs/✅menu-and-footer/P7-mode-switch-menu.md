<a id="p7"></a>
# Mode メニュー新設実装計画（live ↔ replay 切替）

**作成日**: 2026-05-04
**作成者**: Claude Opus 4.7（botterYosuke）
**ステータス**: 未着手・実装計画
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

### 4 軸 matrix（不変条件）

`(現モード, 切替先, In-flight order, EngineBusy)` の 4 軸で挙動を完全に定義する。

| 現モード | 切替先 | In-flight order | EngineBusy | 期待挙動 |
|---------|-------|-----------------|------------|---------|
| live | replay | あり | -（対象外） | restart 中止 + 「未約定注文があります」エラー dialog（WAL は read-only 参照のみ、書き換えない） |
| live | replay | なし | あり | restart 中止 + 「engine がビジー状態です」エラー dialog（`BusyError` 由来） |
| live | replay | なし | なし | dirty なら F4 confirm → 自動保存 flush → engine 停止 → restart |
| replay | live | -（対象外） | あり | restart 中止 + エラー dialog |
| replay | live | -（対象外） | なし | `Command::StopReplay` → `Event::ReplayStopped` 待ち（5s） → engine 停止 → restart。タイムアウト時は `Command::ForceStopReplay` を送って強制停止 fallback を発火 → 再度 `Event::ReplayStopped`（最大 2s）→ engine 停止 → restart。`ForceStopReplay` も失敗したケースのみ restart 中止 + エラー dialog（[統一決定 35](#q4)）|
| live | live | -（対象外） | -（対象外） | no-op（メニューは disable） |
| replay | replay | -（対象外） | -（対象外） | no-op（メニューは disable） |

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

- 取得失敗時（`AlreadySwitching`）はメニュークリックを no-op にする
- メニュー側でも `モード（Mode）` サブメニュー全体を `MODE_SWITCHING == true` の間は disable
- `Flowsurface::new()` の APP_MODE read は **lock 取得 → 値 clone → drop** の順で行い、`MODE_SWITCHING` は確認しない（new() は restart 末尾で呼ばれるため、その時点では既に切替処理が write フェーズに入っている）。lock 取得順は常に `MODE_SWITCHING` → `APP_MODE` → `CURRENT_PATH` の固定順序にしてデッドロックを防ぐ

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
4. 4 軸 matrix の各分岐（in-flight order あり / EngineBusy / 保存失敗 / 停止失敗 / confirm cancel）で restart が中止され、対応するエラー dialog が表示される
5. メニュー連打で `restart_with_mode` が二重起動しない（`MODE_SWITCHING` AtomicBool で保護）
6. モード切替で engine プロセスが必ず再起動し、`engine-session.json` が一度削除→再生成される
7. `tachibana_orders.jsonl` WAL に未約定エントリがある状態で live → replay を選ぶと restart が中止される
8. アクセラレータが Win/Mac で muda 経由のみ発火し、Linux で iced kbd 経由でのみ発火する（二重発火しない）
9. `restart_with_mode` 内 panic 後に次の SwitchMode が `AlreadySwitching` で stuck しないこと（`ModeSwitchGuard` Drop による RAII 解放。`tests/mode_switch_panic_recovery.rs` で保護）
10. replay → live で `Event::ReplayStopped` が 5s タイムアウトしたときに `Command::ForceStopReplay` fallback で停止 → 後続 SwitchMode が成功すること（`tests/mode_switch_stop_timeout.rs` で保護）
11. 2 並行 SwitchMode は片方が `Err(AlreadySwitching)` / skip となり、guard 解放後の再試行で成功すること（`tests/mode_switch_reentry.rs` で保護）
12. WAL in-flight 検知が tail 逆順スキャンで実装され、部分約定→全約定 / プロセスクラッシュ残留の両 fixture で正しく判定されること（`python/tests/test_wal_in_flight_detection.py` で保護）

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

### Python テスト

| ファイル | 期待 | 観測ポイント |
|---------|------|------------|
| `python/tests/test_wal_in_flight_detection.py` | `tachibana_orders.jsonl` の tail 逆順スキャンで in-flight 判定が正しく行われること（[統一決定 34](#q4)）。fixture 2 種：(1) 部分約定→全約定（`partial` の後 `filled`）→ in-flight=空、(2) プロセスクラッシュで `submitted` のみ残留 → in-flight=該当 order_id を含む | `detect_in_flight_orders(path)` の戻り値の集合比較 |

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
