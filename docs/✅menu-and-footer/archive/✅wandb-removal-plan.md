# W&B Menu Removal Plan

`e-station` から `wandb` 関連 UI と subprocess 連携をきれいに取り除き、W&B の auth/login/publish 責務を sibling repo `C:\Users\sasai\Documents\🐃_blacksheep` へ移譲するための計画書。

対応する受け入れ計画: [`🐃_blacksheep/docs/plan/wandb-ownership-migration-plan.md`](file:///C:/Users/sasai/Documents/🐃_blacksheep/docs/plan/wandb-ownership-migration-plan.md)

> 用語規約: 本文書では送信動作を **publish**、認証を **auth / login / logout**、成果物を **run-buffer** と呼ぶ。`submit` / `RunBuffer` / `SubmitToWandb` 等は既存コード symbol を引用するときのみ使う。
>
> Phase 番号規約: 本文書の Phase は `e-station Phase N` と読む。受け入れ側 plan の Phase を参照するときは `blacksheep Phase N` と prefix を付ける。

---

## Goal

- `e-station` のメニューから `W&B にログイン` / `W&B からログアウト` / `W&B に送信` / `送信履歴` / `RunBuffer クリア` など W&B 文脈の操作を削除する
- `e-station` から W&B SDK と W&B subprocess 起動責務を外す
- replay 実行と run-buffer 生成は引き続き `e-station` が担う
- `🐃_blacksheep` が run-buffer を消費して W&B auth/login/publish を担う新境界へ移行する

非目標:

- replay engine 本体の仕様変更
- run-buffer レイアウト変更
- `python/engine/summary.py` の契約破壊

---

## Final Boundary

移行後の責務分担:

- `e-station`
  - replay 実行
  - run-buffer 生成
  - `engine.summary.compute_summary()` の提供
  - `🐃_blacksheep` が後段処理できるよう安定した成果物を出す
- `🐃_blacksheep`
  - W&B auth/login/logout
  - run-buffer からの publish
  - W&B URL を含む研究記録の管理
  - PII scrub（最終所有者）

`e-station` に残すもの:

- `%APPDATA%/flowsurface/run-buffer/<run_id>/` の出力
- `python/engine/summary.py`
- `python/engine/pii_scrub.py`（**run-buffer writer 内部限定**。境界 API としては露出しない。blacksheep への scrub ロジック移管が完了した後は writer から呼び出さない方針へ縮退）

`e-station` から削除するもの:

- `examples/wandb/`（`pii_scrub.py` 含む。ロジックは blacksheep `scripts/pii_scrub.py` に移管後に削除）
- `src/wandb_auth.rs`
- `src/wandb_submit_proc.rs`
- `src/modal/wandb_signin.rs`
- `src/modal/wandb_submit.rs`
- `src/modal/wandb_submission_log.rs`
- `Tools` メニュー上の W&B アクション
- W&B 用テスト群

---

## Stable Contract Surface (blacksheep が依存してよい契約)

`run-buffer/<run_id>/` 内のファイル:

| ファイル | 必須/任意 | 役割 |
|---|---|---|
| `meta.json` | 必須 | `schema_version` を含む run メタデータ |
| `fills.jsonl` | 必須 | 約定列 |
| `equity.jsonl` | 必須 | エクイティ時系列 |
| `narrative.jsonl` | 任意 | strategy が出力した narrative エントリ。存在すれば blacksheep publish に取り込む |

その他:

- `engine.summary.compute_summary(run_buffer_dir) -> dict`
- `meta.json.schema_version` は単調増加。blacksheep 側は未知 version を fail-fast 扱いとし、e-station 側は version bump 時に blacksheep へ事前通知する

---

## Current Surface Area

主な接続点:

- `src/menu.rs`
  - `Action::{SubmitToWandb, SignInWandb, SignOutWandb, OpenSubmissionLog, ClearRunBuffer}`
  - `tools_actions_for_state(...)`
- `src/native_menu.rs`
  - W&B 系 Action の定義と購読
- `src/widget_menu_bar.rs`
  - Tools メニュー表示ラベルと dispatch
- `src/main.rs`
  - `wandb_auth`, `run_buffer`
  - sign-in / submit / submission-log modal
  - `wandb_login`, `wandb_logout`, `submit_wandb_run`
- `src/modal/`
  - `wandb_signin.rs`
  - `wandb_submit.rs`
  - `wandb_submission_log.rs`
- `src/wandb_auth.rs`
  - `check_auth.py` 呼び出し
  - run-buffer index scan
- `src/wandb_submit_proc.rs`
  - `submit_run.py` 起動コマンド構築
- `examples/wandb/`
  - `check_auth.py`
  - `do_login.py`
  - `submit_run.py`
  - `pii_scrub.py`
- `tests/*wandb*` および wandb 由来 guard を持つテスト
  - メニュー、timeout、masking、modal、submit、reentrancy など多数
  - `tests/tools_actions_for_state.rs`（Tools メニュー組成）
  - `tests/mode_switch_blocks_during_submit.rs`（`SUBMIT_IN_FLIGHT` 由来 lock-order）

---

## Removal Strategy

### e-station Phase 1: Document the new boundary

- `AGENTS.md` の `/wandb` 記述（および対応する skill エントリ）を削除または「移管済み」に更新する
- `README.md` と `docs/✅menu-and-footer/README.md` から W&B メニュー機能の説明を外す
- `docs/plan/wandb-vision.md` が残る場合は archive 扱いにする（ファイル存在は Phase 1 着手時に確認する）
- `.claude/` 配下の wandb 関連 skill / template を archive または削除

完了条件:

- 新規開発者が docs を読んで「W&B は e-station の機能ではない」と理解できる
- `rg -i "wandb" docs/ AGENTS.md README.md` の残存件数を 0 もしくは「移管済み」記述のみに縮退

中間ゲート: `cargo check --workspace` が green であること

ロールバック条件:

- blacksheep Phase 1 の docs 反映が未完了なら本 Phase をマージしない（boundary が両側で一致する状態を不変条件として保つ）

### e-station Phase 2: Remove W&B from menu vocabulary

- `src/menu.rs` から W&B Action 群を削除
- `tools_actions_for_state(...)` を廃止するか、Tools メニュー自体を空にする
- `src/native_menu.rs` と `src/widget_menu_bar.rs` から W&B ラベル・dispatch を削除
- `tests/menu_actions_cross_platform.rs` および `tests/tools_actions_for_state.rs` などメニュー構造テストを新境界へ更新する
- 移行期間中に GUI を使った publish が起動しないよう、Phase 2 ブランチでは menu Action を消すと同時に `submit_wandb_run` のエントリポイントも no-op に差し替える（**publish 経路は blacksheep の手動スクリプト 1 本に限定**）
- **e-station Phase 2 マージ完了前** は blacksheep manual publish を本番運用しない（no-op 化マージ後に blacksheep 解禁）

判断ポイント:

- `Tools` トップメニュー自体を残すか
- W&B 削除後に `Tools` が空ならトップメニューごと削除する方が自然

推奨:

- `Tools` が空になるならトップメニューごと削除する

中間ゲート: `cargo check --workspace` および `cargo test --workspace` が green であること

ロールバック条件:

- replay 実行が壊れる回帰が出たら Phase 2 ブランチを revert（feature branch `chore/wandb-removal-phase-2` を main にマージしない）

### e-station Phase 3: Remove runtime state and subprocess ownership

- `src/main.rs` から以下を削除
  - `wandb_auth`
  - `run_buffer` の W&B UI 用 index
  - `wandb_signin_modal`
  - `wandb_submit_modal`
  - `wandb_submission_log_modal`
  - `Wandb*` Message variants
  - `wandb_login`
  - `wandb_logout`
  - `submit_wandb_run`
  - `SUBMIT_CHILD`
  - `WandbSubmitError`
- `SUBMIT_IN_FLIGHT` および対応する mode-switch lock-order の処遇は **次の二択のいずれかを Phase 3 DoD で確定する**:
  1. **削除**: lock の存在理由が W&B publish にしか無いと判断するなら guard ごと撤廃し、`tests/wandb_modeswitch_lock_order.rs` も削除する
  2. **再定義**: 別の長時間 subprocess（将来の export 等）に流用する根拠を `docs/✅menu-and-footer/README.md` に記し、guard を W&B 非依存名にリネーム＋最小 fixture でテストを書き直す
- W&B submit 中のモード切替禁止ロジックを除去する（再定義を選んだ場合は新理由に置き換える）

注意:

- `run-buffer` 自体は replay 成果物として残すため、run-buffer writer と replay 完了導線は消さない

中間ゲート: `cargo check --workspace` が green。`rg -i "Wandb|SUBMIT_CHILD|WandbSubmitError" src/main.rs` が 0 件

ロールバック条件:

- 再定義を選びテストが書けない場合は削除側へフォールバック

### e-station Phase 4: Remove modal and helper modules

- `src/modal.rs` から W&B 関連 module export を削除
- `src/modal/wandb_signin.rs` を削除
- `src/modal/wandb_submit.rs` を削除
- `src/modal/wandb_submission_log.rs` を削除
- `src/wandb_auth.rs` を削除
- `src/wandb_submit_proc.rs` を削除
- `examples/wandb/` 一式を削除（**blacksheep への `pii_scrub` ロジック移管完了かつ `tests/test_pii_scrub_parity.py` が green であることが前提**）
- `Cargo.toml` に W&B 関連 dev-dep が残っていないか確認

中間ゲート: `cargo check --workspace` および `cargo build --release` が green であること

### e-station Phase 5: Re-home or delete tests

削除候補（実在確認済み）:

- `tests/wandb_auth_state.rs`
- `tests/wandb_auth_timeout.rs`
- `tests/wandb_key_masking.rs`
- `tests/wandb_menu_action.rs`
- `tests/wandb_modeswitch_lock_order.rs`（Phase 3 で「削除」を選んだ場合）
- `tests/wandb_reentrancy.rs`
- `tests/wandb_signin_flow.rs`
- `tests/wandb_submission_log_ui.rs`
- `tests/wandb_submit_subprocess.rs`
- `tests/tools_actions_for_state.rs`（Tools メニュー全削除を選んだ場合）
- `tests/mode_switch_blocks_during_submit.rs`（Phase 3「削除」時）
- `examples/wandb/tests/`
  - `test_check_auth.py`（blacksheep 側 `tests/test_wandb_check_auth.py` に役割移管）
  - `test_pii_scrub.py`（blacksheep 側に移管。run-buffer writer 内部 scrub のみ守る分は `python/tests/test_run_buffer_writer.py` の縮小版で代替）
  - `test_submit_run.py`（blacksheep 側 `tests/test_publish_run.py` に役割移管）

残すか移すか要判断:

- `python/tests/test_summary.py`
  - `engine.summary` の安定契約を守る目的なので残す
- `python/tests/test_run_buffer_writer.py`
  - W&B 固有でなく run-buffer 契約検証に必要な部分は残す
  - `examples/wandb/pii_scrub.py` との一致確認は新所有先（blacksheep）へ移すか、テスト自体を縮小する

中間ゲート: `cargo test --workspace` および `uv run --group dev pytest python/tests -m "not live"` が green であること

### e-station Phase 6: Verify the new baseline

**前提（Phase 6 着手ゲート、すべて満たされていること）**:

- blacksheep 側 `wandb-ownership-migration-plan.md` の blacksheep Phase 1〜5 が完了
- blacksheep CI で以下が green:
  - `uv run pytest tests/test_wandb_check_auth.py tests/test_wandb_login.py tests/test_wandb_logout.py tests/test_wandb_key_masking.py tests/test_publish_run.py tests/test_pii_scrub.py tests/test_pii_scrub_parity.py tests/test_e_station_path.py`
- blacksheep 側 docs（README / AGENTS / START_HERE）が新 boundary を語っている
- 1 件の実 run-buffer を blacksheep の `publish_run.py` で publish して W&B URL が返ることを手動確認済み

検証コマンド:

- `cargo test --workspace`
- `cargo build --release`
- `uv run --group dev pytest python/tests -m "not live"`
- 機械検証（残骸ゼロ確認、すべて 0 件で完了）:
  - `rg -i "wandb|SubmitToWandb|wandb_auth|wandb_submit|submit_run\.py|do_login\.py|check_auth\.py" src/ examples/ tests/ python/`
  - `rg -i "SUBMIT_IN_FLIGHT|SUBMIT_CHILD|WandbSubmitError" src/`（Phase 3「再定義」を選んだ場合は新名のみ残ってよい）
  - `python/` 内の残骸例: `python/engine/summary.py` の docstring に `examples/wandb/submit_run.py` 参照が残る可能性。`python/tests/test_summary.py` の docstring に `examples/wandb/tests/` 参照が残る可能性。これらも `rg` でゼロを確認し、残っていれば docstring を更新する

手動確認:

- replay 実行で `run-buffer` が生成されること
- UI 上から W&B 導線が完全に消えていること

ロールバック条件:

- Phase 6 で blacksheep CI が red、または publish 実証ができない場合は本 Phase をマージせず、blacksheep 側の修正を待つ

---

## Risks

### Risk 1: run-buffer 契約まで巻き込んで壊す

症状:

- `🐃_blacksheep` の ingest/publish が成立しなくなる

対策:

- `run-buffer` layout は明示的に non-goal outside change とする
- `python/engine/summary.py` の API を維持する
- `narrative.jsonl` を含む stable contract セクションを唯一のソースとして両 plan で同期する

### Risk 2: mode-switch / footer テストが W&B 前提で壊れる

症状:

- `SUBMIT_IN_FLIGHT` 前提の guard テストが大量に落ちる

対策:

- e-station Phase 3 DoD の二択（削除 / 再定義）を Phase 3 着手前に決める
- W&B 起因の lock-order 説明を docs/test から削除する

### Risk 3: replay 後の成果物導線が見えなくなる

症状:

- ユーザーが run_id の見つけ方や次アクションを失う

対策:

- `README.md` / replay docs に「run-buffer は blacksheep 側で ingest/publish する」導線を書く
- 認証媒体（`WANDB_API_KEY` / `~/.netrc`）は blacksheep 側仕様に従う旨を明示し、e-station からは触れない

### Risk 4: 移行期間中の二重 publish

症状:

- e-station GUI 削除前に blacksheep `publish_run.py` を併用し、同一 run_id を 2 回 publish する

対策:

- **e-station Phase 2 マージ完了前** は blacksheep manual publish を本番運用しない（no-op 化マージ後に blacksheep 解禁）
- 重複検知は run_id ベースで blacksheep 側に委ねる

---

## Sequencing With `🐃_blacksheep`

> 以下の Phase 番号は受け入れ計画 [`wandb-ownership-migration-plan.md`](file:///C:/Users/sasai/Documents/🐃_blacksheep/docs/plan/wandb-ownership-migration-plan.md) の `blacksheep Phase N` を指す。

推奨順:

1. blacksheep Phase 1: docs で boundary を宣言
2. blacksheep Phase 2: auth scripts を追加
3. blacksheep Phase 3: publish script + pii_scrub を追加
4. blacksheep Phase 4: ingest との接続
5. blacksheep Phase 5: wiki 連携完了
6. e-station Phase 1〜5: docs / menu / runtime / modal / tests を削除（各 Phase 末尾の中間ゲートを通過しながら進める）
7. e-station Phase 6: 上記ゲート（blacksheep CI green と publish 実証）を満たしてから検証

理由:

- 先に `e-station` 側を消すと W&B 導線が一時的に消滅する
- 先に `🐃_blacksheep` 側の新導線（特に wiki 連携まで）を作れば移行期間の混乱が少ない

非関与:

- e-station 側は `Bronze/Silver/wiki` 更新フローには関与しない（blacksheep 内部の関心）

---

## Definition Of Done

- `e-station` UI から W&B という語が消えている
- `src/` から W&B auth/login/publish の実装が消えている
- `examples/wandb/` が存在しない
- W&B 固有テストが削除または新境界に合わせて更新されている
- replay 実行と run-buffer 生成が従来通り動く
- docs が `🐃_blacksheep` ownership を明記している
- **前提**: blacksheep 側 plan の DoD（auth/publish/pii_scrub/wiki まで）が満たされ、blacksheep の publish 経路が動作している状態である
- 機械検証 `rg -i "wandb|SubmitToWandb|wandb_auth|wandb_submit|submit_run\.py|do_login\.py|check_auth\.py" src/ examples/ tests/ python/` が 0 件（`python/` 内 docstring の参照も含む）
- W&B URL の生成・保存責務が blacksheep に閉じている（e-station からは URL に触れない）

---

## Open Questions

- `Tools` トップメニューを削除するか、将来用に残すか
- `run-buffer` の手動削除導線を `e-station` に残すか、管理を完全に外へ寄せるか
