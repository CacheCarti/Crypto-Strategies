from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPanicRecovery(Strategy):
    METADATA = {
        "name": "SolPanicRecovery",
        "domain": "sol_usdc",
        "declared_sl_bps": 450.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 18000,  # ~5 hours target swing hold
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.drop_threshold = -0.07  # -7.0% drop over 24h
        self.cooldown_bars = 12       # 12 bars cooldown to prevent re-entering immediate knife-catches
        self.last_exit_bar = -999
        self.event_active = False
        self.panic_low = 0.0
        self.flush_peak = 0.0

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.event_active = False

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        highs = ctx.highs(30)
        lows = ctx.lows(30)

        if len(closes) < self.lookback + 2:
            return None

        # Check for trade exit if position is active
        if ctx.has_position():
            # If price reaches the midpoint of the flush range, take profit dynamically
            if self.event_active and self.flush_peak > self.panic_low:
                midpoint = (self.flush_peak + self.panic_low) / 2.0
                if ctx.bar.close >= midpoint:
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "flush_midpoint_reached",
                            "close": ctx.bar.close,
                            "midpoint": midpoint,
                            "panic_low": self.panic_low,
                        }
                    )
            return None

        # Cooldown guard: must wait after last exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Market regime filter: avoid outright crisis/meltdown to protect against systemic collapses
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Calculate 24-bar cumulative price return
        prior_close = closes[-25]
        curr_close = closes[-1]
        cum_ret = (curr_close - prior_close) / prior_close

        # Track panic events
        lookback_highs = highs[-25:]
        lookback_lows = lows[-25:]
        recent_peak = max(lookback_highs)
        recent_low = min(lookback_lows)

        # Event trigger: 7%+ drawdown over 24h window
        if cum_ret <= self.drop_threshold:
            self.event_active = True
            self.panic_low = recent_low
            self.flush_peak = recent_peak

        # Entry logic: Within active panic event, find the first stabilization bar
        # that closes strictly above the previous bar's high
        if self.event_active:
            prev_high = highs[-2]
            curr_bar_close = ctx.bar.close

            if curr_bar_close > prev_high:
                # Calculate dynamic stop loss to panic low + buffer
                dist_to_low = max(0.001, (curr_bar_close - self.panic_low) / curr_bar_close)
                stop_bps = max(200.0, min(600.0, dist_to_low * 10000.0 + 50.0))

                # Midpoint profit target in bps
                midpoint = (self.flush_peak + self.panic_low) / 2.0
                target_ret = (midpoint - curr_bar_close) / curr_bar_close
                tp_bps = max(250.0, min(800.0, target_ret * 10000.0))

                # Reset event state so we do not re-trigger next bar
                self.event_active = False

                return ctx.signal(
                    "long",
                    confidence=0.8,
                    stop_loss_bps=stop_bps,
                    take_profit_bps=tp_bps,
                    metadata={
                        "reason": "sol_panic_flush_reversal",
                        "cum_24h_ret": round(cum_ret, 4),
                        "panic_low": self.panic_low,
                        "flush_peak": self.flush_peak,
                        "prev_high": prev_high,
                        "close": curr_bar_close,
                    }
                )

        return None