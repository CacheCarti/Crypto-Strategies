from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingSqueezeSelective(Strategy):
    METADATA = {
        "name": "FundingSqueezeSelective",
        "domain": "btc_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 28800,  # 8 hours average hold
        "warmup_bars": 40,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_period = 10
        self.last_exit_bar = -100
        self.bars_in_position = 0
        self.max_hold_bars = 18

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return 50.0
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
        self.bars_in_position = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        if len(closes) < 30:
            return None

        # Manage existing position time exit
        if ctx.has_position():
            self.bars_in_position += 1
            if self.bars_in_position >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "time_based_exit",
                        "bars_in_pos": self.bars_in_position,
                        "close": ctx.bar.close,
                    },
                )
            return None

        # Cooldown guard after recent exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_period:
            return None

        # Skip during abnormal or meltdown regimes to avoid volatility spikes
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # 24-bar price return (momentum of squeeze)
        p_now = closes[-1]
        p_24 = closes[-25]
        ret_24 = (p_now - p_24) / p_24

        funding = ctx.features.get("funding_rate_btcusdt", 0.0)
        rsi = self._rsi(closes, period=14)

        # Breakout / Momentum condition using recent high/low
        highs = ctx.highs(24)
        lows = ctx.lows(24)
        max_high = max(highs[:-1])
        min_low = min(lows[:-1])

        # Long Squeeze: Price strongly advancing (>= 1.5% and new 24h high) while funding stays distinctly negative
        # Trapped shorts are forced to cover into continuation
        if funding < -0.0001 and ret_24 >= 0.015 and p_now >= max_high and rsi < 75.0:
            confidence = min(0.9, 0.65 + abs(funding) * 500.0)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "short_squeeze_continuation",
                    "funding_rate": funding,
                    "ret_24": ret_24,
                    "rsi": rsi,
                    "close": p_now,
                },
            )

        # Short Squeeze / Liquidation Cascade: Price dropping (<= -1.5% and new 24h low) while funding stays distinctly positive
        # Trapped longs are forced to sell into decline
        if funding > 0.00015 and ret_24 <= -0.015 and p_now <= min_low and rsi > 25.0:
            confidence = min(0.9, 0.65 + abs(funding) * 500.0)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "long_cascade_continuation",
                    "funding_rate": funding,
                    "ret_24": ret_24,
                    "rsi": rsi,
                    "close": p_now,
                },
            )

        return None