from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingRateReversion(Strategy):
    METADATA = {
        "name": "Funding Rate Contrarian Reversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 18000,  # ~5 hours average hold
        "warmup_bars": 50,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_history = []
        self.last_exit_bar = -999
        self.cooldown_bars = 6
        self.pos_entry_bar = -999

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
        closes = ctx.closes(55)
        if len(closes) < 50:
            return None

        # Track persistent funding rate
        fr = ctx.features.get("funding_rate_ethusdt", 0.0)
        self.funding_history.append(fr)
        if len(self.funding_history) > 10:
            self.funding_history.pop(0)

        # Require at least 3 bars of funding observation
        if len(self.funding_history) < 3:
            return None

        avg_fr_3 = sum(self.funding_history[-3:]) / 3.0
        rsi = self._rsi(closes, period=14)
        ema50 = self._ema(closes, period=50)

        if rsi is None or ema50 is None:
            return None

        current_close = closes[-1]
        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Handle active position exit conditions
        if has_pos:
            # Normalize exit: funding returned to neutral band or RSI hit extreme
            if pos_dir == "long":
                if avg_fr_3 >= -0.00002 or rsi > 68.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_funding_normalized_or_overbought",
                            "avg_fr_3": avg_fr_3,
                            "rsi": rsi,
                            "close": current_close
                        }
                    )
            elif pos_dir == "short":
                if avg_fr_3 <= 0.00005 or rsi < 32.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_funding_normalized_or_oversold",
                            "avg_fr_3": avg_fr_3,
                            "rsi": rsi,
                            "close": current_close
                        }
                    )
            return None

        # Mandatory cooldown check after any position exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Market regime filter: reject trading in chaotic crisis environments
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Long Setup:
        # 1. Crowd heavily short (funding <= -0.00010 = -0.01% per 8h) sustained over 3 bars
        # 2. RSI < 42 (local pullback / oversold bias, not extended upside)
        # 3. Not in terminal collapse (close must not be > 5% below EMA50)
        if (
            self.funding_history[-1] <= -0.00010
            and self.funding_history[-2] <= -0.00008
            and avg_fr_3 <= -0.00010
            and rsi < 42.0
            and current_close > ema50 * 0.95
        ):
            confidence = min(1.0, 0.65 + abs(avg_fr_3) * 1500)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=350.0,
                take_profit_bps=550.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "negative_funding_squeeze_pullback",
                    "avg_fr_3": avg_fr_3,
                    "rsi": rsi,
                    "ema50": ema50,
                    "close": current_close
                }
            )

        # Short Setup:
        # 1. Crowd heavily long & euphoric (funding >= 0.00030 = +0.03% per 8h) sustained over 3 bars
        # 2. RSI > 58 (extended / overbought momentum stalling)
        # 3. Not in runaway parabolic breakout (close must not be > 5% above EMA50)
        if (
            self.funding_history[-1] >= 0.00030
            and self.funding_history[-2] >= 0.00025
            and avg_fr_3 >= 0.00030
            and rsi > 58.0
            and current_close < ema50 * 1.05
        ):
            confidence = min(1.0, 0.65 + avg_fr_3 * 1000)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=350.0,
                take_profit_bps=550.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "positive_funding_fade_overbought",
                    "avg_fr_3": avg_fr_3,
                    "rsi": rsi,
                    "ema50": ema50,
                    "close": current_close
                }
            )

        return None