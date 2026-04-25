---
name: bug-postmortem
description: 不具合が見つかったとき、既存テストで発見できなかった理由を分析し、必要なテストを追加・検証するスキル。過去の見逃しパターンを蓄積して自己進化する。
origin: ECC (e-station 向けカスタム)
---

# Bug Postmortem — テストカバレッジ自己進化スキル

不具合が修正されたあと、このスキルを起動する。

```
/bug-postmortem
```

---

## なぜ「自己進化」か

同じクラスの不具合は繰り返す。過去の見逃しを `MISSES.md` に記録し、
次の分析でそのパターンを照合することで、テストの盲点を段階的に減らしていく。

---

## フェーズ概要

```
Phase 1: 分析    — 既存テストがなぜ見逃したか
Phase 2: 判断    — テストを追加すべきか・どの層か
Phase 3: 実装    — テストを書く
Phase 4: 検証    — テストを実行して動作確認
Phase 5: 記録    — MISSES.md に知見を追記
```

---

## Phase 1: 分析（Analysis Agent）

### 入力として収集する情報

```
1. 不具合の概要（エラーメッセージ、再現手順）
2. 修正したコードの diff
3. 既存テストの一覧と範囲
4. 実行ログ（あれば）
```

### 分析の問い

以下を順に答える。

**① 既存テストの「守備範囲」**
```
- ユニットテスト: どのモジュールが対象か
- 統合テスト: 実際のサーバー/クライアントを使っているか
- E2E テスト: どのパスを通っているか
- smoke.sh: どの障害パターンを検査しているか
```

**② 見逃しの構造的原因**（MISSES.md の過去パターンと照合）

| パターン | 説明 |
|---------|------|
| **Mock 置換漏れ** | テストがモック（tokio-tungstenite 等）を使い、実実装の挙動を再現できていない |
| **同一言語テスト** | Python→Python, Rust→Rust で完結し、言語境界の挙動が検証されていない |
| **ログ検査漏れ** | smoke.sh が正しい障害パターンを grep していない |
| **再接続隠蔽** | 自動リカバリが成功するため、初回失敗が観測ウィンドウに残らない |
| **デフォルト値前提** | ライブラリのデフォルト設定が将来変わることを想定していない |
| **タイミング依存** | 固定 sleep に依存し、競合状態が確率的にしか現れない |

**③ 「あれば検出できたテスト」の特定**
```
どの層に、どんな前提で書けば今回の不具合を捕まえられたか。
1 〜 3 個に絞って具体的に答える。
```

---

## Phase 2: 判断（Decision）

以下の基準でテスト追加の必要性を評価する。

### 追加すべき条件（いずれかを満たす）

- [ ] 同じパターンの不具合が将来も起こりうる
- [ ] 既存テストスイートに構造的な盲点がある（Mock 置換漏れなど）
- [ ] 修正が `compression=None` のような「設定値の削除で元に戻る」種類である
- [ ] 症状がログにしか現れず、smoke.sh が拾えていない

### 不要な条件（すべてに該当）

- [ ] 一度限りの外部環境起因（API の仕様変更など）であり再発性がない
- [ ] 既存テストが既に同等のカバレッジを持っている
- [ ] テストコストが効果を大きく上回る（ハードウェア依存、実時間待機 > 60s 等）

### 決定の出力

```
判断: 追加する / しない
理由: <1 文>
追加する場合: <層> + <場所> + <テスト名>
```

---

## Phase 3: 実装（Test Writer Agent）

### テスト層の選択基準

| 層 | ファイル | 使う状況 |
|----|---------|---------|
| Python 単体テスト | `python/tests/test_*.py` | Python サーバー単体の挙動を検証 |
| Rust 統合テスト | `engine-client/tests/*.rs` | Rust クライアントのプロトコル検証 |
| smoke.sh チェック | `tests/e2e/smoke.sh` | 実行時ログパターンの検出 |
| E2E シナリオ | `tests/e2e/*.sh` | 起動〜接続〜ストリームの結合 |

### 実装の必須条件

1. **リグレッション検証**: 修正を戻した状態でテストが FAIL することを確認する
   ```bash
   # 修正前に戻す → テスト実行 → FAIL を確認
   # 修正を再適用 → テスト実行 → PASS を確認
   ```

2. **エラーメッセージに修正方法を書く**
   ```python
   assert not has_deflate, (
       "Server negotiated permessage-deflate.\n"
       "Fix: add compression=None to websockets.serve() in engine/server.py."
   )
   ```

3. **テスト名で「何を守っているか」を表現する**
   ```
   good: test_server_refuses_permessage_deflate
   bad:  test_compression
   ```

### Python テストのテンプレート

```python
"""<不具合の概要と修正内容を 1 段落で説明>

Regression test for: <コードの場所と問題>
Fix: <修正内容>
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from engine.server import DataEngineServer
from engine.schemas import SCHEMA_MAJOR, SCHEMA_MINOR


@pytest.fixture
async def server_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    mock_worker = MagicMock()
    mock_worker.prepare = AsyncMock(return_value=None)

    patches = [
        patch("engine.server.BinanceWorker", return_value=mock_worker),
        patch("engine.server.BybitWorker", return_value=mock_worker),
        patch("engine.server.HyperliquidWorker", return_value=mock_worker),
        patch("engine.server.MexcWorker", return_value=mock_worker),
        patch("engine.server.OkexWorker", return_value=mock_worker),
    ]
    for p in patches:
        p.start()
    server = DataEngineServer(port=port, token="test-token")
    task = asyncio.create_task(server.serve())
    await asyncio.sleep(0.1)
    yield port, "test-token"
    server.shutdown()
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass
    for p in patches:
        p.stop()


@pytest.mark.asyncio
async def test_<name>(server_port):
    """<何を守っているか一文で>"""
    port, token = server_port
    # ...
```

### smoke.sh チェックのテンプレート

```bash
# <不具合クラスの説明>
check "<ログパターン>" "$RUST_LOG_FILE" "<ラベル>"

# 接続安定性チェック（再接続ループの検出）
count=$(grep -c "<marker>" "$RUST_LOG_FILE" 2>/dev/null | tr -d '\r\n[:space:]')
count=${count:-0}
if (( count > <閾値> )); then
    log "FAIL: <説明> ($count hits)"
    fail=1
fi
```

---

## Phase 4: 検証（Verification）

### 必須手順

```bash
# 1. 修正を一時的に戻す（リグレッション確認）
#    → テストが FAIL することを確認

# 2. 修正を元に戻す
#    → テストが PASS することを確認

# Python テストの実行
uv run pytest python/tests/test_<name>.py -v

# Rust テストの実行
cargo test -p flowsurface-engine-client <test_name> -- --nocapture

# smoke.sh の実行（30 秒）
bash tests/e2e/smoke.sh
```

### 合格基準

- [ ] 修正前: 対象テストが FAIL する
- [ ] 修正後: 対象テストが PASS する
- [ ] 他のテストに影響がない（`uv run pytest python/tests/` が全 PASS）
- [ ] smoke.sh が新しいチェックで PASS する

---

## Phase 5: 記録（Update MISSES.md）

テストを追加したら、このディレクトリの `MISSES.md` に追記する。

```markdown
## YYYY-MM-DD — <不具合の一行要約>

**見逃しパターン**: <パターン名>
**根本原因**: <技術的な説明>
**追加したテスト**: <ファイル::テスト名>
**教訓**: <次の分析で活かせる一般化されたルール>
```

---

## 出力レポート形式

スキル完了時に以下を出力する。

```
BUG POSTMORTEM REPORT
=====================

不具合: <一行要約>

[Phase 1] 見逃しの原因
  パターン: <パターン名>
  構造的理由: <説明>

[Phase 2] テスト追加の判断
  判断: 追加する
  理由: <理由>

[Phase 3] 追加したテスト
  - <ファイルパス>::<テスト名>  [Python/Rust/smoke]
  - <ファイルパス> (check 行)   [smoke.sh]

[Phase 4] 検証結果
  修正前: FAIL ✓
  修正後: PASS ✓
  既存テスト影響: なし ✓

[Phase 5] MISSES.md 更新
  追記済み
```

---

## 関連スキル

- `rust-testing` — Rust テストパターン
- `e2e-testing` — E2E テストパターン（flowsurface GUI）
- `tdd-workflow` — TDD フロー（新機能・修正時）