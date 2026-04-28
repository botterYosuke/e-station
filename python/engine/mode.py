"""N1.13: 起動時モード固定 (`live` / `replay`) の policy.

Hello で受け取った `mode` を引数として受け取り、HTTP API / IPC ディスパッチの
許可可否を返す純粋関数を提供する。server.py から呼ばれる薄いヘルパー。

`mode` は起動後に切り替えない (D8 起動時固定方針)。
"""

from __future__ import annotations

from typing import Literal

Mode = Literal["live", "replay"]


def is_replay_path_allowed(mode: Mode, path: str) -> bool:
    """`/api/replay/*` は replay モードでのみ許可する。"""
    if not path.startswith("/api/replay/"):
        # /api/replay/* 以外には判断を返さない (常に True とみなす)
        return True
    return mode == "replay"


def order_dispatch_target(mode: Mode) -> Literal["live", "replay"]:
    """注文系 (SubmitOrder / `/api/order/submit`) のディスパッチ先を返す。

    - live   → 立花 `tachibana_orders.submit_order`
    - replay → REPLAY 仮想注文ディスパッチャ (N1.5)
    """
    return mode


class ModeMismatchError(ValueError):
    """`Hello.mode` と `StartEngine.engine` の整合不一致 (M-10)."""


class UnknownEngineKindError(ValueError):
    """`StartEngine.engine` が `"Backtest"` / `"Live"` 以外 (M-10)."""


def validate_start_engine(mode: Mode, engine_kind: str) -> None:
    """Hello.mode と `StartEngine.engine` の整合チェック。

    - mode=replay でなければ `engine="Backtest"` は不可 → ``ModeMismatchError``
    - mode=live でなければ `engine="Live"` は不可 → ``ModeMismatchError``
    - 上記以外の engine_kind → ``UnknownEngineKindError``

    M-10: 呼出側 (server.py) で別 ``code`` (`mode_mismatch` / `unknown_engine_kind`) に
    分岐するため例外型を分けた。``ModeMismatchError``・``UnknownEngineKindError`` は
    ``ValueError`` のサブクラスなので既存の ``except ValueError`` ハンドラとも互換。
    """
    if engine_kind == "Backtest" and mode != "replay":
        raise ModeMismatchError(
            f"StartEngine.engine='Backtest' requires mode='replay', got mode={mode!r}"
        )
    if engine_kind == "Live" and mode != "live":
        raise ModeMismatchError(
            f"StartEngine.engine='Live' requires mode='live', got mode={mode!r}"
        )
    if engine_kind not in ("Backtest", "Live"):
        raise UnknownEngineKindError(f"unknown engine kind: {engine_kind!r}")


def nautilus_capabilities(mode: Mode) -> dict[str, bool]:
    """Ready.capabilities.nautilus の値。

    N1 では live は LiveExecutionEngine を起動しないので常に False。
    backtest は実装済みなので True。
    """
    return {"backtest": True, "live": False}
