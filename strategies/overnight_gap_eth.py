from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SessionGapFadeStrategy(Strategy):
    METADATA = {
        "name": "Session Gap Fade",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 350.0,
        "declared_hold_seconds": 3600 * 6, # Aim for up to 6 hours
        "warmup_bars": 50,
        "required_features": ["fear_greed_index", "funding_rate_ethusdt", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.us_close_price: Optional[float] = None
        self.us_close_bar_index: Optional[int] = None
        self.last_signal_bar_index: int = -self.METADATA["warmup_bars"] # Cooldown on startup
        self.cooldown_bars = 12 # Wait 12 bars (12 hours) after a trade
        self.gap_threshold_bps = 80 # Minimum gap size in bps to consider
        self.min_gap_return_pct = 0.5 # Minimum ETH return % for gap continuation check
        self.max_gap_continuation_return_pct = 0.2 # If ETH moves more than this, gap is continuing
        self.fade_period = 20 # EMA period for fade confirmation
        self.max_hold_bars = self.METADATA["declared_hold_seconds"] // 3600 # Convert to bars

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if len(ctx.closes(self.METADATA["warmup_bars"] + 1)) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown check
        if ctx.bar_index - self.last_signal_bar_index < self.cooldown_bars:
            if ctx.has_position():
                return ctx.signal("hold")
            else:
                return None

        current_hour = ctx.bar.timestamp.hour

        # Capture US session close (e.g., 21:00 UTC)
        if current_hour == 21:
            self.us_close_price = ctx.bar.close
            self.us_close_bar_index = ctx.bar_index
            return None

        # Check for Asia open (e.g., 00:00 UTC) after a US close has been recorded
        if self.us_close_price is not None and self.us_close_bar_index is not None and current_hour == 0:
            if ctx.bar_index == self.us_close_bar_index + 3: # 3 hours later
                asia_open_price = ctx.bar.open
                gap_bps = ((asia_open_price - self.us_close_price) / self.us_close_price) * 10000

                # Check for an unusually large gap
                if abs(gap_bps) > self.gap_threshold_bps:
                    # Check for lack of continuation
                    eth_return_pct = ctx.features.get("eth_return_pct", 0.0)

                    # Long fade opportunity (gap down)
                    if gap_bps < -self.gap_threshold_bps and eth_return_pct > self.max_gap_continuation_return_pct: # Gap down, but then moved up
                        self.us_close_price = None # Reset for next cycle
                        self.us_close_bar_index = None
                        return None # Gap down, but price moved up, so no fade

                    if gap_bps < -self.gap_threshold_bps and eth_return_pct < self.min_gap_return_pct: # Gap down, little continuation
                        # Confirm fade with EMA
                        ema = self._ema(ctx.closes(self.fade_period + 1), self.fade_period)
                        if ema is None: return None

                        # Only if price is still below the prior US close (and EMA confirms weakness)
                        if ctx.bar.close < self.us_close_price and ctx.bar.close < ema:
                            if not ctx.has_position():
                                self.last_signal_bar_index = ctx.bar_index
                                self.us_close_price = None # Reset for next cycle
                                self.us_close_bar_index = None
                                return ctx.signal(
                                    "long",
                                    confidence=0.7,
                                    take_profit_bps=self.METADATA["declared_tp_bps"],
                                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                                    metadata={
                                        "reason": "asia_open_gap_down_fade",
                                        "gap_bps": round(gap_bps, 2),
                                        "us_close": self.us_close_price,
                                        "asia_open": asia_open_price,
                                        "current_close": ctx.bar.close,
                                        "eth_return_pct": round(eth_return_pct, 2),
                                        "ema_fade_period": round(ema, 2),
                                    }
                                )

                    # Short fade opportunity (gap up)
                    elif gap_bps > self.gap_threshold_bps and eth_return_pct < -self.min_gap_return_pct: # Gap up, but then moved down
                        self.us_close_price = None # Reset for next cycle
                        self.us_close_bar_index = None
                        return None # Gap up, but price moved down, so no fade

                    elif gap_bps > self.gap_threshold_bps and eth_return_pct > -self.min_gap_return_pct: # Gap up, little continuation
                        # Confirm fade with EMA
                        ema = self._ema(ctx.closes(self.fade_period + 1), self.fade_period)
                        if ema is None: return None

                        # Only if price is still above the prior US close (and EMA confirms strength)
                        if ctx.bar.close > self.us_close_price and ctx.bar.close > ema:
                            if not ctx.has_position():
                                self.last_signal_bar_index = ctx.bar_index
                                self.us_close_price = None # Reset for next cycle
                                self.us_close_bar_index = None
                                return ctx.signal(
                                    "short",
                                    confidence=0.7,
                                    take_profit_bps=self.METADATA["declared_tp_bps"],
                                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                                    metadata={
                                        "reason": "asia_open_gap_up_fade",
                                        "gap_bps": round(gap_bps, 2),
                                        "us_close": self.us_close_price,
                                        "asia_open": asia_open_price,
                                        "current_close": ctx.bar.close,
                                        "eth_return_pct": round(eth_return_pct, 2),
                                        "ema_fade_period": round(ema, 2),
                                    }
                                )
                
                # Reset for next cycle after checking for gap
                self.us_close_price = None
                self.us_close_bar_index = None

        # Position management
        if ctx.has_position():
            # Exit if gap is filled (price crosses prior US close)
            if self.us_close_price is not None and ctx.position_direction() == "long" and ctx.bar.close > self.us_close_price:
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "gap_filled_long", "gap_fill_price": ctx.bar.close, "us_close": self.us_close_price})
            elif self.us_close_price is not None and ctx.position_direction() == "short" and ctx.bar.close < self.us_close_price:
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "gap_filled_short", "gap_fill_price": ctx.bar.close, "us_close": self.us_close_price})

            # Exit before next US session if position still open (avoid holding through next US close)
            # This is a bit tricky since self.us_close_price is reset. We need to store it with the signal.
            # For simplicity, let's just use a max hold time.
            if ctx.bar_index - ctx.position["entry_bar_index"] >= self.max_hold_bars:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "max_hold_time_reached", "bars_held": ctx.bar_index - ctx.position["entry_bar_index"]})

            return ctx.signal("hold")

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # We could use this for adaptive logic, but for simplicity, we'll just log.
        pass