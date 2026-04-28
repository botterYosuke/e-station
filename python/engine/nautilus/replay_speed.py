"""N1.11: replay pacing ロジック。

streaming=True 経路で tick 間 sleep を計算する純粋関数。
engine_runner.py から呼び出される。

D7 pacing 式:
    sleep_sec = min(max(dt_event_sec, MIN_TICK_DT_SEC) / multiplier, SLEEP_CAP_SEC)

セッション境界ルール:
    - 前場-後場ギャップ（11:30〜12:30 JST）の tick は sleep=0 で即時通過
    - 引け後 / 営業日跨ぎも sleep=0 で即時通過
    - 営業日跨ぎ時は is_new_trading_day() が True を返す（呼出側が DateChangeMarker を emit）
"""

from __future__ import annotations

import datetime as dt

MIN_TICK_DT_SEC: float = 0.001   # 1ms
SLEEP_CAP_SEC: float = 0.200     # 200ms

_JST = dt.timezone(dt.timedelta(hours=9))
_MARKET_BREAK_START = dt.time(11, 30)  # 前場終了
_MARKET_BREAK_END = dt.time(12, 30)    # 後場開始


def compute_sleep_sec(
    dt_event_sec: float,
    multiplier: int,
    *,
    ts_event_ns: int | None = None,
) -> float:
    """D7 pacing 式: sleep_sec = min(max(dt_event_sec, MIN_TICK_DT_SEC) / multiplier, SLEEP_CAP_SEC).

    ts_event_ns が指定され、かつ JST 時刻が前場-後場ギャップ（11:30〜12:30）なら 0.0 を返す。

    Args:
        dt_event_sec: 直前 tick との ts_event 時刻差（秒）。負の場合は 0 扱い。
        multiplier: 再生速度倍率。1=等速、10=10倍速、100=100倍速。0以下は ValueError。
        ts_event_ns: オプション。ns タイムスタンプ。指定時に前場-後場ギャップを判定。

    Returns:
        sleep 秒数（0.0 以上、SLEEP_CAP_SEC 以下）。
    """
    if multiplier <= 0:
        raise ValueError(f"multiplier must be positive, got {multiplier!r}")

    # 前場-後場ギャップ判定
    if ts_event_ns is not None and is_market_break(ts_event_ns):
        return 0.0

    # dt_event_sec が負の場合は 0 扱い（時刻逆転 / 同一 tick）
    effective_dt = max(dt_event_sec, 0.0)

    sleep = min(max(effective_dt, MIN_TICK_DT_SEC) / multiplier, SLEEP_CAP_SEC)
    return sleep


def is_market_break(ts_event_ns: int) -> bool:
    """ns タイムスタンプが前場-後場ギャップ（11:30〜12:30 JST）かどうかを返す。

    境界値は閉区間: 11:30:00.000 <= t <= 12:30:00.000 なら True。
    """
    ts_sec = ts_event_ns / 1_000_000_000
    dt_jst = dt.datetime.fromtimestamp(ts_sec, tz=_JST)
    t = dt_jst.time().replace(tzinfo=None)
    return _MARKET_BREAK_START <= t <= _MARKET_BREAK_END


def is_new_trading_day(prev_ts_ns: int | None, curr_ts_ns: int) -> bool:
    """前 tick と現在 tick が異なる営業日かどうかを返す（JST 日付ベース）。

    prev_ts_ns が None の場合（最初の tick）は False を返す。
    """
    if prev_ts_ns is None:
        return False

    prev_sec = prev_ts_ns / 1_000_000_000
    curr_sec = curr_ts_ns / 1_000_000_000

    prev_date = dt.datetime.fromtimestamp(prev_sec, tz=_JST).date()
    curr_date = dt.datetime.fromtimestamp(curr_sec, tz=_JST).date()

    return prev_date != curr_date
