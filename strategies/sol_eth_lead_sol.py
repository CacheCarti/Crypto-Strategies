from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolEthBetaFollow(Strategy):
    METADATA = {
        "name": "SOL ETH Beta Follower",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 30,
        "required_features": ["eth_return_pct", "sol_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_period = 20
        self.rsi_period = 14
        self.cooldown_bars = 4
        self.last_exit_bar = -100
        self.eth_threshold = 1.8
        self.sol_confirm_threshold = 0.5
        self.prev_eth_ret = 0.0

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _rsi(self, closes, period: int = 14) -> Optional[float]:
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
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        sol_ret = ctx.features.get("sol_return_pct", 0.0)
        eth_rising = eth_ret > self.prev_eth_ret
        eth_falling = eth_ret < self.prev_eth_ret
        self.prev_eth_ret = eth_ret

        ema20 = self._ema(closes, self.ema_period)
        rsi = self._rsi(closes, self.rsi_period)
        if ema20 is None or rsi is None:
            return None

        current_price = ctx.bar.close

        # Position Management & Exits
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long":
                if eth_ret < 0.4 or rsi >= 75.0:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "eth_momentum_flattened_or_rsi_overbought",
                            "eth_return_pct": eth_ret,
                            "sol_return_pct": sol_ret,
                            "rsi": rsi,
                            "price": current_price,
                        },
                    )
            elif direction == "short":
                if eth_ret > -0.4 or rsi <= 25.0:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "eth_momentum_flattened_or_rsi_oversold",
                            "eth_return_pct": eth_ret,
                            "sol_return_pct": sol_ret,
                            "rsi": rsi,
                            "price": current_price,
                        },
                    )
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Long Entry: Strong ETH positive beta confirmed by SOL, trend alignment, non-exhausted RSI
        if (
            eth_ret >= self.eth_threshold
            and eth_rising
            and sol_ret >= self.sol_confirm_threshold
            and current_price > ema20
            and 45.0 <= rsi <= 68.0
        ):
            confidence = min(0.9, 0.6 + abs(eth_ret) * 0.05)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_beta_expansion_long_confirmed",
                    "eth_return_pct": eth_ret,
                    "sol_return_pct": sol_ret,
                    "rsi": rsi,
                    "ema20": ema20,
                    "price": current_price,
                },
            )

        # Short Entry: Strong ETH negative beta confirmed by SOL, trend alignment, non-exhausted RSI
        if (
            eth_ret <= -self.eth_threshold
            and eth_falling
            and sol_ret <= -self.sol_confirm_threshold
            and current_price < ema20
            and 32.0 <= rsi <= 55.0
        ):
            confidence = min(0.9, 0.6 + abs(eth_ret) * 0.05)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_beta_breakdown_short_confirmed",
                    "eth_return_pct": eth_ret,
                    "sol_return_pct": sol_ret,
                    "rsi": rsi,
                    "ema20": ema20,
                    "price": current_price,
                },
            )

        return None