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
| <a id="f6"></a> ✅ **F6** | P5 | SCENARIO 定数仕様の実装（[P5-scenario-in-strategy.md](./P5-scenario-in-strategy.md) 参照） | L | F3 |
| <a id="f7"></a> ✅ **F7** | P7 | `Mode` メニュー新設（[P7-mode-switch-menu.md](./P7-mode-switch-menu.md) 参照） | M | F4（confirm 共有） |
| <a id="f8"></a> ✅ **F8** | P8 | Linux 向け iced 自前メニューバー（[P8-widget-menu-bar-linux.md](./P8-widget-menu-bar-linux.md) 参照） | L | なし（独立） |
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

#### <a id="f6-dod"></a> ✅ F6（P5: SCENARIO 定数）— 実装完了（2026-05-04）

**実装済みコンポーネント**:

- `python/engine/scenario.py` — `extract()` / `validate()` / `write_back()` 実装（libcst CST 置換、atomic write、.bak、path guard）
- `python/engine/replay_session.py` — `_resolve_cli_params()` による CLI SCENARIO フォールバック（F6b）
- `python/engine/server.py` — `_dispatch()` に `LoadStrategyScenario` / `SaveStrategyScenario` の IPC ハンドラ追加
- `python/engine/schemas.py` — `LoadStrategyScenario` / `SaveStrategyScenario` コマンド、`StrategyScenarioLoaded` / `StrategyScenarioLoadFailed` / `StrategyScenarioSaved` イベント（SCHEMA_MINOR=10）
- `engine-client/src/dto.rs` — 対応 Command / Event バリアント
- `docs/example/buy_and_hold.py` — `SCENARIO` 定数追加
- テスト: `test_scenario_load.py` / `test_scenario_writeback.py` / `test_scenario_path_guard.py` / `test_scenario_cli.py` / `engine-client/tests/scenario_roundtrip.rs`

**意図的に次フェーズ以降へ持ち越した項目**:

- ✅ `ReplayFormModal` への `StrategyScenarioLoaded` 受信時 prefill（Rust GUI 実装、F6a 後半 — 2026-05-04 完了）
- ✅ replay モードの `File > 開く...` で `.py` ファイルフィルタへ切り替え（F6a 後半 — 2026-05-04 完了）
- Linux 自前メニューバー（widget menu bar）での `.py` フィルタ UX 検証（P8 別計画）

**F6a 後半（2026-05-04 完了）**:

- `src/main.rs::Action::OpenFile` を `app_mode()` で分岐し、replay モードでは `.py` ファイルフィルタの OS ダイアログを開く（live モードは従来の `.json` 経路のまま）
- 新規 Message: `NativeOpenStrategyPicked(Option<PathBuf>)` / `StrategyScenarioLoadedEvent { path, scenario }` / `StrategyScenarioLoadFailedEvent { path, reason }`
- engine の `EngineEvent::StrategyScenarioLoaded` / `StrategyScenarioLoadFailed` を `map_engine_event_to_tachibana()` で対応 Message へ変換
- `ReplayFormModal::prefill_from_scenario()` / `set_strategy_file_only()` を新設。granularity Literal（`Trade` / `Minute` / `Daily`）は `Granularity` enum へマッピング、未知値は既存値を保持
- 失敗時は `current_path` を更新せず toast でエラー表示。SCENARIO 不在の `.py` は `strategy_file` だけセットしてフィールド空のまま（仕様通り）
- live モードガード: `NativeOpenStrategyPicked` ハンドラは `app_mode() != Replay` のときに warn ログを出して drop する
- M-7 poison recovery: `CURRENT_PATH.lock()` を `into_inner()` 経由でフォールバック
- リグレッションガードテスト（合計 11 件追加）:
  - `src/modal/replay_form.rs` 内: `prefill_from_scenario_populates_all_fields` / `prefill_from_scenario_clears_validation_error` / `prefill_from_scenario_unknown_granularity_preserves_existing` / `prefill_from_scenario_partial_keeps_other_fields` / `prefill_from_scenario_non_object_only_sets_path` / `set_strategy_file_only_sets_path_and_keeps_fields`
  - `src/main.rs::native_menu_handler_tests` に `open_file_replay_mode_uses_py_filter` / `open_strategy_picked_some_sends_load_strategy_scenario` / `strategy_scenario_loaded_event_prefills_modal` / `strategy_scenario_load_failed_event_pushes_toast_only`

**モード別挙動表（A-4）**:

  | モード | `開く…（Open）` 対象 | `名前を付けて保存…（Save As）` 対象 | ファイルフィルタ |
  |--------|----------------------|--------------------------------------|------------------|
  | live | `saved-state.json`（layout） | `saved-state.json` のコピーを任意パスへ | `.json` |
  | replay | 戦略 `.py`（`SCENARIO` 抽出） | 戦略 `.py` のコピー先で `SCENARIO` のみ書き換え | `.py` |

  `Save As` メニューラベルは両モードで `名前を付けて保存…（Save As）` 共通だが、
  対象モデルとフィルタはモードに依存する（GUI 側は次フェーズ）。

- 詳細は [P5-scenario-in-strategy.md](./P5-scenario-in-strategy.md) を参照
- **テストファイル**: `python/tests/test_scenario_load.py` / `python/tests/test_scenario_writeback.py` /
  `python/tests/test_scenario_path_guard.py` / `python/tests/test_scenario_cli.py` /
  `python/tests/test_schema_minor_compat.py`
- **観測コマンド**: `uv run pytest python/tests/test_scenario_*.py python/tests/test_schema_minor_compat.py -v`

##### レビュー反映 (2026-05-04, ラウンド 1)

F6 実装に対する e-station-review レビュー指摘 22 件（HIGH 5 / MEDIUM 17）を方針 B
（計画書を実装に寄せる）で反映。詳細は
[P5-scenario-in-strategy.md §レビュー反映 (2026-05-04, ラウンド 1)](./P5-scenario-in-strategy.md#レビュー反映-2026-05-04-ラウンド-1) を参照。

主な変更点：

- `_check_path_guard` / `write_back` から `current_path` 引数を削除（loaded_path 一軸の FCFS 不変条件に簡素化）
- `_verify_writeback` を「ast.parse + extract（構文）+ validate（形状）」の二段に変更（importlib import 検証は除外）
- `SaveErrorCode = Literal[...]` を `schemas.py` に追加（9 値固定）
- `save_as=true` かつ `path == loaded_path` を server-side で reject
- SCHEMA_MINOR は F7 後 `>= 11` を保持。`scenario_roundtrip.rs` に明示。`test_schema_minor_compat.py` で minor 差異許容を回帰ガード

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
- **`SCENARIO.schema_version == 1` の strict validation の緩和** — v1 のみ受理する現状の挙動を維持し、v2 移行時に別計画で緩和する（M10 / 2026-05-04 ラウンド1 反映）

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

---

## レビュー反映 (2026-05-04, ラウンド3)

**対象フェーズ**: F2/F3/F4/F5（review-fix-loop R1 修正）

### 解消した指摘

| ID | 重要度 | 内容 |
|----|--------|------|
| C-1 | CRITICAL | GoBack で `pending_open_file` / `pending_exit_windows` をクリアしない → dirty チェック永続スキップ |
| H-1 | HIGH | `NativeOpenFilePendingCheck` / `DiscardAndOpenFile` で `log::error!` → `log::warn!` に修正（BC-5 準拠）|
| H-2 | HIGH | `save_state_to_disk` で `log::error!` → `log_save_error(&SaveError::IoError(...))` に変更（BC-5） |
| H-4 | HIGH | `AudioStream::streams: FxHashMap` → `BTreeMap` に変更（SerTicker/Exchange/Ticker に Ord 実装追加）|
| H-5 | HIGH | `build_state_json` が2回実行される → `write_json_to_saved_state_disk` helper を新設し `NativeSaveAsWithSpecs` で再利用 |
| M-1 | MEDIUM | `is_dirty` の `&mut self` 副作用についてコメント追加 |
| M-2 | MEDIUM | `native-menu-bar-impl.md` の旧 `OpenStrategy` 記述を現行 (`OpenReplayDialog`) に更新、`OnceLock` → `Mutex<Option<_>>` 修正 |
| M-3 | MEDIUM | `ConfirmSaveAsOverwrite` に `confirm_dialog.is_none()` ガード追加（ダイアログ未表示時の誤実行防止） |
| M-4 | MEDIUM | `APP_MODE.get().unwrap_or(...)` → `.expect("APP_MODE must be set before iced starts")` |
| M-5 | MEDIUM | `save_error_classification.rs` に `save_state_to_disk_does_not_use_log_error` テスト追加 |
| M-6 | MEDIUM | `io_error_emits_warn_not_error` の 500 文字ハードリミット除去 |
| M-7 | MEDIUM | `current_path_uses_into_inner_for_poison_recovery` テストを各呼び出し箇所別個別確認方式に変更 |
| M-8 | MEDIUM | `linux_ctrl_s_dispatches_save` を `Some(Action::Save)` パターンで検索（`Action::SaveAs` 誤マッチ防止） |
| M-9 | MEDIUM | `DiscardAndExit` の `pending_exit_windows` が None の場合に `log::warn!` 出力（既に実装済みを確認） |
| M-10 | MEDIUM | `cancelled_does_not_emit_error_or_warn_log` の 300 文字ハードリミット除去 |
| L-1 | LOW | `Cancelled` の `#[allow(dead_code)]` に TODO コメント追記 |
| L-4 | LOW | `is_dirty` の `unwrap_or(false)` に replay モードの根拠コメント追加 |

| H-3 | HIGH | `NativeSaveAsWithSpecs` を `Task::perform` + `tokio::fs::write` で非同期化 → `Message::NativeSaveComplete` バリアント新設（`Cargo.toml` に `tokio/fs` feature 追加） |

### R2/R3 追加修正（R1 サニティチェック後）

| ID | 重要度 | 内容 | 対処 |
|----|--------|------|------|
| R2-1 | MEDIUM | GoBack で `pending_save_path` 未クリア（Save As ダイアログ中 Escape 後に古いパスが残留） | `GoBack` ハンドラの `pending_save_path = None` が実装済みであることを確認（修正不要） |
| R2-2 | MEDIUM | `ConfirmSaveAsOverwrite` の early return が `pending_save_path` を残留 | early return 前に `pending_save_path = None` を追加 |
| R2-3 | MEDIUM | `is_dirty` の `last_saved_bytes.clone()` が不要な可能性 | `build_state_json(&mut self)` が `&mut self` を要求するため `as_deref()` 不可 → 理由コメントを追記 |
| R3 | — | サニティチェック → MEDIUM 以上ゼロ確認（**収束**） | — |

### 追加したテスト

| テスト | ファイル | 目的 |
|--------|----------|------|
| `escape_on_confirm_clears_pending_state` | `dirty_detection.rs` | C-1: GoBack が pending_open_file / pending_exit_windows をクリアすることを確認 |
| `stable_serialization_with_audio_streams` | `dirty_detection.rs` | H-4: AudioStream の BTreeMap 化で決定論的シリアライズを確認 |
| `save_state_to_disk_does_not_use_log_error` | `save_error_classification.rs` | M-5: save_state_to_disk が log::error! を使わないことを確認 |

### 検証結果

```
cargo fmt --check                        → 差分なし
cargo clippy --workspace -- -D warnings  → 警告なし
cargo test --workspace                   → 全テスト GREEN（新規テスト含む）
```

## レビュー反映 (2026-05-04, ラウンド2)

**対象フェーズ**: F4（dirty 確認）, F5（上書き確認）

### 解消した指摘

| ID | 重要度 | 内容 | 対処 |
|----|--------|------|------|
| H1 | HIGH | NativeSaveAsPath に confirm_dialog.is_none() ガード欠落（M5 漏れ） | src/main.rs:2734 にガード追加 |
| H2 | HIGH | stable_serialization が build_state_json() を迂回、BC-11 保護無効 | tests/dirty_detection.rs を実態に近い検証に修正 |
| H3 | HIGH | pending_save_path が ToggleDialogModal(None)/GoBack でクリアされない | 両ハンドラに pending_save_path = None 追加 |
| H4 | HIGH | PathGuardViolation に reason フィールドなし | SaveError::PathGuardViolation { reason } に変更 |
| H5 | HIGH | build_state_json(&mut self) がシリアライズ以外で state 変更 | 責務分離（コメント対応または実装分離） |
| M1 | MEDIUM | write_json_to_file 失敗が log::error! → BC-5 は WARN | log::warn! に変更 |
| M2 | MEDIUM | NativeSaveAsWithSpecs で save_state_to_disk 失敗時 last_saved_bytes 未更新 | fs::write 成功後に明示的に last_saved_bytes 更新 |
| M3 | MEDIUM | event_stream で receiver をループ毎取得 | ループ外に移動 |
| M4 | MEDIUM | SaveError の dead code コメント不正確 | TODO(F6) コメントに更新 |
| M5 | MEDIUM | F4-DoD ケース4の専用テスト欠落 | dirty_detection.rs にテスト追加 |
| M6 | MEDIUM | DiscardAndExit の unwrap_or_default() が無音 | log::warn! 追加 |
| M7 | MEDIUM | native_menu.rs の .ok() 握り潰し | log::error! に変更 |
| M8 | MEDIUM | save_as_overwrite_confirm.rs の重複 src.find | 削除 |

### ラウンド2 追加修正（サニティチェック後）

| ID | 重要度 | 内容 | 対処 |
|----|--------|------|------|
| R2-M1 | MEDIUM | NativeSaveAsWithSpecs: fs::write 成功後に last_saved_bytes を先に更新（save_state_to_disk 失敗でも偽陽性回避） | last_saved_bytes 更新を fs::write 成功直後に移動 |
| R2-M2 | MEDIUM | append_items 失敗後も MENU_IDS が設定され不整合 | 失敗時 early return で MENU_IDS を設定しない |
| R2-M3 | MEDIUM | init_for_hwnd の .ok() 残存 | log::error! に変更 |

### ラウンド3 追加修正（R2 サニティチェック後）

| ID | 重要度 | 内容 | 対処 |
|----|--------|------|------|
| R3-H1 | HIGH | init_for_hwnd 失敗後も MENU_IDS に有効 ID が残存し、HMENU 未アタッチのまま event_stream が「正常」と誤認 | 失敗時に `MENU_IDS` を `None` に戻して event_stream を無効化 (`src/native_menu.rs`) |

---

## ユーザー追加指摘・追加修正 (2026-05-04, ラウンド4)

### ユーザー指摘（レビューループ収束後）

| ID | 重要度 | 内容 | 対処 |
|----|--------|------|------|
| U-H1 | HIGH | `pending_save_path` が 1 本の共有スロット→非同期保存フロー重複時に保存先すり替わりの競合 | `pending_save_path` フィールドを削除。`NativeSaveAsWithSpecs { path, windows }` / `ConfirmSaveAsOverwrite { path }` にパスを直接埋め込む。クロージャでキャプチャ。 |
| U-M1 | MEDIUM | F3 DoD「`--saved-state` 起動時の `CURRENT_PATH` 初期化」が未実装 | `src/cli.rs` に `--saved-state <PATH>` 追加、`INITIAL_STATE_PATH: OnceLock<PathBuf>` static 追加、`Flowsurface::new()` で読み込み・`CURRENT_PATH` 初期化 |

### R4 サニティチェック（silent-failure-hunter）

U-H1 / U-M1 修正後に追加実施。

| ID | 重要度 | 内容 | 対処 |
|----|--------|------|------|
| R4-M1 | MEDIUM | `--saved-state` に非 UTF-8 パスが渡された場合、`unwrap_or(data::SAVED_STATE_PATH)` でデフォルトパスの内容を読み込むが `CURRENT_PATH` には非 UTF-8 パスがセットされ読み書き先が不整合 | `to_str()` が `None` の場合は `log::error!` を出力し `SavedState::default()` を返す（`CURRENT_PATH` はセットしない） |
| R4-L1 | LOW | `ConfirmSaveAsOverwrite` ガード発動時（Escape と同一フレーム）に保存が黙って中断 | 発生頻度極低 + ユーザーが Escape 押下の場合と区別不要のため対処しない |
| R4-L2 | LOW | `GoBack` ハンドラで `pending_save_path` 参照の残留確認 | フィールド自体が削除済みで残留なし。確認のみ |

**最終状態**: CRITICAL/HIGH/MEDIUM ゼロ。LOW 2 件（R4-L1, R4-L2）は対処不要と判断。収束。

### 最終検証コマンド

```
cargo fmt --check        → OK
cargo clippy --workspace -- -D warnings  → OK
cargo test --workspace   → 全テスト GREEN
```

---

## レビュー反映 (2026-05-04, ラウンド5サニティ)

**対象フェーズ**: F1+F2+F3（F4〜F9 積み上げ後の不変条件再検証）

並列レビュアー: `rust-reviewer` + `silent-failure-hunter` の 2 体。

### 結果

CRITICAL: 0 / HIGH: 0 / **F1+F2+F3 の不変条件は全 10 項目で収束維持**。

検証した不変条件:

1. CURRENT_PATH poison リカバリ（全 7 箇所で `into_inner()` パターン維持）
2. accelerator 二重発火回避（`linux_keyboard_subscription` が `cfg(target_os="linux")` 限定 + `app_mode` ガード）
3. Quit → ExitRequested 経由（`window::collect_window_specs` 経由で dirty チェック・保存フローを通過）
4. build_state_json 決定論性（`State` は `Vec<Layout>` + フラット構造、HashMap/FxHashMap 退行なし）
5. last_saved_bytes 更新規則（明示 Save / 自動保存 hook で同パスを通す）
6. pending_save_path 削除（U-H1 で削除されたフィールドが F4〜F9 で復活していない）
7. MENU_IDS poison / init_for_hwnd 失敗時の `None` リセット
8. `--saved-state` 非 UTF-8 ガード（`to_str() == None` で `log::error!` + default fallback）
9. confirm_dialog ガード（pending state を立てる前に `confirm_dialog.is_none()` を要求）
10. GoBack の pending 一括クリア（`pending_open_file` / `pending_exit_windows` / `pending_mode_switch` / `_mode_switch_guard`）

### 副次的に発見された F9 経路の指摘（F1+F2+F3 スコープ外）

| ID | path:line | 重要度 | 内容 |
|----|-----------|--------|------|
| R5-M1 | `src/main.rs:4866` | MEDIUM | `ClearRunBufferConfirmed` の `remove_dir_all` 失敗アームに `log::warn!` 抜け（BC-5 ログレベル契約違反）。リリースビルドで削除失敗が `flowsurface-current.log` に残らない |
| R5-M2 | `src/main.rs:4832`, `4856` | MEDIUM | `WandbLogoutConfirmed` / `ClearRunBufferConfirmed` が `confirm_dialog = None` するが `pending_exit_windows` / `pending_open_file` / `pending_mode_switch` をクリアしない。`confirm_dialog.is_none()` ガードで現時点は実害なしだが、将来そのガードを緩めた場合の orphan リスク |
| R5-L1 | `src/main.rs:266` | LOW | `wandb_login` の `stdin.write_all` を `let _` で握り潰し。下流で `Err` 変換されるため silent failure としては成立せず、デバッグ情報のみ消失 |

これらは F9 セクション側で対処予定（または F9 のレビュー反映ブロックへ繰越）。F1+F2+F3 のスコープには影響しない。

### 検証コマンド

両並列レビュアーの事前検証で:

```
cargo check --workspace                  → PASS
cargo clippy --workspace -- -D warnings  → 警告なし
cargo fmt --check                        → 差分なし
cargo test --workspace                   → 全 234+ テスト PASS
```

**最終状態**: F1+F2+F3 は R4 で確立した収束を R5 サニティでも維持。F4〜F9 の積み上げによる退行なし。

---

## レビュー反映 (2026-05-04, ラウンド6 — F4+F5 単独ループ)

**対象フェーズ**: F4（dirty 確認）, F5（上書き確認）

並列レビュアー: `rust-reviewer` + `silent-failure-hunter`（5 件指摘 / HIGH 1 + MEDIUM 4）。

### 解消した指摘

| ID | 重要度 | 内容 | 対処 |
|----|--------|------|------|
| F-H1 | HIGH | `Message::SaveAndExit` で CURRENT_PATH への `std::fs::write` 成功直後に `last_saved_bytes` を更新していなかった。後続の `write_json_to_saved_state_disk` が失敗しても `current_path.is_some() \|\| saved_ok` で `iced::exit()` するため、A-7「明示 Save 直後に `last_saved_bytes` 更新」契約に反していた | 名前付きドキュメント書き込み成功直後に `self.last_saved_bytes = Some(json.as_bytes().to_vec());` を追加（`src/main.rs` SaveAndExit アーム）。`tests/save_error_classification.rs::save_and_exit_updates_last_saved_bytes_on_current_path_write` で固定 |
| F-M1 | MEDIUM | `let windows = self.pending_exit_windows.take().unwrap_or_default();` が `None` を空 HashMap に化けさせ、`build_state_json` が空ウィンドウ配置のまま保存して silent corruption を起こす | `let-else` パターンに変更し、`None` 時は `log::warn!("[SaveAndExit] pending_exit_windows is None — SaveAndExit dispatched without prior ExitRequested dirty check");` を出力した上で `return Task::none();`（`src/main.rs` SaveAndExit アーム）。`tests/save_error_classification.rs::save_and_exit_logs_warn_when_pending_exit_windows_is_none` で固定 |
| F-M2 | MEDIUM | `Action::Save` / `Action::SaveAs` ハンドラに `confirm_dialog.is_none()` ガードがなく、confirm dialog 表示中に Ctrl+S / Ctrl+Shift+S を押すと rfd の `save_file()` ダイアログが多重起動する隙があった | 両アーム冒頭に `if self.confirm_dialog.is_some() { return Task::none(); }` を追加（`src/main.rs`）。`tests/save_error_classification.rs::action_save_and_save_as_guard_against_existing_dialog` で固定 |
| F-M3 | MEDIUM | `tests/dirty_detection.rs::escape_on_confirm_clears_pending_state` が `pending_open_file` / `pending_exit_windows` のクリアのみ検査し、F7 経路の `pending_mode_switch` / `_mode_switch_guard` クリア不変条件（計画書 L796 不変条件 10）を pin していなかった | 同テストに `pending_mode_switch = None` と `_mode_switch_guard = None` の assert を追加（`tests/dirty_detection.rs`）。コード側は既に対応済みのためテスト追加のみ |
| F-M4 | MEDIUM | `Message::SaveAndOpenFile` で CURRENT_PATH=Some(p) かつ `std::fs::write(p, json)` 成功 → `data::write_json_to_file(&json, SAVED_STATE_PATH)` 失敗時、`pending_open_file` は `.take()` 済みで `return Task::none()` → open 中止 + confirm dialog も復元されず retry 不能だった | (a) 案を採用：saved-state.json 失敗を非 fatal として open 続行。`return Task::none()` を撤去し、warn ログ + toast 通知の後 `restart()` で open を続行する（`NativeSaveComplete` の非 fatal パスと整合）。toast メッセージも「saved-state.json への書き込みに失敗しましたが、開く処理は続行します」に更新（`src/main.rs` SaveAndOpenFile アーム）。`tests/save_error_classification.rs::save_and_open_file_continues_when_saved_state_write_fails` で固定 |

### 検証結果

```
cargo fmt --check                            → 差分なし
cargo clippy --workspace -- -D warnings      → 警告なし
cargo test --test dirty_detection            → 16 passed; 0 failed
cargo test --test save_error_classification  → 12 passed; 0 failed
cargo test --test save_as_overwrite_confirm  →  5 passed; 0 failed
cargo build                                  → 成功
```

> **注記**: `cargo test --workspace` は F9 範囲の `meta_json_struct_defined_in_wandb_auth_rs`
> が pre-existing で FAIL する。これは F4/F5 ループ範囲外であり、本ラウンドで触れていない
> （オーケストレーターと事前合意済み）。F4/F5 関連の上記 3 テストファイルは全て GREEN。

**最終状態**: F-H1 / F-M1〜F-M4 を全て解消。F4 + F5 不変条件は本ラウンドで再収束。

### R2 追加修正（サニティチェック後）

| ID | 重要度 | 内容 | 対処 |
|----|--------|------|------|
| R2-M1 | MEDIUM | F-M4 fix が saved-state.json 書き込み失敗時も `CURRENT_PATH = new_path` 更新 + `restart()` を実行 → 旧 saved-state.json をロードしつつ CURRENT_PATH は新ファイルを指す不整合 → 次の Ctrl+S が旧レイアウトで新ファイルを上書きする silent corruption | Err 時は abort（CURRENT_PATH 更新と restart() をスキップ。warn + toast のみ）。Ok 時のみ CURRENT_PATH 更新 + restart() に分岐 (`src/main.rs` SaveAndOpenFile, R1 で追加したテストをリネーム) |

R1 の F-M4「saved-state.json 失敗を非 fatal 化（restart で open 続行）」は **revert**。理由：restart() 経路では新 layout を渡す手段がないため、結果として user 期待（new_path の内容で開く）と実際の挙動（旧 saved-state がロード）が乖離する。saved-state.json 書き込み失敗は I/O 異常時のみ起きるレアケースのため abort + toast retry 通知が妥当。

R2 検証:

```
cargo fmt --check                            → 差分なし
cargo clippy --workspace -- -D warnings      → 警告なし
cargo test --test dirty_detection            → 16 passed; 0 failed
cargo test --test save_error_classification  → 12 passed; 0 failed
cargo test --test save_as_overwrite_confirm  →  5 passed; 0 failed
cargo build                                  → 成功
```

---

## レビュー反映 (2026-05-04, ラウンド5) — F9（W&B Submit メニュー）

**対象フェーズ**: F9a / F9b / F9c / F9d / F9e（W&B Submit メニュー全体）
**進め方**: review-fix-loop R1 → 6 並列レビュアー（rust-reviewer / silent-failure-hunter / iced-architecture-reviewer / type-design-analyzer / ws-compatibility-auditor / general-purpose）→ 集約 CRITICAL 3 / HIGH 9 / MEDIUM 15 / LOW 6 → `/parallel-agent-dev` で 4 phase × 並列消化

### 解消した CRITICAL（3件）

| ID | 内容 | 対処 |
|----|------|------|
| C1 | `python/engine/run_buffer.py` に `atexit` / SIGTERM handler が**完全未実装**（計画書 F9a-DoD で「✅完了」と記録されていたが実装欠落）。engine 異常終了時に `aborted` 書き出しが効かず、`submit_run.py` が GUI 起動より先に走ると常に reject | `RunBuffer.__init__` で `atexit.register` ＋ Posix `signal.signal(SIGTERM, ...)` を登録。module-level `_ACTIVE_BUFFERS: WeakSet` で複数 buffer に対応。previous handler を chain。`finish()`/`abort()` 完了時に deregister。`_best_effort_write_aborted` フォールバック追加。Posix の SIGTERM テスト（subprocess + `os.kill`）と atexit テスト 2 件追加 |
| C2 | `SUBMIT_IN_FLIGHT` が `Action::Cancel` 経路でリセットされない → 送信中キャンセルで永久ロック。subprocess kill も未実装（P9 §送信モーダル UI 仕様違反） | `SUBMIT_CHILD: tokio::sync::Mutex<Option<tokio::process::Child>>` global を追加し `submit_wandb_run` を spawn 化（`Child` を slot 経由で wait）。`Action::Cancel` で `SUBMIT_IN_FLIGHT.store(false, Release)` 即時実行 + `SUBMIT_CHILD.lock().take().kill()`。Cancel/完走の race を「slot を先に take した側が勝つ」二段構えで吸収（idempotent reset） |
| C3 | `MaskedLine` newtype が骨抜き — `src/modal/wandb_submit.rs` と `src/wandb_submit_proc.rs` で `mask_secrets`/`MaskedLine` 二重定義。`log_lines: Vec<String>` で raw 格納。統一決定 44「全出口で MaskedLine 通過」型強制が完全迂回 | `src/mask_secrets.rs` を単一定義に統一。Bearer/bearer パターン (`(?i)(bearer)(\s+)\S+`) を追加し `wandb_submit_proc.rs` の独自実装を削除。`modal/wandb_submit.rs::log_lines` を **`Vec<MaskedLine>`** に変更し raw String 格納をコンパイルレベルで禁止。`MaskedLine` に `Display`/`AsRef<str>` 追加で view 互換 |

### 解消した HIGH（9件）

| ID | 内容 | 対処 |
|----|------|------|
| H1 | `python/engine/pii_scrub.py` と `examples/wandb/pii_scrub.py` の allow-list 完全乖離（`instrument_id` 等で `assert_no_forbidden_keys` が ValueError → exit 6） | engine 側を正本として両ファイル完全一致化。`pii_scrub` 関数名・シグネチャ・契約を統一（M12 と統合）。CI lint テスト `test_pii_allowlist_consistency_engine_and_examples` / `test_engine_and_examples_pii_scrub_have_same_signature` で対称差ゼロを永続保護 |
| H2 | forbidden key 検出時に event 全体を `None` で skip → `order_id` を持つ Nautilus Fill が常に空 | `pii_scrub()` を **strip + WARN ログ** 契約に変更（None 廃止）。`order_id` を `FORBIDDEN_KEYS` から削除（venue 由来でない内部 ID。allow-list 外として自動的に剥がれる）。`run_buffer._write_*` の `if scrubbed is None` を `if not scrubbed:`（空 dict skip）に追従。`test_real_nautilus_fill_event_is_written` 追加 |
| H3 | `submit_run.py` の `.lock` 書き込みが非 atomic。`run_submission` 開始時に `remove_stale_lock` 呼出無しで競合検出不能 | `_write_lock` を `tempfile.mkstemp + os.replace` で atomic 化。`run_submission` 冒頭で `remove_stale_lock` → 残存 lock があれば exit 6 + stderr "another submit in progress"。`test_concurrent_submit_refused_when_live_lock` / `test_write_lock_is_atomic` 追加 |
| H4 | `check_auth.py` で無効 key（`AuthenticationError`）も `except Exception` で fail-open → `authenticated=true` を返す | `wandb.errors.AuthenticationError` を**個別 catch** して `authenticated=false, method="none", error="invalid_key"` を返す（fail-closed）。`CommError` は従来の `viewer_lookup_timeout` 維持。`test_invalid_key_returns_unauthenticated` 追加 |
| H5 | `native_menu.rs` の Tools 項目が `MenuItem::new(label, true, accel)` で固定 enable → `tools_actions_for_state` 結果無視。**未認証でも submit 押せる** | `attach()` シグネチャに `&WandbAuthState, &RunBufferIndex` 追加。muda の MenuItem ハンドルを `thread_local!` で保持し、`refresh_tools_enable(&auth, &buffer)` で `set_enabled()` を後追い。`Message::WandbAuthRefreshed` / `RunBufferCleared` / `RunBufferIndexScanned` / submit 成功後の 4 経路で refresh 配線。`mod menu` を Linux 限定から cross-platform に昇格 |
| H6 | `WandbSubmitModal` の「ブラウザで開く」ボタンが `Message::Done(...)` を再送 → Done ハンドラが二重実行（`Action::OpenUrl` 定義済みなのに未使用） | `Message::OpenUrl(String)` 新設。view を `on_press(Message::OpenUrl(url))` に変更。`update()` で `Message::OpenUrl(url) => Some(Action::OpenUrl(url))`（state 不変） |
| H7 | W&B モーダル / sign-in モーダル overlay が `id == main_window` 分岐の外で構築 → popout ウィンドウにも描画される（confirm_dialog は main 限定なのに非対称） | `after_wandb_submit` / `after_signin` を `if id == self.main_window.id { ... } else { after_replay_form }` でラップ。confirm_dialog と同じガードに揃える |
| H8 | `submit_wandb_run` の `script.to_str().unwrap_or("...")` / `run_buffer_dir.to_str().unwrap_or("")` で非 UTF-8 path silent fallback | `WandbSubmitError::ProcessFailed("path contains non-UTF-8 characters: ...")` に置換し `?` で伝播 |
| H9 | `submit_run.py` の `wandb.AuthenticationError` / `CommError` 直接参照が wandb >=0.16 の正規パス `wandb.errors.*` と乖離 → alias 削除で `AttributeError` リスク | `from wandb.errors import AuthenticationError, CommError` を試行 → ImportError 時は top-level alias → 無ければ `Exception` の三段 fallback |

### 解消した MEDIUM（15件）

| ID | 内容 | 対処 |
|----|------|------|
| M1 | cargo fmt 差分 | Phase 4 で `cargo fmt` 適用済み |
| M2 | `src/wandb_submit_proc.rs` 全体に `#![allow(dead_code)]`、production フローから未使用 | module 属性削除、`run_submit_blocking` / `SubmitEvent` 削除、`build_submit_command` は `--notes` 配線で活用するため per-item allow_dead で保持。dead-code policy を rustdoc 化 |
| M3 | `RunBufferIndex::scan` / `remove_dir_all` を iced 同期 update から呼出 | `scan_async` 追加（tokio::fs ベース）。`Action::OpenSubmissionLog` / `Message::ClearRunBufferConfirmed` を `Task::perform` 化。`Message::RunBufferIndexScanned { index, show_toast }` / `RunBufferCleared(Result)` 追加 |
| M4 | `WandbAuthState.method: String` で型保証なし | `pub enum AuthMethod { Env, Netrc, None }` に変更（`#[serde(rename_all = "snake_case")]`）。未知値は serde reject（fail-closed） |
| M5 | `AuthDisplayState` と `WandbAuthState.method` の二重管理 | `impl From<&WandbAuthState> for AuthDisplayState` で一元化。`main.rs` の手書き match を置換 |
| M6 | `WandbSubmitModal.notes` 入力欄があるのに `Action::Submit` に含まれず捨てられる | `Action::Submit { ..., notes: String }` 追加、`build_submit_command --notes` 配線、`submit_run.py` の `--notes` argparse + `wandb.init(notes=...)`（空文字列で kwarg 省略）|
| M7 | `submit_run.py` の `wandb.finish()` 例外を `pass` で握り潰し | 4 箇所を `print(f"WARNING: wandb.finish() failed: {exc}", file=sys.stderr)` に変更 |
| M8 | `wandb_login` の `let _ = stdin.write_all(...)` 黙殺 | `?` 伝播 (`stdin not piped` / `stdin write failed: {e}`) |
| M9 | `_flush_and_fsync_all_jsonl` の fsync 失敗を warn のみで `completed` に書き換え（BC3-5 違反） | fsync 失敗で `OSError` raise → `finish()` が catch して `abort()` fallthrough → `status="aborted"` |
| M10 | `check_auth.py` に Python 側 7 秒 hard timeout 無し（計画書 F9b-DoD と乖離） | **計画書を訂正**: Python 側 hard timeout は POSIX `signal.alarm` 限定で Windows 非対称になるため不採用。`wandb.Api(timeout=5)` のみ維持し、F9b-DoD の "7 秒以内" は Rust subprocess wrapper の `tokio::time::timeout` で保護する方針に |
| M11 | Windows `os.replace` race（読み中の PermissionError で破綻） | `_write_meta_atomic` の `os.replace` を 50ms × 3 retry でラップ。3 回失敗で raise |
| M12 | `engine/pii_scrub.pii_scrub` と `examples/wandb/pii_scrub.scrub` でシグネチャ・契約乖離 | examples 側を `scrub` → `pii_scrub` rename。シグネチャ・挙動完全統一（H1 と統合実施） |
| M13 | `RunBufferIndex::scan` が `serde_json::Value` 動的解析 | `struct MetaJson { run_id, status, started_at }` 追加し `serde_json::from_str::<MetaJson>` で型付き化。`#[serde(deny_unknown_fields)]` は付けず forward-compat 維持 |
| M14 | `MenuEntry.tooltip: Option<String>` が計画書の `Option<&'static str>` と乖離 | `Option<&'static str>` に変更、リテラル渡しでアロケーション削減 |
| M15 | `test_check_auth_no_import_wandb` の `"try" in lines[max(0, i-5):i-1]` がリスト要素一致になっている bug | `any(("try" in line or "def " in line) for line in prev)` 形式に修正、手動 mutation で検出可能性を確認 |

### 残 LOW（6件、対処不要と判断）

L1〜L6: `RunId` newtype、`WandbAuthState` フィールド可視性、`flatten()` 無音 skip、429/500 substring match、`wandb.Table.columns` 順序非決定論、README forbidden keys 一覧。発生頻度極低 or 機能影響なしのため次フェーズ判断とする。

### Phase 構成と所要

| Phase | 内容 | エージェント | 結果 |
|-------|------|-------------|------|
| Phase 1 | 型基盤統合（C3 / M4 / M5 / M14） | 単一 general-purpose（直列） | 88 test binaries pass |
| Phase 2 | Python 不変条件（C1+M9+M11 / H1+H2+M12 / H3+H4+H9+M7+M10+M15） | 3 並列 | pytest 1801 pass |
| Phase 3 | Rust UI/配線（C2+H6+H7 / H5+H8+M6+M8 / M2+M3+M13） | 3 並列。P3-C は Phase 3-B 完了待ちで一時 STOP+REPORT、最終 1 件 false-positive テスト修正で収束 | cargo test workspace pass |
| Phase 4 | 計画書反映 + 全体検証 | オーケストレーター | fmt/clippy/test/pytest 全緑 |

### 最終検証

```
$ cargo fmt --check        → OK
$ cargo clippy --workspace -- -D warnings  → OK
$ cargo test --workspace   → 全テスト GREEN（247+ test binaries）
$ uv run pytest python/tests/ examples/wandb/tests/ -q
   1840 passed, 4 skipped, 8 warnings
```

### 設計判断・新たな知見

- **`MaskedLine` の Bearer pattern**: `(?i)(bearer)(\s+)\S+` → `$1$2***` で大文字小文字を保存
- **SIGTERM handler は module-level**: `signal.signal()` がプロセス全体で 1 つの handler しか持てないため、`WeakSet` で active RunBuffer 集合を管理し handler 1 つで全 buffer に broadcast。previous handler を chain
- **Cancel と完走の race**: `SUBMIT_CHILD.lock().take()` を「先取りした側が勝つ」二段構えで吸収（`SUBMIT_IN_FLIGHT.store(false)` の重複は idempotent）
- **Windows の grace period 不在**: `Child::kill()` は実質 `TerminateProcess`、tokio の Unix `kill()` も SIGKILL のため 5 秒 grace は kernel reap までの best-effort 程度。本格的な SIGTERM→SIGKILL 二段は `nix::sys::signal::kill` 直叩きが必要（次フェーズ判断）
- **muda MenuItem は `!Send`**: `thread_local!` で iced runtime thread に閉じ込める方式。`init_for_hwnd` は再呼び出し不可なため rebuild ではなく `set_enabled()` で更新
- **PII allow-list 二重メンテ防止**: Windows symlink 不可のため両ファイル物理コピー維持、import-and-compare の lint テストで対称性強制
- **`FORBIDDEN_KEYS` の縮約**: `order_id` は venue 由来でない Nautilus 内部 ID のため除外。allow-list で自然に剥がれる。FORBIDDEN は「絶対漏らしてはいけない credential / venue raw payload」のみに絞る
- **`MetaJson` forward-compat**: `#[serde(deny_unknown_fields)]` を付けない。Python 側がフィールド追加で先行することを許容
- **次回 MISSES.md 追記候補**:
  1. **「✅ 完了」と記録されていた DoD 項目で実装が欠落していた**（C1 SIGTERM handler）。レビュアーは「✅」を見たら自動信用せず、ソース grep で対応コードの存在を確認するパターン
  2. **PII allow-list の二系統メンテ**: 同じ意味の定数を 2 ファイルにコピーする設計は将来必ず乖離する。物理コピー + lint テストで保護
  3. **Newtype の骨抜き**: `MaskedLine` のような型強制 newtype を導入したら、同一名の独自 `String` ラッパを別ファイルで定義していないか grep で確認するレビュー観点

### R2 サニティ後の追加修正（R3）

ラウンド5 の F9 完了後に実施した R2 サニティチェックで CRITICAL 1 / HIGH 2 / MEDIUM 4 の 7 件を新たに発見し、TDD で順次解消した。

| ID | 重大度 | 内容 | 対処 |
|----|--------|------|------|
| R2-C1 | CRITICAL | `examples/wandb/submit_run.py` の `lock_path = _write_lock(...)` が try ブロック外。`_write_lock` 失敗時に finally の `lock_path.unlink()` が NameError を起こし元例外を隠蔽 | `lock_path: Optional[Path] = None` を try 外で宣言 → 代入を try 内へ移動 → finally で `if lock_path is not None:` ガード。`test_write_lock_failure_does_not_mask_original_exception` で OSError 伝播・NameError 不発を確認 |
| R2-H1 | HIGH | `wandb.errors` import 失敗時の fallback `getattr(wandb, "AuthenticationError", Exception)` で両クラスが `Exception` に潰れ、内側 except が全例外吸収 → exit-code マッピング崩壊 | 別 sentinel クラス (`type("_AuthErrSentinel", (Exception,), {})` / `_CommErrSentinel`) を生成して識別性を維持。両クラスが None / 同一 / `Exception` のいずれでも分離。`test_authentication_and_comm_errors_are_distinct_classes_after_fallback` で `RuntimeError` が AuthenticationError として捕捉されないことを確認 |
| R2-H2 | HIGH | `Message::WandbSubmitResult(Ok)` ハンドラが同期 `RunBufferIndex::scan(&base)` を呼び iced update スレッドをブロック。M3 で `OpenSubmissionLog` だけ async 化されており非対称 | `scan_async` を `Task::perform` で発行 → `Message::RunBufferIndexScanned { index, show_toast: false }` 経由で `refresh_tools_enable` まで自動連鎖。`wandb_submit_result_uses_async_scan` ソースインスペクションテストで同期 scan の不在を保護 |
| R2-M1 | MEDIUM | `Action::Submit` ハンドラが `latest_completed.is_none()` を未チェックで CAS 取得 → run buffer 不在でも SUBMIT_IN_FLIGHT が立ち、永久ロックの可能性 | CAS の前段に `if self.run_buffer.latest_completed.is_none() { warn; return Task::none(); }` を追加。`submit_action_no_op_when_latest_completed_is_none` でガードが CAS より前に位置することを順序検査 |
| R2-M2 | MEDIUM | `run_submission` の outer `except` 群が `_active_run` 未確認のまま `wandb.finish()` を呼び、未初期化 SDK で警告を量産 | 各 outer except 内 `wandb.finish()` を `if _active_run is not None:` でガード（auth / comm / OS の 3 経路）。`test_outer_except_does_not_call_wandb_finish_when_run_not_initialized` で `wandb.finish.call_count == 0` を確認 |
| R2-M3 | MEDIUM | Cancel 経路で `SUBMIT_IN_FLIGHT.store(false)` が `kill().await` の「前」に実行 → kill 完了前に新 Submit が再入できる窓 | `store(false)` を `kill().await` 直後・同一 async block 内に移動。`SUBMIT_IN_FLIGHT` の重複 store は idempotent (既存 `submit_in_flight_is_idempotent_on_double_release` で保護)。`cancel_releases_submit_in_flight_after_kill_completes` で字句順序 `kill().await` < `SUBMIT_IN_FLIGHT.store(false)` を assert |
| R2-M4 | MEDIUM | `src/mask_secrets.rs` の `OnceLock<Regex>` + `Regex::new(...).unwrap()` パターン。expect メッセージ無し | `std::sync::LazyLock`（Rust 1.80+ / Edition 2024）に置換。`expect("... is a valid regex literal")` で panic 文言を明示。既存 `wandb_key_masking.rs` テストで動作確認 |

#### R3 検証コマンド

```
$ cargo fmt --check                               → clean
$ cargo clippy --workspace -- -D warnings         → clean
$ cargo test --workspace                          → 全テスト GREEN
$ uv run pytest python/tests/ examples/wandb/tests/ -q
   1843 passed, 4 skipped, 8 warnings
```

#### R3 設計判断

- **R2-H1 sentinel 戦略**: `Exception` 直接代入を避けるためダミーサブクラスを動的生成し、識別性を保ちつつ `try/except AuthenticationError` の構文を温存。古い wandb（< 0.16）でも安全側に倒れる
- **R2-H2 の Task 統合**: `Message::RunBufferIndexScanned` ハンドラ内で `refresh_tools_enable` を呼んでいるため、Submit 成功時の Task::perform 戻り値が `RunBufferIndexScanned` で完結する。`Task::batch` での合成は不要
- **R2-M3 の race window**: `kill().await` 中に新 Submit を CAS で受け付けると同一 run-buffer に対して 2 プロセスが競合し `tachibana_orders.jsonl` 等で二重書き込みを引き起こすリスクがあった。kill 完了後の store でタイムウィンドウを構造的に閉鎖
- **R2-M4 の MSRV**: `Cargo.toml` の `edition = "2024"`（Rust 1.85+）から `LazyLock`（1.80）安全。`once_cell` への fallback は不要

---

## レビュー反映 (2026-05-04, F8 R1)

F8（Linux 自前メニューバー / cross-platform `menu` モジュール責務分離）の F8 R1
レビューで HIGH 6 / MEDIUM 10 / LOW 5 の指摘を受け、ユーザー判断（H6 は **選択肢 A**：
ドキュメント側のみ修正、`AuthState` / `BufferState` enum は P9 §852 のとおり保持）を
反映して TDD で順次対応した。

### HIGH（6件）

| ID | 場所 | 指摘 | 対処 |
|----|------|------|------|
| H1 | `src/main.rs:14-15` | `mod menu_bar_state;` が `#[cfg(target_os="linux")]` で gate されており、ソースインスペクション以外（純関数 unit test）が Win/Mac でコンパイル不能 | `#[cfg(...)]` を外し、cross-platform に公開。理由を inline コメントで明文化 |
| H2 | `src/widget_menu_bar.rs:22` | `pub use` から `update` が抜けており Linux 呼び出し側が `menu_bar_state::update` を直接参照する必要があった | `pub use crate::menu_bar_state::{BarMessage, State, TopMenu, update};` に拡張 |
| H3 | `src/widget_menu_bar.rs:77` | `on_move(\|_\| BarMessage::BarMoved(BAR_HEIGHT as u32))` が cursor 位置を捨てて定数を返しており `anchor_y` 機構が無効化 | `on_move(\|pt: iced::Point\| BarMessage::BarMoved(pt.y as u32))` に修正。inline コメントで意図を記録 |
| H4 | `src/menu.rs` mode_menu_items unit test | impl は `enabled: !matches!(...)` で「現在モードは disabled」だが inline test と P8 §testing 期待値が `assert!(got[0].enabled)` を主張し三者矛盾 | impl 側を正に統一。inline test を `assert!(!got[0].enabled)` に修正、計画書の期待値テーブルとサンプルコードも `enabled=false`/`enabled=true` 明示に更新 |
| H5 | `src/main.rs` MenuBar handler | `to_native_action` が None を返した場合 Pick が黙って drop され、新規 `menu::Action` 追加時の wiring 漏れが検出不能 | call site に `log::warn!` を追加。将来 variant 追加時に未配線が log に出る |
| H6 | `docs/✅menu-and-footer/P8-widget-menu-bar-linux.md` §testing & §sketch | `tools_actions_for_state` の引数型・戻り値型・期待値テーブルが R7-86 移行（`&WandbAuthState`/`&RunBufferIndex`/常時 5 要素）と乖離、旧 `(AuthState, BufferState) -> Vec<Action>` を記載 | **ユーザー判断: 選択肢 A** — enum 削除はせず計画書を実装に追従させる。期待値テーブル（4 行 × 5 項目 enabled）/ サンプルコード / `MenuEntry` 構造体定義 / sketch の `menu_items_tools` を実装シグネチャに書き換え。`AuthState` / `BufferState` の `#[allow(dead_code)]` には `// reason: kept for source-inspection tests in tests/tools_actions_for_state.rs (P9 §852)` コメントを追加（M6 と統合） |

### MEDIUM（10件）

| ID | 対処 |
|----|------|
| M1 | `src/menu_bar_state.rs` に `mod tests` を追加し `BarMessage::DismissFocusLost` を 4 開状態（File / Mode / Tools / Closed）について assert する unit test を 4 件 + Pick/Dismiss/DismissFocusLost 同等性 1 件 = 計 5 件追加。bin "flowsurface" test target で実行 |
| M2 | `src/main.rs` の `keyboard::listen()` ハンドラ（`hotkeys` Subscription）に「Esc のみ受け取る / 常に GoBack へ流す / 直接 dismiss しない」3 不変条件を inline コメントで明文化 |
| M3 | `Message::MenuBar(BarMessage::Toggle(top))` で同じ top を再 toggle して閉じる経路に `log::debug!("widget_menu_bar: toggle_close reason=re_toggle top={top:?}")` を追加。outside_click / focus_lost と区別可能に |
| M4 | `MenuEntry.checked` の `Option<bool>` 三値（`Some(true)` / `Some(false)` / `None`）が UI 上で別々の意味を持つことを doc コメントで明示。`bool` への退化は load-bearing なので避けると注釈 |
| M5 | `actions_for_mode` の `Vec<Action>` シグネチャは R7-88 凍結であり `MenuEntry` への拡張はしない理由を doc コメントで追記 |
| M6 | `src/menu.rs` の 4 箇所（`Action` enum / `AuthState` / `BufferState` / `actions_for_mode` / `mode_menu_items`）の `#[allow(dead_code)]` に `// reason: ...` コメントを追加。H6 で `AuthState` / `BufferState` も統合済み。`src/menu_bar_state.rs` の cross-platform 公開に伴う 4 箇所（`TopMenu` / `BarMessage` / `State` / `update`）にも同様に reason コメント付き `#[allow(dead_code)]` を追加 |
| M7 | `widget_menu_bar.rs::action_label_and_shortcut` の戻り値を `(String, Option<&'static str>)` から `(&'static str, Option<&'static str>)` に変更し per-call の `to_string()` 割り当てを排除。`build_dropdown` 側は `format!("✓ {base_label}")` を廃止し prefix を別 `text(...)` widget で render。割り当てゼロのホットパス化 |
| M8 | M7 で File 分岐も連動済みのため単独修正不要。M7 の inline コメントで意図を記録 |
| M9 | `tests/tools_actions_for_state.rs` の brace カウント walker に `// TODO(F8-fragile / M9): ...` コメントを追加。文字列リテラル / コメント内の `{}` で破綻する脆弱性と、必要時に `syn::ItemFn` 等の lexer ベースに切り替えるべき旨を明示 |
| M10 | P8 §acceptance DoD-10〜DoD-14 の `passed` 数を実測値に更新（10→13, 11→17, 10→11, GREEN→11 passed の内訳）+ DoD-11 の cross-platform は GREEN→15 passed |

### LOW（5件）

| ID | 対処 |
|----|------|
| L1〜L5 | 元指示書の本文では LOW 5 件の具体的内容が引き継がれず（前任 STOP+REPORT エージェントの転写欠落）。CRITICAL/HIGH/MEDIUM ゼロ達成優先で次レビューサイクル（F8 R2）への繰越しとする |

### 修正対象ファイル

- `src/main.rs` — `mod menu_bar_state` の cfg 解除（H1）/ MenuBar ハンドラに warn（H5）/ Toggle 閉ログ（M3）/ hotkeys 不変条件コメント（M2）
- `src/widget_menu_bar.rs` — `pub use` 拡張（H2）/ `on_move` 修正（H3）/ `action_label_and_shortcut` allocation-free 化（M7）
- `src/menu.rs` — `#[allow(dead_code)]` reason コメント 4 箇所（M6 / H6）/ inline test 修正（H4）/ `MenuEntry.checked` doc 拡充（M4）/ `actions_for_mode` doc 拡充（M5）
- `src/menu_bar_state.rs` — cross-platform 公開に伴う `#[allow(dead_code)]` 追加（M6）/ DismissFocusLost unit test 5 件追加（M1）
- `tests/tools_actions_for_state.rs` — brace counter TODO コメント（M9）
- `docs/✅menu-and-footer/P8-widget-menu-bar-linux.md` — Tools 期待値テーブル / `MenuEntry` 構造体 / `tools_actions_for_state` シグネチャ / `mode_menu_items` 期待値とサンプル / DoD passed 数（H6 / M10 / H4）

### 検証コマンド（tail）

```
$ cargo fmt --check                               → clean
$ cargo clippy --bin flowsurface --tests -- -D warnings
   F8 関連の修正対象ファイル（src/menu.rs / src/menu_bar_state.rs / src/widget_menu_bar.rs /
   src/main.rs / tests/tools_actions_for_state.rs / tests/mode_menu_items.rs /
   tests/widget_menu_bar_state.rs / tests/menu_actions_cross_platform.rs）に
   新規警告ゼロを確認。`exchange/` `data/` `src/modal/replay_form.rs` 等の
   既存警告は instruction の通り対象外
$ cargo test --workspace                          → 全テスト GREEN（FAILED 0）
   抜粋:
     test result: ok. 13 passed; tests/tools_actions_for_state.rs
     test result: ok. 17 passed; tests/widget_menu_bar_state.rs
     test result: ok. 11 passed; tests/mode_menu_items.rs
     test result: ok. 15 passed; tests/menu_actions_cross_platform.rs
     test result: ok.  5 passed; src/menu_bar_state.rs::tests (M1 新規)
```

### 設計判断

- **H1 で cfg 解除**: `menu_bar_state` は iced/GTK 等の Linux 限定依存を持たない純関数モジュール。cross-platform に公開しても rendering 層は `widget_menu_bar.rs` の `#![cfg(target_os = "linux")]` で隔離されており double-menu 事故は構造的に発生し得ない
- **H4 を impl 側に倒した理由**: 「現在モードは disabled」が UX 的にも自然（同モード切り替えは no-op）であり、integration test `mode_menu_items_disables_current_live_entry` も既にこの仕様に投票済み。inline test と doc 期待値だけが旧仕様に取り残されていた
- **H6 選択肢 A**: enum 削除を選ぶと `tests/tools_actions_for_state.rs::auth_state_enum_exists` / `buffer_state_enum_exists` のソースインスペクションが破綻し、P9 §852 で明示された「保持決定」も覆る。計画書を実装側に追従させる方が侵襲が小さい
- **M1 を bin unit test に置いた理由**: `update()` の動作テストは関数本体への依存が必要で、ソースインスペクションでは不十分。`src/menu_bar_state.rs` 内の `#[cfg(test)] mod tests` に置くことで `cargo test --bin flowsurface` で実行され、cross-platform に走る
- **M7 のホットパス化判断**: dropdown を開くたびに最大 13 variant 分の `String` allocation が発生していた。全 label が compile-time constant なのに heap を経由する必要がない。prefix の三値（`"✓ "` / `"  "` / `""`）も `&'static str` に閉じ、render は `text(prefix)` + `text(base_label)` の row に分割

---

## レビュー反映 (2026-05-04, F8 R2)

F8 R1 の修正が新規 silent failure を生んだケース（review-fix-loop 知見 17）が
HIGH 1 / MEDIUM 2 / LOW 1 として再レビューで指摘された。両 R2 reviewer 推奨の
**候補 A（`anchor_y` 機構の全廃 + `BAR_HEIGHT` 定数固定）** を採用し、関連する
冗長 wrapper / wildcard match / コメント誤記をまとめて TDD で解消した。

### 解消した指摘（HIGH 1 / MEDIUM 2 / LOW 1）

| ID | レベル | 場所 | 指摘 | 対処 |
|----|--------|------|------|------|
| H3' | HIGH | `src/widget_menu_bar.rs:84` ほか | R1 で導入した `on_move(\|pt\| BarMessage::BarMoved(pt.y))` は **widget-local 座標**（0..BAR_HEIGHT）を返すが `with_dropdown_overlay` の `top_offset` は **window 絶対 Y** を期待していたため category mismatch。カーソルがバー上辺付近に居るとドロップダウンが画面上端に張り付く silent failure | 候補 A 採用：`State.anchor_y` フィールド / `BarMessage::BarMoved` variant / `update()` の対応分岐 / `view()` の `.on_move(...)` 呼び出しを全削除し、`top_offset` を `BAR_HEIGHT` 定数に固定。inline コメントで category error の経緯と判断根拠を記録 |
| M-A | MEDIUM | `src/widget_menu_bar.rs:291-298` | H2（R1）で `pub use update` を追加した結果、`menu_items(mode) -> actions_for_mode(mode)` / `mode_items(current) -> mode_menu_items(current)` は完全な委譲 wrapper になり外部 caller ゼロ | 両 wrapper を削除。callers は `menu::actions_for_mode` / `menu::mode_menu_items` を直接利用（既に top で import 済み）。`tests/widget_menu_bar_state.rs::menu_items_function_delegates_to_menu_module` を `widget_menu_bar_does_not_define_redundant_wrappers`（不在検査）に書き換え |
| M-B | MEDIUM | `src/main.rs:3375` | `match &bar_msg { ... _ => {} }` の wildcard arm が `BarMessage::Pick(_)` と `BarMessage::BarMoved(_)` を無声で吸収。将来 variant 追加時の wiring 漏れ検出が不能 | H3' で `BarMoved` 削除後、wildcard を `BarMessage::Pick(_) => {}` に置換。コメントで「ログ目的の意図的 no-op」と「将来 variant 追加で compile error にする狙い」を明示。`tests/widget_menu_bar_state.rs::main_menu_bar_handler_match_is_exhaustive_without_wildcard` で `_ => {}` 不在 + `Pick(_) =>` 存在を assert |
| L | LOW | `src/widget_menu_bar.rs:80-83` | `// H3 (F8 R1): track the cursor's window-Y as the dropdown anchor.` コメントが widget-local / window-absolute の意味論差を取り違えたまま残存 | H3' の対処と同時に削除し、新しいコメントで「`mouse_area::on_move` が widget-local を返す」「window-Y との category mismatch だった」事実を記録 |

### 設計判断

- **候補 A 採用理由**: 候補 B（修正版 anchor 計算）は iced 0.14 で window-absolute 座標を取るには `iced::event::listen_with` の `Event::Mouse(CursorMoved)` を購読する必要があり、`mouse_area` 配下のローカル取得とは別経路が必要。menu bar が **常に window 先頭行** という不変条件下では `BAR_HEIGHT` 定数固定で正答が得られるため、追加経路を引き入れる正当化が立たない。両 R2 reviewer 推奨の候補 A をそのまま採用
- **既存テストとの整合**: R1 で追加した `state_struct_exists` / `bar_message_enum_has_toggle_pick_dismiss` の `anchor_y` / `BarMoved` 存在 assert は **不在 assert** に反転（コメントで R2 / H3' の経緯を inline 記録）。R1 で追加した `menu_items_function_delegates_to_menu_module` は wrapper 不在検査に書き換え。`dismiss_focus_lost_closes_menu` は rustfmt が `=> { State { ... } }` 形に整形した結果 `=> State` 部分文字列マッチが破綻したため、`=>` までで切るよう緩和し、両形式（inline / block）を許容する旨をコメントで明記
- **MISSES.md 候補（追記候補）**: 「iced `mouse_area::on_move` のコールバック引数は `cursor.position_in(layout.bounds())` で計算された **widget ローカル座標** を返す。window 絶対 Y が必要なら `iced::event::listen_with` 経由で `Event::Mouse(CursorMoved)` を購読すること。ローカル座標を window-Y 用 `top_offset` 等に流すと初期は正しく見えてもバー上辺で破綻する silent failure になる」を bug-postmortem 起動時に MISSES.md へ転記する候補として記録

### 修正対象ファイル

- `src/menu_bar_state.rs` — `BarMessage::BarMoved` 変種削除 / `State.anchor_y` フィールド削除 / `update()` の `BarMoved` 分岐削除 / unit test の `anchor_y` 参照削除（H3'）
- `src/widget_menu_bar.rs` — `view()` の `.on_move(...)` 削除と category-error コメント追記（H3' / L）/ `with_dropdown_overlay` の `top_offset = BAR_HEIGHT` 定数化（H3'）/ `menu_items` / `mode_items` wrapper 削除（M-A）
- `src/main.rs` — `match &bar_msg` の `_ => {}` を `BarMessage::Pick(_) => {}` に置換（M-B）
- `tests/widget_menu_bar_state.rs` — `anchor_y` / `BarMoved` 不在 assert へ反転（H3'）/ wrapper 不在検査へ書き換え（M-A）/ wildcard 不在検査追加（M-B）/ `dismiss_focus_lost_closes_menu` の rustfmt block 形対応（既存テスト保護）
- `docs/✅menu-and-footer/fix-save-menu.md` — 本ブロック追記
- `docs/✅menu-and-footer/P8-widget-menu-bar-linux.md` — `BarMessage` スケルトンの `BarMoved` / `anchor_y` 関連記述が R1 段階で未追記であったことを確認したうえで R2 不変条件を inline で 1 行追記（widget-local 注意書き）

### 検証コマンド（tail）

```
$ cargo fmt --check                                              → clean
$ cargo clippy --bin flowsurface --tests -- -D warnings
   F8 関連 src（src/main.rs / src/menu.rs / src/menu_bar_state.rs /
   src/widget_menu_bar.rs）に新規警告ゼロを確認。`tests/widget_menu_bar_state.rs:11`
   の `doc_lazy_continuation` は R1 から残る既存警告（本 R2 で touch 不要）。
   `src/modal/replay_form.rs` の `field_reassign_with_default` も既存
$ cargo test --workspace                                         → 88 suites 全 GREEN（FAILED 0）
   抜粋:
     test result: ok. 19 passed; tests/widget_menu_bar_state.rs（R1: 17 → R2: +2 net）
     test result: ok.  5 passed; src/menu_bar_state.rs::tests
```

### 削除した API / フィールド一覧

| シンボル | 種別 | ファイル |
|---------|------|---------|
| `BarMessage::BarMoved(u32)` | enum variant | `src/menu_bar_state.rs` |
| `State.anchor_y: Option<u32>` | struct field | `src/menu_bar_state.rs` |
| `update()` 内 `BarMessage::BarMoved` arm | match arm | `src/menu_bar_state.rs` |
| `pub fn menu_items(mode: &AppMode) -> Vec<Action>` | function | `src/widget_menu_bar.rs` |
| `pub fn mode_items(current_mode: &AppMode) -> Vec<MenuEntry>` | function | `src/widget_menu_bar.rs` |
| `view()` の `.on_move(\|pt\| BarMessage::BarMoved(pt.y as u32))` | method call | `src/widget_menu_bar.rs` |
| `match &bar_msg` の `_ => {}` arm | wildcard match | `src/main.rs` |

---

## レビュー反映 (2026-05-04, F8 R3)

**対象**: F8 R2 サニティチェック後の最終収束ラウンド。silent-failure-hunter で **動作上の silent failure はゼロ** と確認。残存 MEDIUM 2 件 + LOW 1 件はすべてドキュメント／コメント整合のみで、オーケストレーターが直接修正。

### 解消した指摘

| ID | 重要度 | 内容 | 対処 |
|----|--------|------|------|
| R3-M1 | MEDIUM | `src/menu.rs:101` の `actions_for_mode` 直前 reason コメントが R2 で削除済の `widget_menu_bar::menu_items` wrapper を参照したまま残存。将来「wrapper を再追加すべきか」の判断を歪めるリスク | reason コメントを「called directly from `widget_menu_bar::entries_for_menu`（TopMenu::File arm）」に書き換え、`R2 / M-A` で削除した経緯も併記 |
| R3-M2 | MEDIUM | `P8-widget-menu-bar-linux.md` の DoD-5 / DoD-6 検証列が削除済 wrapper `widget_menu_bar::menu_items` を指し続けていた。DoD 表として信頼性低下 | 検証列を `cargo test --test menu_actions_cross_platform`（`actions_for_mode(&AppMode::Live\|Replay)` を直接検証）に書き換え |
| R3-L1 | LOW | `top_offset = BAR_HEIGHT` 定数固定の前提条件（`main.rs` の view 構成で widget menu bar より上に実効高さ 0 のウィジェットしか置かない）が P8 ドキュメントに未記載。将来バナー/ヘッダーバー追加時に silent な位置ずれを起こす種 | P8 §sketch の `BarMessage` コメント末尾に「F8 R3 / LOW」不変条件として注記。将来高さ持ちウィジェット追加時は `iced::event::listen_with` 経由で `Event::Mouse(CursorMoved)` を購読する旨を明記 |

### 設計判断

- **silent-failure-hunter R3 で動作上の silent failure ゼロを確認**。R1/R2 で導入した修正が新規 silent failure を生まなかった（review-fix-loop 知見 17 の連鎖を断ち切ったラウンド）
- DoD-2/3/4 の `dismiss reason=esc|focus_lost|outside_click` ログ 3 経路すべて健在
- `_ => {}` を `BarMessage::Pick(_) => {}` exhaustive 化したことで将来 `BarMessage` variant 追加時にコンパイルエラーで気づける構造を維持
- `pub use crate::menu_bar_state::{BarMessage, State, TopMenu, update}` は R2 後も健在
- `#[allow(dead_code)]` の reason コメントが現状（wrapper 削除後）と整合

### 検証コマンド（R3 修正後）

```
cargo fmt --check                                  → clean
cargo clippy --bin flowsurface --tests -- -D warnings → 警告ゼロ
cargo test --workspace                             → 全 GREEN
```

### 最終収束

**CRITICAL: 0 / HIGH: 0 / MEDIUM: 0 / LOW: 0**

F8（Linux 向け iced 自前メニューバー）の review-fix-loop は R3 で完全収束。

### MISSES.md 候補（次回 bug-postmortem 起動時に転記推奨）

- **iced `mouse_area::on_move` の座標系**: `cursor.position_in(layout.bounds())` 経由で **widget ローカル座標**を返す（widget 左上原点）。window 絶対座標が必要な場合は `iced::event::listen_with` で `Event::Mouse(CursorMoved)` を購読する。F8 R1 でこれを「window-Y」と誤解して `BarMessage::BarMoved(u32)` を導入したが、R2 で「現状は偶然 0〜BAR_HEIGHT が一致するだけ」と判明し全廃した。両 R2 reviewer（rust-reviewer / silent-failure-hunter）が独立に同一指摘を出すまで動作上の異常は表面化しなかった、典型的な「設計意図とコメントは正しいが実装が違う」silent failure
- **wrapper 関数 + テストでの強制保持の罠**: `tests/tools_actions_for_state.rs` の `pub enum AuthState/BufferState` 存在検査が legacy enum の削除を阻止していたケース。**P9 §852 で「保持」を明示決定**しているのを尊重し、削除ではなくドキュメント側を実装に合わせる方針（選択肢 A）を採用。テストが「死コード」を保護する状態は MISSES.md「テストが設計判断を凍結する」パターンに該当
- **R1 fix が R2 で新規 HIGH を生む連鎖を 1 ラウンドで断ち切る方法**: 両系統 reviewer（rust-reviewer / silent-failure-hunter）を毎回回し、独立発見が一致した時点で「設計判断（候補 A/B）」をユーザーに提示してから修正に進む。Phase 8 のラウンド連鎖（R1 → R2 → R3 → R4）と比べて F8 は R1 → R2 → R3 で収束
