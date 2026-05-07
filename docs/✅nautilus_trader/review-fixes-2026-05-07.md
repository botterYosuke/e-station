# review-fixes — docs/✅nautilus_trader/🔵execute-live-strategy.md

## ラウンド 1（2026-05-07）

### 統一決定

1. `CurrentEngineState::Stopping` は既存 variant を replay/live 共用で維持。新規追加は `Trading` のみ
2. `_run()` 内に `elif self._mode == "live":` 分岐を追加して `runner.start_live()` を呼ぶ形に修正
3. `LiveSession.run()` シグネチャに `instrument_id: str` を必須パラメータとして追加
4. `LiveSession.__init__` に `second_password: str | None = None` を追加。`run()` で None なら RuntimeError
5. `node.stop()` の sync/async は spike 確認必須と計画に明記

### Findings 一覧

| ID | 重要度 | 対象ファイル | タイトル | 修正概要 |
|---|---|---|---|---|
| A1 | HIGH | 🔵execute-live-strategy.md | LiveStateName が schemas.py と不一致 | Phase 1 に現在値3値→+2値であることを明示 |
| A2 | HIGH | 🔵execute-live-strategy.md | CurrentEngineState::Stopping variant 衝突 | dto.rs への追加を Trading のみに修正 |
| A3 | HIGH | 🔵execute-live-strategy.md / architecture.md | SCHEMA_MINOR=17 の根拠不明確 | 現在値16+1=17の根拠を明記、architecture.md の古い値を修正 |
| A4 | MEDIUM | spec.md | EngineStartConfig Optional 化が未記述 | spec.md に live/replay 排他バリデーション注記を追加 |
| A5 | MEDIUM | architecture.md | LiveBuyingPower が IPC テーブル未登場 | EngineEvent テーブルに LiveBuyingPower を追記 |
| A6 | LOW | 🔵execute-live-strategy.md | LiveSession のファイル配置が不明確 | Phase 5 冒頭に replay_session.py 同居であることを注記 |
| B1 | HIGH | 🔵execute-live-strategy.md | _run() の live 分岐が擬似コードで曖昧 | _run() 内 elif live: 形に修正 |
| B2 | HIGH | 🔵execute-live-strategy.md | _run() 外 result_holder で initial_cash undefined | replay ガードを計画に明示 |
| B3 | HIGH | 🔵execute-live-strategy.md | start_live() stub の assert 残留リスク | stub 全体削除を Phase 4 冒頭に明記 |
| B4 | MEDIUM | 🔵execute-live-strategy.md | p_no_counter 供給元未記載 | PNoCounter() 新規生成を Phase 4 §2 に追記 |
| B5 | LOW | 🔵execute-live-strategy.md | _send_ready() のメソッド名誤記 | _handshake() 内に修正 |
| B6 | MEDIUM | 🔵execute-live-strategy.md | node.run()/stop() の呼び出し方不正確 | spike 確認必須と asyncio.run() パターンを明記 |
| B7 | LOW | — | LiveSession.run() は未実装で整合問題なし | 対応不要 |
| C1 | HIGH | 🔵execute-live-strategy.md | stop_event ポーリング経路未定義 | threading.Thread + call_soon_threadsafe パターンを Phase 4 §9 に追記 |
| C2 | HIGH | 🔵execute-live-strategy.md | node.stop() sync/async 未確認 | spike 確認チェックを Phase 4 に追記 |
| C3 | HIGH | 🔵execute-live-strategy.md | instrument_id 供給元が ... のまま | LiveSession.run() に instrument_id: str を追加 |
| C4 | HIGH | 🔵execute-live-strategy.md | second_password in-process 経路が要確定 | LiveSession.__init__ パラメータ追加で確定 |
| C5 | MEDIUM | 🔵execute-live-strategy.md | STOPPING 遷移が Phase 2 未記述 | _handle_stop_engine() で TRADING→STOPPING を Phase 2 §6 に追記 |
| C6 | MEDIUM | 🔵execute-live-strategy.md | FD/EC queue クリーンアップ未定義 | finally ブロックにドレイン処理を追記 |
| C7 | MEDIUM | 🔵execute-live-strategy.md | warm_up() 失敗時の回復パス未定義 | try/except で Error emit を Phase 4 §5 に追記 |
| C8 | LOW | 🔵execute-live-strategy.md | _replay_streaming_fills.clear() の live 混入 | replay ガードを Phase 2 に追記 |
| C9 | LOW | 🔵execute-live-strategy.md | LiveBuyingPower レート制限の実装方式未定義 | asyncio.call_later + pending フラグを Phase 4 に追記 |
| D1 | HIGH | 🔵execute-live-strategy.md | max_qty=None invalid_config テスト欠落 | Phase 8 に追加 |
| D2 | HIGH | 🔵execute-live-strategy.md | initial_cash=None 回帰テスト欠落 | Phase 8 に追加 |
| D3 | HIGH | 🔵execute-live-strategy.md | second_password=None テスト欠落 | Phase 8 に追加 |
| D4 | MEDIUM | 🔵execute-live-strategy.md | STOPPING 遷移・busy ガードテスト欠落 | Phase 8 に追加 |
| D5 | MEDIUM | 🔵execute-live-strategy.md | bridge thread 停止・join テスト欠落 | Phase 8 に追加 |
| D6 | MEDIUM | 🔵execute-live-strategy.md | fd_queue.Full warning テスト欠落 | Phase 8 に追加 |
| D7 | MEDIUM | 🔵execute-live-strategy.md | login() 前 run() → RuntimeError テスト欠落 | Phase 8 に追加 |
| D8 | MEDIUM | 🔵execute-live-strategy.md | attach 経路 SecondPasswordRequired 伝播テスト欠落 | Phase 8 に追加 |
| D9 | MEDIUM | 🔵execute-live-strategy.md | stop() in-process テスト欠落 | Phase 8 に追加 |
| D10 | LOW | 🔵execute-live-strategy.md | extra="forbid" バリデーションテスト欠落 | Phase 8 に追加 |
| D11 | LOW | 🔵execute-live-strategy.md | 不正 initial_cash replay テスト欠落 | Phase 8 に追加 |
| D12 | LOW | 🔵execute-live-strategy.md | live_sample.py 作成計画未記載 | 変更ファイル一覧に追記 |

---

## ラウンド 2（2026-05-07）

### 統一決定

- StopEngine live 分岐: `_check_live_state("StopEngine", LiveState.TRADING, ws=ws)` → STOPPING 遷移の疑似コードを Phase 2 §6 に追記
- ExecutionMarker の `append` にも `if self._mode == "replay":` ガードを Phase 2 §8 に追記

### Findings 一覧

| ID | 重要度 | 対象ファイル | タイトル | 修正概要 |
|---|---|---|---|---|
| R2-C-N6 | HIGH | 🔵execute-live-strategy.md | _handle_stop_engine() live 分岐の guard 条件欠落 | Phase 2 §6 に具体的な guard 疑似コードを追記 |
| R2-C-N8 | MEDIUM | 🔵execute-live-strategy.md | ExecutionMarker の append にも replay ガード未明記 | Phase 2 §8 に append ガードを追記 |
| R2-C-N9 | MEDIUM | 🔵execute-live-strategy.md | stop() の strategy_id 参照元未記載 | self._strategy_id の格納を Phase 5 に追記 |
| R2-4 | MEDIUM | 🔵execute-live-strategy.md | D11 の assert 内容未記載 | Phase 8 D11 に assert 概要を追記 |
| R2-C-N10 | LOW | 🔵execute-live-strategy.md | result_holder[0] = runner.start_live() 不要代入 | Phase 2 §4 から代入プレフィックスを削除 |

---

## ラウンド 3（2026-05-07）

サニティチェック 7 項目すべて問題なし。HIGH/MEDIUM ゼロ。**収束。**
