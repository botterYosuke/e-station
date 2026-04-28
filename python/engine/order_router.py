"""N1.5: 注文ディスパッチャ — live / replay モードに応じて注文を振り分ける。

live  → tachibana_orders.submit_order(...)
replay → tachibana_orders_replay.jsonl WAL に記録（BacktestEngine への投入は N1.11 実装時）

この時点では replay 側の BacktestEngine.process_order() 統合は未実装。
N1.11（streaming 経路）と合わせて完成する。
本モジュールは orders の WAL 記録と client_order_id 名前空間分離のみを担う。

CLMZanKaiKanougaku ガード（D9.6）:
    replay モードでは CLMZanKaiKanougaku / CLMZanShinkiKanoIjiritu への HTTP 呼出しを
    行わない。submit_order_replay の実行経路には fetch_buying_power / fetch_credit_buying_power
    を呼ぶコードが一切存在しないことが本ファイルのレビュー不変条件。
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

from engine.exchanges.tachibana_orders import (
    NautilusOrderEnvelope,
    SubmitOrderResult,
    submit_order as _tachibana_submit_order,
)

log = logging.getLogger(__name__)

# REPLAY client_order_id プレフィックス（名前空間分離）
_REPLAY_PREFIX = "REPLAY-"

# replay WAL のデフォルトファイル名
_REPLAY_WAL_FILENAME = "tachibana_orders_replay.jsonl"


# ---------------------------------------------------------------------------
# WAL helpers (replay 専用)
# ---------------------------------------------------------------------------


def _current_ts_ms() -> int:
    return int(time.time() * 1000)


def _write_replay_wal_submit(
    wal_path: Path,
    client_order_id: str,
    envelope: NautilusOrderEnvelope,
) -> None:
    """replay WAL に submit 行を書く（JSONL 形式、tachibana_orders.py と同フォーマット）。

    第二暗証番号は replay ルートでは存在しないため WAL にも含まれない。
    fsync で crash safety を確保する。
    """
    record = {
        "phase": "submit",
        "ts": _current_ts_ms(),
        "client_order_id": client_order_id,
        "instrument_id": envelope.instrument_id,
        "order_side": envelope.order_side,
        "order_type": envelope.order_type,
        "quantity": envelope.quantity,
    }
    line = json.dumps(record, ensure_ascii=True)

    wal_path.parent.mkdir(parents=True, exist_ok=True)
    # "a" append モードで開く（既存 WAL に追記）
    f = open(wal_path, "a", encoding="utf-8")  # noqa: SIM115
    try:
        f.write(line + "\n")
        f.flush()
        os.fsync(f.fileno())
    finally:
        f.close()


# ---------------------------------------------------------------------------
# live ディスパッチ
# ---------------------------------------------------------------------------


async def submit_order_live(
    envelope: NautilusOrderEnvelope,
    *,
    session: Any,
    second_password: str,
    p_no_counter: Optional[Any] = None,
    wal_path: Optional[Path] = None,
    request_key: int = 0,
) -> SubmitOrderResult:
    """live モード: tachibana_orders.submit_order(...) に委譲する。

    WAL は tachibana_orders.jsonl（既存）を使う。
    CLMZanKaiKanougaku 呼出しはこの関数の責務ではなく server.py で行う（変更なし）。
    """
    return await _tachibana_submit_order(
        session,
        second_password,
        envelope,
        p_no_counter=p_no_counter,
        wal_path=wal_path,
        request_key=request_key,
    )


# ---------------------------------------------------------------------------
# replay ディスパッチ
# ---------------------------------------------------------------------------


def submit_order_replay(
    envelope: NautilusOrderEnvelope,
    *,
    wal_path: Optional[Path] = None,
) -> dict[str, Any]:
    """replay モード: tachibana_orders_replay.jsonl WAL に記録する。

    BacktestEngine への投入は **no-op**（N1.11 で統合）。

    CLMZanKaiKanougaku への HTTP 呼出しを **行わない**（D9.6 明示ガード）。
    この関数の実行経路には fetch_buying_power / fetch_credit_buying_power を
    呼ぶコードが存在しない。

    Args:
        envelope: 注文エンベロープ
        wal_path: WAL ファイルパス。省略時は tachibana_orders_replay.jsonl（カレントディレクトリ）

    Returns:
        {"status": "accepted", "client_order_id": "REPLAY-...", "venue": "replay"}
    """
    # D9.6: REPLAY 中に立花 CLMZanKaiKanougaku HTTP を skip する明示ガード
    # (CLMZanKaiKanougaku / CLMZanShinkiKanoIjiritu は replay ルートには存在しない)

    # client_order_id に REPLAY- プレフィックスを付与して名前空間を分離する
    replay_client_order_id = _REPLAY_PREFIX + envelope.client_order_id

    # WAL パスのデフォルト解決
    resolved_wal_path = wal_path if wal_path is not None else Path(_REPLAY_WAL_FILENAME)

    # WAL に submit 行を書く（fsync 必須）
    _write_replay_wal_submit(
        resolved_wal_path,
        client_order_id=replay_client_order_id,
        envelope=envelope,
    )

    log.info(
        "submit_order_replay: WAL recorded client_order_id=%s instrument_id=%s",
        replay_client_order_id,
        envelope.instrument_id,
    )

    # BacktestEngine.process_order() への投入は N1.11 で実装する（現時点は no-op）
    return {
        "status": "accepted",
        "client_order_id": replay_client_order_id,
        "venue": "replay",
    }


# ---------------------------------------------------------------------------
# ルーター
# ---------------------------------------------------------------------------


async def route_submit_order(
    mode: str,
    envelope: NautilusOrderEnvelope,
    *,
    session: Any = None,
    second_password: Optional[str] = None,
    p_no_counter: Optional[Any] = None,
    wal_path: Optional[Path] = None,
    request_key: int = 0,
) -> Any:
    """mode に応じて submit_order_live または submit_order_replay を呼ぶ。

    Args:
        mode: "live" | "replay"
        envelope: 注文エンベロープ
        session: TachibanaSession（live モードで必須）
        second_password: 第二暗証番号（live モードで必須）
        p_no_counter: PNoCounter（live モードで使用）
        wal_path: WAL ファイルパス（両モードで使用）
        request_key: xxh3_64 ハッシュ（live モードの WAL に記録）

    Returns:
        live:   SubmitOrderResult
        replay: {"status": "accepted", "client_order_id": "REPLAY-...", "venue": "replay"}

    Raises:
        ValueError: 未知の mode 文字列
    """
    if mode == "live":
        return await submit_order_live(
            envelope,
            session=session,
            second_password=second_password,
            p_no_counter=p_no_counter,
            wal_path=wal_path,
            request_key=request_key,
        )
    elif mode == "replay":
        return submit_order_replay(envelope, wal_path=wal_path)
    else:
        raise ValueError(f"route_submit_order: unknown mode {mode!r}")
