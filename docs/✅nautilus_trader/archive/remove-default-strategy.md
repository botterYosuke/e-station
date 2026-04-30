# デフォルトストラテジー・デフォルトパラメータ廃止計画

## 背景・動機

現状、`scripts/replay_dev_load.sh` および `scripts/run-replay-debug.sh` には
以下のデフォルト値が埋め込まれている：

| 項目 | デフォルト値 |
|---|---|
| インスツルメント | `1301.TSE` |
| 開始日 | `2024-01-04` |
| 終了日 | `2024-01-05` |
| ストラテジー | `BuyAndHoldStrategy`（`strategy_file` 未指定時の fallback） |

**問題**: ストラテジーを指定せずに起動しても黙って `BuyAndHoldStrategy` が走るため、
「自分のストラテジーが使われていない」ことに気が付かないまま結果を信頼してしまうリスクがある。
意図しないデフォルト動作はユーザーの誤解を招く。

## ゴール

- `strategy_file` を指定せずに `/api/replay/start` を呼んだ場合、HTTP 400 を返す
- `scripts/replay_dev_load.sh` のデフォルト値（インスツルメント・日付）も削除し、
  未指定時はエラーで止める
- `scripts/run-replay-debug.sh` も同様

---

## 変更対象と内容

### ✅ 1. `src/replay_api.rs` — HTTP 400 バリデーション追加（**最優先**）

HTTP 応答を決めているのは Rust 側の `handle_replay_start()` である。
`ReplayStartBody` に `strategy_file` フィールドはすでに存在する（`Option<String>`）が、
未指定・空文字を通過させているため、バリデーションブロック（③）に追加する。

```rust
// src/replay_api.rs ③ Validate fields に追記
match &parsed.strategy_file {
    None | Some(s) if s.is_empty() => {
        write_error(stream, 400, "Bad Request", "strategy_file is required").await;
        return;
    }
    _ => {}
}
```

`EngineError` の `code` → HTTP ステータスマップ（現状 `mode_mismatch` のみ 400）にも
`"strategy_file_required"` / `"invalid_config"` を 400 に追加する：

```rust
// src/replay_api.rs L838 付近
let status = match code.as_str() {
    "mode_mismatch" | "strategy_file_required" | "invalid_config" => 400,
    _ => 503,
};
```

**Rust テスト追加**: `strategy_file` なし・空文字で POST したとき HTTP 400 が返ること。

---

### ✅ 2. `scripts/replay_dev_load.sh` — 配線修正 + デフォルト値削除

#### 2a. `strategy_file` の移動: `/api/replay/load` → `/api/replay/start`

現行スクリプトは `strategy_file` を `/api/replay/load` の JSON に入れているが
（[replay_dev_load.sh:45-56](../../../scripts/replay_dev_load.sh#L45-L56)）、
`/api/replay/start` には渡していない
（[replay_dev_load.sh:72-83](../../../scripts/replay_dev_load.sh#L72-L83)）。

fallback 削除後は `/api/replay/start` に `strategy_file` がないと必ず失敗する。
`strategy_file` を `/api/replay/start` の JSON に移し、`/api/replay/load` からは外す。

```bash
# /api/replay/load body — strategy_file を除去
load_body=$(python -c "
import json, sys
print(json.dumps({
    'instrument_id': sys.argv[1],
    'start_date':    sys.argv[2],
    'end_date':      sys.argv[3],
    'granularity':   sys.argv[4],
}))
" "$INSTRUMENT_ID" "$START_DATE" "$END_DATE" "$GRANULARITY")

# /api/replay/start body — strategy_file を追加
start_body=$(python -c "
import json, sys
d = {
    'instrument_id': sys.argv[1],
    'start_date':    sys.argv[2],
    'end_date':      sys.argv[3],
    'granularity':   sys.argv[4],
    'strategy_id':   sys.argv[5],
    'initial_cash':  sys.argv[6],
}
if sys.argv[7]:
    d['strategy_file'] = sys.argv[7]
print(json.dumps(d))
" "$INSTRUMENT_ID" "$START_DATE" "$END_DATE" "$GRANULARITY" \
  "$STRATEGY_ID" "$INITIAL_CASH" "$STRATEGY_FILE")
```

#### 2b. デフォルト値削除・ガード追加

```bash
# Before
INSTRUMENT_ID="${REPLAY_INSTRUMENT_ID:-1301.TSE}"
START_DATE="${REPLAY_START_DATE:-2024-01-04}"
END_DATE="${REPLAY_END_DATE:-2024-01-05}"

# After（未指定時はエラーで終了）
INSTRUMENT_ID="${REPLAY_INSTRUMENT_ID:?REPLAY_INSTRUMENT_ID is required}"
START_DATE="${REPLAY_START_DATE:?REPLAY_START_DATE is required}"
END_DATE="${REPLAY_END_DATE:?REPLAY_END_DATE is required}"
```

`strategy_file` 未指定（空文字）時もエラーで終了する：

```bash
if [[ -z "$STRATEGY_FILE" ]]; then
    echo "[replay-load] ERROR: strategy_file is required"
    echo "  Usage: bash scripts/replay_dev_load.sh <strategy.py>"
    exit 1
fi
```

---

### ✅ 3. `scripts/run-replay-debug.sh` — ガード追加

`STRATEGY_FILE` が空の場合にエラーで終了するガードを追加。

---

### ✅ 4. `python/engine/nautilus/engine_runner.py` — fallback 削除

`_make_replay_strategy()` の `strategy_file is None` 時の
`BuyAndHoldStrategy` fallback を削除し、`ValueError` を raise する。

空文字も `None` と同様に弾く（HTTP 直叩きで `"strategy_file": ""` が来るケース）：

```python
# After
def _make_replay_strategy(..., strategy_file=None, ...):
    if not strategy_file:   # None と "" を両方弾く
        raise ValueError(
            "strategy_file is required. "
            "Specify a .py file path via POST /api/replay/start."
        )
    return _load_user_strategy(strategy_file, strategy_init_kwargs)
```

---

### ✅ 5. `python/engine/server.py` — 早期バリデーション追加

`StartEngine` ハンドラで `strategy_file` が `None` または空文字の場合、
IPC 送信前に `EngineError(code="strategy_file_required")` を返す。
これにより Rust 側の `EngineStartOutcome::EngineError { code, .. }` 経由で
400 が返る（§1 のマップ追加と連動）。

---

### ✅ 6. `python/engine/nautilus/strategies/buy_and_hold.py` — 削除

内部 fallback としての存在意義がなくなるため削除する。

---

### ✅ 7. 連鎖削除・修正

| ファイル | 対応 |
|---|---|
| `python/engine/nautilus/engine_runner.py` | `BuyAndHoldStrategy` import 削除 |
| `python/tests/test_nautilus_buy_and_hold.py` | ファイルごと削除 |
| `python/tests/test_strategy_loader.py` | `buy_and_hold` 参照箇所を削除 |

---

### ✅ 8. テスト追加

| レイヤー | テスト内容 |
|---|---|
| Rust（`src/` unit test） | `strategy_file` なし・空文字で POST → HTTP 400 |
| Python unit | `_make_replay_strategy(strategy_file=None)` → `ValueError` |
| Python unit | `_make_replay_strategy(strategy_file="")` → `ValueError` |
| Python unit | `server.py` の `StartEngine` ハンドラで `strategy_file=None` → `EngineError` |

---

## 実装順序（依存関係）

fallback を先に消すと dev helper の入口配線が追随せず即壊れるため、以下の順で入れる：

```
1. src/replay_api.rs          HTTP バリデーション + エラーマップ + Rust テスト
2. scripts/replay_dev_load.sh strategy_file 配線修正 + デフォルト値削除
3. scripts/run-replay-debug.sh ガード追加
4. python/engine/server.py    早期バリデーション追加
5. python/engine/nautilus/engine_runner.py fallback 削除
6. 削除: buy_and_hold.py + 関連テスト
7. Python テスト追加・更新
```

---

## 影響範囲まとめ

| ファイル | 変更種別 |
|---|---|
| `src/replay_api.rs` | バリデーション追加・エラーマップ拡張・Rust テスト追加 |
| `scripts/replay_dev_load.sh` | `strategy_file` 配線修正 + デフォルト値削除 |
| `scripts/run-replay-debug.sh` | ガード追加 |
| `python/engine/server.py` | 早期バリデーション追加 |
| `python/engine/nautilus/engine_runner.py` | fallback 削除（`not strategy_file` で弾く）、import 削除 |
| `python/engine/nautilus/strategies/buy_and_hold.py` | **削除** |
| `python/tests/test_nautilus_buy_and_hold.py` | **削除** |
| `python/tests/test_strategy_loader.py` | buy_and_hold 参照箇所を削除 |
| `python/tests/` | 新規テスト追加 |

## 注意

- `docs/example/buy_and_hold.py` はユーザー向けサンプルとして残す
  （`strategy_file=docs/example/buy_and_hold.py` と明示指定すれば使える）

---

## 実装メモ

### テスト用ノーオペレーション戦略の新規作成

既存テスト群（`test_engine_runner_replay.py` など）は `strategy_file` なしで動く前提で
書かれていたため、`strategy_file` 必須化に伴い全テストを更新した。
その際、テスト用の軽量戦略として `python/tests/fixtures/test_strategy.py` を新規作成した。

- 引数なしで初期化できる no-op 戦略（`NopStrategy`）を定義
- `NautilusKernel` への登録に必要な最低限のインタフェースのみ実装
- `buy_and_hold.py` を import するテストは全て `test_strategy.py` を参照するよう変更

### 影響を受けた既存テストファイル

以下のファイルは `strategy_file` 必須化に合わせて更新済み：

- `python/tests/test_engine_runner_replay.py`
- `python/tests/test_flowsurface_env_with_nautilus.py`
- `python/tests/test_nautilus_smoke.py`
- `python/tests/test_replay_speed.py`
- `python/tests/test_replay_benchmark.py`
- `python/tests/test_nautilus_determinism.py`
- `python/tests/test_nautilus_determinism_tick.py`
- `python/tests/test_server_engine_dispatch.py`
- `python/tests/test_strategy_live_replay_smoke.py`

### テスト結果（実装完了時点）

- Python テスト: **1321 passed, 2 skipped, 0 failed**
- Rust テスト（bin）: **213 passed, 0 failed**
