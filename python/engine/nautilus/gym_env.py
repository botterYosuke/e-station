"""N1.7: FlowsurfaceEnv — Gymnasium 互換の replay バックテスト環境。

nautilus BacktestEngine を背後に持ち、Gymnasium 互換の step() / reset() API を提供する。
HTTP 経由では**なく**、直接 NautilusRunner を呼び出す Python-native 実装。

Action space: {0: hold, 1: buy, 2: sell}
Observation: 最新の約定価格と数量を [price, qty] に変換した 1D numpy 配列
Reward: 仮想 equity の変化分（initial_cash に対する差分）

gymnasium パッケージは optional。
- 利用可能な場合: gymnasium.Env を継承
- 利用不可な場合: duck-type のみ実装（step / reset / observation_space / action_space）

バッチ型実装（N1.7）:
  - reset() で start_backtest_replay() を呼んでデータをロードし初期観測を返す
  - step() は「全バックテスト完走後の最終 equity」を使って terminated=True を返す
  - 1 エピソード = 全データで backtest 1 回

step-by-step 制御 (streaming=True 経路) は N1.11 依存のため本タスクでは実装しない。
"""

from __future__ import annotations

import logging
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np

from engine.nautilus.engine_runner import NautilusRunner, ReplayBacktestResult

log = logging.getLogger(__name__)

# gymnasium が利用可能かどうかを検出する
try:
    import gymnasium as gym  # type: ignore[import-not-found]
    _GYM_BASE = gym.Env
    _GYM_AVAILABLE = True
except ImportError:
    _GYM_BASE = object
    _GYM_AVAILABLE = False


# ---------------------------------------------------------------------------
# Space 実装（gymnasium なしでも動く最小実装）
# ---------------------------------------------------------------------------


class _BoxSpace:
    """gymnasium.spaces.Box の最小互換実装。"""

    def __init__(self, low: np.ndarray, high: np.ndarray, dtype=np.float64) -> None:
        self.low = low.astype(dtype)
        self.high = high.astype(dtype)
        self.dtype = dtype
        self.shape: tuple[int, ...] = low.shape

    def contains(self, x: np.ndarray) -> bool:
        return bool(np.all(x >= self.low) and np.all(x <= self.high))

    def __repr__(self) -> str:
        return f"BoxSpace(shape={self.shape}, dtype={self.dtype})"


class _DiscreteSpace:
    """gymnasium.spaces.Discrete の最小互換実装。"""

    def __init__(self, n: int) -> None:
        self.n = n

    def contains(self, x: int) -> bool:
        return 0 <= x < self.n

    def __repr__(self) -> str:
        return f"DiscreteSpace(n={self.n})"


def _make_observation_space() -> Any:
    """observation_space を返す。gymnasium があれば gym.spaces.Box を使う。"""
    low = np.array([0.0, 0.0], dtype=np.float64)
    high = np.array([np.inf, np.inf], dtype=np.float64)
    if _GYM_AVAILABLE:
        import gymnasium.spaces as spaces  # type: ignore[import-not-found]
        return spaces.Box(low=low, high=high, dtype=np.float64)
    return _BoxSpace(low=low, high=high, dtype=np.float64)


def _make_action_space() -> Any:
    """action_space を返す。gymnasium があれば gym.spaces.Discrete を使う。"""
    if _GYM_AVAILABLE:
        import gymnasium.spaces as spaces  # type: ignore[import-not-found]
        return spaces.Discrete(3)
    return _DiscreteSpace(n=3)


# ---------------------------------------------------------------------------
# FlowsurfaceEnv
# ---------------------------------------------------------------------------

_ZERO_OBS = np.zeros(2, dtype=np.float64)
_INF = float("inf")


class FlowsurfaceEnv(_GYM_BASE):
    """Gymnasium 互換インターフェース（gymnasium パッケージは optional）。

    gymnasium が利用可能なら Env を継承、そうでなければ duck-type のみ実装。

    Parameters
    ----------
    instrument_id:
        NautilusTrader 形式の Instrument ID（例: "1301.TSE"）。
    start_date:
        バックテスト開始日（"YYYY-MM-DD"）。
    end_date:
        バックテスト終了日（"YYYY-MM-DD"）。
    initial_cash:
        初期仮想資金（円）。
    base_dir:
        J-Quants fixtures ディレクトリ。None の場合は "S:/j-quants" を使う。
    strategy_id:
        NautilusRunner に渡す strategy_id。デフォルト "buy-and-hold"。
    granularity:
        "Trade" / "Minute" / "Daily"。デフォルト "Trade"。
    """

    metadata: dict[str, Any] = {"render_modes": []}

    def __init__(
        self,
        *,
        instrument_id: str,
        start_date: str,
        end_date: str,
        initial_cash: int = 1_000_000,
        base_dir: Path | str | None = None,
        strategy_id: str = "user-strategy",
        granularity: str = "Trade",
        strategy_file: str | None = None,
        strategy_init_kwargs: dict | None = None,
    ) -> None:
        if _GYM_AVAILABLE:
            super().__init__()

        self._instrument_id = instrument_id
        self._start_date = start_date
        self._end_date = end_date
        self._initial_cash = initial_cash
        self._base_dir: Path | None = Path(base_dir) if base_dir is not None else None
        self._strategy_id = strategy_id
        self._granularity = granularity
        self._strategy_file = strategy_file
        self._strategy_init_kwargs = strategy_init_kwargs

        self._observation_space = _make_observation_space()
        self._action_space = _make_action_space()

        # エピソード状態
        self._ready = False  # reset() が呼ばれたか
        self._last_result: ReplayBacktestResult | None = None

    # ------------------------------------------------------------------
    # Gymnasium API
    # ------------------------------------------------------------------

    @property
    def observation_space(self):
        return self._observation_space

    @property
    def action_space(self):
        return self._action_space

    def reset(
        self,
        *,
        seed: int | None = None,
        options: dict | None = None,
    ) -> tuple[np.ndarray, dict]:
        """エピソードを初期化し (observation, info) を返す。

        NautilusRunner.start_backtest_replay() を呼んでバックテストを完走させ、
        最初の約定価格を初期観測として返す。
        約定がない場合はゼロベクトルを返す。
        """
        if _GYM_AVAILABLE and seed is not None:
            super().reset(seed=seed)

        runner = NautilusRunner()
        events: list[dict] = []

        def _on_event(evt: dict) -> None:
            events.append(evt)

        base_dir = self._base_dir
        result = runner.start_backtest_replay(
            strategy_id=self._strategy_id,
            instrument_id=self._instrument_id,
            start_date=self._start_date,
            end_date=self._end_date,
            granularity=self._granularity,  # type: ignore[arg-type]
            initial_cash=self._initial_cash,
            base_dir=base_dir,
            on_event=_on_event,
            strategy_file=self._strategy_file,
            strategy_init_kwargs=self._strategy_init_kwargs,
        )
        self._last_result = result
        self._ready = True

        obs = self._make_observation(result)
        info = {
            "initial_equity": float(self._initial_cash),
            "trades_loaded": result.trades_loaded,
            "bars_loaded": result.bars_loaded,
        }
        return obs, info

    def step(
        self, action: int
    ) -> tuple[np.ndarray, float, bool, bool, dict]:
        """1 エピソードの結果を返す（バッチ型）。

        Parameters
        ----------
        action:
            0: hold, 1: buy, 2: sell

        Returns
        -------
        obs:
            最終観測（約定価格, 約定数量）。
        reward:
            仮想 equity の変化分 = final_equity - initial_cash。
        terminated:
            常に True（バッチ型: 1 step = 1 エピソード）。
        truncated:
            常に False（タイムアウトではなく自然終了）。
        info:
            補足情報 dict。
        """
        if not self._ready or self._last_result is None:
            raise RuntimeError(
                "step() was called before reset(). Call env.reset() first."
            )

        result = self._last_result
        obs = self._make_observation(result)
        reward = float(result.final_equity - Decimal(self._initial_cash))
        terminated = True
        truncated = False
        info = {
            "final_equity": float(result.final_equity),
            "action": action,
            "trades_loaded": result.trades_loaded,
            "bars_loaded": result.bars_loaded,
        }
        return obs, reward, terminated, truncated, info

    def render(self) -> None:  # type: ignore[override]
        """レンダリングは未実装（N1 では headless 動作のみ）。"""

    def close(self) -> None:
        """クリーンアップ（ランナーは都度 dispose 済みのため no-op）。"""
        self._ready = False
        self._last_result = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _make_observation(self, result: ReplayBacktestResult) -> np.ndarray:
        """ReplayBacktestResult から観測ベクトルを生成する。

        観測: [最終約定価格, 最終約定数量]
        - fill_last_prices の最後の有効な価格を使う
        - 約定がない場合はゼロベクトル
        """
        price = 0.0
        qty = 0.0

        # 最後の有効な fill 価格を取得
        for px_str in reversed(result.fill_last_prices):
            if px_str:
                try:
                    price = float(px_str)
                    break
                except (ValueError, TypeError):
                    continue

        return np.array([price, qty], dtype=np.float64)
