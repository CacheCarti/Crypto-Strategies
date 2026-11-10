from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any

class BtcPanicRecovery(Strategy):
    METADATA = {
        "name": "BtcPanicRecovery",
        "domain": "btc_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 28800,  # ~8 hours swing hold
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.flush_threshold = -0.055  # 5.5% drop over 24 bars
        self.cooldown_bars = 12
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.midpoint_target = 0.0

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        # Position management: Target midpoint of the crash window or time-based horizon
        if ctx.has_position():
            curr_price = ctx.bar.close
            bars_held = ctx.bar_index - self.entry_bar
            
            # Midpoint take profit or hold limit
            if curr_price >= self.midpoint_target or bars_held >= 10:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "midpoint_reached" if curr_price >= self.midpoint_target else "max_hold_expired",
                        "price": curr_price,
                        "midpoint": self.midpoint_target,
                        "bars_held": bars_held
                    }
                )
            return None

        # Cooldown guard after trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Economic trigger: 24-bar flush
        prev_close_24 = closes[-25]
        prior_close = closes[-2]
        rolling_return = (prior_close - prev_close_24) / prev_close_24

        if rolling_return > self.flush_threshold:
            return None

        # Event boundary: Capitulation reclaim (current close breaks strictly above previous high)
        prev_high = highs[-2]
        curr_close = ctx.bar.close
        curr_low = ctx.bar.low

        if curr_close > prev_high:
            recent_low = min(lows[-(self.lookback):])
            recent_high = max(highs[-(self.lookback):])
            self.midpoint_target = (recent_high + recent_low) / 2.0
            self.entry_bar = ctx.bar_index

            confidence = min(0.9, max(0.5, abs(rolling_return) * 10.0))

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=350.0,
                take_profit_bps=550.0,
                horizon_seconds=28800,
                metadata={
                    "reason": "flush_reversal_capitulation_break",
                    "rolling_24h_return": rolling_return,
                    "curr_close": curr_close,
                    "prev_high": prev_high,
                    "midpoint_target": self.midpoint_target,
                    "recent_low": recent_low
                }
            )

        return None