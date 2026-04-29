# review-fixes 2026-04-29

対象: `docs/✅python-data-engine/archive/proud-churning-torvalds.md`（91 行・single-file・archived）

スキル: `/review-fix-loop` → PlanLoop

## ラウンド 1（2026-04-29）

### 統一決定

- アーカイブ済みなので冒頭に **Status: 未採用 / 未実装** ブロックを必ず置く
- spawn 側ポートは現行どおり呼び出し側（`main.rs`）が任意空きポートを選択。19876 は external プローブ専用
- `FLOWSURFACE_ENGINE_TOKEN` 未設定なら external 試行を skip して直接 spawn（空 token で必ず HMAC 失敗するため無駄な遅延を排除）
- タイムアウトは TCP connect 2s と既存 `HANDSHAKE_TIMEOUT` を層別に明示。合計 = `2s + HANDSHAKE_TIMEOUT`
- ロガーは本リポ慣行に合わせて `log::info!`（旧記述の `tracing::info!` は廃止）

### 反映内容

| ID | 観点 / 重大度 | 対象節 | 修正概要 |
|---|---|---|---|
| H1 | A / HIGH | 冒頭 (新規 Status 節) | 「未採用 / 未実装」「実装は入っていない」「Superseded by … は後追い」を明記 |
| H2 | B / HIGH | 「変更する成果物 → src/main.rs」 | 旧「変更なし」を撤回。spawn 用任意空きポートと external 用 19876 を両方 `ProcessManager` に渡す API 変更が要る旨を追記 |
| H3 | C / HIGH | 新規「タイムアウト方針」節 | TCP connect 2s と既存 `HANDSHAKE_TIMEOUT` を層別に表で明示。合計上限 `2s + HANDSHAKE_TIMEOUT` |
| M1 | C / MEDIUM | 「デフォルトポート」節 | spawn 側のポートは任意空きポート / 19876 占有でも spawn は別ポートで成功する旨を明記 |
| M2 | B / MEDIUM | 「方針」フロー図 / 「Token の扱い」節 | env 未設定ショートサーキットをフロー図と本文に追加 |
| M3 | D / MEDIUM | 「テスト方針」節 | failure path 3 ケース（token 不一致 / SCHEMA_MAJOR 不一致 / env 未設定）追加。観測点（log target）も明記 |
| M4 | A / MEDIUM | 「ドキュメント」節 CLAUDE.md 追記文言 | 「env 未設定なら external 試行を skip」を併記 |
| L1 | B / LOW | 「変更する成果物 → process.rs」 | 委譲先関数 `EngineConnection::connect_with_mode` を明記 |
| L2 | D / LOW | 「テスト方針」節 | spawn ログ判定の観測点（`engine_client::process` target）を明記 |
| L3 | C / LOW | 「衝突時の挙動」節 | `tracing::info!` → `log::info!` |
| L4 | A / LOW | 「ドキュメント」節 | engine-discovery.md は「破棄」→「アーカイブに退避済み（archive/ に存在）」 |

### 機械検証

- `Grep "tracing::info"` → 0 件（L3 解消）
- `Grep "破棄"` → 0 件（L4 解消）
- `Grep "変更なし"` → 2 件残存。1 件は「**変更必須**（旧計画の「変更なし」は誤り）」の引用（意図的）、もう 1 件は Python 側（実際に変更不要、正当）

### 残存

- HIGH: 0
- MEDIUM: 0
- LOW: 0

**ラウンド 1 終了**（後続でユーザレビューにより HIGH 級の見落とし 4 件が判明 → R2 へ）。

## ラウンド 2（2026-04-29）

ユーザレビューにより、R1 で見落とした HIGH 級 4 件を反映。

### 統一決定

- **F1 (TCP timeout)**: `engine-client/src/connection.rs` を成果物に格上げ。
  `connect_plain_ws` 内の `TcpStream::connect` を `tokio::time::timeout(2s, …)` で
  個別に包む external プローブ専用関数（例: `EngineConnection::probe`）を export。
  既存 `connect_with_mode` を全置換すると spawn 後 retry ループと干渉するため分離
- **F2 (spec.md 修正)**: `docs/✅python-data-engine/spec.md` を成果物に格上げ。
  §3.1 起動フロー（ロックファイル検出）と §4.1.1 ポート秘匿（ロックファイル例外条項）の
  両方を撤回し、19876 プローブ前提に書き換える
- **F3 (責務分離)**: 起動ポリシーは `ProcessManager` 内部 (`start_or_attach()`) に
  閉じ込め、`main.rs` は呼び替えのみ。env チェックも `process.rs` に置く。
  spec.md §5.2「`src/` 側は薄い facade」原則を維持
- **F4 (観測点 seam)**: テストは `ProcessManager::spawn_count()` カウンタ seam で
  attach/spawn 判定する（第一推奨）。ログ依存検証は補助。`PythonProcess::spawn_with`
  に明示 `log::info!` を追加することも成果物に含める

### 反映内容

| ID | 観点 / 重大度 | 対象節 | 修正概要 |
|---|---|---|---|
| F1 | C / HIGH | 「タイムアウト方針」+「変更する成果物 → connection.rs」 (新節) | 現行 `connect_plain_ws` は TCP 個別タイムアウトが無いため `process.rs`/`main.rs` だけでは 2s 打ち切り不能と明記。`connection.rs` 改修を必須成果物に追加 |
| F2 | A / HIGH | 「変更する成果物 → spec.md」 (新節) | 旧「修正不要」を撤回。§3.1 ロックファイル検出と §4.1.1 ポート秘匿例外条項の更新を必須に |
| F3 | B / HIGH | 「変更する成果物 → process.rs」「→ main.rs」 | attach/spawn 判定を `start_or_attach()` として `ProcessManager` に閉じ込める。`main.rs` は呼び替えのみ。再起動ループ ([src/main.rs:433](../../../src/main.rs#L433)) との整合も追記 |
| F4 | D / HIGH | 「変更する成果物 → process.rs (観測点)」「テスト方針 (観測点)」 | `ProcessManager::spawn_count()` seam 追加 + 明示 `log::info!` 追加を成果物に含める。テスト判定は seam を第一推奨と記載 |

実装ステップを 5 項目 → 7 項目に拡張（connection.rs / spec.md を独立ステップ化）。

### 機械検証

- `Grep "spec.md.*修正は不要"` → 0 件（F2 解消）
- `Grep "変更なし"` → Python 側のみ残存（正当）
- `Grep "TcpStream::connect"` → 計画書内 1 件（F1 で意図的に追加）
- `Grep "spawn_count"` → 計画書内 3 件（F4 で意図的に追加）

### 残存

- HIGH: 0
- MEDIUM: 0
- LOW: 0

**収束（ラウンド 2）**。アーカイブ済み計画書として保存。再採用時は本ドキュメント全文 + 本ログを起点とすること。
