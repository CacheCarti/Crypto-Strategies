from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PanicCapitulationRecovery(Strategy):
    METADATA = {
        "name": "PanicCapitulationRecovery",
        "domain": "btc_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 28800,  # 8 hours typical swing recovery
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.flush_threshold_pct = -0.050  # 5.0% flush across 24 bars
        self.cooldown_bars = 10
        self.last_exit_bar = -999
        self.entry_bar_idx = -999
        self.target_midpoint = 0.0

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        # Manage existing position
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar_idx
            curr_price = ctx.bar.close

            # Structured exit: target reached (50% recovery of the crash drop) or max hold exceeded (16 bars / 16 hours)
            if curr_price >= self.target_midpoint or bars_held >= 16:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "midpoint_target_or_max_hold_reached",
                        "bars_held": bars_held,
                        "target_midpoint": self.target_midpoint,
                        "price": curr_price,
                    }
                )
            return None

        # Cooldown guard after trade exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Calculate 24-bar return
        p_start = closes[-self.lookback - 1]
        p_now = closes[-1]
        ret_24 = (p_now - p_start) / p_start

        # Check for crash flush condition
        if ret_24 <= self.flush_threshold_pct:
            prev_high = highs[-2]
            curr_close = closes[-1]
            
            # Capitulation reclaim candle: current close punches above the previous bar's high
            if curr_close > prev_high:
                # Volatility-based structural risk calibration via ATR
                atr = self._atr(highs, lows, closes, 14)
                if atr is None or atr <= 0:
                    return None

                recent_24_high = max(highs[-self.lookback:])
                recent_24_low = min(lows[-self.lookback:])
                self.target_midpoint = (recent_24_high + recent_24_low) / 2.0

                # Ensure target provides viable upside (at least 150 bps above entry)
                midpoint_upside_bps = ((self.target_midpoint - curr_close) / curr_close) * 10000.0
                if midpoint_upside_bps < 150.0:
                    return None

                # Dynamic structure stop: distance to local low plus 0.5 ATR buffer, clamped between 220 and 450 bps
                stop_dist = (curr_close - recent_24_low) + 0.5 * atr
                sl_bps = max(220.0, min(450.0, (stop_dist / curr_close) * 10000.0))
                tp_bps = max(midpoint_upside_bps, 300.0)

                # Sentiment context filter
                fgi = ctx.features.get("fear_greed_index", 50.0)
                confidence = 0.85 if fgi < 35.0 else 0.70

                self.entry_bar_idx = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    horizon_seconds=57600,
                    metadata={
                        "reason": "crash_flush_capitulation_reclaim",
                        "ret_24": round(ret_24, 4),
                        "sl_bps": round(sl_bps, 1),
                        "tp_bps": round(tp_bps, 1),
                        "target_midpoint": round(self.target_midpoint, 2),
                        "fear_greed": fgi,
                    }
                )

        return None