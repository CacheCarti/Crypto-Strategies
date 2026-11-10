from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingSqueezeMomentum(Strategy):
    METADATA = {
        "name": "Funding Squeeze Momentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 18000,   # ~5 hours average hold
        "warmup_bars": 40,
        "required_features": ["funding_rate_btcusdt"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 8
        self.last_exit_bar = -100
        self.min_ret_threshold = 0.015     # 1.5% 24h price displacement
        self.funding_long_thresh = -0.00005 # Negative funding (shorts trapped)
        self.funding_short_thresh = 0.00015 # Moderately elevated positive funding (longs trapped)

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 15)
        if len(closes) < self.lookback + 5:
            return None

        # Cooldown guard to control trade count and friction
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        curr_close = closes[-1]
        past_close = closes[-self.lookback]
        pct_return_24h = (curr_close - past_close) / past_close

        funding = ctx.features.get("funding_rate_btcusdt", 0.0)

        # Volatility & trend context to confirm breakout direction
        ema20 = self._ema(closes, 20)
        if ema20 is None:
            return None

        # Manage open position
        if ctx.has_position():
            direction = ctx.position_direction()
            # Mean reversion or structural breakdown exits
            if direction == "long":
                if curr_close < ema20 and pct_return_24h < 0.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={"reason": "exit_long_loss_of_momentum", "close": curr_close, "ema20": ema20}
                    )
            elif direction == "short":
                if curr_close > ema20 and pct_return_24h > 0.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={"reason": "exit_short_loss_of_momentum", "close": curr_close, "ema20": ema20}
                    )
            return None

        # Short squeeze continuation: Strong upward price move while funding remains negative
        short_squeeze = (pct_return_24h > self.min_ret_threshold) and (funding < self.funding_long_thresh) and (curr_close > ema20)

        # Long flush / liquidation cascade: Strong downward price move while funding stays persistently positive
        long_flush = (pct_return_24h < -self.min_ret_threshold) and (funding > self.funding_short_thresh) and (curr_close < ema20)

        if short_squeeze:
            # Scaled confidence by extent of funding divergence
            funding_divergence = min(abs(funding) * 10000.0, 0.4)
            conf = min(0.6 + funding_divergence, 0.95)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "short_squeeze_divergence",
                    "funding_rate": funding,
                    "return_24h": pct_return_24h,
                    "ema20": ema20,
                    "close": curr_close
                }
            )

        if long_flush:
            funding_divergence = min(funding * 10000.0, 0.4)
            conf = min(0.6 + funding_divergence, 0.95)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "long_trap_cascade",
                    "funding_rate": funding,
                    "return_24h": pct_return_24h,
                    "ema20": ema20,
                    "close": curr_close
                }
            )

        return None