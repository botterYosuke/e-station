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


