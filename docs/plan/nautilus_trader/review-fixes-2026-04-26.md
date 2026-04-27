# nautilus_trader 計画 レビュー修正ログ

## ラウンド 1（2026-04-26）

### 統一決定

- 未実装 IPC 仕様の表記: dto.rs に存在しない Command/Event の仕様記述はすべて「（N{x}.x で追加予定・現時点未実装）」と明示
- アンカー/行番号参照: `#L{n}` 形式の行番号参照はシンボル名参照に置換。dead アンカーは正しいセクション番号に修正
- Phase 番号統一: 旧表記「Phase 2/3」は全箇所で「N2/N3」に統一
- venue IPC 安定名: `PositionOpened.venue = "tachibana"` は IPC スキーマ安定名として認める。立花 API 固有語（sOrderNumber 等）は絶対に IPC フィールドに含めない旨を architecture.md §3 に注記
- CacheConfig enforcement: implementation-plan.md N2.3 に「engine_runner.py 内で database=None をハードコード + assert」の実装方針を明記

### Finding 一覧

| Finding ID | 観点 | 対象ファイル | 修正概要 |
|---|---|---|---|
| A-1 | 文書間整合性 | architecture.md:185 | §7.4 アンカーを §7.3 に修正 |
| A-2 | 文書間整合性 | README.md:9 / architecture.md §6 | docs/plan/README.md Phase 2 dead link への注記追加 |
| A-3 | 文書間整合性 | spec.md:63 | tachibana/spec.md §3.1 の明示リンク化 |
| A-4 | 文書間整合性 | spec.md:6 | 「Phase 2/3 以降」を「N2/N3 以降」に統一 |
| A-5 | 文書間整合性 | data-mapping.md §4.2 | FOK 注意書きの誤 Q8 参照を正確な参照先に修正 |
| A-6 | 文書間整合性 | architecture.md:13 | 図中 schema 1.2+ を schema 1.4（予定）に更新 |
| B-1 | 既存実装ズレ | implementation-plan.md N1.1 | order/ 1.3 PR マージ確認ゲート条件を明記 |
| B-2 | 既存実装ズレ | architecture.md §3 | StartEngine/StopEngine が N0.2 追加予定（未実装）の旨を明示 |
| B-3 | 既存実装ズレ | implementation-plan.md N1.4/N2.1 | tachibana_orders.* 存在ゲート条件を各タスク冒頭に追記 |
| B-4 | 既存実装ズレ | architecture.md §3 | schema 1.3 が「計画内定義・dto.rs 未追加」の旨を明示 |
| B-5 | 既存実装ズレ | implementation-plan.md マイルストーン表 | N2 依存「tachibana Phase 1 完了 = T7 緑」と現状（T4 完了）注記 |
| B-6 | 既存実装ズレ | architecture.md:58 | dto.rs#L13-17 行番号参照をシンボル名参照に変換 |
| C-1 | 仕様漏れ | implementation-plan.md N2.3 | database=None ハードコード + assert の実装方針を明記 |
| C-2 | 仕様漏れ | architecture.md §7 | tkinter subprocess ↔ engine プロセス間 credential 渡し方を具体化 |
| C-3 | 仕様漏れ | architecture.md §5 | DEV_TACHIBANA_DEMO + TACHIBANA_ALLOW_PROD の 2 段ガード再利用方針を追記 |
| C-4 | 仕様漏れ | architecture.md §3 | venue IPC 安定名規約を精度保持規約ブロックに追記 |
| C-5 | 仕様漏れ | open-questions.md Q5 | venv 配布時 Q5 即 Resolved の条件を明記 |
| D-1 | テスト不足 | implementation-plan.md N0 Exit | python-test.yml（新設）具体的ワークフロー名を記載 |
| D-2 | テスト不足 | implementation-plan.md N1 Exit | 決定論性テスト再走コマンドを追記 |
| D-3 | テスト不足 | data-mapping.md §1〜§5 | 各写像節にテストファイル名・主要 assert 内容を追記 |
| D-4 | テスト不足 | implementation-plan.md N2.2 | N2.6 冪等性テストへの相互参照を追記 |
| D-5 | テスト不足 | implementation-plan.md N0.5 | s60_*.py の smoke.sh からの呼び出し方を明記 |
| D-6 | テスト不足 | implementation-plan.md N1.4 | test_order_router_dispatch.py 追加の旨を記載 |
