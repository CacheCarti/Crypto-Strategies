from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPanicRecovery(Strategy):
    METADATA = {
        "name": "SolPanicRecovery",
        "domain": "sol_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 21600,  # ~6 hours intended hold
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.drop_threshold = -0.07  # -7% over 24 bars
        self.cooldown_bars = 18      # mandatory cooldown between events
        self.last_exit_bar = -100
        self.entry_bar = -100
        self.panic_low = 0.0
        self.flush_peak = 0.0
        self.target_midpoint = 0.0

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        # Position management
        if ctx.has_position():
            bars_in_trade = ctx.bar_index - self.entry_bar
            curr_price = ctx.bar.close

            # Target reached (rebound to flush midpoint)
            if self.target_midpoint > 0 and curr_price >= self.target_midpoint:
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "flush_midpoint_target_reached",
                    "target": round(self.target_midpoint, 2),
                    "price": round(curr_price, 2),
                    "bars_held": bars_in_trade
                })

            # Time stop / dynamic exit after 12 bars (thesis expiration)
            if bars_in_trade >= 12:
                return ctx.signal("flat", confidence=0.5, metadata={
                    "reason": "thesis_time_decay_exit",
                    "price": round(curr_price, 2),
                    "bars_held": bars_in_trade
                })

            return None

        # Check cooldown to prevent clustering entries
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Avoid entries in meltdown regime if catastrophic
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime == "MELTDOWN" or crisis_score > 0.85:
            return None

        # Measure cumulative return over the last 24 bars
        peak_price = max(highs[-self.lookback:])
        trough_price = min(lows[-self.lookback:])
        cur_close = ctx.bar.close
        lookback_return = (cur_close - closes[-self.lookback]) / closes[-self.lookback]

        # Panic criteria: 24-bar drop exceeds threshold
        if lookback_return <= self.drop_threshold:
            # Stabilization trigger: current close > previous bar's high
            prev_high = highs[-2]
            if cur_close > prev_high:
                rsi = self._rsi(closes, period=14)
                # Ensure we are bouncing out of oversold/stressed conditions
                if rsi is not None and rsi <= 45.0:
                    midpoint = (peak_price + trough_price) / 2.0
                    distance_to_midpoint_pct = (midpoint - cur_close) / cur_close

                    # Minimum reward-to-risk sanity check: must offer at least 2.5% to midpoint
                    if distance_to_midpoint_pct >= 0.025:
                        self.panic_low = trough_price
                        self.flush_peak = peak_price
                        self.target_midpoint = midpoint
                        self.entry_bar = ctx.bar_index

                        # Calculate adaptive stop loss based on panic trough
                        risk_pct = (cur_close - trough_price) / cur_close
                        sl_bps = max(150.0, min(500.0, risk_pct * 10000.0 + 50.0))
                        tp_bps = max(250.0, min(800.0, distance_to_midpoint_pct * 10000.0))

                        confidence = min(0.9, max(0.5, abs(lookback_return) * 5.0))

                        return ctx.signal(
                            "long",
                            confidence=confidence,
                            stop_loss_bps=sl_bps,
                            take_profit_bps=tp_bps,
                            metadata={
                                "reason": "panic_flush_reversal_stabilization",
                                "lookback_return": round(lookback_return, 4),
                                "rsi": round(rsi, 2),
                                "peak_price": round(peak_price, 2),
                                "panic_low": round(trough_price, 2),
                                "midpoint_target": round(midpoint, 2),
                                "risk_bps": round(sl_bps, 1)
                            }
                        )

        return None