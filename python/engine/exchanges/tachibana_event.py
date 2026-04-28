"""立花証券 e支店 EVENT WebSocket 受信ループ + EC パーサ。

責務:
    - EVENT WebSocket からフレームを受信する（FD フレーム + EC フレームの合流）
    - EC フレーム（約定通知）を正規化された OrderEcEvent に変換する
    - (venue_order_id, trade_id) キーによる重複検知

EC フレームのフィールド対応（architecture.md §6）:
    p_NO  → venue_order_id
    p_EDA → trade_id（重複検知キー。内部コメントで p_eda_no と対応）
    p_NT  → notification_type（1=受付, 2=約定, 3=取消, 4=失効）
    p_DH  → last_price
    p_DSU → last_qty
    p_ZSU → leaves_qty
    p_OD  → ts_event_ms（約定日時 JST YYYYMMDDHHMMSS → UTC ms）

不変条件:
    - 重複検知キーは (venue_order_id, trade_id) のみ（C-H3）
    - IPC フィールド名は trade_id 固定（eda_no / p_EDA / p_eda_no は内部コメントのみ）
    - EVENT URL 制御文字は reject（C4, C-R2-L1）
"""

from __future__ import annotations

import asyncio
import logging
import time as _time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# JST タイムゾーン（UTC+9）
_JST = timezone(timedelta(hours=9))

# EC 通知種別定数（p_NT の値）
_NT_RECEIVED = "1"   # 受付
_NT_FILLED = "2"     # 約定
_NT_CANCELED = "3"   # 取消
_NT_EXPIRED = "4"    # 失効


# ---------------------------------------------------------------------------
# OrderEcEvent — EC フレームから正規化した約定通知イベント
# ---------------------------------------------------------------------------


@dataclass
class OrderEcEvent:
    """EC フレームから正規化した約定通知イベント。

    IPC `OrderFilled` / `OrderCanceled` / `OrderExpired` への写像源。
    フィールド名はすべて nautilus 用語（立花固有の p_EDA 等は内部コメントのみ）。
    """

    venue_order_id: str         # p_NO → venue_order_id
    trade_id: str               # p_EDA → trade_id（立花内部名: p_eda_no）
    notification_type: str      # p_NT（"1"=受付 / "2"=約定 / "3"=取消 / "4"=失効）
    last_price: Optional[str]   # p_DH（約定単価。取消/失効時は None or ""）
    last_qty: Optional[str]     # p_DSU（約定数量。取消/失効時は None or ""）
    leaves_qty: Optional[str]   # p_ZSU（残数量。0=全約定）
    ts_event_ms: int            # p_OD（約定日時 JST → UTC ms）


# ---------------------------------------------------------------------------
# EC フレームパーサ
# ---------------------------------------------------------------------------


def _parse_p_od_to_utc_ms(p_od: str) -> int:
    """立花 p_OD（JST YYYYMMDDHHMMSS）を UTC ミリ秒に変換する。

    p_OD は「約定日時」を表す（注文日時ではない）。

    Args:
        p_od: YYYYMMDDHHMMSS 形式の JST 日時文字列

    Returns:
        UTC ミリ秒（int）。パース失敗時は 0 を返す。
    """
    if not p_od or len(p_od) < 14:
        return 0
    try:
        dt = datetime.strptime(p_od[:14], "%Y%m%d%H%M%S").replace(tzinfo=_JST)
        return int(dt.timestamp() * 1000)
    except ValueError:
        logger.warning("p_OD パースエラー: %r、現在時刻で代替", p_od)
        return int(_time.time() * 1000)


def _parse_ec_frame(items: list[tuple[str, str]]) -> OrderEcEvent:
    """立花 EC フレームを OrderEcEvent に正規化する。

    architecture.md §6 のフィールド対応表:
        p_NO  → venue_order_id
        p_EDA → trade_id（IPC では trade_id 固定。立花内部: p_eda_no）
        p_NT  → notification_type（"1"=受付, "2"=約定, "3"=取消, "4"=失効）
        p_DH  → last_price
        p_DSU → last_qty
        p_ZSU → leaves_qty
        p_OD  → ts_event_ms（約定日時 JST YYYYMMDDHHMMSS → UTC ms）

    Args:
        items: EC フレームの (key, value) ペアリスト

    Returns:
        正規化された OrderEcEvent
    """
    # items を dict に変換
    frame: dict[str, str] = dict(items)

    venue_order_id = frame.get("p_NO", "")
    trade_id = frame.get("p_EDA", "")        # 内部: p_eda_no / p_EDA → IPC: trade_id
    notification_type = frame.get("p_NT", "")

    # 価格・数量（約定時のみ存在。取消/失効時は空文字または欠落）
    last_price_raw = frame.get("p_DH", None)
    last_qty_raw = frame.get("p_DSU", None)
    leaves_qty_raw = frame.get("p_ZSU", None)

    # 空文字は None として扱う（取消/失効フレームで区別しやすくする）
    last_price: Optional[str] = last_price_raw if last_price_raw else None
    last_qty: Optional[str] = last_qty_raw if last_qty_raw else None
    leaves_qty: Optional[str] = leaves_qty_raw if leaves_qty_raw is not None else None

    # p_OD: 約定日時（JST YYYYMMDDHHMMSS → UTC ms）
    # NOTE: 注文日時ではなく約定日時を使う（architecture.md §6）
    p_od = frame.get("p_OD", "")
    ts_event_ms = _parse_p_od_to_utc_ms(p_od)

    return OrderEcEvent(
        venue_order_id=venue_order_id,
        trade_id=trade_id,
        notification_type=notification_type,
        last_price=last_price,
        last_qty=last_qty,
        leaves_qty=leaves_qty,
        ts_event_ms=ts_event_ms,
    )


# ---------------------------------------------------------------------------
# TachibanaEventClient — EVENT WebSocket の受信ループ（FD + EC 合流）
# ---------------------------------------------------------------------------


class TachibanaEventClient:
    """EVENT WebSocket の受信ループ（FD フレーム + EC フレームの合流責務）。

    重複検知:
        _seen: set[tuple[str, str]] で (venue_order_id, trade_id) のペアを追跡する。
        再接続時の再送 EC を弾くために使用する（C-H3 統一）。

    当日リセット:
        夜間閉局検知時に reset_seen_trades() を呼ぶ。
    """

    def __init__(self) -> None:
        # (venue_order_id, trade_id) キー — 重複検知用（C-H3）
        self._seen: set[tuple[str, str]] = set()

    def _is_duplicate(self, venue_order_id: str, trade_id: str) -> bool:
        """EC 重複検知。

        (venue_order_id, trade_id) が既に seen セットにあれば True（重複）を返す。
        新規キーの場合は seen セットに追加して False を返す。

        Args:
            venue_order_id: 注文番号（= 立花 p_NO / sOrderNumber）
            trade_id: 約定枝番（= 立花 p_EDA / p_eda_no）

        Returns:
            True: 重複（スキップすべき）
            False: 新規（処理すべき）
        """
        key = (venue_order_id, trade_id)
        if key in self._seen:
            return True
        self._seen.add(key)
        return False

    def reset_seen_trades(self) -> None:
        """当日分の重複検知セットをリセットする。

        夜間閉局検知時に呼び出す。
        翌営業日に同じ (venue_order_id, trade_id) が来ても処理できるようにする。
        """
        self._seen.clear()

    async def receive_loop(
        self,
        ws: object,
        on_event: object,
        *,
        reconnect_fn: object = None,
        max_retries: int = 10,
        base_backoff: float = 1.0,
    ) -> None:
        """EVENT WebSocket からフレームを受信して on_event コールバックを呼ぶ。

        フレーム種別の判定:
            - FD フレーム（p_cmd="FD"）: 銘柄データ → on_event に FD として渡す
            - EC フレーム（p_cmd="EC"）: 約定通知 → _parse_ec_frame → 重複検知 → on_event

        再接続 (B-7):
            `reconnect_fn` が渡された場合、WebSocket 切断後に指数バックオフで再接続を試みる。
            `reconnect_fn` は引数なしで呼び出し可能な async callable で、
            新しい WebSocket 接続オブジェクトを返す必要がある。
            `reconnect_fn` が None の場合は再接続なし（従来動作）。
            `max_retries` 回連続失敗したら諦めてループを終了する。

        Args:
            ws: EVENT WebSocket 接続オブジェクト（websockets.ClientConnection 相当）
            on_event: コールバック。on_event(frame_type: str, event: OrderEcEvent | dict) を呼ぶ
            reconnect_fn: 再接続コールバック（async callable）。None の場合は再接続しない
            max_retries: 連続再接続失敗の上限（デフォルト 10）
            base_backoff: 再接続バックオフの基底秒数（デフォルト 1.0）
        """
        # NOTE: 実際の接続は Phase O2 統合テスト / E2E で検証する。
        # ここでは受信ループの骨格のみ実装する（モック化可能な形）。
        current_ws = ws
        retry_count = 0
        _connect_time = _time.monotonic()

        while True:
            try:
                async for raw_frame in current_ws:
                    try:
                        await self._process_frame(raw_frame, on_event)
                    except Exception as exc:
                        logger.error("EVENT フレーム処理エラー: %s", exc)
                # WS が正常に終了した（サーバ側 close）
                logger.info("EVENT WebSocket が正常終了した")
                # 30 秒以上安定接続していた場合のみカウンタをリセット（即切断ループ防止）
                if _time.monotonic() - _connect_time > 30.0:
                    retry_count = 0
                # 正常クローズ後も reconnect_fn が None でなければ再接続試行する。
                # reconnect_fn=None の場合は従来どおり終了する。
                self.reset_seen_trades()
                if reconnect_fn is None:
                    break
            except Exception as exc:
                logger.warning("EVENT WebSocket 受信エラー: %s", exc)

            # 再接続ロジック
            if reconnect_fn is None:
                break

            retry_count += 1
            if retry_count > max_retries:
                logger.error(
                    "EVENT WebSocket 再接続上限 (%d 回) に達した。ループを終了する", max_retries
                )
                break

            backoff = min(base_backoff * (2 ** (retry_count - 1)), 60.0)
            logger.info("EVENT WebSocket 再接続待機 %.1f 秒 (attempt %d/%d)", backoff, retry_count, max_retries)
            await asyncio.sleep(backoff)

            # reconnect_fn が失敗した場合は current_ws を更新せず再試行する。
            # stale な current_ws を async for するとループ先頭で再び例外が発生し
            # retry_count が二重インクリメントされるため、成功するまで
            # ここで内側ループを回す（M-2 二重インクリメント修正）。
            while True:
                try:
                    current_ws = await reconnect_fn()
                    _connect_time = _time.monotonic()
                    logger.info("EVENT WebSocket 再接続成功")
                    break
                except Exception as exc:
                    logger.warning("EVENT WebSocket 再接続失敗 (attempt %d): %s", retry_count, exc)
                    if retry_count >= max_retries:
                        logger.error(
                            "EVENT WebSocket 再接続上限 (%d 回) に達した。ループを終了する", max_retries
                        )
                        return
                    retry_count += 1
                    backoff = min(base_backoff * (2 ** (retry_count - 1)), 60.0)
                    logger.info(
                        "EVENT WebSocket 再接続待機 %.1f 秒 (attempt %d/%d)",
                        backoff,
                        retry_count,
                        max_retries,
                    )
                    await asyncio.sleep(backoff)

    async def _process_frame(self, raw_frame: object, on_event: object) -> None:
        """1 つのフレームを処理する。

        フレームを ^A（\x01）区切りで分割し、p_cmd で FD / EC を判定する。

        Args:
            raw_frame: 受信した生フレーム（str または bytes）
            on_event: コールバック
        """
        if isinstance(raw_frame, bytes):
            raw_frame = raw_frame.decode("shift_jis", errors="replace")

        # ^A（\x01）区切りで key=value ペアに分割
        items: list[tuple[str, str]] = []
        for part in raw_frame.split("\x01"):
            if "=" in part:
                key, _, value = part.partition("=")
                items.append((key.strip(), value.strip()))

        frame_dict = dict(items)
        evt_cmd = frame_dict.get("p_cmd", "")

        if evt_cmd == "EC":
            await self._handle_ec_frame(items, on_event)
        elif evt_cmd == "FD":
            # FD フレーム（銘柄データ）: depth/ticker 処理に渡す
            if callable(on_event):
                await _maybe_await(on_event("FD", frame_dict))
        else:
            # その他のフレーム種別はログのみ（拡張可能性のため）
            logger.debug("EVENT フレーム: evt_cmd=%r (未処理)", evt_cmd)

    async def _handle_ec_frame(
        self,
        items: list[tuple[str, str]],
        on_event: object,
    ) -> None:
        """EC フレームを処理する。

        _parse_ec_frame → 重複検知 → on_event(type, event) を呼ぶ。
        重複検知でスキップされた場合は on_event を呼ばない。
        """
        try:
            ec_event = _parse_ec_frame(items)
        except Exception as exc:
            logger.error("EC フレームパースエラー: %s", exc)
            return

        if self._is_duplicate(ec_event.venue_order_id, ec_event.trade_id):
            logger.debug(
                "EC 重複スキップ: venue_order_id=%r trade_id=%r",
                ec_event.venue_order_id,
                ec_event.trade_id,
            )
            return

        if callable(on_event):
            await _maybe_await(on_event("EC", ec_event))


async def _maybe_await(result: object) -> None:
    """コルーチンであれば await する。通常の戻り値であれば何もしない。"""
    if asyncio.iscoroutine(result):
        await result
