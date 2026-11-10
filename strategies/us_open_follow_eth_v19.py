from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import statistics

class UsOpenContinuation(Strategy):
    METADATA = {
        "name": "US Open Continuation ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,  # Max intraday hold ~6 hours
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_date = None
        self.entry_bar_index = -999
        self.cooldown_bars = 4

    def _median_candle_range(self, highs: list, lows: list, period: int = 40) -> float:
        ranges = [h - l for h, l in zip(highs[-period:], lows[-period:])]
        return statistics.median(ranges) if ranges else 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        highs = ctx.highs(50)
        lows = ctx.lows(50)
        opens = ctx.opens(50)

        if len(closes) < 50:
            return None

        current_dt = ctx.bar.timestamp
        current_date = current_dt.date()
        hour = current_dt.hour

        # Force exit by 21:00 UTC regardless of PnL
        if ctx.has_position():
            if hour >= 21 or hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_timeout", "hour": hour, "close": ctx.bar.close}
                )
            return None

        # Check cooldown and one-trade-per-day restriction
        if self.last_traded_date == current_date:
            return None

        if (ctx.bar_index - self.entry_bar_index) < self.cooldown_bars:
            return None

        # Only evaluate at UTC 15:00 (end of 13:00-15:00 2-hour opening window)
        if hour != 15:
            return None

        # Two-hour window: bar at 13:00 open was 2 bars ago open: opens[-2]
        # Current bar is 15:00 (closing at 16:00 UTC) -> evaluate move from open[-2] to close[-1]
        session_open = opens[-2]
        session_close = closes[-1]
        window_high = max(highs[-2], highs[-1])
        window_low = min(lows[-2], lows[-1])

        session_move = session_close - session_open
        session_span = max(window_high - window_low, 1e-6)

        # Baseline range benchmark: 40-bar median of hourly ranges
        median_range = self._median_candle_range(highs, lows, period=40)
        if median_range <= 0.0:
            return None

        # The net 2-hour move must exceed recent median single-bar range (directionality)
        abs_move = abs(session_move)
        if abs_move < (1.2 * median_range):
            return None

        # Directional efficiency ratio: move vs total span (avoids erratic choppy candles)
        efficiency = abs_move / session_span
        if efficiency < 0.60:
            return None

        # Avoid extreme overextension: bar close shouldn't be too stretched vs 48h trend
        sma_slow = sum(closes[-48:]) / 48.0
        distance_pct = (ctx.bar.close - sma_slow) / sma_slow

        # Skip if severe crisis/meltdown
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Long continuation
        if session_move > 0 and distance_pct < 0.045:
            self.last_traded_date = current_date
            self.entry_bar_index = ctx.bar_index
            conf = min(0.9, 0.5 + 0.3 * efficiency)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=220.0,
                take_profit_bps=380.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "session_move": round(session_move, 2),
                    "efficiency": round(efficiency, 3),
                    "median_range": round(median_range, 2),
                    "dist_sma": round(distance_pct, 4)
                }
            )

        # Short continuation
        if session_move < 0 and distance_pct > -0.045:
            self.last_traded_date = current_date
            self.entry_bar_index = ctx.bar_index
            conf = min(0.9, 0.5 + 0.3 * efficiency)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=220.0,
                take_profit_bps=380.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "session_move": round(session_move, 2),
                    "efficiency": round(efficiency, 3),
                    "median_range": round(median_range, 2),
                    "dist_sma": round(distance_pct, 4)
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.entry_bar_index = ctx.bar_index