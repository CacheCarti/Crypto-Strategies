from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolEthBetaFollower(Strategy):
    METADATA = {
        "name": "SOL ETH Beta Follower",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 640.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
        "required_features": ["eth_return_pct", "sol_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 10
        self.slow_period = 25
        self.rsi_period = 14
        self.eth_threshold = 2.2
        self.sol_threshold = 0.8
        self.cooldown_bars = 4
        self.last_exit_bar = -100
        self.entry_bar = 0
        self.prev_eth_ret = 0.0

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.slow_period + 10)
        if len(closes) < self.slow_period + 5:
            return None

        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        sol_ret = ctx.features.get("sol_return_pct", 0.0)

        eth_delta = eth_ret - self.prev_eth_ret
        self.prev_eth_ret = eth_ret

        ema_fast = self._ema(closes, self.fast_period)
        ema_slow = self._ema(closes, self.slow_period)
        rsi = self._rsi(closes, self.rsi_period)

        if ema_fast is None or ema_slow is None or rsi is None:
            return None

        price = ctx.bar.close

        # Position management and exit logic
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar

            if pos_dir == "long":
                # Exit when ETH driver flattens or turns negative, or overbought fatigue
                if eth_ret < 0.4 or sol_ret < -0.5 or (rsi > 78 and eth_delta < -0.3) or bars_held > 16:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_beta_exhaustion_or_driver_flatten",
                            "eth_ret": eth_ret,
                            "sol_ret": sol_ret,
                            "rsi": rsi,
                            "bars_held": bars_held,
                        },
                    )
            elif pos_dir == "short":
                # Exit when ETH dump flattens or turns positive, or oversold fatigue
                if eth_ret > -0.4 or sol_ret > 0.5 or (rsi < 22 and eth_delta > 0.3) or bars_held > 16:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_beta_exhaustion_or_driver_flatten",
                            "eth_ret": eth_ret,
                            "sol_ret": sol_ret,
                            "rsi": rsi,
                            "bars_held": bars_held,
                        },
                    )
            return None

        # Entry logic with mandatory cooldown check
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Long beta setup: ETH surging and accelerating, SOL confirmed direction, SOL above EMA
        if (
            eth_ret >= self.eth_threshold
            and eth_delta >= 0.0
            and sol_ret >= self.sol_threshold
            and price > ema_fast >= ema_slow
            and 45.0 < rsi < 75.0
        ):
            confidence = min(1.0, 0.6 + abs(eth_ret) * 0.05)
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_bull_driver_sol_beta_follow",
                    "eth_ret": eth_ret,
                    "sol_ret": sol_ret,
                    "eth_delta": eth_delta,
                    "rsi": rsi,
                    "ema_fast": ema_fast,
                    "ema_slow": ema_slow,
                    "price": price,
                },
            )

        # Short beta setup: ETH dumping and accelerating downward, SOL confirmed negative, SOL below EMA
        if (
            eth_ret <= -self.eth_threshold
            and eth_delta <= 0.0
            and sol_ret <= -self.sol_threshold
            and price < ema_fast <= ema_slow
            and 25.0 < rsi < 55.0
        ):
            confidence = min(1.0, 0.6 + abs(eth_ret) * 0.05)
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_bear_driver_sol_beta_follow",
                    "eth_ret": eth_ret,
                    "sol_ret": sol_ret,
                    "eth_delta": eth_delta,
                    "rsi": rsi,
                    "ema_fast": ema_fast,
                    "ema_slow": ema_slow,
                    "price": price,
                },
            )

        return None