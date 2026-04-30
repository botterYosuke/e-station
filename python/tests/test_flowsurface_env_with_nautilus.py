"""N1.7: FlowsurfaceEnv — Gymnasium 互換の replay バックテスト環境のテスト。

gymnasium パッケージは optional。本テストは duck-type 実装を対象とする。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

FIXTURES = Path(__file__).parent / "fixtures"
REPO_ROOT = Path(__file__).parent.parent.parent
_STRATEGY_FILE = str(Path(__file__).parent / "fixtures" / "test_strategy.py")


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def env(fixtures_dir: Path):
    from engine.nautilus.gym_env import FlowsurfaceEnv

    return FlowsurfaceEnv(
        instrument_id="1301.TSE",
        start_date="2024-01-04",
        end_date="2024-01-05",
        initial_cash=1_000_000,
        base_dir=fixtures_dir,
        strategy_file=_STRATEGY_FILE,
    )


# ---------------------------------------------------------------------------
# reset()
# ---------------------------------------------------------------------------


class TestEnvReset:
    def test_reset_returns_observation_and_info(self, env) -> None:
        """reset() が (observation, info) を返すこと。"""
        obs, info = env.reset()
        assert obs is not None
        assert isinstance(info, dict)

    def test_reset_observation_is_numpy_array(self, env) -> None:
        """observation は numpy 配列であること。"""
        obs, _ = env.reset()
        assert isinstance(obs, np.ndarray)

    def test_reset_observation_shape(self, env) -> None:
        """observation の shape は (2,) = [price, qty]。"""
        obs, _ = env.reset()
        assert obs.shape == (2,)

    def test_reset_observation_dtype_float(self, env) -> None:
        """observation は float64 配列であること。"""
        obs, _ = env.reset()
        assert obs.dtype == np.float64

    def test_reset_info_has_initial_equity(self, env) -> None:
        """info に initial_equity が含まれること。"""
        _, info = env.reset()
        assert "initial_equity" in info

    def test_reset_can_be_called_twice(self, env) -> None:
        """reset() を 2 回呼んでも例外が出ないこと（エピソード再利用）。"""
        env.reset()
        obs, info = env.reset()
        assert obs is not None
        assert isinstance(info, dict)


# ---------------------------------------------------------------------------
# step()
# ---------------------------------------------------------------------------


class TestEnvStep:
    def test_step_returns_correct_shape(self, env) -> None:
        """step() が (obs, reward, terminated, truncated, info) の 5 要素タプルを返すこと。"""
        env.reset()
        result = env.step(0)  # hold
        assert len(result) == 5
        obs, reward, terminated, truncated, info = result
        assert isinstance(obs, np.ndarray)
        assert isinstance(reward, float)
        assert isinstance(terminated, bool)
        assert isinstance(truncated, bool)
        assert isinstance(info, dict)

    def test_step_observation_shape(self, env) -> None:
        """step() の observation も shape (2,)。"""
        env.reset()
        obs, _, _, _, _ = env.step(0)
        assert obs.shape == (2,)

    def test_step_terminates_after_full_episode(self, env) -> None:
        """バッチ型実装では step() 後に terminated=True になること。"""
        env.reset()
        _, _, terminated, _, _ = env.step(0)
        assert terminated is True

    def test_step_truncated_is_false(self, env) -> None:
        """バッチ型実装では truncated=False（タイムアウトではなく自然終了）。"""
        env.reset()
        _, _, _, truncated, _ = env.step(0)
        assert truncated is False

    def test_step_hold_no_exception(self, env) -> None:
        """action=0 (hold) で例外が出ないこと。"""
        env.reset()
        env.step(0)

    def test_step_buy_no_exception(self, env) -> None:
        """action=1 (buy) で例外が出ないこと。"""
        env.reset()
        env.step(1)

    def test_step_sell_no_exception(self, env) -> None:
        """action=2 (sell) で例外が出ないこと。"""
        env.reset()
        env.step(2)

    def test_step_without_reset_raises(self, env) -> None:
        """reset() なしで step() を呼ぶと RuntimeError が出ること。"""
        with pytest.raises(RuntimeError, match="reset"):
            env.step(0)


# ---------------------------------------------------------------------------
# equity / reward
# ---------------------------------------------------------------------------


class TestEnvEquityAndReward:
    def test_step_reward_is_float(self, env) -> None:
        """reward は float であること（0.0 でも許容）。"""
        env.reset()
        _, reward, _, _, _ = env.step(0)
        assert isinstance(reward, float)

    def test_buy_action_no_exception(self, env) -> None:
        """action=1 (buy) で reward が float として返ること（0 でも可）。"""
        env.reset()
        _, reward, _, _, _ = env.step(1)
        assert isinstance(reward, float)

    def test_step_info_has_final_equity(self, env) -> None:
        """step() の info に final_equity が含まれること。"""
        env.reset()
        _, _, _, _, info = env.step(0)
        assert "final_equity" in info

    def test_step_info_final_equity_nonnegative(self, env) -> None:
        """final_equity は非負（破産でも 0 以上）。"""
        env.reset()
        _, _, _, _, info = env.step(0)
        assert float(info["final_equity"]) >= 0


# ---------------------------------------------------------------------------
# observation_space / action_space
# ---------------------------------------------------------------------------


class TestEnvSpaces:
    def test_observation_space_exists(self, env) -> None:
        """observation_space プロパティが存在すること。"""
        assert env.observation_space is not None

    def test_action_space_exists(self, env) -> None:
        """action_space プロパティが存在すること。"""
        assert env.action_space is not None

    def test_observation_space_has_shape(self, env) -> None:
        """observation_space.shape が (2,) であること。"""
        assert env.observation_space.shape == (2,)

    def test_action_space_has_n(self, env) -> None:
        """action_space.n == 3 (hold / buy / sell)。"""
        assert env.action_space.n == 3


# ---------------------------------------------------------------------------
# buy-and-hold strategy smoke
# ---------------------------------------------------------------------------


class TestBuyAndHoldStrategySmoke:
    def test_buy_and_hold_strategy_in_env_no_exception(self, env) -> None:
        """buy-and-hold 戦略でエピソード 1 本 (reset → step) が例外なく完走すること。"""
        obs, info = env.reset()
        assert obs is not None

        total_reward = 0.0
        obs, reward, terminated, truncated, info = env.step(1)  # buy
        total_reward += reward

        # バッチ型実装では 1 step で terminated
        assert terminated is True
        assert isinstance(total_reward, float)

    def test_env_deterministic_across_resets(self, fixtures_dir: Path) -> None:
        """同一フィクスチャで 2 エピソードの reward がビット一致すること（決定論性）。"""
        from engine.nautilus.gym_env import FlowsurfaceEnv

        kwargs = dict(
            instrument_id="1301.TSE",
            start_date="2024-01-04",
            end_date="2024-01-05",
            initial_cash=1_000_000,
            base_dir=fixtures_dir,
            strategy_file=_STRATEGY_FILE,
        )
        env1 = FlowsurfaceEnv(**kwargs)
        env2 = FlowsurfaceEnv(**kwargs)
        env1.reset()
        env2.reset()
        _, r1, _, _, _ = env1.step(0)
        _, r2, _, _, _ = env2.step(0)
        assert r1 == r2


# ---------------------------------------------------------------------------
# EC frame 重複受信 mock テスト（N1.7 追加テスト）
# ---------------------------------------------------------------------------


class TestECFrameDedupMock:
    """部分約定 / cancel-after-fill レースの mock テスト。

    replay バックテストでは EC frame はないが、OrderFilled の重複受信を
    mock でシミュレートして env が壊れないことを確認する。
    """

    def test_duplicate_order_filled_does_not_crash(self, fixtures_dir: Path) -> None:
        """on_event が同一 strategy_id / same equity で 2 回 EngineStopped を
        受け取っても FlowsurfaceEnv が例外を出さないこと。

        mock で NautilusRunner.start_backtest_replay() を差し替え、
        on_event callback を直接 2 回呼び出す。
        """
        from engine.nautilus.gym_env import FlowsurfaceEnv
        from engine.nautilus.engine_runner import ReplayBacktestResult
        from decimal import Decimal

        env = FlowsurfaceEnv(
            instrument_id="1301.TSE",
            start_date="2024-01-04",
            end_date="2024-01-05",
            initial_cash=1_000_000,
            base_dir=fixtures_dir,
        )

        mock_result = ReplayBacktestResult(
            strategy_id="buy-and-hold",
            final_equity=Decimal("1_050_000"),
            fill_timestamps=[1000000],
            fill_last_prices=["3775.0"],
            trades_loaded=4,
        )

        def fake_replay(**kwargs):
            # on_event を 2 回呼び出す（重複 EngineStopped シミュレーション）
            on_event = kwargs.get("on_event")
            if on_event:
                stop_evt = {
                    "event": "EngineStopped",
                    "strategy_id": "buy-and-hold",
                    "final_equity": "1050000",
                    "ts_event_ms": 12345,
                }
                on_event(stop_evt)
                on_event(stop_evt)  # 重複
            return mock_result

        with patch(
            "engine.nautilus.gym_env.NautilusRunner.start_backtest_replay",
            side_effect=fake_replay,
        ):
            env.reset()
            # 重複 EngineStopped があっても例外なく step() できること
            obs, reward, terminated, truncated, info = env.step(0)
            assert terminated is True

    def test_partial_fill_then_cancel_no_exception(self, fixtures_dir: Path) -> None:
        """部分約定後にキャンセルが来るシナリオ（mock）で env が壊れないこと。

        ReplayBacktestResult.fill_timestamps が複数あり、
        fill_last_prices の一部が欠損（空文字）でも例外が出ないことを確認。
        """
        from engine.nautilus.gym_env import FlowsurfaceEnv
        from engine.nautilus.engine_runner import ReplayBacktestResult
        from decimal import Decimal

        env = FlowsurfaceEnv(
            instrument_id="1301.TSE",
            start_date="2024-01-04",
            end_date="2024-01-05",
            initial_cash=1_000_000,
            base_dir=fixtures_dir,
        )

        # 部分約定: 2 回 fill, うち 1 件は価格不明（空文字）
        mock_result = ReplayBacktestResult(
            strategy_id="buy-and-hold",
            final_equity=Decimal("980000"),
            fill_timestamps=[1000000, 2000000],
            fill_last_prices=["3775.0", ""],  # 2 件目は欠損
            trades_loaded=4,
        )

        with patch(
            "engine.nautilus.gym_env.NautilusRunner.start_backtest_replay",
            return_value=mock_result,
        ):
            env.reset()
            obs, reward, terminated, truncated, info = env.step(0)
            assert isinstance(reward, float)
            assert terminated is True
