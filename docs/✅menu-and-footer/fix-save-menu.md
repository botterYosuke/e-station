# File メニュー / Save 周りの問題点と改善候補

**作成日**: 2026-05-04
**作成者**: Claude Opus 4.7（botterYosuke）
**ステータス**: 未着手・改善候補リストアップ
**関連**: [native-menu-bar-impl.md](./native-menu-bar-impl.md) / [footer-impl.md](./footer-impl.md)

---

## 凡例（番号体系）

本計画書では以下のプレフィックスを区別して使う。混同を避けるため H2/H3 表記は廃止し、
本文は **F\*** / **P\*** で参照する。

| プレフィックス | 意味 | 例 |
|----------------|------|-----|
| **F\*** | 本計画書（fix-save-menu.md）の実装フェーズ番号 | F1, F2, ..., F9 |
| **P\*** | 関連問題番号（このドキュメント内で識別する課題） | P1, P5, P7, P8 |
| **Phase 8.x** | 既存の `python-helper-direct-api` 系列のフェーズ番号（**別系列**） | Phase 8.1b, Phase 8.3 |

> **F\* 番号空間の補足**: F\* は ✅menu-and-footer/ 配下計画書共通の実装フェーズ番号空間（fix-save-menu/P5/P7/P8/P9 にまたがって付番される）。

各 P 計画書（`P5-scenario-in-strategy.md` / `P7-mode-switch-menu.md` /
`P8-widget-menu-bar-linux.md`）からは F\* 参照で揃える。

### ファイル間リンクの表記規則

- 同一ディレクトリ内（`docs/✅menu-and-footer/`）は `./<file>.md` の相対パスで揃える
  （`../✅menu-and-footer/` は禁止）
- 他ディレクトリへのリンクはプレフィックス付きフルファイル名を保つ
- `spec.md` は 0 byte の空ファイルなのでアンカー参照を貼らない。代替先は
  `./native-menu-bar-impl.md` / `./footer-impl.md` の対応節

<a id="menu-labels"></a>
### メニューラベル表記の統一

本文・テスト・コミットメッセージで以下の **日本語（英名）** 形式に揃える。
P9・README からは `#menu-labels` で参照する。

| 統一表記 | 用途 |
|----------|------|
| `開く…（Open）` | File メニュー: 任意パスから layout / 戦略を読み込む |
| `上書き保存（Save）` | File メニュー: 現在開いているパスへ書き戻し |
| `名前を付けて保存…（Save As）` | File メニュー: 新規パスへ書き出し |
| `Replay を開始…` | Replay 制御メニュー |
| `Replay を停止` | Replay 制御メニュー |
| `ツール（Tools）` | W&B 系メニューの親ラベル（P9） |
| `送信履歴を開く（Open Submission Log）` | W&B メニュー（P9）: 過去の submission ログ閲覧 |
| `バッファを削除…（Clear Run Buffer）` | W&B メニュー（P9）: ローカル run buffer 削除 |
| `W&B にログイン…（Sign in to W&B）` | W&B メニュー（P9）: W&B 認証 |
| `W&B からログアウト（Sign out of W&B）` | W&B メニュー（P9）: W&B からサインアウト |
| `W&B に登録…（Submit to W&B）` | W&B メニュー（P9）: 現在の run を W&B に submit |

> 三点リーダはすべて U+2026（`…`）を使用する（ASCII `...` は不可）。

---

## 背景

[native-menu-bar-impl.md](./native-menu-bar-impl.md) で OS ネイティブメニューバーを導入したが、
一般的なデスクトップアプリ（VS Code / メモ帳 / Excel など）と比較すると Save 周りの
ユーザー体験に欠落が多い。本文書は問題点と改善候補をフラットに列挙する。

調査時の現行実装：

- [src/native_menu.rs](../../src/native_menu.rs)
- [src/main.rs](../../src/main.rs)（`NativeMenuAction` / `NativeOpenFileApply` / `NativeSaveAsWithSpecs` ハンドラ群）

---

## 問題点

### P1. 「上書き保存（Save）」が存在しない

- 現行のメニューは `名前を付けて保存…（Save As）` のみ
- 一般アプリの `上書き保存（Save）`（Ctrl+S, 既存パスへ上書き）に相当する項目がない
- 内部に「現在開いているファイル」の概念がないため、`開く…（Open）` で読み込んだ
  ファイルの場所をアプリは記憶していない（読み込み後すぐ `saved-state.json` に
  上書きしてしまう）
- 結果として「設定ファイルを編集 → 保存」のループが Save As ダイアログ毎回開く
  動線になり、煩雑

**根本原因**: 自動保存（`%APPDATA%\flowsurface\saved-state.json`）に強く結合した設計で、
ユーザーが任意パスを「現在のドキュメント」として保持する状態モデルになっていない。

### P2. キーボードショートカット完全不在

- [src/native_menu.rs:66-67](../../src/native_menu.rs#L66-L67) の `MenuItem::new("開く...", true, None)`
  の第 3 引数（accelerator）はすべて `None`
- グローバル keyboard handler（`keyboard::on_key` / `Modifiers::CTRL`）もコードベースに
  なし（grep 0 件）
- 期待される最低ライン：
  - `Ctrl+O` → `開く…（Open）`
  - `Ctrl+S` → `上書き保存（Save）`（P1 の前提）
  - `Ctrl+Shift+S` → `名前を付けて保存…（Save As）`
  - `Ctrl+Q` → 終了（macOS は `Cmd+Q` が OS predefined Quit に既に bind 済み）
- アクセラレータ経路は **muda が正規**。iced 側の `keyboard::on_key` は
  `cfg(target_os="linux")` 限定（muda が Linux GTK で完全動作しないため）。
  これを不変条件として F2 のテストで保護する（C-7 二重発火回避）

[native-menu-bar-impl.md:311](./native-menu-bar-impl.md#L311) でも *優先度 低* として
TODO 化されている。

### P3. 「開く」がアプリ全体再起動を伴う

- [src/main.rs:2672](../../src/main.rs#L2672) で `self.restart()` を呼び `Flowsurface::new()`
  からやり直す
- 未保存変更の検知・警告ダイアログがない（live モードで開いた直後に何かを編集していても
  そのまま破棄される可能性がある）
- 一般アプリは「ホットスワップ」または「未保存変更があります。保存しますか？」の
  確認ダイアログを挟む

### P4. Save As に「上書き確認」がアプリ自身では存在しない

- rfd の `save_file()` は OS 側の上書き確認に依存
- 動作確認が未実施（[native-menu-bar-impl.md:315](./native-menu-bar-impl.md#L315) の TODO）
- OS によって挙動が異なる可能性があり、アプリ層で confirm する設計のほうが堅い

### P5. replay の再現条件（戦略 + 銘柄 + 期間）が 1 ファイルにまとまっていない

現状、replay を再現するには 3 つの情報を別々に揃える必要がある：

- `strategy_file`（.py のパス）
- `instrument`（例: `1301.TSE`）
- `start` / `end`（期間）

GUI では `ReplayFormModal` の各フィールドに毎回手入力、CLI では
`--strategy ... --instrument ... --start ... --end ...` の引数列を組み立てる。
**「同じ条件でもう一度回したい」だけのことに毎回 3 箇所の入力が必要**で、
人為ミスで条件がずれると結果も変わる（再現性の損失）。

**解決方針**: 再現条件を **戦略 .py 内のモジュール定数 `SCENARIO`** として宣言し、
replay は「この .py 1 個」だけで回るようにする（補助設定ファイルを作らない）。

**実装計画**: [P5-scenario-in-strategy.md](./P5-scenario-in-strategy.md) を参照。以下は仕様の根拠記録として残す。

```python
# docs/example/buy_and_hold.py
from typing import TypedDict

class Scenario(TypedDict):
    schema_version: int
    instrument: str
    start: str
    end: str
    granularity: str
    initial_cash: int

SCENARIO: Scenario = {
    "schema_version": 1,
    "instrument": "1301.TSE",
    "start": "2025-01-06",
    "end": "2025-03-31",
    "granularity": "1m",
    "initial_cash": 1_000_000,
}

class MyStrategy(Strategy):
    ...
```

> **形式の許容範囲**: 上記サンプルは AnnAssign 形（`SCENARIO: Scenario = {...}`）だが、抽出側は素の Assign 形（`SCENARIO = {...}`）も同等に許容する。詳細は [P5-scenario-in-strategy.md](./P5-scenario-in-strategy.md) §F6a 参照。

**読み込み経路**:

- GUI: `開く…（Open）` で `.py` を選ぶと **`ast.parse + ast.literal_eval`** で
  `SCENARIO` 定数のみを安全抽出し、`ReplayFormModal` が prefill される。
  Run 押下時のみ `importlib.util.spec_from_file_location` でフルロードする
  （任意コード実行は明示的なユーザー操作の後に限定）
- CLI: `uv run python -m engine.replay_session run --strategy docs/example/buy_and_hold.py`
  （`--instrument` / `--start` / `--end` 引数が省略された場合 `SCENARIO` を使う）

**この設計の利点**:

- パーサ不要（`ast.parse + ast.literal_eval` で読むだけ）／ TypedDict で mypy 型チェック可能
- git diff が綺麗・grep 可能・戦略の import 再利用も維持
- 補助 `.replay.json` を作らないので「.py と .json の不整合」が原理的に発生しない

**書き戻し経路（GUI から .py への上書き）**:

GUI で `ReplayFormModal` のフィールドを変更してから保存すると、対象の戦略 .py 内の
`SCENARIO = {...}` ブロックだけが置換される。**戦略本体・コメント・docstring・import は
一切触らない**。

- `上書き保存（Save）`（Ctrl+S）→ 現在開いている .py の `SCENARIO` を更新
- `名前を付けて保存…（Save As）`（Ctrl+Shift+S）→ 現在の戦略 .py をコピーし、
  コピー先の `SCENARIO` を新値で書き出す（戦略コードはそのまま）

**書き戻し実装方式**:

- **推奨: `libcst` による Concrete Syntax Tree 置換** — `SCENARIO = {...}` の代入文ノードを
  ピンポイントで差し替え、その他の空白・コメント・改行を完全保持できる
- 代替: `ast` + 自前 unparse（コメント消失リスクあり）/ 正規表現（脆い）→ 不採用

**書き戻しの安全装置**:

- 書き込みは **`tempfile + os.replace()` による atomic write** を必須化（途中失敗で
  元ファイルが破壊されないようにする）
- 元ファイルを `.bak.<UTC秒>` 形式（例: `buy_and_hold.py.bak.1714800000`）で
  **世代付きバックアップ**として残す（1 世代固定ではなく、UTC 秒で複数世代が積もる）
- **`上書き保存（Save）` 経路では `SCENARIO` 不在の .py への書き戻しを拒否**（`current_path` が指す .py が戦略外だった場合に誤って上書きしない）。
  **`名前を付けて保存…（Save As）` 経路では許容**し、新規 `SCENARIO` ブロックを `.py` 冒頭の import 直後に挿入する（L214-217 / P5 §確定方針 と整合）
- 書き戻し後に `importlib` で再ロード検証し、import エラーになる場合は rollback
- **path ガード**（`SaveStrategyScenario` ハンドラで強制）:
  - 拡張子 `.py` 必須
  - 直前の `LoadStrategyScenario` で読み込んだ path と一致すること（Save の意味論を保証）
  - 永続状態ファイルディレクトリ（`%APPDATA%\flowsurface\` /
    `~/.cache/flowsurface/engine/`）への書き込みは禁止
    （`saved-state.json` / `engine-session.json` / `tachibana_orders.jsonl` を
    誤って .py 書き戻しで踏み潰さない）

**Save / Save As の path guard 分岐（R7-85 確定方針）**:

`SaveStrategyScenario` ハンドラの path ガードは `上書き保存（Save）` と
`名前を付けて保存…（Save As）` で分岐する。**Save** は直前の `LoadStrategyScenario`
で読み込んだ path と**同一 path** にのみ書き戻すことを要求する（誤上書き防止）。
**Save As** は Load 済み元 path とは**異なる派生 path** への書き出しを許可する
（コピー先を新規に決められるのが Save As の本質のため）。両経路とも `.py` 拡張子必須・
永続状態ディレクトリ書き込み禁止の二条件は共通で課す。詳細は
[P5-scenario-in-strategy.md](./P5-scenario-in-strategy.md) §path ガード を参照。

**論点（実装前に確定）**:

- `SCENARIO` 定数名で固定するか、複数候補名（`scenario` 小文字も許容）を認めるか
  → **`SCENARIO` 固定**（書き戻しの曖昧性を避ける）
- `SCENARIO` が無い .py は **許容する**（後方互換・スイープ用途・他人の戦略を借りた直後）
  - GUI: `ReplayFormModal` のフィールドはユーザー入力で埋める（prefill されないだけ）
  - CLI: `--instrument` / `--start` / `--end` をユーザーが渡す（無ければエラー）
  - `名前を付けて保存…（Save As）` 時に **新規 `SCENARIO` ブロックを .py 冒頭に挿入** して保存。
    これにより「読み込みは寛容、書き戻しで永続化」の流れが成立する
- CLI 引数（`--instrument` 等）と `SCENARIO` が両方ある場合のオーバーライド方向
  → **CLI 引数優先**（ad-hoc 実行を妨げない）。ただし CLI 経由の値は `SCENARIO` には書き戻さない
- `libcst` の依存追加可否（軽量・純 Python・MIT ライセンス。問題なしと判断）
- GUI の `開く…（Open）` / `名前を付けて保存…（Save As）` のフィルタ：replay モード時は `.py` /
  live モード時は従来通り `saved-state.json`（`.json`）

**スコープ外（旧 P5 案からの変更）**:

旧 P5「replay 結果の永続化レイヤ（fills / equity / signals / narratives JSONL）」は **ボツ**。
理由：

- 「同じ戦略の 2 回の replay を比較したい」目的は **W&B 一本化**で達成する
  （`/wandb` スキル / [wandb-vision.md](../plan/wandb-vision.md) 参照）
- ローカルに JSONL を吐いても **読む UI を作らない方針**のため、ユーザーが
  pandas / jupyter のセットアップとボイラープレートを毎回踏む必要があり、
  実運用ハードルが高く dead code 化しやすい
- W&B web UI なら run 一覧で sharpe / pnl を sort して比較が完結する

旧計画書 `P5-replay-persistence-layer.md` は廃案。実体ファイルは存在しない（リンクは付けない）。
（[P9-wandb-submit-menu.md](./P9-wandb-submit-menu.md) も参照）

### P6. ドキュメントが現行実装と部分的に乖離

- [native-menu-bar-impl.md:169-180](./native-menu-bar-impl.md#L169-L180) は旧 `OpenStrategy` フローを
  記述しているが、現行は `OpenReplayDialog`（`ReplayFormModal`）に統合済み
- *Python フィルタ除く* の記述（136 行目付近）は実コードと不正確：
  実装は `.add_filter("JSON", &["json"])` で JSON のみに絞っているだけで、
  「`.py` を除外」というロジックではない
- 当該ドキュメントは **完了済み** マーク（`✅` プレフィックス）の下に置かれているが、
  Phase 8.x 系列（python-helper-direct-api）の更新がインライン追記されているだけで
  節構造の整合は取れていない

### P7. モード切替（live / replay）のメニュー導線がない

- 起動時に `--mode {live|replay}` を指定する必要があり、起動後は切り替え不可
- 一般ユーザーは CLI 引数の存在を知らないため、replay を試したくても手段にたどり着けない
- 一度起動した後にモードを変えるには、アプリを終了して別の引数で再起動する必要がある
- メニューバーから現在のモードが視認できない（ウィンドウタイトルにも出ていない）
-（対象外） `Edit` / `View` / `Help` の追加は本アプリでは不要と判断する
  （テキスト編集領域なし・テーマ切替はサイドバーで充足・Help は優先度が低い）

**解決方針**: 実装計画書 [P7-mode-switch-menu.md](./P7-mode-switch-menu.md) を参照。

### P8. プラットフォーム間で File 操作経路が分断

- Linux では `attach` / `subscription` を完全 no-op にしているため、Linux ユーザーは
  サイドバー UI 経由でしか同等操作にアクセスできない
- サイドバーには `名前を付けて保存…（Save As）`・`開く…（Open）` 相当の項目がそもそも
  無いため、Linux では事実上「任意パスへの保存・読込」が不可能
- 設計判断としては妥当（GTK 依存を避ける）だが、UX が断絶している

**解決方針**: 実装計画書 [P8-widget-menu-bar-linux.md](./P8-widget-menu-bar-linux.md) を参照。

---

## 永続状態ファイルとモード切替の関係

F4（dirty 確認）/ F7（モード切替）/ F6（SCENARIO 書き戻し）の影響範囲を明確にするため、
[CLAUDE.md](../../.claude/CLAUDE.md) の「永続状態ファイル」節と本計画書の対応関係を固定する。

| ファイル | lifecycle | 本計画書での扱い |
|---------|-----------|-----------------|
| `saved-state.json` | 起動時 load / 終了時 save / live モードのみ | F3 の `current_path` と独立。`現在開いているドキュメント = 任意パス` の概念を導入しても、自動保存先としての `saved-state.json` は変更しない |
| `engine-session.json` | engine プロセス起動中のみ存在（atomic write）/ Drop 時削除 | F7 のモード切替で engine プロセスを **再起動** する（Drop で `engine-session.json` を削除 → bootstrap で再生成）。詳細フローは [P7-mode-switch-menu.md](./P7-mode-switch-menu.md) §切替時の挙動 参照。F6 の SCENARIO 書き戻し path ガードで誤上書きを防ぐ |
| `tachibana_orders.jsonl` | live モードでの発注 WAL（Python が write / Rust が read） | モード切替で **path・内容を書き換えない**（重複発注防止が壊れる）。F7 の SwitchMode ハンドラは live → replay 切替直前に未約定有無を **read-only で参照** することのみ許容（P7 5 軸 matrix 参照）。書き込みは行わない |

**不変条件**:
これらのファイルは F6 の SCENARIO 書き戻し path ガード（拡張子 `.py` 必須 +
永続状態ディレクトリ書き込み禁止）で物理的に保護される。

---

## 実装ロードマップ

P1〜P6 を依存関係と難易度に沿ってフェーズ化する（P5 は別計画書、P7・P8 もそれぞれ別計画書）。
各フェーズは独立コミット（または独立 PR）として着地できる粒度に保つ。

| Phase | 対応 P | 内容 | 規模 | 依存 |
|-------|--------|------|------|------|
| <a id="f1"></a> **F1** | P6 | `native-menu-bar-impl.md` の旧 `OpenStrategy` 記述削除・`.py` フィルタ説明の正規化 | XS（doc only） | なし |
| <a id="f2"></a> **F2** | P2 | `Ctrl+O` / `Ctrl+S` / `Ctrl+Shift+S` / `Ctrl+Q` の muda accelerator + iced `keyboard::on_key`（Linux 限定） | S | なし |
| <a id="f3"></a> **F3** | P1 | `Flowsurface.current_path` 相当を static で導入。`上書き保存（Save）` メニュー追加 | M | F2 と統合推奨 |
| <a id="f4"></a> ✅ **F4** | P3 | `last_saved_bytes` で dirty 判定 → Open / Quit / SwitchMode 時の確認ダイアログ（F7 共通化ポイント） | M | F3 |
| <a id="f5"></a> ✅ **F5** | P4 | rfd OS confirm に頼らずアプリ層の上書き確認ダイアログ | S | F3 |
| <a id="f6"></a> **F6** | P5 | SCENARIO 定数仕様の実装（[P5-scenario-in-strategy.md](./P5-scenario-in-strategy.md) 参照） | L | F3 |
| <a id="f7"></a> **F7** | P7 | `Mode` メニュー新設（[P7-mode-switch-menu.md](./P7-mode-switch-menu.md) 参照） | M | F4（confirm 共有） |
| <a id="f8"></a> **F8** | P8 | Linux 向け iced 自前メニューバー（[P8-widget-menu-bar-linux.md](./P8-widget-menu-bar-linux.md) 参照） | L | なし（独立） |
| <a id="f9"></a> **F9** | P9 | W&B Submit メニュー — 詳細は [P9-wandb-submit-menu.md](./P9-wandb-submit-menu.md) | — | — |

### 並行実装可能性

`/parallel-agent-dev` で並列消化できる組み合わせ：

| グループ | 並行可能タスク |
|----------|---------------|
| G1（初期） | F1（doc）・F2（accelerator）・F8（Linux 独立）・F6 内の `libcst` 書き戻しユーティリティ単体実装 |
| G2（F3 後） | F4・F5 |
| G3（F3 後） | F6a（GUI prefill）・F6b（CLI フォールバック） |
| G4（F4 後） | F7 |

直列ステップ：F2 → F3 → （F4 / F5 / F6 を並列）→ F7

### 各フェーズの完了条件（DoD）

#### <a id="f1-dod"></a> F1（P6: ドキュメント正規化）

- **対象**: [native-menu-bar-impl.md](./native-menu-bar-impl.md) の `OpenStrategy` 言及削除 →
  `OpenReplayDialog` / `ReplayFormModal` 経路に書き換え
- **テスト方針**: doc-only のためコードテストなし。レビューで節構造整合と filter 記述
  （`.add_filter("JSON", &["json"])` のみ）の正確性を確認
- **観測コマンド**: `git diff docs/✅menu-and-footer/native-menu-bar-impl.md`

#### <a id="f2-dod"></a> F2（P2: ショートカット bind）

- muda 側：`MenuItem::new("開く…（Open）", true, Some(accelerator))` で `Ctrl+O` 等を bind
- iced 側：`keyboard::on_key_press` の subscription を `cfg(target_os="linux")` に**限定**追加
  （macOS / Windows は muda 一本化、二重発火を回避 / C-7）
- 両経路で同じ `Message::NativeMenuAction(Action)` を発火する
- macOS は `Cmd+O` / `Cmd+S` / `Cmd+Shift+S` に変換（muda が自動変換するか要検証）
- **テストファイル**: `tests/accelerator_bind.rs`
- **DoD assert**:
  - `Ctrl+O` 押下 → `Action::OpenFile` dispatch（Linux / Windows）
  - `Ctrl+S` 押下 → `Action::Save` dispatch
  - `Ctrl+Shift+S` 押下 → `Action::SaveAs` dispatch
  - macOS 用 `cfg(target_os="macos")` テストで `Cmd+O` を expect
  - 既存テスト `actions_for_mode` が新メニュー項目（`上書き保存（Save）`）を含むこと
- **期待ログ文字列**: `accelerator dispatched action=OpenFile`（debug ビルド: stdout /
  release: `~/AppData/Roaming/flowsurface/flowsurface-current.log`）
- **観測コマンド**: `cargo test --test accelerator_bind`

#### <a id="f3-dod"></a> F3（P1: 現在のファイル + 上書き保存）

- **static の確定**:
  - 名前: `static CURRENT_PATH: std::sync::Mutex<Option<PathBuf>>`
  - 配置: `src/main.rs`（`APP_MODE` と同形式の static）
  - lock 戦略: poison 時は `into_inner()` でリカバリして書き戻す
  - deadlock 検出は `parking_lot::deadlock::check_deadlock()` に頼らず（`std::sync::Mutex` に効かないため）、Mutex helper による tracing 取得順記録 + `debug_assert!` 違反時 panic で行う（**統一決定 R6-82**。詳細は [./P7-mode-switch-menu.md#acceptance](./P7-mode-switch-menu.md#acceptance) 受け入れ基準 14 / `tests/wandb_modeswitch_lock_order.rs`）
- `上書き保存（Save）` メニュー項目追加（live モード時のみ enable）
- `開く…（Open）` 成功時 / `名前を付けて保存…（Save As）` 成功時 / GUI 起動時の
  `--saved-state` 指定時に `CURRENT_PATH` をセット
- `上書き保存（Save）`：`CURRENT_PATH` ありなら直書き、無ければ Save As にフォールバック
- `restart()` 経路で `CURRENT_PATH` を保全（static 経由で `Flowsurface::new()` を貫通）
- **保存先決定ロジック（A-3）**:
  - `current_path` が `Some(p)` のとき：`Save` は `p` に書き、`saved-state.json` の
    自動保存も従来通り走る（**両方書く**）。これにより「次回 GUI 起動時の自動復元」と
    「ユーザーが管理する任意パスファイル」が両立する。
  - `current_path` が `None` のとき：従来通り `saved-state.json` のみ。
  - **fallback ではなく常時両方書く**ことで「Save 後にクラッシュしても任意パス側だけ
    最新で saved-state は古い」というスキューを排除する
  - **「両方書く」の対象は明示 Save / Save As のみ**（R3 統一決定）。
    自動保存 hook は `CURRENT_PATH` を参照せず、常に `saved-state.json` のみへ書く。
    詳細は F4 の「自動保存の path 契約」を参照
- **テストファイル**: `tests/current_path_persists_across_restart.rs`
- **DoD assert**:
  - 「Open `foo.json` → 編集 → Ctrl+S」で `foo.json` に書き戻り、同時に
    `saved-state.json` にも反映される
  - `restart()` を挟んでも `CURRENT_PATH` が保持される
  - poison させた Mutex に対して `into_inner()` リカバリが効くこと
- **観測コマンド**: `cargo test --test current_path_persists_across_restart`

#### <a id="f4-dod"></a> F4（P3: 未保存変更の confirm）

- **dirty 判定の観測ポイント（B-2 / C-9）**:
  - `Flowsurface.last_saved_bytes: Option<Vec<u8>>` を追加（最後に保存したバイト列）
  - `build_state_json()` は **`BTreeMap` ベースの決定論的シリアライズ**を不変条件化
    （HashMap 由来の順序不定で dirty が偽陽性化するのを防ぐ / C-9）
  - **`last_saved_bytes = None` は clean 扱い（BC-9）**:
    初期状態（一度も保存していない・起動直後）は **未編集とみなし dirty=false**。
    Open / Quit 時に confirm dialog を出さない。ただし起動後にユーザーが編集した
    時点で `build_state_json()` の結果と差分が生じるため、その後の Quit では
    confirm dialog が出る（ケース 4 で保護）
  - `dirty = match last_saved_bytes { None => false, Some(b) => build_state_json() != b }`
  - **`last_saved_bytes` 更新タイミング（A-7）**:
    - 明示 Save / Save As 直後に更新
    - **live の自動保存 hook も同じパスを通す**（自動保存後 `last_saved_bytes` 更新）。
      これにより「自動保存直後に Quit したのに confirm が出る」偽陽性を防ぐ
  - **自動保存の path 契約（R3 統一決定）**:
    **自動保存は CURRENT_PATH へ書き込まない**。自動保存先は常に
    `%APPDATA%\flowsurface\saved-state.json` のみ。`last_saved_bytes` は
    `build_state_json()` の出力 = saved-state.json 書き出し直前のバイト列に固定する
    （CURRENT_PATH へ書いたバイト列ではない）。これにより F3 の「明示 Save は両方書く」
    と「自動保存は saved-state.json のみ」の二系統が衝突せず、dirty 判定の基準も
    `build_state_json()` の単一出力で一意に確定する。
- Open / Quit / SwitchMode 経路で dirty かつ live モード時に
  `confirm_dialog_overlay` を表示（既存実装流用）
- 「保存して続行」「破棄して続行」「キャンセル」の 3 択
- **保存エラー分類（BC-5）**: F4 / F6 共通の保存失敗ハンドラは以下の 3 種に区別する：

  | エラー種別 | 発生条件 | UI 挙動 | ログ出力 |
  |-----------|---------|--------|---------|
  | `Cancelled` | rfd `save_file()` で user が Cancel ボタンを押した | モード切替・Quit を中止。**エラーダイアログは出さない**（ユーザー意図のキャンセル） | INFO レベル相当（特定文字列なし） |
  | `IoError(kind)` | ディスクフル / 権限不足 / device disconnected 等の `std::io::ErrorKind` | モード切替・Quit を中止し、エラーダイアログ表示 | WARN レベル（kind を含む通常メッセージ） |
  | `PathGuardViolation` | F6 path ガード違反（`.py` 拡張子・`current_path` 不一致・永続状態ディレクトリへの書き込み） | モード切替・Quit を中止し、エラーダイアログ表示 | **ERROR レベル + 期待ログ文字列 `BUG: path guard violation path=<p> reason=<r>`** を残す（バグ検出シグナル） |

  ログ出力先: debug ビルド → ターミナル stdout / release →
  `~/AppData/Roaming/flowsurface/flowsurface-current.log`

  **ログレベル契約（R3 統一決定）**: `PathGuardViolation` のみ ERROR レベル + `BUG:` 接頭辞
  （バグ検出シグナル）。`Cancelled` / `IoError` は **WARN** レベル止まり（ERROR を出さない）。
  この区別は `tests/save_error_classification.rs` のケース 6 で保護する。
- F7（mode-switch-menu）の confirm 共通化ポイントとして再利用可能な API に切り出す
- **テストファイル**: `tests/dirty_detection.rs` / `tests/save_error_classification.rs`
- **DoD assert**:
  - **ケース 1（test_initial_state_is_clean / BC-9）**: 起動直後（`last_saved_bytes = None`）→
    無編集 → Quit で confirm dialog が**出ない**
  - **ケース 2**: Open → 無編集 → Quit で confirm dialog が**出ない**
  - **ケース 3**: Open → 何か編集 → Quit で confirm dialog が**出る**
  - **ケース 4（BC-9 補完）**: 起動直後 → 何か編集 → Quit で confirm dialog が**出る**
    （`last_saved_bytes = None` でも編集後は dirty 判定経路に入ることを確認）
  - **ケース 5（stable_serialization / BC-11）**: 同一 state を 100 回 `build_state_json()`
    で serialize → **全 100 回の bytes が一致**することを assert（HashMap への構造的退行を防ぐ）
  - **ケース 6（save_error_classification）**:
    - `Cancelled`: rfd cancel → モード切替中断・エラーダイアログ無し・ERROR ログ無し（WARN レベル相当）
    - `IoError(PermissionDenied)`: WARN ログ出力・ERROR ログ無し
    - `PathGuardViolation`: **ERROR レベルのみ** `BUG: path guard violation path=... reason=...` が出る
      （他の分類は WARN まで）
  - **ケース 7（auto_save_does_not_touch_current_path / R3 統一決定）**:
    `CURRENT_PATH = Some(任意パス)` の状態で自動保存 hook を起動 → 任意パス側のファイルは
    **書き換えられない**（mtime / 内容ともに変化しない）。`saved-state.json` のみ更新され、
    `last_saved_bytes` は `build_state_json()` の出力と一致する
- **観測コマンド**: `cargo test --test dirty_detection` / `cargo test --test save_error_classification`

#### <a id="f5-dod"></a> F5（P4: アプリ層の上書き確認）

- `名前を付けて保存…（Save As）` 経路で rfd `save_file()` 呼び出し前 or 後にアプリ自身の
  確認ダイアログ
- 既存ファイル存在チェック → 存在時のみ confirm 表示
- ダイアログ UI は F4 の `confirm_dialog_overlay` を流用
- **テスト方針**: 既存ファイル存在パスの分岐を unit テストで覆う
- **観測コマンド**: `cargo test save_as_overwrite_confirm`

#### <a id="f6-dod"></a> F6（P5: SCENARIO 定数）

- **モード別挙動表（A-4）**:

  | モード | `開く…（Open）` 対象 | `名前を付けて保存…（Save As）` 対象 | ファイルフィルタ |
  |--------|----------------------|--------------------------------------|------------------|
  | live | `saved-state.json`（layout） | `saved-state.json` のコピーを任意パスへ | `.json` |
  | replay | 戦略 `.py`（`SCENARIO` 抽出） | 戦略 `.py` のコピー先で `SCENARIO` のみ書き換え | `.py` |

  `Save As` メニューラベルは両モードで `名前を付けて保存…（Save As）` 共通だが、
  対象モデルとフィルタはモードに依存する。

- 詳細は [P5-scenario-in-strategy.md](./P5-scenario-in-strategy.md) を参照
- **テストファイル**: `python/tests/test_scenario_extract.py` /
  `python/tests/test_scenario_writeback.py`
- **観測コマンド**: `uv run pytest python/tests/test_scenario_*.py -v`

#### <a id="f7-dod"></a> F7（P7: モード切替メニュー）

- 詳細は [P7-mode-switch-menu.md](./P7-mode-switch-menu.md) を参照（本計画書からは
  F4 の confirm 共通化ポイントを流用する依存関係のみ提示）
- **本計画書側の DoD**:
  - F4 の confirm dialog が SwitchMode 経路でも発火すること
  - SwitchMode で **engine プロセスは再起動** され、`engine-session.json` は Drop で削除→
    bootstrap で再生成される（詳細は P7 §切替時の挙動 参照）
  - SwitchMode で `tachibana_orders.jsonl` を **書き換えない**（read-only 参照のみ許容）
- **テスト方針**: P7 計画書の `mode_switch_*` テストに合流。本計画書側は
  「F4 confirm dialog が SwitchMode から呼ばれる」回帰のみ守る
- **観測コマンド**: `cargo test mode_switch_invokes_confirm`

#### <a id="f8-dod"></a> F8（P8: Linux 向け自前メニューバー）

- 詳細は [P8-widget-menu-bar-linux.md](./P8-widget-menu-bar-linux.md) を参照
- **本計画書側の DoD**:
  - F2 の iced `keyboard::on_key`（Linux 限定）と整合し二重発火しないこと
  - 同じ `Message::NativeMenuAction(Action)` 経路でメニュー操作が dispatch されること
- **テスト方針**: P8 計画書の `linux_menu_bar_*` テストに合流
- **観測コマンド**: `cargo test --target x86_64-unknown-linux-gnu linux_menu_bar`

---

## 確定済み設計判断

実装前の論点に対する確定方針。決着の理由を残す。

### Q1. 「現在のファイル」の状態をどこに持つか → **A 案採用**

`Flowsurface` 構造体に `current_path: Option<PathBuf>` を追加し、`restart()` を
貫通させるため `static CURRENT_PATH: std::sync::Mutex<Option<PathBuf>>` を
`src/main.rs` に併設する（`APP_MODE` と同形式）。

**理由**: B 案（`saved-state.json` メタデータ）は自動保存ファイルに「自分以外のパス」を
書く設計上の関心混入が気持ち悪い。A 案で発生する `restart()` 課題は引継ぎ用 static で解決可能。

不採用案：

- **B. `saved-state.json` に "last opened path" メタデータを書く**
  - 再起動を挟んでも維持できる
  - 自動保存ファイル自体に「自分以外のパス」を記録するのは関心が混ざる

### Q2. dirty 判定方式 → **`last_saved_bytes` との等価判定 + `BTreeMap` 決定論シリアライズ**

live の自動保存が常に走るため、別途変更追跡フラグを持つと二重管理になる。
`build_state_json()` の出力と最後に保存済みのバイト列（`last_saved_bytes`）を比較して
dirty を判定する。**`BTreeMap` で決定論的にシリアライズする**ことを不変条件化し、
順序不定による偽陽性を回避する（テスト `dirty_detection::stable_serialization` で保護）。

### Q3. `libcst` 依存追加 → **承認**

P5 の SCENARIO 書き戻しに `libcst` を採用する（純 Python・MIT・コメント保持）。
`pyproject.toml` の `[project.dependencies]` に追加する。

### Q4. Open 後の再起動を廃止できるか → **当面は restart() 維持**

in-place 差し替えは「ペイン・ウィンドウ・購読の動的差し替え」のバグ温床になり得る。
F3〜F4 では既存 `restart()` を流用して `current_path` を引き継ぐ実装で十分機能する。
in-place 化は将来の最適化として deferred。

### Q5. replay モードのローカル保存対象 → **やらない（W&B 一本化）**

旧 P5 の「replay 結果永続化レイヤ（fills / equity / narrative JSONL）」は不採用。
`/wandb` スキル / [wandb-vision.md](../plan/wandb-vision.md) で達成する。
ローカル JSONL を吐いても読む UI を作らない方針のため dead code 化しやすい。

### Q6. アクセラレータ経路の一本化 → **muda が正規・iced は Linux 限定**

macOS / Windows は muda が完全に動作するため iced `keyboard::on_key` は登録しない。
Linux のみ muda が GTK 制約で完全動作しないので、iced 側に
`cfg(target_os="linux")` 限定で fallback ハンドラを置く。これにより
Linux 以外で二重発火（同じキーで OpenFile が 2 回 dispatch される）を構造的に防ぐ。
F2 のテスト `accelerator_bind::no_double_dispatch` で保護する。

---

## 非スコープ（やらないこと）

- **クラウド同期**（OneDrive / iCloud / Dropbox 連携） — ローカルファイルのみで完結する
- **自動バックアップ世代管理** — `read_from_file` の破損時バックアップ機構で十分
  （SCENARIO 書き戻しの `.bak.<UTC秒>` は別系統）
- **複数ドキュメント同時編集（MDI）** — アプリは単一 layout を扱う前提
- **テキストエディタ的な Edit メニュー（Cut / Copy / Paste）** — テキスト編集領域がない

---

## 関連ファイル早見表

| ファイル | 役割 |
|---------|------|
| [src/native_menu.rs](../../src/native_menu.rs) | メニュー構築・muda 統合 |
| [src/main.rs](../../src/main.rs) | `NativeMenu*` ハンドラ群・`build_state_json` |
| [native-menu-bar-impl.md](./native-menu-bar-impl.md) | 既存実装記録（旧仕様の残骸あり） |
| [footer-impl.md](./footer-impl.md) | フッター実装記録（メニュー関連の補足含む） |
| [P5-scenario-in-strategy.md](./P5-scenario-in-strategy.md) | F6: SCENARIO 定数仕様 |
| [P7-mode-switch-menu.md](./P7-mode-switch-menu.md) | F7: モード切替メニュー仕様 |
| [P8-widget-menu-bar-linux.md](./P8-widget-menu-bar-linux.md) | F8: Linux 向け自前メニューバー仕様 |

---

## レビュー反映 (2026-05-04, ラウンド1)

**対象フェーズ**: F1（doc 整理）, F2（アクセラレータ）, F3（CURRENT_PATH + Save）

### 発見された指摘と対処

| ID | 重要度 | 内容 | 対処 |
|----|--------|------|------|
| HIGH-1 | HIGH | Linux replay モードで live 専用ショートカット（Open/Save/SaveAs）が動作してしまう | `linux_keyboard_subscription(app_mode: AppMode)` に引数を追加し、`is_live` ガードで OpenFile/Save/SaveAs を抑制。`subscription()` の API も `app_mode: AppMode` を受け取るよう変更 |
| HIGH-2 | HIGH | Ctrl+Q（終了）が未実装 | `Action::Quit` バリアントを追加。Windows: `Code::KeyQ` の `MenuItem`（MenuIds に記録）。macOS: `PredefinedMenuItem::quit`（OS 処理, ID 追跡不要）。Linux: `Character("q")` + ctrl。main.rs に `Action::Quit => iced::window::close(self.main_window.id)` ハンドラ追加 |
| MEDIUM-1 | MEDIUM | リグレッションテストが欠如 | `tests/accelerator_bind.rs`（12 テスト）と `tests/current_path_persists_across_restart.rs`（7 テスト）を新規作成。ソースインスペクション方式で GUI ランタイム不要 |

### 検証結果

```
cargo test --workspace → 全テスト GREEN（新規 19 テスト含む）
```

**残 LOW 指摘**: なし

**次フェーズ**: F4（dirty 検知 + 破棄確認ダイアログ）
| [P9-wandb-submit-menu.md](./P9-wandb-submit-menu.md) | F9: W&B submit メニュー仕様（送信履歴・バッファ削除・Sign in/out・Submit） |

---

## レビュー反映 (2026-05-04, ラウンド2)

**対象フェーズ**: F2（アクセラレータ）, F3（CURRENT_PATH + Save）, F4（dirty 確認）

### 解消した指摘

| ID | 重要度 | 内容 | 対処 |
|----|--------|------|------|
| H1 | HIGH | `event_stream()` 内 `MENU_IDS.lock()` poison 時に `break` していたため、外側 `loop` が継続しすべてのメニュー操作が無音で無視されていた | `Err(_) => break` を `Err(poisoned) => poisoned.into_inner()` のリカバリに変更。`CURRENT_PATH` と同パターンで処理を継続 (`src/native_menu.rs`) |
| H2 | HIGH | `Action::Quit` が `iced::window::close()` を直接呼び出しており、`ExitRequested` 経路（dirty チェック・保存）を迂回していた | `window::collect_window_specs(active_windows, Message::ExitRequested)` 経由に変更し、既存の dirty チェック・保存フローを再利用 (`src/main.rs`) |
| LOW-2 | LOW | `attach()` 内 `MENU_IDS.lock()` が `if let Ok(...)` で poison 時を無音無視していた | `match` に変えて `Err(poisoned) => *poisoned.into_inner() = Some(...)` でリカバリ (`src/native_menu.rs`) |
| M1 | MEDIUM | `current_path_uses_into_inner_for_poison_recovery` テストの `into_inner()` カウントにコメント行が含まれ、誤差が生じていた | `.lines().filter(|l| !l.trim_start().starts_with("//")).filter(|l| l.contains("into_inner()"))` でコメント行を除外。また `NativeOpenFilePendingCheck` のマッチアームが `cargo fmt` で複数行に展開されてテストが壊れていたため、`\n            Message::NativeOpenFilePendingCheck {` プレフィックスで先頭一致検索に変更 (`tests/current_path_persists_across_restart.rs`, `tests/dirty_detection.rs`) |
| M2 | MEDIUM | `NativeSaveAsWithSpecs` で `build_state_json` が `None` を返した場合の `else` ブランチが存在しなかった | `else { log::warn!("[NativeSaveAsWithSpecs] build_state_json returned None ...") }` を追加 (`src/main.rs`) |
| M3 | MEDIUM | `NativeSaveAsWithSpecs` の成功パスで `save_state_to_disk` 呼び出し後に `self.last_saved_bytes = Some(json.into_bytes())` が重複更新していた | `save_state_to_disk` 内部でも `last_saved_bytes` を更新しているため、後続の重複代入を削除してコメントで明示 (`src/main.rs`) |
| M4 | MEDIUM | `subscription()` 内 `APP_MODE.get().unwrap_or(...)` の安全性根拠コメントが欠如していた | `// SAFETY: APP_MODE is initialised in main() before iced starts; ...` コメントを追加 (`src/main.rs`) |
| M5 | MEDIUM | `ExitRequested` と `NativeOpenFilePendingCheck` で dirty チェックが `confirm_dialog` の存在チェックなしに実施されるため、先着 intent が orphan 化するリスクがあった | 両箇所に `&& self.confirm_dialog.is_none()` ガードを追加 (`src/main.rs`) |

### 持ち越し

| ID | 内容 | 理由 |
|----|------|------|
| H3 | macOS Cmd+Q の F4 dirty チェック bypass | F4 は実装済みだが、macOS の `NSApplicationDelegate applicationShouldTerminate:` フックを `muda` / `iced` 側に設けないと Cmd+Q が OS により直接処理され dirty チェックを迂回する。F4（dirty-check 実装）完了後に `NSApplicationDelegate` フックが必要になる点を既知制約として記録。F4 の DoD には含めない |

### 検証結果

```
cargo fmt --check     → 差分なし
cargo clippy --workspace -- -D warnings → 警告なし
cargo test --workspace → 全テスト GREEN
```
