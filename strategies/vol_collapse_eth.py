from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class PostSpikeCoilBreakout(Strategy):
    METADATA = {
        "name": "PostSpikeCoilBreakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 190,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.atr_period = 12
        self.median_lookback = 168
        self.spike_ratio = 1.75
        self.spike_memory_bars = 36
        self.breakout_period = 12
        self.cooldown_bars = 10
        self.last_trade_bar = -999
        self.last_spike_bar = -999

    def _calc_atr_series(self, highs, lows, closes, period):
        n = len(closes)
        if n < period + 1:
            return []
        trs = []
        for i in range(1, n):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        
        atrs = []
        for i in range(period, len(trs) + 1):
            window = trs[i - period:i]
            atrs.append(sum(window) / period)
        return atrs

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        total_needed = self.median_lookback + self.atr_period + 5
        closes = ctx.closes(total_needed)
        if len(closes) < total_needed or ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        # Check post-trade cooldown
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        highs = ctx.highs(total_needed)
        lows = ctx.lows(total_needed)

        atrs = self._calc_atr_series(highs, lows, closes, self.atr_period)
        if len(atrs) < self.median_lookback:
            return None

        recent_atrs = atrs[-self.median_lookback:]
        sorted_atrs = sorted(recent_atrs)
        median_atr = sorted_atrs[len(sorted_atrs) // 2]
        if median_atr <= 0:
            return None

        current_atr = atrs[-1]

        # Detect volatility spike
        if current_atr >= median_atr * self.spike_ratio:
            self.last_spike_bar = ctx.bar_index

        # Evaluate coiled state (chaos -> compression)
        bars_since_spike = ctx.bar_index - self.last_spike_bar
        is_coiled = (bars_since_spike <= self.spike_memory_bars) and (current_atr <= median_atr * 1.05)

        if not is_coiled:
            return None

        # Donchian channel breakout levels over preceding bars (excluding current bar)
        breakout_highs = ctx.highs(self.breakout_period + 1)[:-1]
        breakout_lows = ctx.lows(self.breakout_period + 1)[:-1]
        if len(breakout_highs) < self.breakout_period or len(breakout_lows) < self.breakout_period:
            return None

        highest_high = max(breakout_highs)
        lowest_low = min(breakout_lows)
        current_close = ctx.bar.close

        # Crisis filter
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.80:
            return None

        fg_index = ctx.features.get("fear_greed_index", 50.0)

        # Signal logic: Long breakout
        if current_close > highest_high:
            self.last_trade_bar = ctx.bar_index
            self.last_spike_bar = -999  # Reset coil state upon release
            confidence = 0.70
            if fg_index > 55:
                confidence = 0.85
            elif fg_index < 30:
                confidence = 0.60

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "post_spike_coil_long_breakout",
                    "current_atr": round(current_atr, 4),
                    "median_atr": round(median_atr, 4),
                    "bars_since_spike": bars_since_spike,
                    "breakout_level": round(highest_high, 2),
                    "price": round(current_close, 2),
                    "fear_greed": fg_index,
                },
            )

        # Signal logic: Short breakout
        elif current_close < lowest_low:
            self.last_trade_bar = ctx.bar_index
            self.last_spike_bar = -999  # Reset coil state upon release
            confidence = 0.70
            if fg_index < 45:
                confidence = 0.85
            elif fg_index > 70:
                confidence = 0.60

            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "post_spike_coil_short_breakout",
                    "current_atr": round(current_atr, 4),
                    "median_atr": round(median_atr, 4),
                    "bars_since_spike": bars_since_spike,
                    "breakout_level": round(lowest_low, 2),
                    "price": round(current_close, 2),
                    "fear_greed": fg_index,
                },
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index