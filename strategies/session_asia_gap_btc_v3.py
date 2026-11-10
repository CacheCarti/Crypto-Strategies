from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcAsiaGapReversion(Strategy):
    METADATA = {
        "name": "BTC Asia Gap Reversion",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Intended to hold for up to 4 hours or until Asia close
        "warmup_bars": 30,
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_asia_close_price = None
        self.traded_today = False
        self.cooldown_bars = 0
        self.cooldown_duration = 5 # bars

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"] + 1)
        if len(closes) < self.METADATA["warmup_bars"] + 1:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Reset traded_today flag at the start of a new UTC day
        if current_hour == 0 and not self.traded_today:
            self.traded_today = False

        # Cooldown logic
        if self.cooldown_bars > 0:
            self.cooldown_bars -= 1
            return None

        # Asia session: UTC 0 to 8
        # Capture the close of the Asia session (bar at 7:00 UTC, closing at 8:00 UTC)
        if current_hour == 7:
            self.last_asia_close_price = ctx.bar.close
            # Reset traded_today for the upcoming trading session
            self.traded_today = False
            return None

        # Trading window: After Asia close (8:00 UTC) and before Europe open fully (around 9:00-10:00 UTC)
        # We are looking for a gap from Asia close that doesn't continue
        if self.last_asia_close_price is None:
            return None

        # Trade only if no position is open and not traded today
        if ctx.has_position():
            # If we are in a position, and it's approaching the end of the Asia session (UTC 8),
            # or end of our intended hold, we might want to flatten.
            # This logic will be handled by horizon_seconds, but we can add an extra safety check.
            if current_hour >= 8 and ctx.position_direction() is not None:
                return ctx.signal("flat", confidence=0.5, metadata={"reason": "exit_before_europe_open", "current_hour": current_hour})
            return None # Hold current position

        if self.traded_today:
            return None

        # Entry logic: if it's after Asia close (e.g., 8:00 UTC bar or 9:00 UTC bar)
        # and we haven't traded yet today.
        if current_hour == 8 or current_hour == 9:
            gap_from_asia_close = ctx.bar.open - self.last_asia_close_price
            price_change_since_open = ctx.bar.close - ctx.bar.open

            # Define a threshold for a "large gap" in BPS
            gap_threshold_bps = 30.0
            gap_magnitude_bps = (abs(gap_from_asia_close) / self.last_asia_close_price) * 10000

            # Define a threshold for "lack of continuation"
            continuation_threshold_bps = 10.0
            continuation_magnitude_bps = (abs(price_change_since_open) / ctx.bar.open) * 10000

            # We are looking for a significant gap that is then reversed or shows no continuation
            if gap_magnitude_bps > gap_threshold_bps:
                # Long signal: Market gapped down, but then bounced back up (or didn't continue down)
                if gap_from_asia_close < 0: # Gapped down
                    if price_change_since_open > 0 or continuation_magnitude_bps < continuation_threshold_bps: # Price moved up or didn't continue down
                        confidence = min(1.0, gap_magnitude_bps / 100.0) # Scale confidence by gap size
                        self.traded_today = True
                        self.cooldown_bars = self.cooldown_duration
                        return ctx.signal(
                            "long",
                            confidence=confidence,
                            metadata={
                                "reason": "asia_gap_down_reversion",
                                "asia_close": self.last_asia_close_price,
                                "gap_open": ctx.bar.open,
                                "gap_bps": gap_magnitude_bps,
                                "current_close": ctx.bar.close,
                                "continuation_bps": continuation_magnitude_bps,
                                "direction": "long"
                            }
                        )
                # Short signal: Market gapped up, but then fell back down (or didn't continue up)
                elif gap_from_asia_close > 0: # Gapped up
                    if price_change_since_open < 0 or continuation_magnitude_bps < continuation_threshold_bps: # Price moved down or didn't continue up
                        confidence = min(1.0, gap_magnitude_bps / 100.0)
                        self.traded_today = True
                        self.cooldown_bars = self.cooldown_duration
                        return ctx.signal(
                            "short",
                            confidence=confidence,
                            metadata={
                                "reason": "asia_gap_up_reversion",
                                "asia_close": self.last_asia_close_price,
                                "gap_open": ctx.bar.open,
                                "gap_bps": gap_magnitude_bps,
                                "current_close": ctx.bar.close,
                                "continuation_bps": continuation_magnitude_bps,
                                "direction": "short"
                            }
                        )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown on position close
        self.cooldown_bars = self.cooldown_duration
        pass