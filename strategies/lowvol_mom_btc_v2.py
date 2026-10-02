from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any, List
import math
import statistics

class LowVolTrendFollower(Strategy):
    METADATA = {
        "name": "LowVolTrendFollower",
        "domain": "btc_usdc",
        "declared_sl_bps": 240.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 240,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_period = 25
        self.vol_short_window = 24
        self.vol_lookback = 200
        self.slope_lookback = 3
        self.cooldown_bars = 6
        self.last_exit_bar = -100

    def _ema(self, values: List[float], period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _stddev(self, values: List[float]) -> float:
        n = len(values)
        if n < 2:
            return 0.0
        mean = sum(values) / n
        var = sum((x - mean) ** 2 for x in values) / (n - 1)
        return math.sqrt(max(0.0, var))

    def _get_vol_stats(self, closes: List[float]) -> Optional[tuple]:
        total_needed = self.vol_lookback + self.vol_short_window + 2
        if len(closes) < total_needed:
            return None
        
        # Calculate returns
        returns = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes))]
        
        # Current short-window realized vol (annualized or per-bar bps)
        current_vol = self._stddev(returns[-self.vol_short_window:]) * 10000.0
        
        # Rolling short-window vols over the lookback history
        vol_history = []
        step = max(1, self.vol_lookback // 50)  # sample for high performance
        end_idx = len(returns)
        start_idx = end_idx - self.vol_lookback
        
        for i in range(start_idx, end_idx, step):
            slice_ret = returns[i - self.vol_short_window:i]
            if len(slice_ret) == self.vol_short_window:
                vol_history.append(self._stddev(slice_ret) * 10000.0)
                
        if not vol_history:
            return None
            
        median_vol = statistics.median(vol_history)
        return current_vol, median_vol

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        needed_bars = self.vol_lookback + self.vol_short_window + self.ema_period + 10
        closes = ctx.closes(needed_bars)
        if len(closes) < needed_bars:
            return None

        current_close = closes[-1]
        
        # Calculate EMA values for current bar and past bar for slope
        ema_now = self._ema(closes, self.ema_period)
        ema_prev = self._ema(closes[:-self.slope_lookback], self.ema_period)
        if ema_now is None or ema_prev is None:
            return None
        
        ema_slope = (ema_now - ema_prev) / ema_prev

        # Realized volatility metrics
        vol_stats = self._get_vol_stats(closes)
        if vol_stats is None:
            return None
        current_vol, median_vol = vol_stats

        # Regime evaluation: calm tape when current vol is meaningfully below long-term median
        is_calm_tape = current_vol < (median_vol * 0.95)
        is_vol_spike = current_vol > (median_vol * 1.45)
        
        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Handle active position management / exits
        if has_pos:
            # Stand down / exit if volatility violently spikes or trend structure breaks
            if pos_dir == "long":
                if current_close < ema_now or is_vol_spike:
                    self.last_exit_bar = ctx.bar_index
                    reason = "vol_spike_exit" if is_vol_spike else "ema_trend_break_long"
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": reason,
                        "current_vol": round(current_vol, 2),
                        "median_vol": round(median_vol, 2),
                        "close": current_close,
                        "ema": round(ema_now, 2)
                    })
            elif pos_dir == "short":
                if current_close > ema_now or is_vol_spike:
                    self.last_exit_bar = ctx.bar_index
                    reason = "vol_spike_exit" if is_vol_spike else "ema_trend_break_short"
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": reason,
                        "current_vol": round(current_vol, 2),
                        "median_vol": round(median_vol, 2),
                        "close": current_close,
                        "ema": round(ema_now, 2)
                    })
            return None

        # Cooldown guard: enforce rest period after any trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Entry logic: only deploy in calm tape with clear trend momentum
        if is_calm_tape:
            # Long condition: price above EMA, EMA sloping upward with minimum threshold
            if current_close > ema_now and ema_slope > 0.001:
                confidence = min(0.85, 0.55 + max(0.0, (median_vol - current_vol) / (median_vol + 1e-6)) * 0.3)
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "calm_tape_bullish_trend_continuation",
                        "current_vol": round(current_vol, 2),
                        "median_vol": round(median_vol, 2),
                        "ema_slope": round(ema_slope * 10000, 2),
                        "close": current_close,
                        "ema": round(ema_now, 2)
                    }
                )

            # Short condition: price below EMA, EMA sloping downward
            if current_close < ema_now and ema_slope < -0.001:
                confidence = min(0.85, 0.55 + max(0.0, (median_vol - current_vol) / (median_vol + 1e-6)) * 0.3)
                return ctx.signal(
                    "short",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "calm_tape_bearish_trend_continuation",
                        "current_vol": round(current_vol, 2),
                        "median_vol": round(median_vol, 2),
                        "ema_slope": round(ema_slope * 10000, 2),
                        "close": current_close,
                        "ema": round(ema_now, 2)
                    }
                )

        return None