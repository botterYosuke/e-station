# nautilus_trader 統合: Open Questions

## 着手前に確定すべき事項

### Q1. nautilus のバージョン pin 戦略
nautilus_trader は活発に開発中で、minor バージョンでも破壊変更が入りうる。
- 案 A: `>=1.211,<2.0` の SemVer pin（spec.md §5 の暫定案）
- 案 B: 厳密な `==1.211.x` pin、アップグレードは手動ブランチで検証

**判断軸**: 立花の発注往復が動いた構成を壊したくないため、**Phase N2 完了後は厳密 pin に切り替える**のが妥当か？

### Q2. Strategy はユーザーが書くのか、組み込みか
- 案 A: ユーザーが Python ファイルを書く（外部スクリプトを `--strategy-file` で渡す）
- 案 B: Flow Surface 内蔵の "Manual Trading Strategy"（UI クリックを `OrderFactory` に流す）を提供
- 案 C: 両方

`docs/plan/README.md` の長期ビジョン（エージェントのナラティブ可視化）を踏まえると **A が本線、B は Phase 1 で発注 UI を出すための薄い橋渡し** という整理になりそうだが、UI 設計を詰める前に確定したい。

### Q3. `BacktestEngine` の clock 注入方式
仮想時刻 (`current_time`) を nautilus に渡す方法:
- 案 A: イベントごとに Rust → Python `Tick { ts_ms }` を送る（高頻度・確実）
- 案 B: 開始時に時間範囲を渡し nautilus に自走させる（簡単・既存リプレイの「ステップ実行」UX と整合しなくなる）

リプレイの **StepForward**（1 本ずつ進める）を維持するなら A が必須。確認したい。

### Q4. nautilus persistence の扱い
nautilus は約定履歴・bar データを Parquet キャッシュする機能を持つ。
- 既定では **無効化**（spec.md §3.2）
- ただし長時間ライブ運用での再起動復元には便利
- Phase N2 で「立花の注文台帳を nautilus 側に persistence するか、それとも毎回 `CLMKabuOrderList` から復元するか」を決める必要がある

### Q5. ライセンスの再配布形態
nautilus_trader は LGPL-3.0。
- Python パッケージとして PyPI から取り込むだけなら問題ない
- ただし Flow Surface を **PyInstaller で one-binary 化**する場合（`engine.spec` あり）、LGPL の動的リンク条項を満たすため nautilus の差し替えが可能な構造にする必要がある
- Phase 0 段階で配布形態（venv 配布 / PyInstaller / インストーラ同梱）を確定したい

### Q6. 既存暗号資産 venue の発注経路（Rust 実装）はあるのか
- `exchange/src/adapter/` を読むと depth・unit 等のデータ系のみで、`place_order` 系メソッドが存在するかは未確認
- もし存在しない（= Rust 側に発注経路がない）なら **Phase N3 は移植ではなく新規実装**になり、優先度を下げる
- N1 着手前に grep で確認する

### Q7. Phase N2 の発注 UI を Rust（iced）に出すか、Python tkinter に出すか
- 立花 Phase 1 のログイン画面は tkinter（Python 単独モード方針との整合）
- 発注 UI も Python tkinter にすれば iced に発注関連コードが入らず、長期方針と一貫する
- 一方、暗号資産 venue の発注 UI を後で iced に追加するなら不整合になる

→ **長期方針を「全 venue の発注 UI は Python 側」に統一**するのが筋。確認したい。

---

## 着手後に決めれば良い事項

- バックテスト性能 SLA の具体値（spec.md §3.3 で「1 年・日足・30 秒」を仮置き、N1 終盤で実測して確定）
- nautilus の `MessageBus` を IPC イベントに 1:1 で写すか、要約するか
- ナラティブ `reasoning` テキストの自動生成: nautilus の `Strategy.log` を流用するか、ユーザーが明示的に書くか
