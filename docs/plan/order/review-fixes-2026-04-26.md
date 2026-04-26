# 立花 注文計画 レビュー修正ログ（2026-04-26）

> 前日ログ: docs/plan/order/review-fixes-2026-04-25.md（ラウンド 1〜6 完了）

## ラウンド 1（2026-04-26）

### 統一決定
- T0.4 対象シンボル（TachibanaSessionHolder / TachibanaWire*）参照に「（T0.4 新設）」注釈を追加
- stale 注記（spec.md §5.2 へ反映必要...）を削除
- spec.md §5.2 SECOND_PASSWORD_LOCKED 行に解除トリガー・Modify/Cancel reject を追記
- implementation-plan.md lockout テストに freezegun.freeze_time 使用を明記

| Finding ID | 観点 | 対象ファイル | 修正概要 |
|---|---|---|---|
| A-M1-R7 R1-26 | 文書間整合 | architecture.md:449 | spec.md §5.2 反映済みの stale 注記を削除 |
| B-1 R1-26 | 既存実装ズレ | architecture.md §2.2 シーケンス図 | TachibanaSessionHolder 参照に「T0.4 新設」注釈追加 |
| B-2 R1-26 | 既存実装ズレ | architecture.md §10 型マッピング表 | TachibanaWire* 表に「T0.4 実装対象（未実装）」注記追加 |
| MEDIUM-C4 R1-26 | 仕様漏れ | spec.md §5.2 行196 | SECOND_PASSWORD_LOCKED 行に解除トリガー・Modify/Cancel への 423 拡張を追記 |
| NEW-D-M1 R1-26 | テスト不足 | implementation-plan.md 行174 | lockout テストに freezegun.freeze_time 使用を明記 |

