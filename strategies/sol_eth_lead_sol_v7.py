from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolEthBetaFollower(Strategy):
    METADATA = {
        "name": "SolEthBetaFollower",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
        "required_features": ["eth_return_pct", "sol_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.eth_long_threshold = 1.8
        self.eth_short_threshold = -1.8
        self.sol_confirm_threshold = 1.0
        self.ema_period = 14
        self.rsi_period = 14
        self.cooldown_bars = 4
        self.last_exit_bar = -999

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
        closes = ctx.closes(40)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        sol_close = ctx.bar.close
        sol_ema = self._ema(closes, self.ema_period)
        sol_rsi = self._rsi(closes, self.rsi_period)

        if sol_ema is None or sol_rsi is None:
            return None

        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        sol_ret = ctx.features.get("sol_return_pct", 0.0)

        in_cooldown = (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars

        # Manage open position exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()

            if pos_dir == "long":
                # Exit when ETH driver loses momentum, RSI hits extreme overbought, or price breaks EMA
                if eth_ret < 0.25 or sol_rsi > 78.0 or sol_close < sol_ema * 0.992:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_momentum_flattened",
                            "eth_ret": eth_ret,
                            "sol_ret": sol_ret,
                            "sol_rsi": sol_rsi,
                            "sol_ema": sol_ema,
                            "sol_close": sol_close,
                        },
                    )

            elif pos_dir == "short":
                # Exit when ETH downward momentum stops, RSI hits extreme oversold, or price recovers EMA
                if eth_ret > -0.25 or sol_rsi < 22.0 or sol_close > sol_ema * 1.008:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_momentum_flattened",
                            "eth_ret": eth_ret,
                            "sol_ret": sol_ret,
                            "sol_rsi": sol_rsi,
                            "sol_ema": sol_ema,
                            "sol_close": sol_close,
                        },
                    )

            return None

        # Cooldown guard before new entries
        if in_cooldown:
            return None

        # Long Entry: Strong positive ETH return, confirmed by positive SOL return & price above EMA
        if (
            eth_ret >= self.eth_long_threshold
            and sol_ret >= self.sol_confirm_threshold
            and sol_close > sol_ema
            and 42.0 < sol_rsi < 72.0
        ):
            confidence = min(0.9, 0.55 + (eth_ret - self.eth_long_threshold) * 0.08)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_beta_breakout_long",
                    "eth_ret": eth_ret,
                    "sol_ret": sol_ret,
                    "sol_rsi": sol_rsi,
                    "sol_ema": sol_ema,
                    "sol_close": sol_close,
                },
            )

        # Short Entry: Strong negative ETH return, confirmed by negative SOL return & price below EMA
        if (
            eth_ret <= self.eth_short_threshold
            and sol_ret <= -self.sol_confirm_threshold
            and sol_close < sol_ema
            and 28.0 < sol_rsi < 58.0
        ):
            confidence = min(0.9, 0.55 + (abs(eth_ret) - abs(self.eth_short_threshold)) * 0.08)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_beta_breakdown_short",
                    "eth_ret": eth_ret,
                    "sol_ret": sol_ret,
                    "sol_rsi": sol_rsi,
                    "sol_ema": sol_ema,
                    "sol_close": sol_close,
                },
            )

        return None