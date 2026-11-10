from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingSqueezeBtc(Strategy):
    METADATA = {
        "name": "FundingSqueezeBtc",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 21600,  # 6h expected hold for swing
        "warmup_bars": 35,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_bars = 24
        self.cooldown_bars = 12
        self.last_exit_bar = -999
        self.bars_since_entry = 0

    def _donchian(self, values, period):
        if len(values) < period:
            return None, None
        return max(values[-period:]), min(values[-period:])

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.bars_since_entry = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_bars + 5)
        highs = ctx.highs(self.lookback_bars + 5)
        lows = ctx.lows(self.lookback_bars + 5)

        if len(closes) < self.lookback_bars + 2:
            return None

        # Post-exit cooldown gate
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Position tracking and max-hold timeout guard
        if ctx.has_position():
            self.bars_since_entry += 1
            if self.bars_since_entry >= 16:  # 16-bar holding ceiling
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "time_based_horizon_exit", "bars_held": self.bars_since_entry}
                )
            return None

        # Check market stress regime
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ["CRISIS", "MELTDOWN"]:
            return None

        # 24-bar price return
        price_ret = (closes[-1] - closes[-self.lookback_bars]) / closes[-self.lookback_bars]

        # Current hourly funding rate
        funding_rate = ctx.features.get("funding_rate_btcusdt", 0.0)

        # Breakout event boundaries (strict transition trigger rather than permanent state)
        # Prevents overtrading while capturing squeeze impulses
        prev_highs = highs[-self.lookback_bars - 1 : -1]
        prev_lows = lows[-self.lookback_bars - 1 : -1]
        
        highest_prev = max(prev_highs)
        lowest_prev = min(prev_lows)

        current_close = closes[-1]
        prev_close = closes[-2]

        # Setup 1: Short Squeeze (Price breaks 24h high while funding is distinctly negative)
        # Stricter event: cross above recent high + return > 1.2% + persistent negative funding
        is_long_breakout = (current_close > highest_prev) and (prev_close <= highest_prev)
        short_crowded = (funding_rate < -0.00005) and (price_ret > 0.012)

        if is_long_breakout and short_crowded:
            self.bars_since_entry = 0
            conf = min(0.9, 0.65 + abs(funding_rate) * 2000.0)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "short_squeeze_breakout",
                    "price_ret_24b": round(price_ret, 4),
                    "funding_rate": round(funding_rate, 6),
                    "breakout_level": round(highest_prev, 2),
                    "price": round(current_close, 2),
                }
            )

        # Setup 2: Long Squeeze (Price breaks 24h low while funding is distinctly positive)
        # Stricter event: cross below recent low + return < -1.2% + persistent positive funding
        is_short_breakout = (current_close < lowest_prev) and (prev_close >= lowest_prev)
        long_crowded = (funding_rate > 0.00015) and (price_ret < -0.012)

        if is_short_breakout and long_crowded:
            self.bars_since_entry = 0
            conf = min(0.9, 0.65 + funding_rate * 1500.0)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "long_squeeze_breakout",
                    "price_ret_24b": round(price_ret, 4),
                    "funding_rate": round(funding_rate, 6),
                    "breakout_level": round(lowest_prev, 2),
                    "price": round(current_close, 2),
                }
            )

        return None