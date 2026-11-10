from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthFundingContrarian(Strategy):
    METADATA = {
        "name": "EthFundingContrarian",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 21600,  # ~6 hours intended hold
        "warmup_bars": 40,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_long_thresh = -0.00015   # -0.015% funding per 8h (shorts heavily crowded)
        self.funding_short_thresh = 0.00030   # +0.030% funding per 8h (longs heavily crowded)
        self.funding_norm_long = 0.00000      # exit long once funding normalizes to neutral/positive
        self.funding_norm_short = 0.00010     # exit short once funding eases
        self.cooldown_bars = 6                # mandatory rest bars after position exit
        self.last_exit_bar = -100

    def _sma(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _rsi(self, closes: list, period: int = 14) -> Optional[float]:
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < 40:
            return None

        # Cooldown guard to avoid churn and friction bleed
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        current_funding = ctx.features.get("funding_rate_ethusdt", 0.0001)
        sma30 = self._sma(closes, 30)
        rsi14 = self._rsi(closes, 14)

        if sma30 is None or rsi14 is None:
            return None

        current_close = ctx.bar.close

        # Check existing position for thesis resolution
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long":
                # Exit when funding normalizes or price approaches local overbought
                if current_funding >= self.funding_norm_long or rsi14 > 65.0:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "funding_normalized_long_exit",
                            "funding": current_funding,
                            "rsi": rsi14,
                        },
                    )
            elif pos_dir == "short":
                # Exit when funding normalizes or price reaches oversold relief
                if current_funding <= self.funding_norm_short or rsi14 < 35.0:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "funding_normalized_short_exit",
                            "funding": current_funding,
                            "rsi": rsi14,
                        },
                    )
            return None

        # Entry logic: fade crowded positioning if not in runaway adverse trend
        # Long Setup: Funding heavily negative, but not in a falling knife breakdown (RSI not completely collapsed)
        if current_funding <= self.funding_long_thresh and rsi14 >= 30.0 and current_close > (sma30 * 0.965):
            confidence = min(0.85, 0.55 + abs(current_funding) * 1000.0)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=220.0,
                take_profit_bps=340.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "contrarian_short_squeeze_entry",
                    "funding": current_funding,
                    "rsi": rsi14,
                    "close": current_close,
                    "sma30": sma30,
                },
            )

        # Short Setup: Funding heavily positive, but not in a runaway vertical pump
        if current_funding >= self.funding_short_thresh and rsi14 <= 70.0 and current_close < (sma30 * 1.035):
            confidence = min(0.85, 0.55 + (current_funding - self.funding_short_thresh) * 1000.0)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=220.0,
                take_profit_bps=340.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "contrarian_long_squeeze_entry",
                    "funding": current_funding,
                    "rsi": rsi14,
                    "close": current_close,
                    "sma30": sma30,
                },
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index