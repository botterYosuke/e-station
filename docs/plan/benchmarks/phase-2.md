# Phase 2 ベンチマーク — IPC レイテンシ・復旧時間・depth 再同期

計測日: 2026-04-24
対象ブランチ: `phase-2/engine-client`
環境: Windows 11 (開発機)、`cargo build --release`

---

## 1. 合格ライン（implementation-plan.md §計測指標と合格ライン より）

| 指標 | 合格ライン |
|---|---|
| IPC 追加レイテンシ（中央値） | **< 2 ms** |
| IPC 追加レイテンシ（p99） | **< 10 ms** |
| Python クラッシュ → 自動復旧完了 | **< 3 秒** |
| depth 再同期（DepthGap → 板復元） | **< 500 ms** |
| CPU 使用率（Python + Rust 合計） | 現行 Rust 直結の **+30% 以内** |
| depth gap 検知漏れ | **0** |

---

## 2. 計測方法

### 2.1 IPC 追加レイテンシ

**手順**:
1. `python -m engine` を起動し Binance BTCUSDT に subscribe。
2. `--data-engine-url ws://127.0.0.1:<port>` で flowsurface を起動。
3. Python 側で `EngineEvent.Trades` 送出時刻 (`sent_at_ms`) をペイロードに付与（デバッグ用 flag）。
4. Rust 側で受信時刻 `recv_at_ms = SystemTime::now()` を記録し `recv_at_ms - sent_at_ms` を集計。
5. 1000 サンプル以上でパーセンタイルを算出。

**現時点のステータス**: Python 側のタイムスタンプ注入は未実装（Phase 2 計測用拡張として今後追加）。
以下の値はベースライン計測後に記入する。

| パーセンタイル | 計測値（ms） | 合格 |
|---|---|---|
| p50 (中央値) | - | - |
| p95 | - | - |
| p99 | - | - |
| max | - | - |

### 2.2 Python クラッシュ → 自動復旧

**手順**:
1. `--data-engine-url` なしで起動（将来 spawn モード実装後に適用）。
   または `ProcessManager` を直接呼び出す統合テストで計測。
2. `taskkill /F /PID <python_pid>` で Python を強制終了。
3. 最初の購読再送完了まで (`on_ready` コールバック呼び出し) の経過時間を記録。

| 試行 | Python kill → on_ready (ms) | 合格 (< 3000ms) |
|---|---|---|
| 1 | - | - |
| 2 | - | - |
| 3 | - | - |

### 2.3 depth 再同期（DepthGap → 板復元）

**手順**:
1. Binance BTCUSDT depth stream を開く。
2. Python の `DepthGap` 送出を手動でトリガー（または WS 切断再接続で誘発）。
3. `DepthGap` イベント受信から次の `DepthSnapshot` 適用 (`DepthTracker.on_snapshot`) まで。

| 試行 | DepthGap → Snapshot 適用 (ms) | 合格 (< 500ms) |
|---|---|---|
| 1 | - | - |
| 2 | - | - |
| 3 | - | - |

### 2.4 CPU / メモリ

計測コマンド（Windows）:
```powershell
# flowsurface プロセス
Get-Process flowsurface | Select-Object CPU, WorkingSet64
# python データエンジン
Get-Process python | Select-Object CPU, WorkingSet64
```

| 状態 | flowsurface CPU% | Python CPU% | 合計 | ベースライン比 |
|---|---|---|---|---|
| アイドル（1 ticker） | - | - | - | - |
| BTCUSDT trade+depth+kline × 5 | - | - | - | - |

---

## 3. 障害試験手順（手動）

### 3.1 前提

- `python -m engine` が起動済みで環境変数 `FLOWSURFACE_ENGINE_TOKEN` / `FLOWSURFACE_ENGINE_PORT` が設定済み。
- `flowsurface --data-engine-url ws://127.0.0.1:<port>` で起動。
- Binance BTCUSDT のチャートが描画されていること。

### 3.2 手順

1. **正常確認**: Binance チャートが trade/depth でリアルタイム更新されていること。
2. **強制終了**: 別ターミナルで `taskkill /F /IM python.exe` または
   ```
   Stop-Process -Id (Get-Process python).Id -Force
   ```
3. **UI 確認**: 「データエンジン再起動中 — チャートは復旧後に自動更新されます」の通知が表示されること。
   チャートは最後の状態を維持（消えないこと）。
4. **自動復旧確認**（将来 spawn モード実装後）:
   - `ProcessManager` が指数バックオフで Python を再 spawn。
   - 3 秒以内に再接続 → 購読再送 → チャートが再描画されること。
5. **depth 整合性確認**: 復旧後に板の bid/ask が正常な値を示すこと（スプレッドが非現実的に広くないこと）。

### 3.3 現時点の制約

`--data-engine-url` は外部エンジンへの接続のみ（Python を自動 spawn しない）。
Python kill 後の自動復旧は `ProcessManager::run_with_recovery` の実装を
`src/main.rs` の spawn モードとして配線した後に検証可能となる（フェーズ 2 後半）。

---

## 4. 結果サマリー

> **計測保留**: Python spawn モードの配線完了後に実測値を記入する。
> 上記手順を実施し、合格ラインを満たすことを確認したうえで ✅ を記入する。

| 指標 | 結果 | 合格 |
|---|---|---|
| IPC p50 レイテンシ | TBD | ⬜ |
| IPC p99 レイテンシ | TBD | ⬜ |
| 自動復旧時間 | TBD | ⬜ |
| depth 再同期 | TBD | ⬜ |
| CPU 増加率 | TBD | ⬜ |
| depth gap 検知漏れ | TBD | ⬜ |

---

## 5. 未達時の対応

- **レイテンシ / CPU 不足** → `spec.md §4.3.1` のバイナリ化（MessagePack + 固定小数 i64）を適用。
- **慢性的な性能差** → `spec.md §7.1` 案 C（`native-backend` optional feature）を再検討。
- **depth gap 漏れ** → `DepthTracker` のシーケンス検証ロジックを精査し統合テストを追加。
