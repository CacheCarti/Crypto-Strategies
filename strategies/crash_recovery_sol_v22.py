from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPanicRebound(Strategy):
    METADATA = {
        "name": "SolPanicRebound",
        "domain": "sol_usdc",
        "declared_sl_bps": 450.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 21600,  # ~6 hours target swing
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.min_drop_pct = -0.065       # 6.5% flush over 24h
        self.cooldown_bars = 8           # Wait 8 bars (~8 hours) after any exit
        self.last_exit_bar = -999
        self.entry_panic_low = 0.0
        self.target_midpoint = 0.0
        self.bars_in_trade = 0

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        highs = ctx.highs(30)
        lows = ctx.lows(30)

        if len(closes) < 26:
            return None

        # Position Management
        if ctx.has_position():
            self.bars_in_trade += 1
            curr_price = ctx.bar.close

            # Dynamic Volatility & Structure-based exit checks
            # 1. Structure Exit: reached or surpassed the panic flush midpoint
            if self.target_midpoint > 0 and curr_price >= self.target_midpoint:
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "flush_midpoint_reversion_target_hit",
                        "price": curr_price,
                        "target_midpoint": self.target_midpoint,
                        "bars_held": self.bars_in_trade
                    }
                )

            # 2. Structural Stop: price penetrates below the stabilization low
            if self.entry_panic_low > 0 and curr_price < self.entry_panic_low:
                return ctx.signal(
                    "flat",
                    confidence=0.85,
                    metadata={
                        "reason": "structure_break_below_panic_low",
                        "price": curr_price,
                        "entry_panic_low": self.entry_panic_low,
                        "bars_held": self.bars_in_trade
                    }
                )

            # 3. Timed decay exit: if thesis has not resolved in 14 bars (14 hours), take dynamic exit
            if self.bars_in_trade >= 14:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "timed_thesis_expiration",
                        "price": curr_price,
                        "bars_held": self.bars_in_trade
                    }
                )

            return None

        # Entry logic (long only)
        # Cooldown guard: prevent over-trading in choppy follow-throughs
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Calculate 24-bar drop
        p_24_ago = closes[-25]
        curr_close = closes[-1]
        drop_pct = (curr_close - p_24_ago) / p_24_ago

        # Check for severe sell-off (> 6.5% drop in 24 bars)
        if drop_pct <= self.min_drop_pct:
            prev_high = highs[-2]
            # Stabilization trigger: current bar pushes above previous bar's high
            if curr_close > prev_high and closes[-1] > ctx.bar.open:
                # Flush range context
                rolling_high = max(highs[-25:])
                panic_low = min(lows[-6:])
                midpoint = (rolling_high + panic_low) / 2.0

                # Ensure sufficient upside potential to the midpoint (> 1.2% room)
                if midpoint > curr_close * 1.012:
                    atr = self._atr(highs, lows, closes, 14)
                    if atr is None:
                        atr = curr_close * 0.015

                    # Risk sizing based on distance to structural panic low
                    dist_to_low_bps = ((curr_close - panic_low) / curr_close) * 10000.0
                    sl_bps = max(250.0, min(500.0, dist_to_low_bps + 40.0))
                    tp_bps = max(350.0, min(650.0, ((midpoint - curr_close) / curr_close) * 10000.0))

                    fg = ctx.features.get("fear_greed_index", 50)
                    confidence = 0.70 if fg < 40 else 0.60

                    self.entry_panic_low = panic_low
                    self.target_midpoint = midpoint
                    self.bars_in_trade = 0

                    return ctx.signal(
                        "long",
                        confidence=confidence,
                        stop_loss_bps=sl_bps,
                        take_profit_bps=tp_bps,
                        horizon_seconds=21600,
                        metadata={
                            "reason": "sol_panic_flush_reversal_confirmation",
                            "drop_pct_24b": round(drop_pct * 100, 2),
                            "panic_low": panic_low,
                            "midpoint_target": round(midpoint, 2),
                            "fear_greed": fg,
                            "atr": round(atr, 3)
                        }
                    )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_panic_low = 0.0
        self.target_midpoint = 0.0
        self.bars_in_trade = 0