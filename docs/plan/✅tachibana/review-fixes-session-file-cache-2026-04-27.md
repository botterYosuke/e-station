# session-file-cache PlanLoop レビューログ

対象: `docs/plan/✅tachibana/session-file-cache.md`（+ 関連 spec/architecture/implementation-plan）  
開始日: 2026-04-27

---

<!-- ラウンド開始後にエージェントが追記 -->

## ラウンド 1（2026-04-27）

### 統一決定

1. `_is_session_fresh` 境界値: `>= JST 15:30:00` を無効（閉で無効側）。`saved_at_ms > now_ms` はクロックスキューとして無効扱い
2. アトミック書き込み: `os.rename` → `os.replace` 使用（Windows/Unix 両対応）
3. T-SC4 スコープ: `engine-client/tests/` 9 ファイルの削除/書き換えを明示。`data::wire::tachibana` モジュールと `data/tests/tachibana_keyring_roundtrip.rs` も削除対象に追加
4. T-SC5 JST mock: `freezegun.freeze_time` を使用
5. T-SC6 スコープ: `spec.md §3.2`, `§4.A1`, `architecture.md §1/§2/§3/§5/§8` を更新対象として明示列挙
6. `LoginCancelled` 遷移: `VenueLoginCancelled{venue, request_id}` を Python→Rust 送信し `VenueState::Idle` へ遷移
7. `architecture.md` への直接修正は T-SC6 まで先送り

### Findings

| ID | 観点 | 重要度 | 対象ファイル | 修正概要 |
|---|---|---|---|---|
| A-001 | A | HIGH | spec.md:63-65 | §3.1 セキュリティ要件に T-SC6 更新予定の注記を追加 |
| A-002 | A | HIGH | spec.md:83-84,89,111 | §3.2 keyring/SetVenueCredentials 参照箇所に注記を追加 |
| A-003 | A | HIGH | architecture.md:9-12,21,31-498 | architecture.md §1/§2/§3 を T-SC6 スコープに明示追加（直接修正は先送り） |
| A-004 | A | MEDIUM | architecture.md:287-288 | T-SC6 スコープに §5 keyring 行の書き換えを追加 |
| A-005 | A | MEDIUM | architecture.md:571-572 | T-SC6 スコープに §8.3 テスト記述削除を追加 |
| A-006 | A | MEDIUM | session-file-cache.md:185 | §4「残るもの」に VenueState FSM は変更なしの注記を追加 |
| A-007 | A | LOW | session-file-cache.md:270-272 | T-SC6 列挙に spec.md §3.2 / §4.A1 を追加 |
| B-001 | B | MEDIUM | session-file-cache.md §3.2 | `_spawn_login_dialog` 呼び出しを `prefill={"user_id":...,"is_demo":...}` に修正 |
| B-002 | B | MEDIUM | session-file-cache.md §3.2 | `_is_session_fresh` は T-SC1 で新設と注記 |
| B-003 | B | MEDIUM | session-file-cache.md T-SC3 | 実際のハンドラ名を確認してから削除するよう注記 |
| B-004 | B | MEDIUM | session-file-cache.md T-SC4 | secrecy/zeroize の非 Tachibana 利用確認と Cargo.toml 削除手順を追加 |
| B-005 | B | MEDIUM | session-file-cache.md §4 | `data::wire::tachibana` と `data/tests/tachibana_keyring_roundtrip.rs` を削除表に追加 |
| C-001 | C | HIGH | session-file-cache.md §3.1/T-SC1 | `os.rename` → `os.replace`（Windows 対応）に修正 |
| C-002 | C | HIGH | session-file-cache.md §3.3/T-SC3 | `_login_in_flight` フラグによる二重起動防止を追記 |
| C-003 | C | HIGH | session-file-cache.md §3.3/T-SC3 | `LoginCancelled` → `VenueLoginCancelled` 送信と Idle 遷移を追記 |
| C-004 | C | MEDIUM | session-file-cache.md T-SC2 | ネットワークエラー時の `VenueError` 送信を追記 |
| C-005 | C | MEDIUM | session-file-cache.md T-SC1 | `saved_at_ms > now_ms` はクロックスキューで無効扱いを追記 |
| C-006 | C | MEDIUM | session-file-cache.md T-SC2 | `DEV_TACHIBANA_PASSWORD` の `dev_tachibana_login_allowed` フラグ制御を追記 |
| C-007 | C | MEDIUM | session-file-cache.md T-SC1 | `>= 15:30:00 JST` を無効（閉で無効側）と明記 |
| C-008 | C | MEDIUM | session-file-cache.md §5 | schema_minor の後方互換根拠と同期更新手順を追記 |
| C-009 | C | LOW | session-file-cache.md §3.2 | p_no=1 リセット理由（立花 API 仕様）を注記 |
| C-010 | C | LOW | session-file-cache.md §4/§5 | 重複記載は整合しているため修正不要 |
| D-001 | D | HIGH | session-file-cache.md T-SC4 | engine-client/tests/ 9 ファイルを T-SC4 の削除対象に明示追加 |
| D-002 | D | HIGH | session-file-cache.md T-SC5 | invariant-tests.md に F-SC-NoPassword/F-SC-Atomic/F-SC-FreshJST を起票する項目を追加 |
| D-003 | D | HIGH | session-file-cache.md T-SC5 | freezegun 使用・JST 3 点テスト・クロックスキューテストを明記 |
| D-004 | D | MEDIUM | session-file-cache.md T-SC5 | _spawn_login_dialog を AsyncMock で patch する方針を明記 |
| D-005 | D | MEDIUM | session-file-cache.md T-SC5 | os.replace を patch して中断 → ファイル残存なしを assert する観測点を追記 |
| D-006 | D | MEDIUM | session-file-cache.md T-SC5 | CI 自動収集（特別なマーカー不要）を明記 |
| D-007 | D | LOW | session-file-cache.md T-SC4 | schema_v1_2_roundtrip.rs は該当 2 関数のみ削除、ファイル維持 |

## ラウンド 2（2026-04-27）

### 収束確認

| 観点 | 結果 |
|---|---|
| A（文書間整合性） | 収束（A-001〜A-007 全反映確認）|
| B（既存実装とのズレ） | 収束（B-001〜B-005 全反映確認）|
| C（仕様漏れ・設計リスク） | 収束（C-001〜C-009 全反映確認）|
| D（テスト不足） | MEDIUM 1件残存 → 修正済み |

### 新規 Finding

| ID | 観点 | 重要度 | 対象ファイル | 修正概要 |
|---|---|---|---|---|
| D2-001 | D | MEDIUM | session-file-cache.md T-SC5 | T-SC5 に `_login_in_flight` / `VenueLoginCancelled` / ネットワーク `VenueError` の 3 テストケースを追加 |

### 最終機械検証（ラウンド 2 完了時）

- `os.rename` 残存: なし（説明文中のみ）✓
- `_login_in_flight` §3.3/T-SC3/T-SC5: 全て存在 ✓
- `VenueLoginCancelled` §3.3/T-SC3/§4/T-SC5: 全て存在 ✓
- `VenueError` T-SC2/T-SC5: 全て存在 ✓
- T-SC6 スコープ（architecture.md §1/§2/§3/§5/§8, spec.md §3.2/§4.A1）: 明記済み ✓

## ラウンド 3（2026-04-27）

### 収束確認

全 4 観点とも HIGH/MEDIUM ゼロ。ループ終了条件充足。

| 観点 | 結果 |
|---|---|
| A（文書間整合性） | 収束（新規 HIGH/MEDIUM なし）|
| B（既存実装とのズレ） | 収束（新規 HIGH/MEDIUM なし）|
| C（仕様漏れ・設計リスク） | 収束（新規 HIGH/MEDIUM なし）|
| D（テスト不足） | 収束（新規 HIGH/MEDIUM なし）|

### 新規 LOW（対応不要）

| ID | 観点 | 対象ファイル | 内容 |
|---|---|---|---|
| A-008 | A | session-file-cache.md L202 | spec.md §2.1 アンカーが不正確（VenueState FSM の実際の記述場所と微妙にズレ）。T-SC6 作業時に修正 |
| B-LOW-001 | B | session-file-cache.md §3.3 L164 | §3.3 本文が `_login_in_flight: bool` と記述しているが T-SC3 は正しく `_tachibana_login_inflight`（asyncio.Lock）を参照。読者への注意喚起として記録 |
| B-LOW-002 | B | session-file-cache.md §3.3 / T-SC3 | 行番号 L1564/L1784 が現行 server.py（L1569/L1557）とズレ。実装時はシンボル名で検索する |
| D-LOW-001 | D | invariant-tests.md | F-SC-NoPassword / F-SC-Atomic / F-SC-FreshJST が未起票。T-SC5 実装 PR 着地時に同時追記する設計で正常 |

### 最終機械検証（ラウンド 3 完了時）

- `os.rename` 仕様記述残存: なし（説明文中のみ）✓
- `VenueLoginCancelled`: §3.3 / §4 / T-SC3 / T-SC5 全存在 ✓
- `VenueError`: §4 / T-SC2 / T-SC5 全存在 ✓
- `schema_major`: §5 / T-SC4 全存在 ✓
- `_tachibana_login_inflight`: T-SC3 / T-SC5 に存在 ✓

**収束確定。全タスク（T-SC1〜T-SC6）の計画記述は実装に進める状態。**
