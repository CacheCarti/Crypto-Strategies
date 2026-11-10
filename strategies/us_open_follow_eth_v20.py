from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import statistics

class USOpenMomentumContinuation(Strategy):
    METADATA = {
        "name": "US Open Momentum Continuation",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,  # up to 6 hours hold
        "warmup_bars": 48,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_day = -1
        self.last_exit_bar = -100
        self.cooldown_bars = 4

    def _atr_series(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return trs

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(48)
        if len(closes) < 48:
            return None

        bar_time = ctx.bar.timestamp
        current_hour = bar_time.hour
        current_day = bar_time.toordinal()

        # Exit trigger: Mandated session flat by 21:00 UTC
        if ctx.has_position():
            if current_hour >= 21 or current_hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_utc21", "hour": current_hour, "bar_index": ctx.bar_index}
                )
            return None

        # Enforce cooldown after prior exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Strict event boundary: Enter exactly at the close of 15:00 UTC (i.e. start of 16:00 bar)
        # Evaluates the opening move of UTC 13:00 to 15:00
        if current_hour != 15:
            return None

        # Max 1 trade per day
        if current_day == self.last_traded_day:
            return None

        # Regime safety filter from market context
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        highs = ctx.highs(48)
        lows = ctx.lows(48)
        opens = ctx.opens(48)

        # 2-hour window: bar at 14:00 (index -2) and bar at 15:00 (index -1)
        # Session open price at 13:00 UTC corresponds to open of bar -2
        window_open = opens[-2]
        window_close = closes[-1]
        window_high = max(highs[-2], highs[-1])
        window_low = min(lows[-2], lows[-1])

        window_move = window_close - window_open
        window_range = window_high - window_low
        if window_range <= 0:
            return None

        # Compute rolling median 2-bar range over previous 40 bars (excluding current window)
        two_bar_ranges = []
        for i in range(len(highs) - 4, max(0, len(highs) - 40), -2):
            r_high = max(highs[i], highs[i + 1])
            r_low = min(lows[i], lows[i + 1])
            two_bar_ranges.append(r_high - r_low)

        if len(two_bar_ranges) < 10:
            return None

        median_range = statistics.median(two_bar_ranges)
        if median_range <= 0:
            return None

        # 1) Opening move must exceed median range (expansion condition)
        # 2) Directionality: window_move must comprise at least 55% of the total 2-hour range
        # 3) Not overextended: move must not exceed 2.6x the median range
        abs_move = abs(window_move)
        expansion_ratio = abs_move / median_range
        efficiency_ratio = abs_move / window_range

        if not (1.05 <= expansion_ratio <= 2.6):
            return None

        if efficiency_ratio < 0.55:
            return None

        # Long continuation setup
        if window_move > 0:
            self.last_traded_day = current_day
            conf = min(0.9, 0.6 + (efficiency_ratio - 0.55) * 0.5)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_long_continuation",
                    "expansion_ratio": round(expansion_ratio, 2),
                    "efficiency_ratio": round(efficiency_ratio, 2),
                    "window_move": round(window_move, 2),
                    "median_range": round(median_range, 2),
                }
            )

        # Short continuation setup
        elif window_move < 0:
            self.last_traded_day = current_day
            conf = min(0.9, 0.6 + (efficiency_ratio - 0.55) * 0.5)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_short_continuation",
                    "expansion_ratio": round(expansion_ratio, 2),
                    "efficiency_ratio": round(efficiency_ratio, 2),
                    "window_move": round(window_move, 2),
                    "median_range": round(median_range, 2),
                }
            )

        return None