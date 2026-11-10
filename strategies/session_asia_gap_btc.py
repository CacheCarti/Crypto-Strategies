from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaGapReversion(Strategy):
    METADATA = {
        "name": "Asia Gap Reversion BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Intended hold until end of Asia session
        "warmup_bars": 50,
        "required_features": [], # No external features used for this pure price action strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_asia_session_close = None
        self.last_trade_bar_index = -self.METADATA["warmup_bars"] # Allow trading immediately after warmup
        self.cooldown_bars = 12 # Cooldown for 12 hours after a trade (12 bars on 1h timeframe)
        self.entry_threshold_bps = 30.0 # Minimum gap size to consider, in bps
        self.continuation_threshold_bps = 10.0 # Max movement in gap direction before we consider it "continued"
        self.exit_hour = 8 # UTC hour to close positions (end of Asia session)

        # Indicator periods
        self.ema_period = 20

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[0:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        opens = ctx.opens(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Calculate EMA for confirmation
        current_ema = self._ema(closes, self.ema_period)
        if current_ema is None:
            return None

        # Logic for setting last_asia_session_close
        # The Asia session is UTC 0-8. The close of the *previous* session is relevant.
        # We assume the prior session ends at UTC 23 (end of NY session, or start of Asia)
        # So, if current_hour is 0, we're at the start of a new "day" for this logic.
        # The close of the previous day (UTC 23:00) is what we care about for the gap.
        if current_hour == 0: # Start of a new "trading day" for this session structure
            # Store the close of the UTC 23:00 bar (last bar of previous day)
            # This will be `closes[-2]` if `closes[-1]` is the 00:00 bar
            if len(closes) > 1:
                self.last_asia_session_close = closes[-2]
            else:
                self.last_asia_session_close = None
            # Reset cooldown for the new day's trading opportunity
            self.last_trade_bar_index = -self.cooldown_bars

        # Check for exit conditions
        if ctx.has_position():
            # If current hour is 8 UTC, close position (end of Asia session)
            if current_hour == self.exit_hour:
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "session_close_time_exit", "exit_hour": self.exit_hour})

            # Reversion target check (optional, but good for take profit)
            position = ctx.position
            if position["direction"] == "long" and ctx.bar.close >= position["metadata"]["target_price"]:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "reversion_target_hit_long", "target_price": position["metadata"]["target_price"]})
            elif position["direction"] == "short" and ctx.bar.close <= position["metadata"]["target_price"]:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "reversion_target_hit_short", "target_price": position["metadata"]["target_price"]})


        # Entry logic: Only consider trades if no position is open and cooldown is over
        if not ctx.has_position() and (ctx.bar_index - self.last_trade_bar_index > self.cooldown_bars):
            # Only trade during the Asia session (UTC 0-7)
            if 0 <= current_hour < self.exit_hour and self.last_asia_session_close is not None:

                current_open = ctx.bar.open # Open of the current 0-8h bar
                price_diff = current_open - self.last_asia_session_close
                gap_bps = (price_diff / self.last_asia_session_close) * 10000

                # Check for a significant gap
                if abs(gap_bps) >= self.entry_threshold_bps:
                    # Check for lack of continuation in the current bar
                    # If gap up, we expect current close to be lower than open (reversion)
                    # If gap down, we expect current close to be higher than open (reversion)
                    
                    # Calculate continuation from current bar's open
                    continuation_from_open_bps = ((ctx.bar.close - current_open) / current_open) * 10000

                    # Gap Up, looking for reversion (short)
                    if gap_bps > 0 and continuation_from_open_bps < self.continuation_threshold_bps:
                        # Price should be below EMA for bearish confirmation
                        if ctx.bar.close < current_ema:
                            target_price = self.last_asia_session_close # Target is previous session's close
                            self.last_trade_bar_index = ctx.bar_index
                            return ctx.signal(
                                "short",
                                confidence=0.65,
                                take_profit_bps=self.METADATA["declared_tp_bps"], # Use declared TP
                                stop_loss_bps=self.METADATA["declared_sl_bps"], # Use declared SL
                                horizon_seconds=self.METADATA["declared_hold_seconds"],
                                metadata={
                                    "reason": "asia_gap_reversion_short",
                                    "gap_bps": gap_bps,
                                    "current_open": current_open,
                                    "last_asia_close": self.last_asia_session_close,
                                    "current_close": ctx.bar.close,
                                    "current_ema": current_ema,
                                    "target_price": target_price
                                }
                            )

                    # Gap Down, looking for reversion (long)
                    elif gap_bps < 0 and continuation_from_open_bps > -self.continuation_threshold_bps:
                        # Price should be above EMA for bullish confirmation
                        if ctx.bar.close > current_ema:
                            target_price = self.last_asia_session_close # Target is previous session's close
                            self.last_trade_bar_index = ctx.bar_index
                            return ctx.signal(
                                "long",
                                confidence=0.65,
                                take_profit_bps=self.METADATA["declared_tp_bps"], # Use declared TP
                                stop_loss_bps=self.METADATA["declared_sl_bps"], # Use declared SL
                                horizon_seconds=self.METADATA["declared_hold_seconds"],
                                metadata={
                                    "reason": "asia_gap_reversion_long",
                                    "gap_bps": gap_bps,
                                    "current_open": current_open,
                                    "last_asia_close": self.last_asia_session_close,
                                    "current_close": ctx.bar.close,
                                    "current_ema": current_ema,
                                    "target_price": target_price
                                }
                            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last trade bar index to enforce cooldown, regardless of exit reason
        self.last_trade_bar_index = ctx.bar_index