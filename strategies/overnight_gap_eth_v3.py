from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SessionGapFade(Strategy):
    METADATA = {
        "name": "Session Gap Fade",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,
        "warmup_bars": 50, # Need enough bars for 24-hour lookback for gap calculation, plus EMA
        "required_features": ["eth_return_pct", "funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.gap_threshold_bps = 50.0  # Minimum gap size to consider, in bps
        self.continuation_threshold_bps = 20.0 # How much price moves against the gap before fading
        self.ema_period = 20
        self.cooldown_bars = 6
        self.last_signal_bar = -self.cooldown_bars # Initialize to allow immediate trading

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown check
        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        # US session close is typically 21:00 UTC (or 17:00 ET depending on DST)
        # Asia open is typically 00:00 UTC (8:00 AM SGT)
        # We need to find the close of the US session (e.g., 21:00 UTC)
        # And the open of the next Asia session (e.g., 00:00 UTC)

        # For 1-hour bars, let's simplify and look for gaps around 00:00 UTC.
        # The bar at 00:00 UTC (timestamp 00:00) represents the hour from 00:00 to 01:00.
        # The previous bar's close (23:00 UTC bar, timestamp 23:00) is the "US close" for this purpose.
        # The current bar's open (00:00 UTC bar, timestamp 00:00) is the "Asia open".

        current_hour = ctx.bar.timestamp.hour

        # Check for gap at 00:00 UTC (start of Asia session)
        # We need the close of the previous day (23:00 UTC bar) and the open of the current 00:00 UTC bar
        # This requires at least 2 bars to calculate.
        if current_hour == 0 and len(ctx.opens(2)) >= 2 and len(ctx.closes(2)) >= 2:
            prev_close = ctx.closes(2)[0] # Close of the 23:00 UTC bar
            current_open = ctx.opens(1)[0] # Open of the 00:00 UTC bar

            gap_raw = current_open - prev_close
            gap_bps = (gap_raw / prev_close) * 10000

            # Check for a significant gap
            if abs(gap_bps) >= self.gap_threshold_bps:
                # Check for lack of continuation
                # If gap was up, we expect price to drop from open (or not rise significantly)
                # If gap was down, we expect price to rise from open (or not drop significantly)
                current_high = ctx.bar.high
                current_low = ctx.bar.low

                if gap_bps > 0: # Up gap
                    # If current bar's high is not significantly above open, or it dipped
                    continuation_up_bps = ((current_high - current_open) / current_open) * 10000
                    if continuation_up_bps < self.continuation_threshold_bps:
                        # Consider fading the up gap (go short)
                        if not ctx.has_position():
                            self.last_signal_bar = ctx.bar_index
                            return ctx.signal(
                                "short",
                                confidence=0.7,
                                metadata={
                                    "reason": "fade_up_gap_no_continuation",
                                    "gap_bps": round(gap_bps, 2),
                                    "prev_close": round(prev_close, 2),
                                    "current_open": round(current_open, 2),
                                    "continuation_bps": round(continuation_up_bps, 2),
                                    "current_price": ctx.bar.close,
                                }
                            )
                elif gap_bps < 0: # Down gap
                    # If current bar's low is not significantly below open, or it rallied
                    continuation_down_bps = ((current_open - current_low) / current_open) * 10000
                    if continuation_down_bps < self.continuation_threshold_bps:
                        # Consider fading the down gap (go long)
                        if not ctx.has_position():
                            self.last_signal_bar = ctx.bar_index
                            return ctx.signal(
                                "long",
                                confidence=0.7,
                                metadata={
                                    "reason": "fade_down_gap_no_continuation",
                                    "gap_bps": round(gap_bps, 2),
                                    "prev_close": round(prev_close, 2),
                                    "current_open": round(current_open, 2),
                                    "continuation_bps": round(continuation_down_bps, 2),
                                    "current_price": ctx.bar.close,
                                }
                            )

        # Exit logic:
        # 1. If gap has filled (price crosses the previous close)
        # 2. Before the next US session (e.g., 21:00 UTC)
        if ctx.has_position():
            position = ctx.position
            entry_price = position["entry_price"]
            direction = position["direction"]

            # Gap fill check (simplified: if current close crosses the previous close that formed the gap)
            # This requires storing the gap-forming previous close when entering a position.
            # For simplicity, we'll assume the target is the previous day's close.
            # We need to know what the 'prev_close' was when we entered.
            # For this strategy, the target is `prev_close` at the time of signal.
            # Let's add prev_close to metadata on entry, and retrieve it here.

            target_prev_close = position["metadata"].get("prev_close")
            if target_prev_close:
                if direction == "long" and ctx.bar.close >= target_prev_close:
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.8,
                        metadata={
                            "reason": "gap_filled_long",
                            "target_prev_close": round(target_prev_close, 2),
                            "current_close": round(ctx.bar.close, 2),
                            "entry_price": round(entry_price, 2),
                        }
                    )
                elif direction == "short" and ctx.bar.close <= target_prev_close:
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.8,
                        metadata={
                            "reason": "gap_filled_short",
                            "target_prev_close": round(target_prev_close, 2),
                            "current_close": round(ctx.bar.close, 2),
                            "entry_price": round(entry_price, 2),
                        }
                    )

            # Exit before next US session (e.g., 20:00 UTC to be safe, before 21:00 close)
            # This prevents holding through another potential gap.
            if current_hour == 20: # Exit at 20:00 UTC bar
                self.last_signal_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "exit_before_us_session",
                        "current_hour": current_hour,
                        "current_close": round(ctx.bar.close, 2),
                        "entry_price": round(entry_price, 2),
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for this strategy yet.
        pass