from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPanicRecovery(Strategy):
    METADATA = {
        "name": "SolPanicRecovery",
        "domain": "sol_usdc",
        "declared_sl_bps": 550.0,
        "declared_tp_bps": 650.0,
        "declared_hold_seconds": 28800,  # ~8 hours target hold
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_drop = 24
        self.min_drop_pct = 0.065        # 6.5% - 7.0% flush threshold
        self.cooldown_bars = 10
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.flush_high = 0.0
        self.flush_low = 0.0
        self.target_price = 0.0
        self.stop_price = 0.0

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        highs = ctx.highs(30)
        lows = ctx.lows(30)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Manage open position with structural profit taking & time cap
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            curr_price = ctx.bar.close

            # Target 1: Midpoint of flush range
            if self.target_price > 0 and curr_price >= self.target_price:
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "flush_midpoint_target_reached",
                    "price": curr_price,
                    "target": self.target_price,
                    "bars_held": bars_held
                })

            # Time-based de-risk: if recovery doesn't happen within 14 bars (~14h), exit
            if bars_held >= 14:
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "flush_recovery_timeout",
                    "price": curr_price,
                    "bars_held": bars_held
                })

            return None

        # Cooldown guard after trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Check market regime guard: avoid absolute meltdown/crisis freeze
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime == "MELTDOWN":
            return None

        # 24-bar drop calculation
        past_closes = ctx.closes(self.lookback_drop + 1)
        if len(past_closes) < self.lookback_drop + 1:
            return None

        recent_high = max(past_closes)
        current_close = past_closes[-1]
        drop_pct = (recent_high - current_close) / recent_high

        # Look for stabilization bar: current close breaks above previous bar high
        prev_bar_high = highs[-2]
        is_stabilization = (current_close > prev_bar_high) and (current_close > ctx.bar.open)

        if drop_pct >= self.min_drop_pct and is_stabilization:
            # Measure the flush range
            recent_lows = lows[-self.lookback_drop:]
            flush_low = min(recent_lows)
            midpoint = (recent_high + flush_low) / 2.0

            # Volatility buffer for structural stop
            atr = self._atr(highs, lows, closes, 14)
            atr_val = atr if atr is not None else (current_close * 0.015)

            # Structural stop under the panic low
            stop_dist = max(current_close - flush_low + (0.5 * atr_val), current_close * 0.025)
            sl_bps = min(max((stop_dist / current_close) * 10000.0, 300.0), 650.0)

            # Upside potential to range midpoint
            upside_dist = max(midpoint - current_close, current_close * 0.035)
            tp_bps = min(max((upside_dist / current_close) * 10000.0, 400.0), 850.0)

            # Sentiment factor
            fg_index = ctx.features.get("fear_greed_index", 50)
            confidence = 0.75 if fg_index < 35 else 0.65

            self.entry_bar = ctx.bar_index
            self.flush_high = recent_high
            self.flush_low = flush_low
            self.target_price = midpoint

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                metadata={
                    "reason": "sol_flush_reversal_stabilization",
                    "drop_pct": round(drop_pct * 100.0, 2),
                    "flush_high": round(recent_high, 2),
                    "flush_low": round(flush_low, 2),
                    "target_midpoint": round(midpoint, 2),
                    "sl_bps": round(sl_bps, 1),
                    "tp_bps": round(tp_bps, 1),
                    "fear_greed": fg_index
                }
            )

        return None