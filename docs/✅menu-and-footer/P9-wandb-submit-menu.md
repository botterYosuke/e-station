<a id="p9"></a>
# W&B 登録メニュー実装計画（replay 結果のポストラン送信）

**作成日**: 2026-05-04
**作成者**: Claude Opus 4.7（botterYosuke）
**ステータス**: 未着手・実装計画
**起点課題**: [fix-save-menu.md](./fix-save-menu.md) P9（W&B 登録の動線がメニューに無い）
**関連 F\***: [fix-save-menu.md](./fix-save-menu.md) F2（accelerator）/ [F4](./fix-save-menu.md#f4)（confirm 共通化）/ [F6](./fix-save-menu.md#f6)（SCENARIO 抽出）
**関連スキル**: [/wandb](../../.claude/skills/wandb/SKILL.md) / [wandb-vision.md](../plan/wandb-vision.md)
（`examples/wandb/` 配下のレシピを追加・修正するときは必ず `/wandb` スキルを起動すること。）

> 凡例（F\* / P\* / Phase 8.x）は [fix-save-menu.md](./fix-save-menu.md) 冒頭の表を参照。
> **F\* 番号空間**: F\* は `✅menu-and-footer/` 配下計画書共通の実装フェーズ番号空間。本書の F9a〜F9e はこの共通番号空間の一部（統一決定 R1-P9 #40）。
>
> **表記凡例**: `W&B` = 製品名（UI／本文）、`wandb` = Python パッケージ／CLI、`Weights & Biases` = フルネーム（初出時のみ）。
> 用語は P5 用語表（[fix-save-menu.md](./fix-save-menu.md) 用語節）に揃える。
>
> **RunBuffer 表記凡例（統一決定 R3-74）**:
> - `RunBuffer` = 概念・仕様レベルの呼称（本文・図・テスト名で使用）
> - `run-buffer/` = ファイルシステム上のディレクトリ名（パス表記）
> - `run_buffer_*` = Python / Rust の識別子（関数・モジュール・変数名）

---

<a id="overview"></a>
## やること

メニューバーに `ツール（Tools）` サブメニューを新設し、`W&B に登録…（Submit to W&B）`
を置く。replay が完了している（= 1 回以上「Replay を停止」まで到達した）状態でのみ enable。

```text
File   モード（Mode）   ツール（Tools）
                       └─ W&B に登録…（Submit to W&B）  Ctrl+Shift+W
```

> 詳細表（メニューラベル英日対応・W&B 系含む）は
> [fix-save-menu.md §メニューラベル表記の統一](./fix-save-menu.md#menu-labels) を参照。

押下すると、**直近の replay 実行中に蓄積した narrative / fills / equity 列を
読み込み、別プロセスの送信ヘルパー (`examples/wandb/submit_run.py`) に
JSONL アーティファクトのパスと SCENARIO config を渡して `wandb.init() →
wandb.log() → wandb.finish()` を回す**。Flow Surface 本体（`src/` /
`python/engine/`）は **`import wandb` を一切持たない**。

### この設計の不変条件

- ストラテジー `.py` は `import wandb` 禁止（`/wandb` スキルのコア非汚染ルール）
- `python/engine/` も `import wandb` 禁止。送信は `examples/wandb/` の独立スクリプト
- replay 結果は **その場では W&B に送らず**、まずローカル JSONL（`run-buffer/`）に蓄積。
  メニュー操作は「既に確定した結果」を別プロセスで送るだけのワンショット
- replay 中に wandb 障害があっても strategy / 決定論性は影響を受けない
- live モードは対象外（W&B 登録メニューは disable）

---

<a id="background"></a>
## 背景

[wandb-vision.md](../plan/wandb-vision.md) は「ストラテジー内で `wandb.init()` を呼ぶ」
SDK 形を想定していたが、ユーザー方針として **戦略 .py に W&B 依存を持たせない**
ことが確定した（[memory: project_user_strategy_responsibility / project_no_bundled_ai]
および会話確認 2026-05-04）。

理由：
- replay の決定論性を保つ（`wandb.init` のネットワーク I/O が backtest 結果に混ざらない）
- 同一戦略を live で走らせる時に W&B 接続を強制したくない
- W&B 障害がストラテジー実行を巻き添えにしない
- wandb は重量依存（gql / sentry-sdk 等）。コア配布物に同梱しない

→ replay 終了後にユーザーが **明示的にメニューから登録** する経路に分離する。

---

<a id="data-flow"></a>
## データフロー

```
[ Strategy .py (純粋) ]
        │ events (fills / pnl / narrative)
        ▼
[ ReplaySession.run(on_event=...) ]
        │ tee
        ├─────────────► GUI 描画（既存）
        ▼
[ RunBuffer (新設) ]
   %APPDATA%\flowsurface\run-buffer\<run_id>\
   ├── meta.json          # SCENARIO + strategy_file + git rev + timestamps
   ├── fills.jsonl        # 約定列
   ├── equity.jsonl       # 1 バー毎 equity / position / cash
   └── narrative.jsonl    # NarrativeHook 出力（任意）
        │
        │  ── ユーザーがメニュー押下 ──
        ▼
[ examples/wandb/submit_run.py (subprocess) ]
   - wandb.init(project=..., config=meta.json)
   - wandb.log({equity, pnl, ...}) を順次
   - wandb.Table(narrative.jsonl) を 1 つ
   - wandb.Artifact で fills/narrative を添付
   - wandb.finish()
        ▼
[ W&B Cloud Dashboard ]
```

`run_id` は replay 開始時に `<UTC秒>-<strategy_stem>-<instrument>` で確定する
（例: `1714800123-buy_and_hold-1301_TSE`）。

---

<a id="run-buffer-spec"></a>
## RunBuffer 仕様

> **IPC 拡張不要の不変条件（統一決定 R3-63）**: `meta.json` の `scenario` フィールドは
> **Python 内（`replay_session.py` の RunBuffer writer + `submit_run.py`）で完結**する。
> Rust ↔ Python WebSocket IPC schema には追加フィールドが乗らないため、`SCHEMA_MINOR`
> 増分は **P5 F6a（SCENARIO 抽出時）でのみ**発生する。本 P9 で `SCHEMA_MAJOR` /
> `SCHEMA_MINOR` を bump しない。

### 配置

| OS | パス |
|----|------|
| Windows | `%APPDATA%\flowsurface\run-buffer\<run_id>\` |
| macOS | `~/Library/Application Support/flowsurface/run-buffer/<run_id>/` |
| Linux | `~/.local/share/flowsurface/run-buffer/<run_id>/` |

`saved-state.json` と同じ data dir 配下。F6 の path ガード（永続状態ディレクトリへの
`.py` 書き戻し禁止）と整合させるため、**`run-buffer/` 配下は SCENARIO 書き戻し対象外**
であることをガードに明記する。

**ログ severity 契約（BC3-10）**: F6c が emit する `error="path_guard_violation"` 行は、
Rust 側 receiver が**特別扱い**する。`path_guard_violation` のみ **`tracing::error!`** で
`BUG:` 接頭辞を付けて記録（実装バグの可能性が高いため）。それ以外の F6 系 error は
**`tracing::warn!`** に留める（ユーザー入力起因が多いため）。

### ファイル形式

`meta.json`:

```json
{
  "schema_version": 1,
  "run_id": "1714800123-buy_and_hold-1301_TSE",
  "strategy_file": "docs/example/buy_and_hold.py",
  "strategy_sha256": "<file digest>",
  "git_rev": "<HEAD or 'dirty'>",
  "scenario": {
    "instrument": "1301.TSE",
    "start": "2025-01-06",
    "end": "2025-03-31",
    "granularity": "1m",
    "initial_cash": 1000000
  },
  "started_at": "2026-05-04T07:42:03Z",
  "finished_at": "2026-05-04T07:43:11Z",
  "status": "completed"
}
```

`fills.jsonl` / `equity.jsonl` / `narrative.jsonl`: 1 行 1 JSON。
**append-only で `os.replace` 不要**（途中クラッシュ時は `status="aborted"` として残り、
送信時はそれをスキップ。下記正規化ロジック参照）。

#### PII allow-list（統一決定 47）

`fills.jsonl` / `equity.jsonl` / `narrative.jsonl` の書き出し層は
`examples/wandb/pii_scrub.py` を **必須経由**する。許可フィールドは
`symbol, side, qty, price, ts, pnl` のみ。立花口座番号・token・venue raw payload・
ログイン credential はバッファに 1 バイトも書かれてはならない（F9a DoD で assert）。
`submit_run.py` 側でも upload 直前に再 sanity check（不明 key 検出で abort）して二重化する。

### 書き出しタイミング

- **replay 開始（`Command::StartEngine` 受領時）**: `meta.json` を `status="running"` で書く
- **各 event 到着時（Python helper の `_AttachClient` / `_InProcess` の event loop）**:
  該当 jsonl に append（`fills` は Fill event、`equity` は EquityUpdate / PnLSnapshot 系、
  `narrative` は NarrativeWritten event を写す）
- **`Event::ReplayStopped` 受領時の書き戻し順序契約（統一決定 BC3-5。5 ステップ）**:
  1. `Event::ReplayStopped` を helper の event loop で受領
  2. `fills.jsonl` を `flush()` + `os.fsync(fd)`
  3. `equity.jsonl` を `flush()` + `os.fsync(fd)`
  4. `narrative.jsonl` を `flush()` + `os.fsync(fd)`
  5. `meta.json` を `status="completed"` + `finished_at` で **atomic rewrite**（`tempfile + os.replace`）

  **不変条件**: jsonl 群の fsync が **完了する前に** `meta.json` を `completed` に切り替えない。
  クラッシュで jsonl 末尾が欠落した状態で `completed` と記録されると submit が壊れた run を
  upload するため。F9a DoD に
  `python/tests/test_run_buffer_writer.py::test_jsonl_flushed_before_meta_completed`
  を追加し、`meta.json` rewrite 前に全 jsonl の fd が fsync されたことを mock 順序で assert する。
- **engine プロセス Drop / SIGTERM / クラッシュ時**: engine の `atexit` / signal handler が
  `status="aborted"` を **atomic 書き込み**（最善努力。統一決定 50）。
  GUI 起動時のスキャナは「`running` のまま & ロックファイル無し」を `aborted` 扱いに
  正規化する（F9a DoD に正規化テスト）。
- **wandb run lifecycle と graceful finish**（統一決定 45）:
  `submit_run.py` は `try/finally: wandb.finish(exit_code=non_zero, quiet=True)` ＋
  `signal.signal(SIGTERM, ...)` で graceful finish。Rust 側は subprocess に SIGTERM を
  送ったあと **5 秒 grace period** を待ってから kill する。ModeSwitchGuard (P7) は
  active submit 中のモード切替を block する不変条件として相互参照する。

### バッファ保持ポリシー

- **無制限保持しない**: `run-buffer/` 配下を **作成日時で 30 件まで**保持。
  超過分は古い順に削除（GUI 起動時に 1 回だけ実施）
- 上限・保持期間は `%APPDATA%\flowsurface\config.toml`（無ければデフォルト）で
  上書き可能（V2 以降）
- **LRU race 回避**（統一決定 51 + R3-59）: `submit_run.py` 起動時に該当 run ディレクトリへ
  `.lock` ファイルを置き、削除側（GUI 起動時 LRU sweep）は `.lock` の存在するディレクトリを
  skip する。送信完了後に `.lock` を削除。

  **`.lock` ファイル構造（統一決定 R3-59）**: 中身は単一の JSON オブジェクト。

  ```json
  {
    "pid": 12345,
    "started_at": "2026-05-04T07:42:03Z"
  }
  ```

  - `pid`: `submit_run.py` プロセスの PID（`os.getpid()`）
  - `started_at`: lock 取得時刻（UTC ISO8601）

  **dead PID 検出ロジック**: GUI 起動時の LRU sweep スキャナは `.lock` を見つけたら
  JSON を読み、以下の手順で dead 判定する：

  1. JSON parse 失敗 → 破損 lock 扱い → 強制削除
  2. `pid` のプロセスが存在しない（POSIX: `os.kill(pid, 0)` が `ProcessLookupError` /
     Windows: `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, pid)` が失敗）→ 強制削除
  3. プロセスは存在するが `started_at` から **24 時間以上**経過 → 強制削除（暴走 submit 救済）
  4. それ以外 → skip（生存中）

  F9b DoD に `examples/wandb/tests/test_submit_run.py::test_stale_lock_removed`
  を追加し、（a）破損 JSON、（b）存在しない PID、（c）24h 超の lock をそれぞれ
  起動時 sweep が削除することを assert する。

---

<a id="menu"></a>
## メニュー仕様

### ラベル / アクセラレータ

ラベル英日対応は [fix-save-menu.md §メニューラベル表記の統一](./fix-save-menu.md#menu-labels)
に集約されている。本書ではラベル名のみ参照し、表記の冗長記述は削減する。

| ラベル | アクセラレータ | enable 条件 |
|--------|---------------|------------|
| `W&B に登録…` | `Ctrl+Shift+W` | replay モード **かつ** 直近 1 件以上の `status="completed"` run buffer が存在 **かつ 認証済み**（未認証なら disable + tooltip「W&B にログインしてください」、buffer 空なら disable + tooltip「送信可能な run がありません（最初に replay を実行してください）」。判定は Python 側のみ — [Q10](#q10) / [Q11](#q11) 参照） |
| `W&B にログイン…` | なし | 未ログイン時のみ enable、ログイン済みなら disable + tooltip「ログイン済みです」（`SignOutWandb` と相互 disable） |
| `W&B からログアウト` | なし | netrc にエントリがある時のみ enable、未ログインなら disable + tooltip「ログインしていません」（`SignInWandb` と相互 disable） |
| `送信履歴を開く` | なし | **常に表示**。buffer 0 件なら `enabled=false` でグレー表示 + tooltip「送信履歴がまだありません」 |
| `バッファを削除…` | なし | run buffer が 1 件以上存在で enable、0 件なら disable + tooltip「削除できるバッファがありません」 |

各項目の `enabled` / `tooltip` の正本は [§enable / disable 計算](#enable--disable-計算) の
`tools_actions_for_state` 戻り値表（**統一決定 R7-86** で `Vec<MenuEntry>` に統一）。本表はその要約。

mac は `Cmd+Shift+W` に muda が自動変換する（[F2](./fix-save-menu.md#f2) と同じ仕組み）。
**アクセラレータ二重発火回避**: `Ctrl+Shift+W` は [F2 / Q6](./fix-save-menu.md#f2) と同じ二重発火回避ポリシー
（メニュー disable と accelerator dispatch の同期）に従う。F9c DoD に `no_double_dispatch`
相当ケースを追加（統一決定 55）。

#### 再入禁止（統一決定 46）

active な submit がある間は、メニュー / accelerator の双方を disable する。

```rust
struct Flowsurface {
    // ...
    submit_in_flight: Mutex<Option<SubmitInFlight>>,
}
```

`submit_in_flight` が `Some(_)` の間は `Action::SubmitToWandb` を発火させない。
F9c DoD に `wandb_submit_no_double_dispatch` / `wandb_accelerator_disabled_during_submit`
の 2 ケースを追加。

#### mode 切替の不変条件（AD3-1 / 統一決定 R3-58）

P7 ModeSwitchGuard との二重保護のため、本 P9 では「submit 中の mode 切替を block する」
責務を `submit_in_flight` という独立 lock として定義する。
P7 §5 軸 matrix に `submit_in_flight = Some` 行を追加し、本書とは双方向参照する。

**lock 取得順（統一決定 R3-58、P7 §5 と双方向に明記）**:

```
MODE_SWITCHING → submit_in_flight → APP_MODE → CURRENT_PATH
```

- `MODE_SWITCHING`: P7 で定義する mode 切替の atomic flag
- `submit_in_flight`: 本 P9 の `Mutex<Option<SubmitInFlight>>`
- `APP_MODE`: 現在の `live` / `replay` を保持する state
- `CURRENT_PATH`: 開いている saved-state ファイルパス

**逆順取得は禁止**（dead-lock 検出の観点から構造的に不可）。
F9c DoD に `tests/wandb_modeswitch_lock_order.rs` を追加し、4 lock を反転順で取ろうとした
ケースが debug_assert で panic することを assert する。詳細・matrix は P7 §5 を正本とする。

### Action / Message

`native_menu::Action` に以下を追加：

```rust
pub enum Action {
    // 既存...
    SubmitToWandb,
    SignInWandb,
    SignOutWandb,
    OpenSubmissionLog,
    ClearRunBuffer,
}
```

`Message::NativeMenuAction(Action::SubmitToWandb)` で受け、最新の
`status="completed"` run buffer を選んで `examples/wandb/submit_run.py` を spawn する。
**複数 run の一括選択 UI は V2**。V1 は「最新 1 件」固定。

### enable / disable 計算

Tools サブメニュー各項目の enable/disable / tooltip / 相互 disable は
**`tools_actions_for_state(auth_state: &WandbAuthState, buffer_state: &RunBufferIndex) -> Vec<MenuEntry>` の戻り値**で計算する（**統一決定 R7-86**）。

戻り値型は `Vec<Action>` ではなく `Vec<MenuEntry>` に統一する。`MenuEntry` は
[P8 §実装スケッチ](./P8-widget-menu-bar-linux.md#impl-sketch) で単一定義し、本書はそれを参照する：

```rust
// 単一定義は P8 §実装スケッチ。再掲（参照）。
pub struct MenuEntry {
    pub action: Action,
    pub enabled: bool,
    pub tooltip: Option<&'static str>,
    pub checked: bool, // 将来の checkable 項目向け。Tools では常に false。
}
```

`Vec<Action>` のままでは「表示するが disable」「tooltip 文言を出し分ける」「ログイン / ログアウトの相互 disable」を表現できないため、
本 R7 修正で全 Tools サブメニュー項目の状態を `MenuEntry` で具体的に列挙する。
`actions_for_mode` のシグネチャには触れない（[P8 DoD-11](./P8-widget-menu-bar-linux.md#dod) 整合：
`actions_for_mode` の期待値は File/Mode 由来のみで、Tools サブメニュー Action は混入しない。R3-66/69 / R6-83 で確定）。

- `run_buffer_state = RunBufferIndex { latest_completed: Option<RunId>, total: usize }` は
  `tools_actions_for_state` の引数として参照する（後述 [Cargo / プラットフォーム](#cargo-platform) の
  `menu_items_tools(auth_state, buffer_state) -> Vec<MenuEntry>` と同じ純関数族。
  `MenuEntry` の単一定義は [P8 §実装スケッチ](./P8-widget-menu-bar-linux.md#impl-sketch) を参照）
- 再計算タイミング（GUI 起動時 / `Event::ReplayStopped` 時 / メニュー再構築時に `run-buffer/` を
  ディレクトリ走査）は据え置き。コストは数十エントリ程度なので毎回スキャンで十分

#### 状態 × 項目の表（`tools_actions_for_state` の戻り値仕様）

`auth_state.authenticated` を `auth ∈ {未, 済}`、`buffer_state.latest_completed.is_some()` を
`buffer ∈ {空, 有}` と表記する。各項目の `MenuEntry { enabled, tooltip }` は次表のとおり：

| 項目 (Action) | auth | buffer | enabled | tooltip |
|---|---|---|---|---|
| `SignInWandb`（W&B にログイン…） | 未 | * | `true` | `None` |
| `SignInWandb`（W&B にログイン…） | 済 | * | `false` | `Some("ログイン済みです")` |
| `SignOutWandb`（W&B からログアウト） | 済 | * | `true` | `None` |
| `SignOutWandb`（W&B からログアウト） | 未 | * | `false` | `Some("ログインしていません")` |
| `SubmitToWandb`（W&B に登録…） | 済 | 有 | `true` | `None` |
| `SubmitToWandb`（W&B に登録…） | 未 | * | `false` | `Some("W&B にログインしてください")` |
| `SubmitToWandb`（W&B に登録…） | 済 | 空 | `false` | `Some("送信可能な run がありません（最初に replay を実行してください）")` |
| `OpenSubmissionLog`（送信履歴を開く） | * | 有 | `true` | `None` |
| `OpenSubmissionLog`（送信履歴を開く） | * | 空 | `false` | `Some("送信履歴がまだありません")` |
| `ClearRunBuffer`（バッファを削除…） | * | 有 | `true` | `None` |
| `ClearRunBuffer`（バッファを削除…） | * | 空 | `false` | `Some("削除できるバッファがありません")` |

不変条件：
- `SignInWandb` と `SignOutWandb` は **常にどちらか一方のみ enabled**（相互 disable）。
  両方 enabled / 両方 disabled になるケースは存在しない（auth は二値）
- `OpenSubmissionLog` は **常に `Vec<MenuEntry>` に含まれる**（buffer 空でも要素として返り、
  `enabled=false` でグレー表示する）。`buffer=空` で要素ごと省略しない
- `tooltip` は `enabled=true` のときは原則 `None`（説明不要）。`enabled=false` のときに
  理由を必ず提示する（`Option<&'static str>` の `Some(_)`）

F9c DoD `tests/wandb_menu_action.rs` には auth × buffer の **2×2 = 4 組合せ**を
パラメタライズドで網羅し、各組合せで返る `Vec<MenuEntry>` の `(action, enabled, tooltip)`
タプル列を上表どおりに assert する。

### 送信モーダル UI

メニュー押下 → `WandbSubmitModal`（新設）を表示：

| フィールド | 既定値 | 必須/任意 |
|-----------|--------|----------|
| W&B Project | `flowsurface-strategies` | ✓ |
| Run name | `<strategy_stem> @ <instrument> <start>..<end>` | ✓ |
| Tags（カンマ区切り） | `replay,<strategy_stem>` | 任意 |
| Notes | 空 | 任意 |
| API key 状態 | `未設定 / env 経由 / netrc 経由` の 3 値表示 | ✓（`未設定`ならボタン disable + 「W&B にログインしてください」。なお未認証時は `tools_actions_for_state` が `SubmitToWandb` を `enabled=false` で返すためそもそもモーダルを開けない経路が正。本欄はモーダル内の二重ガード） |

API key 状態の表示は **`未設定 / env 経由 / netrc 経由` の 3 値のみ**。key 文字列は 1 文字も
UI に出さない契約（統一決定 53）。テストでモーダル表示文字列に key 値が含まれないことを assert。

「送信」ボタン押下で `examples/wandb/submit_run.py` を spawn し、stdout を
モーダル内のログ領域に tail 表示。stdout / log / tracing の **全出口は `mask_secrets()`
を必ず通す**（統一決定 44 / 後述 `MaskedLine` newtype を参照）。
終了コード 0 → 「成功」緑文字 + URL をモーダルに表示（クリックでブラウザを開く）。
非 0 → `WandbError.kind`（`auth / rate_limit / network / server_5xx / partial`、
exit code 0/2/3/4/5/6）に応じて個別文言のダイアログ（統一決定 48）。F9b DoD に kind 別テスト。

「キャンセル」は subprocess に SIGTERM を送り、5 秒 grace period 後に kill（送信失敗扱い）。

---

<a id="submit-script"></a>
## examples/wandb/submit_run.py 仕様

```bash
uv run --with wandb python examples/wandb/submit_run.py \
    --run-buffer "%APPDATA%\flowsurface\run-buffer\1714800123-buy_and_hold-1301_TSE" \
    --project flowsurface-strategies \
    --run-name "buy_and_hold @ 1301.TSE 2025-01-06..2025-03-31" \
    --tags replay,buy_and_hold
```

- **`uv run --with wandb`** で `wandb` パッケージを ad-hoc 注入（プロジェクト本体の `pyproject.toml`
  に `wandb` を追加しない／コア非汚染ルール）
- `meta.json` を読んで `wandb.init(config=meta["scenario"] | extra)` を呼ぶ
- `equity.jsonl` を 1 行ずつ読み `wandb.log({"equity": ..., "pnl": ..., "step": bar_index})`
- `narrative.jsonl` を `wandb.Table` に流し込んで `wandb.log({"narrative": table})`
- `fills.jsonl` を `wandb.Artifact("fills", type="dataset")` に添付
- 終了時 `wandb.finish()` の戻り値の `Run.url` を **stdout の最終行に `URL: <url>` で出力**
  （Rust 側はこの行をパースしてモーダルに表示）

### Rust 側の subprocess 起動

`std::process::Command::new("uv").args(["run", "--with", "wandb", "python",
script_path, ...])` で起動。`spawn()` して `BufReader` で stdout を行毎に読み、
`Subscription` に流す（既存の engine spawn と同じパターン）。

`WANDB_API_KEY` は **親プロセスの env をそのまま継承**（GUI 起動時に
`launch.json` / shell に export しておく前提）。**Rust 側で API key を読まない**
（メモリに残さない・ログに出さない）。

### スクリプトの場所

`examples/wandb/submit_run.py` を新設する。`/wandb` スキルが規定する
`examples/wandb/` 配置と整合。同ディレクトリに `README.md` で「メニューから
自動起動される。手動でも `uv run --with wandb python examples/wandb/submit_run.py
--run-buffer <path>` で叩ける」と書く。

---

<a id="cargo-platform"></a>
## Cargo / プラットフォーム

- muda（Win/Mac）と iced 自前メニュー（Linux、[./P8-widget-menu-bar-linux.md](./P8-widget-menu-bar-linux.md)）
  双方に `ツール（Tools）` サブメニューを実装
- **Linux 自前メニュー側の追加責務（BC3-9）**: F9c DoD で
  `src/widget/widget_menu_bar.rs`（P8 で導入）に `TopMenu::Tools` バリアントと、
  `menu_items_tools(auth_state: &WandbAuthState, buffer_state: &RunBufferIndex) -> Vec<MenuEntry>`
  純関数を追加する（**統一決定 R7-86**。戻り値型は `tools_actions_for_state` と同じ
  `Vec<MenuEntry>`。`MenuEntry` の単一定義は [P8 §実装スケッチ](./P8-widget-menu-bar-linux.md#impl-sketch)）。`src/widget/widget_menu_bar_state.rs` の遷移 matrix（現状 3×3）を
  `File / Mode / Tools / Help` の **4×4** に拡張し、`TopMenu::Tools` 開閉時の
  キーボード focus 推移を既存 matrix と同じ方式で網羅する
- アクセラレータは [F2](./fix-save-menu.md#f2) の方針（muda 正規 + Linux 限定 iced fallback）に従う
- **W&B URL のブラウザオープン方針**（統一決定 56）: 矛盾解消のため次のいずれかに確定する。
  - **採用案**: `Cargo.toml` に `webbrowser` クレートを追加し、`webbrowser::open(url)` を使う
    （Win/Mac/Linux 横断で薄いラッパ。コードの簡潔さを優先）
  - 代替案（採用しない）: subprocess 経由で `cmd /c start <url>`（Win）/ `xdg-open <url>`（Linux）/
    `open <url>`（Mac）を呼び分ける（依存ゼロ）
  - 「新規依存なし」と「無ければ追加」が併記されていた旧記述は本決定で上書きする。

---

<a id="design-questions"></a>
## 設計判断（確定済み）

### Q1. 戦略 .py に wandb を入れるか → **入れない**

ストラテジーは pure（[memory: project_user_strategy_responsibility]）。
W&B は外部ダッシュボード扱いで `examples/wandb/submit_run.py` に閉じる。

### Q2. replay 中にリアルタイム送信するか → **しない（V1）**

理由：
- 決定論性保証（ネットワーク I/O が混ざらない）
- W&B 障害が strategy を巻き添えにしない
- ユーザー操作（メニュー押下）を明示トリガーにすることで「意図せず private な
  実験結果がアップロードされる」事故を防ぐ

将来 streaming sink 対応する場合は `ReplaySession` 側に optional hook を置く
（V2 以降）。strategy 側は変えない。

### Q3. RunBuffer の保存場所 → **`%APPDATA%\flowsurface\run-buffer\`**

`saved-state.json` と同じ data dir 配下。F6 path ガードで「`.py` 書き戻し時に
このディレクトリへの書き込み禁止」を明記する。

### Q4. wandb 依存の取り扱い → **`uv run --with wandb` で ad-hoc 注入**

- 本体 `pyproject.toml` には追加しない（コア非汚染）
- `examples/wandb/submit_run.py` は wandb をハードリクエストする（`requirements`
  はファイル冒頭コメントで明記）
- ユーザー側で `uv` が無い場合は「`uv` をインストールしてください」エラーダイアログ

### Q5. API key の扱い → **`WANDB_API_KEY` env と `~/.netrc` の両対応（[Q10](#q10) で詳細）**

- Rust 側はファイルにも GUI state にも保存しない
- 解決ロジックは **Python 側に一元化**（[Q11](#q11) 参照）。Rust は判定結果（bool）のみ受け取る
- 未認証時は **`W&B に登録…` メニュー自体を disable** にし、tooltip / status bar で
  「W&B にログインしてください」と理由を提示する（モーダル内 disable 案は不採用）

### Q6. 複数 run の一括送信 → **V1 は最新 1 件のみ**

UI の複雑度を抑える。V2 以降で `送信履歴を開く` モーダルから過去 run を
個別に再送信できるようにする。

### Q7. 送信失敗時の retry → **手動再送のみ（V1）**

run buffer は残り続けるので、ユーザーが再度メニューから送信すれば良い。
自動 retry はネットワーク不具合時に重複 run を作りやすく、トレードオフが悪い。

### Q8. live モードの登録 → **対象外（V1）**

live は無限長で「終わり」がないため、W&B run の境界を切る判断が UI に必要になる
（時間切り？ 損益切り？）。本計画では replay のみに絞る。
live 対応は別計画として deferred。

<a id="q10"></a>
### Q10. W&B 認証情報の管理 → **`~/.netrc` 委譲（wandb 標準フロー）**

Flow Surface 自身が credential store を持たず、**wandb CLI が公式に使う `~/.netrc` に委ねる**。
鍵をアプリのメモリ・設定ファイル・OS keyring に保存しない。

#### 解決優先順位（wandb ライブラリの仕様）

1. `WANDB_API_KEY` 環境変数（CI / 一時デバッグ用に最優先）
2. `~/.netrc`（Windows は `%USERPROFILE%\_netrc`）の `machine api.wandb.ai` エントリ
3. （TTY なら）対話プロンプト — Flow Surface からは到達しない

→ Flow Surface は **2 番目を整備するメニュー**を提供するだけで良い。

#### ログイン UX フロー

`ツール > W&B にログイン…（Sign in to W&B）` 押下 → `WandbSignInModal` 表示：

1. 「ブラウザで API キーを取得」ボタン → `webbrowser` クレートで
   `https://wandb.ai/authorize` を開く（ユーザーは表示された key をコピーして戻る）
2. 「API キー」入力欄（**password field、表示マスク**、貼り付けは可）
3. 「ログイン」押下 → `wandb login --relogin <key>` を subprocess 実行
   （`uv run --with wandb wandb login --relogin <key>`）
4. exit code 0 → モーダル閉じ、「ログインしました: `<username>`」を toast 表示
5. **subprocess 終了と同時にモーダルの key 文字列を `String::clear()` + zeroize**
   （メモリ上に残さない）
6. exit code 非 0 → エラーメッセージ表示（key は欄に残さず即クリア）

`wandb login` は内部で netrc を 0600 で書き込むため、Flow Surface は permission 操作を
持たない。

#### ログアウト UX

`ツール > W&B からログアウト（Sign out）` → confirm dialog → `wandb logout` subprocess。
成功すると netrc のエントリが削除される。Flow Surface は確認するだけ。

#### ログイン状態の検出 → **Python 側に一元化（[Q11](#q11) 参照）**

判定ロジック（env / netrc / username 解決）は **Python の `examples/wandb/check_auth.py` のみが持つ**。
Rust は subprocess を呼び stdout の JSON を受けとって菜単状態を更新するだけ。

`check_auth.py` が返す JSON（[Q11](#q11) で正規定義）:

```json
{"authenticated": true, "method": "netrc", "username": "alice"}
{"authenticated": true, "method": "env", "username": null}
{"authenticated": false, "method": "none", "username": null}
```

Rust 側はこの JSON を `WandbAuthState` enum にデシリアライズしてメニュー有効化と
status 表示に使う。**`~/.netrc` を Rust から読まない**。**`WANDB_API_KEY` env を
Rust から `std::env::var` で参照しない**（参照は `Command::env` で subprocess に
継承させる目的に限る）。

#### 不変条件（セキュリティ）

- **API key を `Message` enum / `Flowsurface` struct のフィールドに格納しない**。
  入力モーダルの局所変数のみ
- **ログ・パニック出力・エラーダイアログ・トレースに API key を含めない**。
  マスキングは **単一 `mask_secrets()` 関数に集約**し、subprocess stdout reader →
  UI / log / tracing の **全出口で必ず通す**ことを `MaskedLine` newtype で型レベル強制する
  （統一決定 44）。raw `String` を UI / ログに渡す経路はコンパイルエラーになる設計。
  `submit_run.py` 側は `WANDB_SILENT=true` 環境変数 + 自前 logger で key を絶対 print
  しない契約。property-based test: 任意 40 桁 hex を含む文字列が出口で必ず `***` 化される
  ことを `proptest` で確認。検出パターンは正規表現
  `(?i)(wandb[_-]?api[_-]?key|WANDB_API_KEY)\s*[=:]\s*\S+` と「40 桁 hex 連続パターン」。
- `wandb login --relogin` への API key は **stdin pipe 経由**で渡す（統一決定 43）。
  **コマンドライン引数で渡すのは禁止**（プロセスリスト・OS のコマンドライン記録に
  露出するため）。具体的には `Command::new("uv").args(["run", "--with", "wandb", "wandb",
  "login", "--relogin"]).stdin(Stdio::piped())` で spawn し、`child.stdin.take().write_all(key)`
  で書き込み close する。F9c DoD に `wandb_signin_argv_no_key` テスト（subprocess 起動時の
  argv に key が含まれないこと）を追加。
- Windows のコマンドライン記録対策として、subprocess は `CREATE_NO_WINDOW` フラグ付きで
  起動する（コンソール履歴ファイルに残さない）
- **OS keyring を使わない**（プラットフォーム依存コードの増加 + wandb 標準と二重化）

#### スコープ外

- SAML / SSO ログイン（OAuth flow を GUI 内で踏む）→ wandb CLI が未対応の経路があるため
  V1 は API key 直貼りのみ
- 複数アカウント / Team 切り替え（netrc は単一エントリ前提）→ V2 以降

<a id="q11"></a>
### Q11. 認証情報の有無を誰が判定するか → **Python 側に完全集約（Rust はロジックを持たない）**

**不変条件**: 「W&B 認証が満たされているか」を判断するロジックは Flow Surface コアの
不変条件として **Python 側のみ**に置く。Rust は判定結果（bool + 補助情報）を **不透明な
データとして受け取り、メニュー状態の switching に使うだけ**。

#### 動機

- W&B の認証解決順序は wandb ライブラリのバージョン更新で変わる可能性がある
  （netrc location の Windows 差異・新規認証経路の追加等）。Rust に書くと wandb の
  仕様変化に追従するため Rust を毎回触ることになる
- `import wandb` を Rust から間接呼び出しできない以上、wandb の正規 API
  （`wandb.api.api_key()` 相当）で判定するのが最も堅い。これは Python でないとできない
- セキュリティ面でも、credential 周りのファイル読み・env 参照を **コア非汚染ルール**に
  従って `examples/wandb/` に閉じ込められる

#### `examples/wandb/check_auth.py` 仕様

```bash
uv run --with wandb python examples/wandb/check_auth.py
# 標準出力に JSON 1 行を返して即終了
```

**stdout 出力形式（厳密）**:

```json
{"authenticated": <bool>, "method": "env"|"netrc"|"none", "username": <string|null>, "error": <string|null>}
```

- `authenticated`: `True` なら送信メニューを enable してよい
- `method`: 認証ソース。`env` は `WANDB_API_KEY` 由来、`netrc` は netrc 由来
- `username`: `method="netrc"` のときのみ `wandb.Api().viewer.username`（または相当）を
  解決して返す。`env` は username 不明（API call をしないと判明しないため）→ `null`
- `error`: 解決中に例外が出た場合のメッセージ。`authenticated=false` を返しつつ
  `error` に詳細を入れる（Rust はこれを tooltip に流す）

**実装の素朴版**:

```python
import json, os, sys

def main():
    try:
        if os.environ.get("WANDB_API_KEY"):
            print(json.dumps({"authenticated": True, "method": "env",
                              "username": None, "error": None}))
            return
        import netrc
        try:
            n = netrc.netrc()
            auth = n.authenticators("api.wandb.ai")
        except (FileNotFoundError, netrc.NetrcParseError):
            auth = None
        if auth:
            # username 解決は API call が必要。失敗しても authenticated=true で返す。
            # 統一決定 49: wandb.Api(timeout=5)、subprocess 全体に 7 秒ハード timeout。
            # timeout 時は authenticated=true, username=null, error="viewer_lookup_timeout"
            # で fallback する。
            try:
                import wandb
                viewer = wandb.Api(timeout=5).viewer
                username = getattr(viewer, "username", None)
                err = None
            except Exception:
                username = None
                err = "viewer_lookup_timeout"
            print(json.dumps({"authenticated": True, "method": "netrc",
                              "username": username, "error": err}))
            return
        print(json.dumps({"authenticated": False, "method": "none",
                          "username": None, "error": None}))
    except Exception as e:
        print(json.dumps({"authenticated": False, "method": "none",
                          "username": None, "error": str(e)}))
        sys.exit(0)  # exit 0 で JSON だけで結果を表現する
```

**重要**: `sys.exit(0)` で常にゼロ終了する。**例外を非ゼロ exit code で表現しない**
（Rust 側の error handling 分岐を増やさない / Python が常に「結果 1 件」を返す契約）。

#### Rust 側の責務（最小化）

```rust
// src/wandb_auth.rs（新設、ロジック無し・データ運搬のみ）

#[derive(Deserialize, Clone, Debug)]
pub struct WandbAuthState {
    pub authenticated: bool,
    pub method: String,        // "env" | "netrc" | "none"
    pub username: Option<String>,
    pub error: Option<String>,
}

impl WandbAuthState {
    pub fn unauthenticated() -> Self { /* method="none" を返すだけ */ }
}

// 起動時 / メニュー操作後に呼ぶ
pub async fn refresh_wandb_auth() -> WandbAuthState {
    // uv run --with wandb python examples/wandb/check_auth.py を spawn
    // stdout を JSON parse、失敗時は WandbAuthState::unauthenticated()
}
```

Rust が持って **良い**もの:
- subprocess spawn / stdout 読み取り / JSON deserialize
- 結果 (`WandbAuthState`) のキャッシュとメニュー有効化に流す配線

Rust が持って **いけない** もの:
- `~/.netrc` のパース・存在確認・パーミッション検査
- `WANDB_API_KEY` env の判定（`std::env::var("WANDB_API_KEY")` の参照禁止。
  ただし subprocess に env を継承させる `Command::env_clear` を **使わない**形での
  spawn は OK — これは「読む」のではなく「子プロセスに通す」だけ）
- `wandb.ai` ホスト名・netrc machine 名のハードコード
- 「username 不明なら未認証扱い」のような半端な判定（Python が `authenticated=true`
  を返したらそれが正）

#### 判定タイミング

| トリガー | 動作 |
|---------|------|
| アプリ起動時 | バックグラウンド task で 1 回 `refresh_wandb_auth()` を非同期呼び。完了まではメニュー disable |
| `Action::SignInWandb` のログイン subprocess 成功時 | キャッシュを invalidate → 即 refresh |
| `Action::SignOutWandb` のログアウト subprocess 成功時 | キャッシュを invalidate → 即 refresh |
| `ツール（Tools）` メニューを開いた時 | キャッシュを使う（毎回 spawn しない、≈100ms 級の遅延を避ける） |
| 手動 refresh メニュー（V2） | 明示的に再判定 |

#### grep ガード

Rust 側に判定ロジックが混入しないことを保証するため、以下を CI / テストで grep 検証:

- `src/` 配下に `WANDB_API_KEY` 文字列が出現しない（subprocess 起動時の env 継承は
  OS デフォルト動作で十分なため明示参照不要）
- `src/` 配下に `api.wandb.ai` 文字列が出現しない（netrc machine 名のハードコード禁止）
- `src/` 配下に `.netrc` / `_netrc` 文字列が出現しない
- `src/wandb_auth.rs` の中身が `serde::Deserialize` 構造体定義 + subprocess spawn のみで、
  `if env::var(...)` 系の分岐を含まない

具体テストは F9c の `tests/wandb_key_masking.rs` に同居（`no_auth_logic_in_rust` ケース）。

---

<a id="roadmap"></a>
## 実装ロードマップ

| Phase | 内容 | 規模 | 依存 |
|-------|------|------|------|
| **✅F9a** | RunBuffer Python 側書き出し（`replay_session.py` の event loop に tee） | M | F6（SCENARIO 抽出が無いと meta.json の scenario 欄が埋まらない） |
| **✅F9b** | `examples/wandb/submit_run.py` 実装 + 単体スモーク | M | F9a |
| **✅F9c** | `ツール（Tools）` メニュー追加（muda + Linux 自前）+ `WandbSubmitModal` UI + `WandbSignInModal`（ログイン / ログアウト / netrc 委譲）+ key マスキング | L | F9a / F2 |
| **✅F9d** | Rust subprocess 起動 + stdout tail + URL パース | S | F9c |
| **✅F9e** | `送信履歴を開く` / `バッファを削除…` の補助 UI | S | F9c |

並列消化（`/parallel-agent-dev`）：F9a / F9b / F9c は依存が浅いため、F6 完了後に
3 並列で着手できる。F9d は F9c 後、F9e は F9c / F9d 後。

---

<a id="dod"></a>
## DoD（完了条件）

### ✅F9a: RunBuffer 書き出し（2026-05-04 完了）

**実装ファイル**:
- `python/engine/run_buffer.py` — RunBuffer クラス（write_event / finish / abort / sweep_old_runs）
- `python/engine/pii_scrub.py` — PII allow-list スクラバー（FILLS/EQUITY/NARRATIVE_ALLOWED_KEYS / FORBIDDEN_KEYS / pii_scrub()）
- `python/engine/replay_session.py` — RunBuffer tee 統合（run() の event loop に tee、line 900-1012）
- `python/tests/test_run_buffer_writer.py` — 8 テストケース
- `python/tests/test_scenario_writeback.py` — test_write_back_refuses_run_buffer_path 追加

**設計判断**:
- PII 禁止キー検出 → event 丸ごと skip（None 返却）。許可外キーは strip のみ
- `FORBIDDEN_KEYS` に `venue_order_id`, `client_order_id`, `raw_data`, `payload` 等を含む
- `finish()` = jsonl flush+fsync → meta.json atomic rewrite の順序契約（BC3-5）
- `sweep_old_runs()` = running & no .lock → aborted に正規化（統一決定 50）
- `run_buffer.py` は `import wandb` 禁止（コア非汚染ルール準拠）

- **テストファイル**: `python/tests/test_run_buffer_writer.py`
- **assert**:
  - replay 1 本走らせると `meta.json` / `fills.jsonl` / `equity.jsonl` /
    `narrative.jsonl` が生成される
  - `meta.json` の `status` が起動時 `running` → `Event::ReplayStopped` 受領後
    `completed` に rewrite される
  - SIGTERM / クラッシュ後、`atexit` / signal handler で `status="aborted"` に
    atomic 書き換えされる（統一決定 50）
  - signal handler が呼ばれず `running` のまま残ったケースは、GUI 起動時スキャナが
    「`running` & `.lock` 無し」を `aborted` に正規化する（統一決定 50。F9a 正規化テスト）
  - `run-buffer/` 配下が **[F6](./fix-save-menu.md#f6) の `.py` 書き戻し path ガードで拒否**
    されることを `python/tests/test_scenario_writeback.py` 側にケース追加（横断保護）
  - **PII allow-list（統一決定 47）**: `fills.jsonl` / `equity.jsonl` / `narrative.jsonl`
    に **立花口座番号 / token / venue raw payload が 1 バイトも書かれない**ことを
    Property-based テストで assert（任意イベントを `pii_scrub.py` 経由 → 出力に
    禁止 key が現れない）
- **観測コマンド**: `uv run pytest python/tests/test_run_buffer_writer.py -v`

### ✅F9b: submit_run.py（2026-05-04 完了）

- **テストファイル**: `examples/wandb/tests/test_submit_run.py`
  （`wandb.init` を `monkeypatch` でモック化。`examples/wandb/tests/` 配下は
  **コア非汚染ルールの射程内で `import wandb` が許可される**唯一の場所
  — 統一決定 54。SKILL.md 側にも追記）
- **assert**:
  - `meta.json` の scenario が `wandb.init(config=...)` の引数に含まれる
  - `equity.jsonl` の各行が `wandb.log` に渡る
  - 終了 stdout 最終行が `URL: https://wandb.ai/...` 形式
  - `WANDB_API_KEY` 未設定時は exit code 2 + stderr に明確なメッセージ
  - **`WandbError.kind` 別の exit code マッピング**（統一決定 48）:
    `auth=2 / rate_limit=3 / network=4 / server_5xx=5 / partial=6` を kind 別に assert
  - **`check_auth.py` の 7 秒 timeout**（統一決定 49）:
    オフライン環境で `examples/wandb/check_auth.py` が **7 秒以内に終了**し、
    `{"authenticated": ..., "error": "viewer_lookup_timeout"}` 形式で stdout を返すことを assert
  - **graceful finish**（統一決定 45）: SIGTERM 投入時に `wandb.finish(exit_code=non_zero)`
    が必ず呼ばれることを mock で確認
- **観測コマンド**: `uv run --with wandb pytest examples/wandb/tests/ -v`
- **CI matrix**（統一決定 52）: ロードマップ／CI 設定に
  `examples-wandb` job を追加し `uv run --with wandb pytest examples/wandb/tests/`
  を CI 上で常時実行する。

### ✅F9c: メニュー / モーダル / 認証

#### ✅ F9c-menu 完了（2026-05-04）— native_menu.rs Tools submenu 配線 + main.rs スタブハンドラ

**実装内容**:
- `src/native_menu.rs` — `Action` enum に `SubmitToWandb` / `SignInWandb` / `SignOutWandb` / `OpenSubmissionLog` / `ClearRunBuffer` を追加。`MenuIds` struct に 5 フィールド追加。`attach()` に `ツール（Tools）` サブメニューを追加（`Ctrl+Shift+W` アクセラレータ付き）。`event_stream()` に 5 アクションのマッピングを追加
- `src/main.rs` — `NativeMenuAction` ハンドラに 5 アクションのスタブハンドラを追加（`log::info!` + `Task::none()` のみ。F9d/F9e で実装）
- `tests/wandb_menu_action.rs` — ソースインスペクション方式の 22 テストケース（全通過）

**テスト結果**:
- `cargo test --test wandb_menu_action` — 22 passed
- `cargo test --workspace` — FAILED 0
- `cargo clippy -- -D warnings` — 警告なし
- `cargo fmt --check` — 差分なし

#### ✅ F9c-base 完了（2026-05-04）— Rust 型レイヤーのアップグレードと W&B 認証・マスキング基盤

**実装ファイル（新設）**:
- `src/wandb_auth.rs` — `WandbAuthState`（Python stdout JSON を受け取るデータ運搬 struct）/ `RunBufferIndex`（run-buffer/ スキャン）
- `src/mask_secrets.rs` — `MaskedLine` newtype + `mask_secrets()` 関数（WANDB_API_KEY / 40桁 hex を `***` に置換）

**変更ファイル**:
- `src/menu.rs` — `tools_actions_for_state` を `Vec<Action>` → `Vec<MenuEntry>` に変更（R7-86）。引数を `(&WandbAuthState, &RunBufferIndex)` に変更。内部テストを 4 組合せ × 5 項目の `MenuEntry` 検証に書き換え。`AuthState`/`BufferState` は `#[allow(dead_code)]` で保持
- `src/main.rs` — `mod mask_secrets`・`mod wandb_auth` 追加。`fn main()` 冒頭に panic hook 登録（mask_secrets で 40 桁 hex をマスク）
- `Cargo.toml` — `regex` を本体依存に追加、`proptest = "1"` / `walkdir = "2"` を dev-dependencies に追加
- `tests/tools_actions_for_state.rs` — R7-86 対応の新しい構造インスペクションテストに全面書き換え

**新規テストファイル**:
- `tests/wandb_auth_state.rs` — WandbAuthState JSON deserialize テスト（11 ケース）+ ソースインスペクション
- `tests/wandb_key_masking.rs` — mask_secrets 基本動作・proptest（16 ケース）+ grep ガード（no_wandb_api_key_literal / no_api_wandb_ai / no_netrc / no_key_field）+ panic hook 確認

**設計判断**:
- `WandbAuthState` を `src/wandb_auth.rs` に置いた理由: メニュー計算ロジック（menu.rs）と型定義を分離し、将来の配線コード（subprocess spawn / cache）も同じモジュールに集約できるようにするため
- `tools_actions_for_state` の引数型選択: `&WandbAuthState` / `&RunBufferIndex` に統一（R7-86 決定）。コピーコストを避けるため参照渡し
- `AuthState`/`BufferState` は削除せず `#[allow(dead_code)]` で保持: ソースインスペクションテスト（tools_actions_for_state.rs）が文字列として検出しているため
- `MaskedLine` newtype 強制: raw String を UI/ログに渡す経路をコンパイルエラーで防ぐ設計。bin-only crate のため外部テストは独立実装で検証

**テスト結果**:
- `cargo test --test wandb_auth_state` — 11 passed
- `cargo test --test wandb_key_masking` — 16 passed（proptest 含む）
- `cargo test --test tools_actions_for_state` — 13 passed
- `cargo test --workspace` — 全件 OK（FAILED 0）
- `cargo clippy -- -D warnings` — 警告なし
- `cargo fmt --check` — 差分なし

- **テストファイル**:
  - `tests/wandb_menu_action.rs`（メニュー有効化 / アクセラレータ / 二重発火回避）
  - `tests/wandb_signin_flow.rs`（ログイン / ログアウト / **`wandb_signin_argv_no_key`** — 統一決定 43）
  - `tests/wandb_auth_state.rs`（Python `check_auth.py` の JSON を deserialize → メニュー反映）
  - `tests/wandb_key_masking.rs`（key マスキング + Rust 側に判定ロジックが無いことの grep ガード + property-based test）
  - `tests/wandb_reentrancy.rs`（**`wandb_submit_no_double_dispatch`** /
    **`wandb_accelerator_disabled_during_submit`** — 統一決定 46）
  - `examples/wandb/tests/test_check_auth.py`（**Python 側の判定ロジック本体**）
- **assert（メニュー）**:
  - replay モード + completed buffer 1 件あり + **`WandbAuthState.authenticated = true`** → `W&B に登録…` が enable
  - replay モード + buffer 0 件 → `W&B に登録…` disable
  - live モード → `W&B に登録…` disable
  - **未認証（`authenticated = false`）→ `W&B に登録…` メニュー自体を disable** + tooltip「W&B にログインしてください」
  - 起動直後（`refresh_wandb_auth()` 未完了）→ disable（fail-closed）
  - `Ctrl+Shift+W` 押下時にメニューが disable なら **何も dispatch しない**（accelerator も disable と同期）
  - macOS で `Cmd+Shift+W` に変換される
  - 未認証時は `W&B にログイン…` enable / `ログアウト` disable
  - netrc 認証時は `W&B にログイン…` disable / `ログアウト` enable
  - **モーダル API key 状態表示**（統一決定 53）: モーダルに表示される文字列は
    `未設定 / env 経由 / netrc 経由` の 3 値のみで、テスト用 dummy key（例: `40 桁 hex`）
    がモーダル DOM / log / tracing event のどこにも 1 文字も現れないことを assert
  - **再入禁止 / 二重発火回避**（統一決定 46 / 55）: `submit_in_flight = Some(_)` 中に
    `Action::SubmitToWandb` を dispatch しても何も起こらない。`Ctrl+Shift+W` accelerator
    も同じく disable と同期して dispatch されない（[F2 / Q6](./fix-save-menu.md#f2)
    の `no_double_dispatch` ポリシーを共有）
- **assert（ログインフロー）**:
  - `Action::SignInWandb` dispatch → `WandbSignInModal` 表示
  - 「ブラウザで API キーを取得」押下 → `https://wandb.ai/authorize` が
    `webbrowser::open` 経由で呼ばれる（モック化して assert）
  - API キー入力 + 「ログイン」 → `wandb login --relogin` subprocess 起動。
    **API key は stdin pipe 経由で渡され、argv には含まれない**（統一決定 43。
    `wandb_signin_argv_no_key` テストで「subprocess 起動時の argv 配列に key が含まれない」
    ことを assert）。stdin に書き込んだ後 close される
  - subprocess exit 0 → モーダル閉じ + `whoami` キャッシュ invalidate
  - subprocess exit 非 0 → モーダル開いたままエラーメッセージ + 入力欄クリア
  - **モーダルの key 文字列が subprocess 終了直後に local scope から drop される**ことを
    Drop インスツルメントで assert（`String::clear()` 後に length 0）
  - `Action::SignOutWandb` → confirm dialog → `wandb logout` subprocess → netrc
    エントリ削除を mock fs で確認
- **assert（auth state / Python 一元化）**:
  - `examples/wandb/tests/test_check_auth.py`（Python 側ロジックの核）:
    - `WANDB_API_KEY=xxx` 設定時 → stdout `{"authenticated": true, "method": "env", ...}`
    - env 無し + netrc に `api.wandb.ai` あり → `{"authenticated": true, "method": "netrc", "username": ...}`
    - env 無し + netrc 無し → `{"authenticated": false, "method": "none", ...}`
    - netrc パースエラー → `{"authenticated": false, "method": "none", "error": "..."}`
    - **どのケースでも exit code 0**（契約）
    - JSON は **stdout 1 行のみ**で他に出力しない（パース安定性）
  - `tests/wandb_auth_state.rs`（Rust 側のデータ運搬のみ）:
    - 各 JSON 形式を `WandbAuthState` に deserialize 成功
    - `authenticated=true` + buffer 有 → `tools_actions_for_state(auth_state, buffer_state)`
      の戻り値 `Vec<MenuEntry>` 中、`SubmitToWandb` の `MenuEntry` が
      `enabled=true` / `tooltip=None`（`actions_for_mode` には触れない／P8 DoD-11 整合）
    - `authenticated=false` → 同 `MenuEntry` が `enabled=false` /
      `tooltip=Some("W&B にログインしてください")`
    - `authenticated=true` + buffer 空 → 同 `MenuEntry` が `enabled=false` /
      `tooltip=Some("送信可能な run がありません（最初に replay を実行してください）")`
    - 同 `Vec<MenuEntry>` には `OpenSubmissionLog` が **常に含まれる**（buffer 空なら
      `enabled=false` + tooltip「送信履歴がまだありません」）
    - `SignInWandb` / `SignOutWandb` の **相互 disable**（一方が `enabled=true` なら他方は
      必ず `enabled=false`）が `MenuEntry` レベルで成立する
    - subprocess 失敗（spawn 失敗 / JSON 不正）→ `WandbAuthState::unauthenticated()` で
      fail-closed
- **assert（Rust にロジック無し / grep ガード — `tests/wandb_key_masking.rs::no_auth_logic_in_rust`）**:
  - `src/` 配下を全 `*.rs` 走査して、以下のいずれも **0 件**であることを assert:
    - 文字列リテラル `"WANDB_API_KEY"`
    - 文字列リテラル `"api.wandb.ai"`
    - 文字列リテラル `".netrc"` / `"_netrc"`
    - パス `std::env::var("WANDB_API_KEY")` 相当
  - `src/wandb_auth.rs` の AST を簡易解析して `if` / `match` による条件分岐が
    **subprocess の Result 分岐と JSON deserialize 結果分岐のみ**であることを確認
    （許容パターンを allowlist し、それ以外を reject する形のテスト。AST は syn で解析）
- **assert（key マスキング — 統一決定 44）**:
  - **マスキング集約（`MaskedLine` newtype）**: subprocess stdout reader → UI / log /
    tracing の **全出口は `mask_secrets()` を通った `MaskedLine` でしか流れない**ことを
    型レベルで強制。raw `String` を UI / ログに直接渡すコードはコンパイルエラー
  - **property-based test**（`proptest`）: 任意 40 桁 hex を含む文字列が、全出口の
    どの経路を通っても必ず `***` 化されている
  - 入力 `"WANDB_API_KEY=abc123def456..."` を含む文字列がログ関数に渡された場合、
    出力は `"WANDB_API_KEY=***"` にマスクされる
  - 40 桁 hex 連続パターン（`a1b2c3...`）も `***` にマスクされる
  - subprocess の stdout/stderr が UI に流れる経路で同じマスクが適用される
  - panic ハンドラ / `tracing` event のフォーマッタを通しても key が漏れない
    （`tracing_test` でログを capture して assert）
  - **panic hook 登録（統一決定 R3-65）**: `src/main.rs` 冒頭（`fn main()` の最初の行）で
    `std::panic::set_hook(Box::new(|info| { /* mask_secrets を通して stderr / tracing に出す */ }))`
    を登録する。デフォルト hook（`std::env::var` の値や `Debug` 出力をそのまま吐く）が
    動く前に差し替えなければ key 漏れの窓ができるため、`set_hook` 呼び出し位置は
    main 冒頭固定。F9c DoD に `tests/wandb_key_masking.rs::panic_hook_masks_key`
    を追加し、panic 経路で 40 桁 hex を含む payload が `***` 化されることを assert する
  - `submit_run.py` 側は `WANDB_SILENT=true` + 自前 logger 契約で key を絶対 print しない
    ことを Python 側テストで assert
  - `Message` enum / `Flowsurface` struct のフィールドに `String` で key を保持していない
    ことを **コンパイル時に近い保証**として grep ベースのテストで補完
    （`tests/wandb_key_masking.rs::no_key_field_in_state`：`src/` 配下を grep して
    `wandb_api_key:` のような field 定義が無いことを assert）
- **観測コマンド**:
  - `cargo test --test wandb_menu_action`
  - `cargo test --test wandb_signin_flow`
  - `cargo test --test wandb_auth_state`
  - `cargo test --test wandb_key_masking`
  - `cargo test --test wandb_reentrancy`
  - `uv run --with wandb pytest examples/wandb/tests/test_check_auth.py -v`

#### ✅ F9c-modal 完了（2026-05-04）— WandbSubmitModal main.rs 配線

**実装内容**:
- `src/modal/wandb_signin.rs` — WandbSignInModal（API キー入力・マスク表示・stdin pipe 送信）
- `src/modal/wandb_submit.rs` — WandbSubmitModal（project / run_name / tags / notes 入力・submit_in_flight ガード）
- `src/wandb_submit_proc.rs` — build_submit_command / parse_url_from_output（F9d subprocess ユーティリティ）
- `src/main.rs` — `wandb_submit_modal` フィールド追加・`WandbSubmitMsg` Message 追加・`Action::SubmitToWandb` でモーダル表示に変更・`submit_wandb_run()` に project/run_name/tags 引数追加・`WandbSubmitResult` ハンドラでモーダル Done/Failed 更新・view() にモーダルオーバーレイ追加

**新規テストファイル**:
- `tests/wandb_signin_flow.rs` — 8 ケース（ログイン / ログアウト / argv に key なし）
- `tests/wandb_submit_subprocess.rs` — 11 ケース（subprocess 構造・URL パース・モーダル構造）
- `tests/wandb_reentrancy.rs` — 7 ケース（submit_in_flight ガード）
- `tests/wandb_modeswitch_lock_order.rs` — 4 ケース（R3-58 ロック順序）
- `tests/wandb_submission_log_ui.rs` — 5 ケース（F9e UI 確認）

**テスト結果**:
- `cargo test --workspace` — 全件 OK（FAILED 0）
- `cargo clippy -- -D warnings` — 警告なし
- `cargo fmt --check` — 差分なし

### ✅F9d: subprocess 起動（2026-05-04 完了）

- **テストファイル**: `tests/wandb_submit_subprocess.rs`
  （`submit_run.py` をダミースクリプトに置換した dry-run テスト）
- **assert**:
  - `uv run --with wandb python <script>` の引数列が期待通り
  - stdout `URL: ...` 行をパースして `Message::WandbSubmitDone(Url)` 発火
  - 非ゼロ終了 → `Message::WandbSubmitFailed(stderr)` 発火
  - `WANDB_API_KEY` env が subprocess に継承されることを assert
  - **API key が Rust 側ログに出ないことを assert**（ログ捕捉テスト）
- **観測コマンド**: `cargo test --test wandb_submit_subprocess`

### ✅F9e: 履歴 UI / バッファ削除（2026-05-04 完了）

- **テストファイル**: `tests/wandb_submission_log_ui.rs`
- **assert**:
  - `送信履歴を開く` で過去 run がリスト表示される
  - `バッファを削除…` で confirm dialog → `run-buffer/` が空になる
  - 削除後はメニューが disable に戻る

---

<a id="non-goals"></a>
## 非スコープ（やらないこと）

- **W&B Sweeps / Hyperparameter 探索 UI** — `/wandb` スキルの V2 案件
- **W&B 上の比較プロット を GUI 内に埋め込む** — 外部ダッシュボード方針
- **MLflow / TensorBoard 等の他プラットフォーム対応** — V1 は W&B のみ
- **戦略 .py から `wandb.log()` を呼べるようにする SDK** — 設計判断 Q1 で否定
- **Live モードの run 切り出し UI** — Q8 で deferred
- **送信前の差分プレビュー（前回 run との PnL 比較）** — V2 以降

---

<a id="related"></a>
## 関連ファイル早見表

| ファイル | 役割 |
|---------|------|
| [src/native_menu.rs](../../src/native_menu.rs) | `ツール（Tools）` サブメニュー追加先 |
| [src/main.rs](../../src/main.rs) | `Message::NativeMenuAction(SubmitToWandb)` ハンドラ |
| [python/engine/replay_session.py](../../python/engine/replay_session.py) | RunBuffer 書き出しを tee する位置 |
| examples/wandb/submit_run.py（新設） | wandb を ad-hoc に呼ぶ独立スクリプト |
| [.claude/skills/wandb/SKILL.md](../../.claude/skills/wandb/SKILL.md) | コア非汚染ルール / examples/ 配置規約 |
| [docs/plan/wandb-vision.md](../plan/wandb-vision.md) | W&B 統合の元ビジョン（本計画はその「ストラテジー外側で送る」変種） |
| [fix-save-menu.md](./fix-save-menu.md) | F2（accelerator）/ F4（confirm）/ F6（path ガード）依存元 |
