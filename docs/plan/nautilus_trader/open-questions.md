# nautilus_trader 統合: Open Questions

## N-pre ブロッカー（着手前に必ず resolve すること）

### Q1. nautilus のバージョン pin 戦略 ★N-pre ブロッカー

nautilus_trader は活発に開発中で、minor バージョンでも破壊変更が入りうる。

- 案 A: `>=1.211,<2.0` の範囲 pin
- 案 B: `==1.211.x` 厳密 pin。アップグレードは手動ブランチで検証

**判断軸**: 立花の発注往復が動いた構成を壊したくないため、N2 完了後は厳密 pin に切り替えるのが妥当か？

**アクション**: N-pre Tpre.2 で決定し、spec.md §5 の暫定表記を確定版に書き換える。

---

### Q3. `BacktestEngine` の clock 注入方式 ★N-pre ブロッカー

仮想時刻 (`current_time`) を nautilus に渡す方法:

- 案 A: イベントごとに Rust → Python `AdvanceClock { ts_ms }` を送る。StepForward（1 本ずつ進む UX）を維持できる
- 案 B: 開始時に時間範囲を渡し nautilus に自走させる。簡単だが StepForward と整合しなくなる

**feasibility 確認が必須**: nautilus 1.211 の公開 API で案 A を組めるかを N-pre Tpre.1 でプロトタイプ検証する。

- 案 A が組めた場合 → architecture.md §3 に `AdvanceClock` Command を残す
- 案 B になった場合 → `AdvanceClock` を削除し、`StartEngine.config.range_start_ms / range_end_ms` のみで完結する設計に修正

**アクション**: N-pre Tpre.1 で resolve、architecture.md §3 の `AdvanceClock` 条件書きを確定版に書き換える。

---

### Q5. ライセンスの再配布形態 ★N-pre ブロッカー

nautilus_trader は LGPL-3.0。

- Python パッケージとして PyPI から取り込むだけなら問題なし
- **PyInstaller one-binary 化**する場合（`engine.spec` がリポジトリに存在）: LGPL 動的リンク条項を満たすため、nautilus を差し替え可能な構造にする必要がある

**アクション**: N-pre Tpre.3 で配布形態（venv 配布 / PyInstaller / インストーラ同梱）を確定し、spec.md §5 を書き換える。PyInstaller 採用時は NOTICE ファイルと差し替え可能性の実装方針を同タスクで決める。

**Exit 条件**: venv 配布を選択した場合は LGPL 追加対応不要 → Q5 即 Resolved。PyInstaller 採用を選択した場合のみ NOTICE ファイルと差し替え可能性の実装方針を同タスクで決めること。

---

### Q6. 既存暗号資産 venue の発注経路（Rust 実装）はあるのか ★N-pre ブロッカー

`exchange/src/adapter/` にはデータ系のみが見えるが、`place_order` 系メソッドが存在するか未確認。

- 0 hit なら **Phase N3 は移植ではなく新規実装**になり工数が変わる
- hit ありなら移植のまま

**アクション**: N-pre Tpre.4 で `git grep` し、N3 ラベルと工数概算を確定する。

---

### Q7. 発注 UI の所在（iced vs Python） ★N-pre ブロッカー

spec.md §4 の公開 API 表は iced から `POST /api/order/submit` を叩く前提で書かれている。一方、Python 単独モード方針（memory: `project_python_only_mode.md`）に徹底するなら「全 venue の発注 UI を Python 側に統一」するのが筋。

- 案 A: iced が HTTP API を叩く（現状の spec.md §4）
- 案 B: Python tkinter に発注 UI を置き、iced は監視・表示のみ

長期方針から案 B の方が一貫する。ただし暗号資産 venue の発注 UI を将来 iced に追加するなら案 A の方が整合する。

**アクション**: N-pre Tpre.6 で決定。案 B 採用時は spec.md §4 の公開 API 表の備考欄を更新し、order/ 計画の UI 層の設計も合わせる。

---

### Q8. 動的呼値テーブルと nautilus `Instrument.price_increment` ★N-pre ブロッカー（C6 由来）

nautilus の `Instrument` は `price_increment` を **不変な scalar** として持つが、立花の呼値テーブルは価格帯（例: ≤500 円は 0.1 円刻み、>500〜3,000 円は 0.5 円刻み…）で変わる。現在 tachibana Phase 1 では「呼値テーブル動的反映は Phase 2 以降」と引退表示。

nautilus 統合では `OrderFactory` が `price_increment` を使って価格丸めを行うため、価格帯違反のオーダーが nautilus 内部で reject される可能性がある。

- 案 A: 銘柄ごとに `price_increment` を最小単位（0.1 円）で固定し、実際の呼値丸めは Python 写像層で行う
- 案 B: 価格帯ごとに `Instrument` を複数切り替える（nautilus 非標準の扱い）
- 案 C: 呼値テーブル動的反映を nautilus 統合前倒しで実装する

**アクション**: N-pre Tpre.5 で決定し、data-mapping.md §3 に反映する。

---

## 着手後に決めれば良い事項

### Q2. Strategy はユーザーが書くのか、組み込みか

N0〜N2 は **「組み込み Strategy のみ」**に制限（M3、spec.md §3.2）。`--strategy-file` によるユーザー Strategy ロードを許す場合、立花 creds が同居するプロセスへの任意コード実行になる。信頼境界の設計（別プロセス実行・credential 非同居・サンドボックス）が必要。

- 案 A: ユーザーが Python ファイルを書く（別プロセスで隔離実行、creds を渡さない）
- 案 B: 内蔵 "Manual Trading Strategy"（UI クリックを `OrderFactory` に流す bridge）だけ提供
- 案 C: 両方

**アクション**: N2 完了後に設計し、security review を通す。

### Q4. nautilus persistence の扱い（長時間ライブ運用）

spec.md §3.2 で `CacheConfig.database = None`（ディスク永続化 OFF）に確定。long-running ライブ運用での再起動復元は `CLMOrderList` から毎回 warm-up（N2.3 で実装）。

Parquet キャッシュを有効化したい場合は、立花の session 情報・約定履歴を nautilus 側ファイルに二重保存しない前提で別途検討（N3 以降）。

### その他（実装フェーズで確定）

- バックテスト性能 SLA の具体値（spec.md §3.3 の「30 秒以内」を N1.7 実測で確定）
- nautilus の `MessageBus` を IPC イベントに 1:1 で写すか、要約するか
- ナラティブ `reasoning` テキストの自動生成: nautilus の `Strategy.log` を流用するか、ユーザーが明示的に書くか
