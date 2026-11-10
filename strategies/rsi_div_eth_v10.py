from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any, List

class RsiDivergencePivot(Strategy):
    METADATA = {
        "name": "RsiDivergencePivot",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 21600,  # 6 hours
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.lookback = 32
        self.last_exit_bar = -999
        self.cooldown_bars = 6

    def _calc_rsi(self, closes: List[float], period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        gains, losses = 0.0, 0.0
        for i in range(1, period + 1):
            diff = closes[i] - closes[i - 1]
            if diff >= 0:
                gains += diff
            else:
                losses -= diff
        avg_gain = gains / period
        avg_loss = losses / period
        for i in range(period + 1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gain = diff if diff > 0 else 0.0
            loss = -diff if diff < 0 else 0.0
            avg_gain = (avg_gain * (period - 1) + gain) / period
            avg_loss = (avg_loss * (period - 1) + loss) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        need_bars = self.lookback + self.rsi_period + 4
        closes = ctx.closes(need_bars)
        lows = ctx.lows(need_bars)
        highs = ctx.highs(need_bars)

        if len(closes) < need_bars:
            return None

        # Build RSI series for divergence search
        rsi_series: List[float] = []
        for end_idx in range(len(closes) - self.lookback, len(closes) + 1):
            sub_closes = closes[:end_idx]
            r = self._calc_rsi(sub_closes, self.rsi_period)
            if r is not None:
                rsi_series.append(r)

        if len(rsi_series) < self.lookback + 1:
            return None

        curr_rsi = rsi_series[-1]
        prev_rsi = rsi_series[-2]
        curr_close = closes[-1]

        # Manage open position exits
        if ctx.has_position():
            direction = ctx.position_direction()
            bars_held = 0
            if ctx.position and "entry_bar" in ctx.position:
                bars_held = ctx.bar_index - ctx.position["entry_bar"]

            # Mean-reversion target reached or RSI normalized
            if direction == "long" and curr_rsi >= 55.0:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "rsi_reversion_long_exit", "rsi": curr_rsi})
            elif direction == "short" and curr_rsi <= 45.0:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "rsi_reversion_short_exit", "rsi": curr_rsi})
            return None

        # Cooldown guard
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Detect swing pivots over lookback window (excluding current candle)
        # We need a clear prior pivot to anchor against the current swing
        recent_lows = lows[-self.lookback:]
        recent_highs = highs[-self.lookback:]
        min_prev_low = min(recent_lows[:-2])
        min_prev_idx = recent_lows.index(min_prev_low)
        max_prev_high = max(recent_highs[:-2])
        max_prev_idx = recent_highs.index(max_prev_high)

        # Bullish Divergence Trigger:
        # Price made a lower low recently, but current RSI is higher than RSI at that price low,
        # plus clear bounce confirmation on current candle (RSI hooked up from oversold region).
        if lows[-2] <= min_prev_low and curr_close > closes[-2]:
            prior_rsi_at_low = rsi_series[min_prev_idx]
            if prior_rsi_at_low < 35.0 and curr_rsi > prior_rsi_at_low and curr_rsi > prev_rsi:
                return ctx.signal(
                    "long",
                    confidence=0.8,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "bullish_rsi_div_hook",
                        "rsi": curr_rsi,
                        "prior_rsi": prior_rsi_at_low,
                        "low": lows[-2],
                        "prior_low": min_prev_low,
                    },
                )

        # Bearish Divergence Trigger:
        # Price made a higher high, but current RSI is lower than RSI at that peak,
        # plus rejection confirmation on current candle (RSI hooked down from overbought region).
        if highs[-2] >= max_prev_high and curr_close < closes[-2]:
            prior_rsi_at_high = rsi_series[max_prev_idx]
            if prior_rsi_at_high > 65.0 and curr_rsi < prior_rsi_at_high and curr_rsi < prev_rsi:
                return ctx.signal(
                    "short",
                    confidence=0.8,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "bearish_rsi_div_rejection",
                        "rsi": curr_rsi,
                        "prior_rsi": prior_rsi_at_high,
                        "high": highs[-2],
                        "prior_high": max_prev_high,
                    },
                )

        return None