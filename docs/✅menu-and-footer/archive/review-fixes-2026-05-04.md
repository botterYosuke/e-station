# review-fix-loop ログ — docs/✅menu-and-footer/ R1〜

対象: fix-save-menu.md / P5-scenario-in-strategy.md / P7-mode-switch-menu.md / P8-widget-menu-bar-linux.md

## ラウンド 1（2026-05-04）

### 統一決定

> **番号空間注記**: R1 の決定は `(R1-N)`、R2 の決定は `(R2-N)` で参照する。
> 旧表記の素の `(N)` は R3-1 で衝突解消のため廃止。

- (R1-1) ファイル間リンクは prefix 付きフルファイル名・同一ディレクトリは `./` 相対
- (R1-2) spec.md は空ファイル → アンカー参照禁止、代替先は native-menu-bar-impl.md / footer-impl.md
- (R1-3) fix-save-menu.md 冒頭凡例で F*/P*/Phase 8.x の別系列を明示
- (R1-4) メニューラベル統一: `開く…（Open）` / `上書き保存（Save）` / `名前を付けて保存…（Save As）` / `Replay を開始…` / `Replay を停止`
- (R1-5) P5 用語表: Strategy class / SCENARIO 辞書（再現条件メタデータ）/ scenario-bearing file
- (R1-6) SCENARIO 抽出は `ast.parse + ast.literal_eval`（read-only）、importlib は Run 押下時のみ
- (R1-7) Python 書き戻しは `tempfile + os.replace()` atomic、`.bak.<UTC秒>` 世代付き
- (R1-8) SaveStrategyScenario の path ガード: 拡張子 `.py` / 直前 Load の path 一致 / 永続状態ファイルディレクトリ書き込み禁止
- (R1-9) `static APP_MODE / CURRENT_PATH: Mutex<Option<...>>` を src/main.rs に。poison は into_inner
- (R1-10) dirty 判定: `Flowsurface.last_saved_bytes: Option<Vec<u8>>` + BTreeMap canonical シリアライズ
- (R1-11) P7 4 軸 matrix（現モード×切替先×in-flight order×EngineBusy）+ engine-session.json は restart で Drop→再生成
- (R1-12) アクセラレータは muda 正規、iced kbd は `cfg(target_os="linux")` 限定
- (R1-13) iced_aw 採用判定は `cargo tree | grep iced_aw` 結果を P8 に貼って確定
- (R1-14) 各 F/P タスク DoD に「テストファイル名 / 期待ログ / 観測コマンド」必須
- (R1-15) P8 に Wayland/X11 スモーク + Esc/focus-lost 契約 + ライブラリ version pin
- (R1-16) P5 F6a DoD に `/ipc-schema-check` 実行必須
- (R1-17) 連打防止に `static MODE_SWITCHING: AtomicBool` + メニュー disable
- (R1-18) スコープ外マーカー `✕` → `-（対象外）`
- (R1-19) ASCII art / tree 図に ```text 言語タグ
- (R1-20) 参照見出しに `<a id="..."></a>` 明示アンカー

> **追加注記（log agent タスク）**: README.md への新規 4 ファイル導線追加は本ラウンド log agent
> が担当。R1 統一決定の番号空間（R1-1〜R1-20）からは外し、Findings A-9 / D-3 で追跡する。

### Findings 一覧

| ID | 観点 | 重大度 | 対象ファイル:行 | 修正概要 |
|---|---|---|---|---|
| A-1 | A | HIGH | P7:7,42,91 / P8:102 | リンク先 prefix 欠落を `P7-` `P8-` 付きに修正 |
| A-2 | A | HIGH | P7:32-34 | replay→live の saved-state.json 取り扱いを CLAUDE.md D9 と整合する文に修正 |
| A-3 | A | MEDIUM | fix-save:30-37 | current_path と saved-state.json の保存先決定ロジックを明示 |
| A-4 | A | MEDIUM | fix-save:130 / P5:146 | Save As ラベル共通だがモード依存モデルの挙動表追加 |
| A-5 | A | MEDIUM | P5 全般 | 用語表（Strategy/SCENARIO/scenario file）追加 |
| A-6 | A | MEDIUM | fix-save:214-223 等 | F/P/Phase 8.x 番号体系凡例を冒頭に追加 |
| A-7 | A | MEDIUM | fix-save:268,307-311 | last_saved_bytes 更新タイミング明記 |
| A-8 | A | MEDIUM | 全般 | engine-session.json / WAL lifecycle を P7 / fix-save に追加 |
| A-9 | A | LOW | README.md | 新規 4 ファイル導線追加（本タスク） |
| A-10 | A | LOW | P8:74 | iced_aw `cargo tree` 結果を貼って確定 |
| B-1 | B | HIGH | fix-save:247-254 | F2 DoD にテスト名・観測ログ追加 |
| B-2 | B | HIGH | fix-save:266-271 | F4 last_saved_bytes と dirty_detection.rs DoD 化 |
| B-3 | B | HIGH | P7:64-67 | APP_MODE Mutex 化の grep / poisoning / regression test 追加 |
| B-4 | B | HIGH | P7:30-37 | replay→live engine 停止コマンド・完了イベント・タイムアウト定義 |
| B-5 | B | MEDIUM | P5:172-177 | rollback fixture（int instrument）と byte-diff assert 明記 |
| B-6 | B | MEDIUM | P5:82-86 | SCHEMA_MINOR 同期 / `/ipc-schema-check` DoD |
| B-7 | B | MEDIUM | P8:64-74 | iced_aw 確定 or overlay+mouse_area skeleton 提示 |
| B-8 | B | MEDIUM | P8 全般 | Linux ユニットテスト / cross-platform actions_for_mode |
| B-9 | B | MEDIUM | fix-save:295-299 | CURRENT_PATH static 名・型・lock 戦略確定 |
| B-10 | B | LOW | P5:186-196 | サンプル戦略更新の影響 grep 結果記載タスク追加 |
| B-11 | B | LOW | fix-save:240-246 | F1 DoD の節ラベル付け（保持/統合/削除） |
| C-1 | C | HIGH | P5:138 / fix-save:142-145 | atomic write + .bak 世代付き |
| C-2 | C | HIGH | P5:75-77,89-93 | ast.literal_eval 経路へ切替（importlib は Run 時のみ） |
| C-3 | C | HIGH | P5:155-159 / fix-save:131-145 | path ガード（.py / dir allow-list / 直前 Load 一致） |
| C-4 | C | HIGH | P7:32-34 / Q3 | 4 軸 matrix + engine-session.json lifecycle |
| C-5 | C | HIGH | P7:64-66 | MODE_SWITCHING AtomicBool 再入禁止 |
| C-6 | C | MEDIUM | fix-save:266-271 / P7:24,Q3 | 保存失敗・cancel 時 rollback とエラー dialog |
| C-7 | C | MEDIUM | fix-save:46-48,252 / P8 | アクセラレータ単一経路（muda 正規 + Linux 限定 iced kbd） |
| C-8 | C | MEDIUM | P8:38-52,Q1 | Wayland/X11 スモーク + focus + version pin |
| C-9 | C | MEDIUM | fix-save:307-311 | BTreeMap canonical シリアライズ不変条件 |
| C-10 | C | LOW | P5:92 | __pycache__ 抑制が process-wide の旨を明記 |
| D-1 | D | HIGH | fix-save:6,348 | spec.md# 死リンクを native-menu-bar-impl.md / footer-impl.md に貼り直し |
| D-2 | D | HIGH | P7/P8 | リンク prefix 修正（A-1 と重複対応） |
| D-3 | D | HIGH | README.md | 新規 4 ファイル導線追加（本タスク） |
| D-4 | D | MEDIUM | 全般 | Phase 番号体系凡例（A-6 と重複対応） |
| D-5 | D | MEDIUM | 各 P*:6 | 明示アンカー `<a id="..."></a>` 追加 |
| D-6 | D | MEDIUM | 全般 | メニュー項目名「日本語（英名）」併記統一 |
| D-7 | D | MEDIUM | P7:6 / P8:6 | `../✅menu-and-footer/` を `./` に統一 |
| D-8 | D | LOW | P7 / P8 | 受け入れ基準・テスト方針節を追加 |
| D-9 | D | LOW | P8 タイトル等 | widget menu bar 表記初出に英名併記 |
| D-10 | D | LOW | P7:15-19 / P8:30-35 等 | コードブロック言語タグ `text` 付与 |
| D-11 | D | LOW | 全般 | `✕` を `-（対象外）` に置換 |

> **R1 表の重複対応に関する補注**:
> D-2 は A-1 と同一修正でクローズ / D-3 は README.md 導線追加タスク（log agent 担当）でクローズ /
> D-4 は A-6 と同一修正 / D-7 は (R1-1) と同一修正。

---

## ラウンド 2（2026-05-04）

### 追加統一決定（R2-21〜R2-39）

- (R2-21) engine 再起動方針は P7 が正（fix-save-menu.md は P7 整合に書き換え）
- (R2-22) 保存エラー分類: Cancelled / IoError(kind) / PathGuardViolation 3 種
- (R2-23) last_saved_bytes=None は clean（初期状態 confirm 出さない）
- (R2-24) BTreeMap 決定論シリアライズの regression guard（100 回 serialize 同一 bytes）
- (R2-25) P5 起点課題は F6（F5 は P4）。F5 言及を F6 に置換
- (R2-26) atomic write 順序: tempfile→backup→write→fsync→os.replace + cleanup 責任
- (R2-27) 保存エラーコード列挙: permission_denied / parent_missing / disk_full / path_guard_violation / rename_failed / tempfile_failed
- (R2-28) path ガード Save without prior Load 分岐: Load 履歴 None なら current_path None + Save As flag のみ許可
- (R2-29) ast.literal_eval エラー文言を具体化（dict unpacking/comprehension/関数呼び出しを禁止）
- (R2-30) SCHEMA_MAJOR/MINOR 区別 assert（major 一致 / minor 不一致でも接続成立）
- (R2-31) ast.literal_eval read-only regression test（副作用 .py で /tmp/SIDE_EFFECT が作られないこと）
- (R2-32) `.bak.<UTC秒>` GC 方針（手動 or 起動時 N=20 世代）
- (R2-33) ModeSwitchGuard RAII（Drop で AtomicBool 解放、panic 経由 stuck 防止）
- (R2-34) WAL in-flight 検知: tail 逆順スキャン×order_id 最新 status
- (R2-35) StopReplay 5s timeout fallback: SIGKILL or ForceStopReplay
- (R2-36) 2 並行 SwitchMode regression test
- (R2-37) WAL touch 表現: 書き換えない / read-only 参照許容
- (R2-38) P7 日本語アンカーを ASCII id に
- (R2-39) P8 iced_aw 案 b の純関数 update 切り出し + tests/widget_menu_bar_state.rs

### Findings 一覧（R2）

| ID | 観点 | 重大度 | 対象ファイル:行 | 修正概要 | 注記 |
|---|---|---|---|---|---|
| AD-1 | A | HIGH | fix-save-menu.md / P7 | engine 再起動方針の文言不一致を P7 起点に統一 | (R2-21) |
| AD-2 | A | HIGH | fix-save-menu.md F4 | last_saved_bytes=None=clean を明文化 | (R2-23) |
| AD-3 | A | MEDIUM | fix-save-menu.md | 保存エラー分類 3 種を列挙 | (R2-22) |
| AD-4 | A | MEDIUM | P5 全般 | F5 → F6 言及置換 | (R2-25) |
| AD-5 | A | MEDIUM | fix-save-menu.md | atomic write 順序を 5 ステップで明記 | (R2-26) |
| AD-6 | A | LOW | P7 | 日本語アンカーを ASCII id 化 | (R2-38) |
| AD-7 | A | LOW | fix-save-menu.md | Save without prior Load 分岐の説明追加 | (R2-28) |
| BC-1 | B | HIGH | fix-save-menu.md F4 | BTreeMap 決定論シリアライズの 100 回 regression test | (R2-24) |
| BC-2 | B | HIGH | P7 | ModeSwitchGuard RAII 実装と panic 経路テスト | (R2-33) |
| BC-3 | B | HIGH | P7 | StopReplay 5s timeout fallback の経路定義 | (R2-35) |
| BC-4 | B | HIGH | P7 | 2 並行 SwitchMode regression test | (R2-36) |
| BC-5 | B | HIGH | P5 | ast.literal_eval read-only 副作用 regression test | (R2-31) |
| BC-6 | B | MEDIUM | P5 | ast エラー文言の具体化（dict unpacking 等） | (R2-29) |
| BC-7 | B | MEDIUM | engine-client / schemas.py | SCHEMA_MAJOR/MINOR 区別 assert 追加 | (R2-30) |
| BC-8 | B | MEDIUM | fix-save-menu.md | 保存エラーコード列挙（6 種） | (R2-27) |
| BC-9 | C | MEDIUM | fix-save-menu.md | `.bak.<UTC秒>` GC 方針確定 | (R2-32) |
| BC-10 | C | MEDIUM | P7 | WAL in-flight 検知 tail 逆順スキャン手順 | (R2-34) |
| BC-11 | C | LOW | P7 | WAL touch 表現を read-only 参照許容に | (R2-37) |
| BC-12 | C | LOW | P8 | iced_aw 案 b の純関数 update + widget_menu_bar_state.rs | (R2-39) |

## ラウンド 3（2026-05-04）

### 統一決定
- (R3-1) review-fixes ログの統一決定番号を `(R1-N)` / `(R2-N)` の名前空間に分離（番号衝突解消）
- (R3-2) README の機能サマリ表に live `上書き保存（Save）` と replay `Replay を停止` を追記し 5 項目併記化

### Findings
| ID | 観点 | 重大度 | 対象 | 修正概要 | 対応決定 |
|---|---|---|---|---|---|
| R3-1 | AD | MEDIUM | review-fixes-2026-05-04.md | 統一決定番号 (21) の衝突を `(R1-N)` / `(R2-N)` 名前空間で解消 | (R3-1) |
| R3-2 | AD | MEDIUM | README.md | 機能サマリ表 5 項目併記化 | (R3-2) |

## ラウンド 4（2026-05-04・収束サニティ）

### Findings
| ID | 観点 | 重大度 | 対象 | 修正概要 |
|---|---|---|---|---|
| R4-1 | D | MEDIUM | fix-save-menu.md:225 | 廃案計画 `P5-replay-persistence-layer.md` への dead link 削除（リンクなし文言のみ） |
| R4-2 | D | LOW | fix-save-menu.md:89,104,229 | `native-menu-bar-impl.md#L169-L180` 等 GitHub 限定行アンカー（**繰越**: 機能上の影響なし、別 PR で section id 化） |

### 収束判定
HIGH 0 / MEDIUM 0（残 LOW 1 件は次フェーズに繰越）。**=== 収束 ===**

---

## ラウンド R1-P9（2026-05-04, 単独追加）

対象: `P9-wandb-submit-menu.md`（W&B Submit メニュー・wandb run lifecycle・PII scrubber）

### 統一決定（40〜57）

- (40) F* は ✅menu-and-footer/ 配下計画書共通の実装フェーズ番号空間（fix-save 凡例で拡張済み）
- (41) P9 のメニューラベルは fix-save §メニューラベル表記の統一 を参照
- (42) 表記凡例: W&B = 製品名（UI/本文）、wandb = Python パッケージ/CLI、Weights & Biases = フルネーム（初出のみ）
- (43) wandb login の API key は stdin pipe 経由（コマンドライン引数禁止）
- (44) secret マスキングは mask_secrets() に集約 + MaskedLine newtype で型強制 + WANDB_SILENT=true + property-based test
- (45) wandb run lifecycle: try/finally: wandb.finish + SIGTERM handler + 5s grace period + ModeSwitchGuard 連携
- (46) 再入禁止: submit_in_flight: Mutex<Option<SubmitInFlight>> + アクセラレータ disable
- (47) PII allow-list scrubber: pii_scrub.py 必須経由、許可フィールド (symbol/side/qty/price/ts/pnl)
- (48) wandb エラー分類: auth/rate_limit/network/server_5xx/partial
- (49) check_auth.py 7 秒ハード timeout、wandb.Api(timeout=5)、fallback
- (50) meta.json aborted 正規化（atexit/signal + GUI 起動時スキャナ）
- (51) run-buffer LRU race を .lock で防止
- (52) CI に examples-wandb job 追加
- (53) モーダル UI に API key 文字列を 1 文字も出さない（3 値表示のみ）
- (54) examples/wandb/tests/ も import wandb 許可（SKILL.md 側追記は別途）
- (55) アクセラレータ二重発火回避は F2 (fix-save-menu.md §F2/Q6) ポリシーに従う
- (56) webbrowser 依存の矛盾解消（Cargo.toml 追加 or subprocess 代替）
- (57) Tags/Notes は 必須/任意 列で表現（`-（対象外）` と区別）

### Findings 一覧（R1-P9）

> **観点列の注記（R3-75 で追記）**: 本 R1-P9 表では、R1 / R2 で用いた 4 観点（A/B/C/D）のうち
> **観点 A（仕様整合）と観点 D（ドキュメント・導線）を A9 系（A9-N）に合算**して扱っている。
> B 系は B9-N に対応。観点列は元の A/B 表記を残しているが、A9-N 行は A・D の両側面を
> 含む統合行として読むこと。

| ID | 観点 | 重大度 | 対象ファイル:行 | 修正概要 |
|---|---|---|---|---|
| A9-1 | A | HIGH | P9-wandb-submit-menu.md（wandb login 経路） | API key を CLI 引数で渡さず stdin pipe 経由に変更（プロセスリスト漏洩防止）— (43) |
| A9-2 | A | MEDIUM | P9 / SKILL.md 整合 | examples/wandb/tests/ も `import wandb` 許可域に含める旨を P9 に明記 — (54) |
| A9-3 | A | MEDIUM | P9 メニューラベル節 | P9 ラベル定義は fix-save §メニューラベル表記の統一 を単一の正典として参照 — (41) |
| A9-4 | A | MEDIUM | P9 表記凡例 | W&B / wandb / Weights & Biases の使い分け凡例を冒頭に追加 — (42) |
| A9-5 | A | MEDIUM | P9 / fix-save 番号空間 | F* 番号空間が ✅menu-and-footer/ 共通であることを冒頭で再宣言 — (40) |
| A9-6 | A | MEDIUM | P9 webbrowser 依存節 | Cargo.toml 追加 or subprocess 代替で経路を一本化 — (56) |
| A9-7 | A | LOW | P9 Tags/Notes 表 | 必須/任意 列を分け `-（対象外）` と区別 — (57) |
| A9-8 | A | LOW | P9 lifecycle 節 | ModeSwitchGuard との連携順序図を追加 — (45) |
| A9-9 | A | LOW | P9 UI 節 | モーダルが API key 文字列を 1 文字も表示しない明文化 — (53) |
| A9-10 | A | LOW | P9 全般 | wandb エラー分類 5 種を列挙 — (48) |
| A9-11 | A | LOW | P9 auth 節 | check_auth.py の timeout 値（7s/5s）を表で明示 — (49) |
| A9-12 | A | LOW | P9 meta.json 節 | aborted 正規化の atexit/signal/起動時スキャナの 3 経路を列挙 — (50) |
| A9-13 | A | LOW | P9 run-buffer 節 | LRU race 防止の .lock ファイル運用を追記 — (51) |
| A9-14 | A | LOW | P9 CI 節 | examples-wandb job の依存と実行条件を追記 — (52) |
| B9-1 | B | HIGH | P9 secret マスキング DoD | mask_secrets() 集約 + MaskedLine newtype + property-based test を DoD 化 — (44) |
| B9-2 | B | HIGH | P9 wandb run lifecycle DoD | try/finally + SIGTERM handler + 5s grace の regression test を DoD 化 — (45) |
| B9-3 | B | HIGH | P9 再入禁止 DoD | submit_in_flight Mutex + アクセラレータ disable の 2 並行 regression test — (46) |
| B9-4 | B | HIGH | P9 PII scrubber DoD | pii_scrub.py 必須経由 + allow-list 外フィールド reject test — (47) |
| B9-5 | B | HIGH | P9 wandb login DoD | stdin pipe 経路の subprocess test（CLI 引数に key が乗らないこと） — (43) |
| B9-6 | B | MEDIUM | P9 wandb エラー分類 DoD | auth/rate_limit/network/server_5xx/partial 5 種の単体テスト — (48) |
| B9-7 | B | MEDIUM | P9 check_auth DoD | 7s ハード timeout / wandb.Api(timeout=5) / fallback の 3 経路テスト — (49) |
| B9-8 | B | MEDIUM | P9 meta.json DoD | aborted 正規化の atexit/signal/起動時スキャナ 3 経路テスト — (50) |
| B9-9 | B | MEDIUM | P9 run-buffer DoD | .lock 競合の regression test — (51) |
| B9-10 | B | MEDIUM | P9 アクセラレータ DoD | F2 ポリシー準拠の二重発火回避テスト — (55) |
| B9-11 | B | MEDIUM | P9 webbrowser DoD | Cargo.toml 追加 or subprocess 代替の選択を確定し依存テスト追加 — (56) |
| B9-12 | B | LOW | P9 CI job | examples-wandb job の workflow 雛形を貼る — (52) |
| B9-13 | B | LOW | P9 UI test | モーダルが API key を表示しない snapshot test — (53) |

---

## ラウンド R3（2026-05-04, 5 ファイル統合）

対象: fix-save-menu.md / P5-scenario-in-strategy.md / P7-mode-switch-menu.md /
P8-widget-menu-bar-linux.md / P9-wandb-submit-menu.md の 5 ファイル統合レビュー。

### 統一決定（58〜76）

- (58) lock 取得順: MODE_SWITCHING → submit_in_flight → APP_MODE → CURRENT_PATH
- (59) .lock JSON: PID + iso8601 起動時刻、dead PID 検出で強制削除
- (60) 自動保存は CURRENT_PATH へ書き込まない、last_saved_bytes = saved-state.json bytes
- (61) P7 4 軸 → 5 軸 matrix（submit_in_flight 軸追加）
- (62) ReplayStopped → 全 jsonl flush+fsync → meta.json atomic rewrite の書き戻し順序
- (63) SCHEMA_MINOR 増分は P5 F6a のみ（P9 meta.json は IPC 拡張不要）
- (64) accelerator 側でも MODE_SWITCHING.load() を確認
- (65) panic hook で mask_secrets 登録（main 冒頭 set_hook）
- (66) P8 widget_menu_bar に TopMenu::Tools + menu_items_tools 追加
- (67) F4 エラー分類: PathGuardViolation のみ ERROR + BUG: 接頭辞、他は WARN
- (68) P7 に submit_in_flight = Some 中は SwitchMode reject 不変条件 + P9 back-link
- (69) P8 Tools は tools_actions_for_state 別純関数で責務分離
- (70) fix-save ロードマップ表に F9 行追加
- (71) README リンクを ./archive/review-fixes-2026-05-04.md に修正
- (72) P9 `<a id="やること">` を `<a id="overview">` に
- (73) fix-save §メニューラベル表記の統一 に `<a id="menu-labels">` + ツール（Tools）親ラベル行
- (74) P9 RunBuffer 表記凡例（RunBuffer/run-buffer/run_buffer_*）
- (75) R1-P9 表に「観点 A/D を A9 系に合算」注記を追加
- (76) fix-save L228 廃案理由に P9 リンク追加

### Findings 一覧（R3）

| ID | 観点 | 重大度 | 対象ファイル:行 | 修正概要 | 対応決定 |
|---|---|---|---|---|---|
| AD3-1 | AD | HIGH | fix-save / P7 / P9 | lock 取得順序を MODE_SWITCHING → submit_in_flight → APP_MODE → CURRENT_PATH に統一 | (58) |
| AD3-2 | AD | HIGH | P7 mode-switch matrix | 4 軸 → 5 軸（submit_in_flight 追加）。submit_in_flight=Some 中は SwitchMode reject 不変条件を明文化 | (61)(68) |
| AD3-3 | AD | MEDIUM | fix-save .lock 仕様 | .lock JSON フォーマット（PID + iso8601 起動時刻 + dead PID 検出で強制削除）を確定 | (59) |
| AD3-4 | AD | MEDIUM | fix-save F4 / 自動保存節 | 自動保存は CURRENT_PATH に書き込まない、last_saved_bytes は saved-state.json bytes 基準 | (60) |
| AD3-5 | AD | MEDIUM | P9 ReplayStopped 経路 | jsonl flush+fsync → meta.json atomic rewrite の書き戻し順序を明記 | (62) |
| AD3-6 | AD | MEDIUM | P5 F6a / P9 meta.json | SCHEMA_MINOR 増分は P5 F6a のみ。P9 meta.json は IPC 拡張不要であることを明記 | (63) |
| AD3-7 | AD | MEDIUM | README.md L23 | review-fixes リンク path を `./archive/review-fixes-2026-05-04.md` に修正 | (71) |
| AD3-8 | AD | MEDIUM | P9 アンカー | `<a id="やること">` → `<a id="overview">`（ASCII id 化） | (72) |
| AD3-9 | AD | MEDIUM | fix-save §メニューラベル表記 | `<a id="menu-labels">` 明示アンカー + ツール（Tools）親ラベル行を追加 | (73) |
| AD3-10 | AD | MEDIUM | fix-save L228 廃案理由 | P9 への back-link を追加し廃案理由の追跡性を確保 | (76) |
| BC3-1 | BC | HIGH | main.rs panic hook | panic hook で mask_secrets を登録（main 冒頭 `std::panic::set_hook`）— secret 漏洩防止 | (65) |
| BC3-2 | BC | HIGH | accelerator handler | accelerator 経路でも `MODE_SWITCHING.load()` を確認（メニュー disable と二重ガード） | (64) |
| BC3-3 | BC | HIGH | F4 エラー分類 | PathGuardViolation のみ ERROR + `BUG:` 接頭辞、他は WARN とする regression test | (67) |
| BC3-4 | BC | MEDIUM | P8 widget_menu_bar | `TopMenu::Tools` + `menu_items_tools` 追加。`tools_actions_for_state` 純関数で責務分離 | (66)(69) |
| BC3-5 | BC | MEDIUM | fix-save ロードマップ表 | F9 行追加（lock 取得順 / 自動保存ポリシー / panic hook を束ねるタスク） | (70) |
| BC3-6 | BC | MEDIUM | P9 RunBuffer 表記 | RunBuffer / run-buffer / run_buffer_* の表記凡例を追加（コード/ドキュメント/関数名の使い分け） | (74) |
| BC3-7 | BC | MEDIUM | review-fixes R1-P9 表 | 「観点 A/D を A9 系に合算」注記を表直前に追加 | (75) |
| BC3-8 | BC | MEDIUM | P5 / P7 / P9 lock 連携 | 各計画書の lock 取得シーケンス図を AD3-1 の取得順に揃える | (58) |
| BC3-9 | BC | MEDIUM | P9 SubmitInFlight | submit_in_flight = Some 中の SwitchMode reject の regression test を DoD に追加 | (68) |
| BC3-10 | BC | MEDIUM | P9 panic hook test | panic 発生時に API key / token がログに出ないことを確認する property-based test | (65) |

### 重大度サマリ

- HIGH: AD3-1 / AD3-2 / BC3-1 / BC3-2 / BC3-3（5 件）
- MEDIUM: 残り 15 件
- LOW: 0 件


## ラウンド R4-R5（2026-05-04, サニティ + 残 MEDIUM/LOW 解消）

### サニティ結果（R4）
収束: HIGH 0 / MEDIUM 1 / LOW 3 → R5 で全件解消

### Findings 一覧（R4 検出 / R5 解消）

| ID | 観点 | 重大度 | 対象ファイル:行 | 修正概要 |
|---|---|---|---|---|
| AD-N1 | A+D | MEDIUM | P9-wandb-submit-menu.md:276,278,291 | R3-61 で P7 が「5 軸 matrix」に改称済みのため P9 表記を「P7 §5 軸 matrix」に統一 |
| D1 | D | LOW | fix-save-menu.md:38 vs README.md | README に `#menu-labels` への直接リンク追加 |
| A1 | A | LOW | fix-save-menu.md:17 | 凡例例示を F1〜F9 に更新（R3-70 整合） |
| D2 | D | LOW | P9-wandb-submit-menu.md:37,241 | 日本語自動アンカーを `./fix-save-menu.md#menu-labels` に変更 |

### 統一決定（R5）

```
- (77) P9 内の「4 軸 matrix」表記は全て「5 軸 matrix」に統一（R3-61 整合）
- (78) fix-save 凡例例示は最新の F 系列（F1〜F9）に追従
- (79) アンカー参照は明示 anchor（`#menu-labels` 等）を優先、日本語自動アンカーは避ける
```

### 完了サマリ

```
全ラウンド数: 5（R1, R2, R1-P9, R3, R4-R5）
修正した Finding 総数: HIGH 19 / MEDIUM 31 / LOW 13（うち R1=14/19/9, R2=7/9/3, R1-P9=7/13/6, R3=5/12/3, R4=0/1/3）
残存 LOW（対応不要）: 0
収束ラウンド: R5
```

## ラウンド R6（2026-05-04, ユーザー指摘）

ユーザーが R5 後に手動レビューで検出した 4 件を解消。

### 統一決定（80〜83）

- (80) `Save` 経路は SCENARIO 不在を拒否、`Save As` 経路は新規挿入を許可（fix-save L199 を分岐記述に）
- (81) rollback トリガーは「import エラー」 OR 「`engine.scenario.validate()` 失敗」の二段
- (82) lock の deadlock 検出は tracing 取得順記録 + `debug_assert!` 方式（std::sync::Mutex 維持、parking_lot::deadlock は不採用）
- (83) P9 Submit enable/disable は `tools_actions_for_state(auth_state, buffer_state)` 経由に固定。`actions_for_mode` シグネチャは不変（P8 DoD-11 整合）

### Findings 一覧（R6）

| ID | 観点 | 重大度 | 対象ファイル:行 | 修正概要 |
|---|---|---|---|---|
| R6-1 | A+B | HIGH | fix-save-menu.md:199 | SCENARIO 不在 .py 書き戻しを Save / Save As で分岐させ、L214-217 / P5 §確定方針との矛盾解消 |
| R6-2 | B+C | HIGH | P5-scenario-in-strategy.md:199-201, :269-271 | `importlib.reload()` では TypedDict 違反を検出できない false-green を解消。`engine.scenario.validate()` を rollback 二段トリガーに追加 |
| R6-3 | B+C | MEDIUM | P7-mode-switch-menu.md:143, :190, :209 / fix-save 凡例 | parking_lot::deadlock::check_deadlock() は std::sync::Mutex を監視できない。tracing 取得順記録 + `debug_assert!` 方式に置換 |
| R6-4 | A+D | HIGH | P9-wandb-submit-menu.md:314-317 | `actions_for_mode(mode, run_buffer_state)` 拡張案を撤回。`tools_actions_for_state(auth_state, buffer_state)` 経由で Submit enable/disable を計算（P8 DoD-11 / R3-66/69 整合） |

### 完了サマリ（R1〜R6 通算）

```
全ラウンド数: 6（R1, R2, R1-P9, R3, R4-R5, R6）
修正した Finding 総数: HIGH 22 / MEDIUM 32 / LOW 13
収束ラウンド: R6
最後の HIGH 3 件はユーザー手動検出（R5 サニティでは見逃し）。原因は「クロス文書の方針整合」「runtime 検証の有無」「ライブラリ機能と lock 種別の整合性」が grep で拾えない構造的見逃しパターン。
```

## ラウンド R7（2026-05-04, ユーザー指摘 #2）

ユーザーが R6 後に手動レビューで検出した 4 件を解消。

### 統一決定（84〜88）

- (84) AST 走査は `Assign` と `AnnAssign` の両方を対象。`SCENARIO: Scenario = {...}` と `SCENARIO = {...}` の両形式を許可
- (85) path guard を Save / Save As で分岐: Save=同一 path 必須、Save As=新規 path（current_path=None）または Load 済み元からの派生 path（current_path=Some, path!=loaded_path）の両方を許可
- (86) 純関数戻り値を `Vec<Action>` → `Vec<MenuEntry { action, enabled, tooltip, checked }>` に変更（disable + tooltip / 相互 disable / 排他チェック を表現可能に）
- (87) `mode_menu_items(current_mode) -> Vec<MenuEntry>` 純関数を P8 に新設（Live/Replay 排他チェック + SwitchAppMode dispatch）
- (88) `actions_for_mode(mode) -> Vec<Action>` のシグネチャは不変（File 系のみ、P8 DoD-11 / R3-66/69 / R6-83 整合）

### Findings 一覧（R7）

| ID | 観点 | 重大度 | 対象ファイル:行 | 修正概要 |
|---|---|---|---|---|
| R7-1 | A+B | HIGH | P5:42,90,186 / fix-save:149 | サンプルが `AnnAssign` 形なのに走査は `Assign` のみで読めない false-mute。両形式対応に |
| R7-2 | C | HIGH | P5:226-241 | Save As の通常フロー（current_path!=None かつ別 path）が path guard で拒否される。Save / Save As 分岐に |
| R7-3 | B+D | MEDIUM | P8:142,274 / P7:8 | Linux Mode メニュー仕様欠落。`mode_menu_items` 純関数 + Live/Replay 排他チェック DoD 追加 |
| R7-4 | C+D | HIGH | P8:160 / P9:246,249,314 | `Vec<Action>` では disable + tooltip / 相互 disable を表現不能。`MenuEntry { action, enabled, tooltip, checked }` に統一 |

### 完了サマリ（R1〜R7 通算）

- 全ラウンド数: 7（R1, R2, R1-P9, R3, R4-R5, R6, R7）
- 修正した Finding 総数: HIGH 25 / MEDIUM 33 / LOW 13
- 収束ラウンド: R7
- ユーザー手動検出計 7 件（R6 で 4 件、R7 で 3 件）。共通の見逃しパターン:
  - 構造体 / 関数シグネチャと UX 要件の整合（`Vec<Action>` vs disable + tooltip）
  - サンプルコードと走査アルゴリズムの整合（AnnAssign vs Assign）
  - 「正常フローが path guard で弾かれる」逆走査
  - クロス計画書の「依存先で定義されているはずの仕様」の存在確認
  - 観点 C（不変条件）に「データ構造の表現力」「依存先の実在確認」を追加すべき
