# Review-Fix Log — 2026-04-28

対象ファイル（Order Phase ドキュメント更新後レビュー）:
- `docs/plan/✅python-data-engine/schemas/commands.json`
- `docs/plan/✅python-data-engine/schemas/events.json`
- `docs/plan/✅order/implementation-plan.md`
- `docs/plan/✅order/invariant-tests.md`
- `.claude/skills/tachibana/SKILL.md`
- `docs/plan/✅tachibana/architecture.md`

## ラウンド 1（2026-04-28）

### 統一決定

- market フィールド: `["string", "null"]` optional で全 fetch/subscribe 系に追加（Python 側 Optional 準拠）
- order 系 request_id: `$ref RequestId` に統一（F-L7）
- OrderStatus enum: INITIALIZED/SUBMITTED/ACCEPTED/PARTIALLY_FILLED/FILLED/PENDING_UPDATE/PENDING_CANCEL/CANCELED/EXPIRED/REJECTED
- reason_code known values: 11 値を description に列挙
- SKILL.md S3: keyring → Python file cache 書き換え（tachibana_session.json）
- invariant-tests.md: C-M2/C-R2-H2 は test_tachibana_session_holder.py 実装済みに更新、A-H2/C-H1/C-H2 は Phase O1 以降に再スケジュール

### Finding 一覧

| ID | 観点 | 対象ファイル | 修正概要 |
|---|---|---|---|
| H1 | A | SKILL.md S3 | keyring / data::config::tachibana 参照を Python file cache に書き換え（S3・R3・R10・S6・冒頭bullet・description frontmatter） |
| H2 | A | SKILL.md §冒頭 | SetVenueCredentials/VenueCredentialsRefreshed を削除済みとして明記 |
| H3 | A/B | commands.json | Ping コマンドを oneOf + $defs に追加 |
| H4 | A/B | commands.json | Subscribe/Unsubscribe に market フィールド追加（optional） |
| H5 | A/B | commands.json | FetchKlines/FetchOpenInterest に start_ms/end_ms/market、FetchTrades に market/data_path 追加 |
| H6 | C | commands.json | ModifyOrder/CancelOrder の description に WAL スコープ外注記 |
| H7 | C | events.json | SecondPasswordRequired に tachibana 専用の注記 |
| H8 | D | invariant-tests.md | C-M2/C-R2-H2 を test_tachibana_session_holder.py の実際の関数名で更新し ✅ に |
| M1 | B | architecture.md | §7.7 に SetSecondPassword IPC フロー1行追記 |
| M2 | C | commands.json | order 系 6 コマンドの request_id を $ref RequestId に統一 |
| M3 | C | events.json | OrderRecordWire.status に OrderStatus enum 追加 |
| M4 | C | events.json | OrderRejected.reason_code に known values 列挙 |
| M5 | D | invariant-tests.md | A-H2/C-H1/C-H2 を Phase O1 以降に再スケジュール |
| M6 | D | implementation-plan.md | T0.3 に ForgetSecondPassword テスト参照追記 |
| M7 | A/B | events.json | Pong イベントを oneOf + $defs に追加 |
| L-2 | A | commands.json | FetchTickerStats/ListTickers/RequestDepthSnapshot に market 追加 |
| Q-CI-1 | D | open-questions.md | cargo test --workspace CI ジョブ未設定を open-questions に追記 |

## ラウンド 2（2026-04-28）

### 重点検査結果

| 検査項目 | 結果 |
|---|---|
| market フィールド全コマンド網羅（GetTickerMetadata は dto.rs 側にもない為除外） | ✅ |
| Ping/Pong $defs が dto.rs と整合 | ✅ |
| SKILL.md に肯定的 keyring 記述なし | ✅ |
| architecture.md §7.7 SetSecondPassword IPC フローと F-H5 の整合 | ✅ |
| commands.json 全コマンドに additionalProperties: false | ✅ |
| events.json OrderStatus 値がドキュメント記録として機能 | ✅ |

### 判定: **収束（HIGH/MEDIUM = 0）**

残存 LOW（対応不要）:
- L-new-1: Pong.request_id が `$ref RequestId` でなく `type: string`（Rust が Ping の request_id をエコーバックするため実用上無害）
- L-new-2: GetTickerMetadata の market が dto.rs/commands.json にないが schemas.py には存在（schemas.py 先行定義、実害なし）
- L-new-3: schemas.py の OrderRecordWire.status が str 型で実行時 enum 検証なし（events.json の OrderStatus はドキュメント記録として機能）
