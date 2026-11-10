from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthFailedBreakoutFade(Strategy):
    METADATA = {
        "name": "EthFailedBreakoutFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 330.0,
        "declared_hold_seconds": 18000,  # ~5 hours
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 20
        self.cooldown_bars = 6
        self.max_hold_bars = 6
        self.last_exit_bar = -999
        self.entry_bar = -999

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_bar = -999

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        total_bars_needed = self.lookback + 14 + 1
        closes = ctx.closes(total_bars_needed)
        if len(closes) < total_bars_needed:
            return None

        highs = ctx.highs(total_bars_needed)
        lows = ctx.lows(total_bars_needed)

        # Handle time-based exit if held too long
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "time_based_exit", "bars_held": bars_held, "price": ctx.bar.close}
                )
            return None

        # Check mandatory cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime filter: avoid taking counter-trend traps during extreme market crisis
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        atr = self._atr(highs, lows, closes, 14)
        if atr is None or atr <= 0:
            return None

        # 20-bar high/low strictly excluding the current bar
        prior_highs = highs[-self.lookback - 1:-1]
        prior_lows = lows[-self.lookback - 1:-1]
        highest_prev = max(prior_highs)
        lowest_prev = min(prior_lows)

        curr_high = ctx.bar.high
        curr_low = ctx.bar.low
        curr_close = ctx.bar.close

        # Require a clear wick poke beyond prior range to confirm a genuine stop-hunt (>= 0.25 ATR)
        min_penetration = 0.25 * atr
        
        # Bull Trap / Failed Breakout above high:
        # Poked meaningfully above range high, but closed back firmly inside
        if curr_high >= highest_prev + min_penetration and curr_close < highest_prev:
            wick_size = curr_high - max(ctx.bar.open, curr_close)
            candle_range = max(curr_high - curr_low, 1e-6)
            # Rejection confirmation: wick should comprise at least 35% of the total candle range
            if wick_size / candle_range >= 0.35:
                stop_bps = max(100.0, min(260.0, ((curr_high - curr_close) / curr_close) * 10000.0 + 20.0))
                tp_bps = stop_bps * 1.5
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=stop_bps,
                    take_profit_bps=tp_bps,
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "failed_breakout_bear_trap",
                        "high": curr_high,
                        "prior_high": highest_prev,
                        "close": curr_close,
                        "atr": atr,
                        "stop_loss_bps": stop_bps
                    }
                )

        # Bear Trap / Failed Breakdown below low:
        # Poked meaningfully below range low, but closed back firmly inside
        if curr_low <= lowest_prev - min_penetration and curr_close > lowest_prev:
            wick_size = min(ctx.bar.open, curr_close) - curr_low
            candle_range = max(curr_high - curr_low, 1e-6)
            # Rejection confirmation: lower wick should comprise at least 35% of total candle range
            if wick_size / candle_range >= 0.35:
                stop_bps = max(100.0, min(260.0, ((curr_close - curr_low) / curr_close) * 10000.0 + 20.0))
                tp_bps = stop_bps * 1.5
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=stop_bps,
                    take_profit_bps=tp_bps,
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "failed_breakout_bull_trap",
                        "low": curr_low,
                        "prior_low": lowest_prev,
                        "close": curr_close,
                        "atr": atr,
                        "stop_loss_bps": stop_bps
                    }
                )

        return None