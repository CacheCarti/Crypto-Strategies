from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class IntradayLiquidityPause(Strategy):
    METADATA = {
        "name": "Intraday Liquidity Pause",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 3, # Roughly 3 hours max hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.max_lookback = 48 # For volume and price moves
        self.entry_cooldown_bars = 10
        self.last_trade_bar = -self.entry_cooldown_bars
        self.morning_move_period = 3 # 13-16 UTC is 3 bars on 1h timeframe
        self.fade_threshold_pct = 0.5 # 0.5% move in morning to consider fading
        self.min_volume_ratio = 1.2 # Morning volume must be higher than average

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.max_lookback)
        opens = ctx.opens(self.max_lookback)
        highs = ctx.highs(self.max_lookback)
        lows = ctx.lows(self.max_lookback)
        volumes = ctx.volumes(self.max_lookback)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Exit logic
        if ctx.has_position():
            position = ctx.position
            entry_price = position["entry_price"]
            morning_move_start_price = position.get("metadata", {}).get("morning_move_start_price")
            morning_move_end_price = position.get("metadata", {}).get("morning_move_end_price")

            # Exit by UTC 21 (end of 20th hour bar)
            if current_hour == 20: # The bar for 20:00-21:00 UTC
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "time_based_exit", "current_hour": current_hour})

            # Exit if half the morning move retraces
            if morning_move_start_price and morning_move_end_price:
                move_direction = 1 if morning_move_end_price > morning_move_start_price else -1
                full_move_abs = abs(morning_move_end_price - morning_move_start_price)
                half_retrace_level = morning_move_end_price - move_direction * (full_move_abs / 2)

                if move_direction == 1 and ctx.bar.close < half_retrace_level: # Long position, price drops below retrace
                    return ctx.signal("flat", confidence=0.8, metadata={"reason": "half_retrace_exit", "current_price": ctx.bar.close, "half_retrace_level": half_retrace_level})
                elif move_direction == -1 and ctx.bar.close > half_retrace_level: # Short position, price rises above retrace
                    return ctx.signal("flat", confidence=0.8, metadata={"reason": "half_retrace_exit", "current_price": ctx.bar.close, "half_retrace_level": half_retrace_level})
            
            return None # Hold position if no exit condition met


        # Entry Cooldown
        if ctx.bar_index - self.last_trade_bar < self.entry_cooldown_bars:
            return None

        # Regime Filter: Skip trending crisis regimes
        if ctx.market.get("regime") in ["CRISIS", "MELTDOWN"]:
            return None
        
        # Additional filter: Avoid extreme fear (market too unstable for fading)
        fear_greed_index = ctx.features.get("fear_greed_index", 50)
        if fear_greed_index <= 20: # Extreme Fear
            return None

        # Check for US morning move (UTC 13-16, which are bars starting at 13, 14, 15)
        # We need the bar at 16:00 UTC to have completed to analyze the 13-16 window
        if current_hour == 16:
            # Check if there are enough bars for the morning move period
            if len(closes) < self.morning_move_period + 1: # Need 13, 14, 15, and the price before 13
                return None

            # The bars for 13:00, 14:00, 15:00 UTC
            # We need to find the closes at these specific hours relative to current bar
            # This is tricky with `closes(n)` which returns last `n` values.
            # We need to look back from the current 16:00 bar.
            # The bars are indexed from oldest (0) to newest (len-1)
            # So, current bar is len-1. The bar at 15:00 is len-2, 14:00 is len-3, 13:00 is len-4.
            
            # Let's assume the current bar is 16:00 UTC.
            # We need the close of 15:00 (closes[-2]), 14:00 (closes[-3]), 13:00 (closes[-4])
            # And the open of 13:00 (opens[-4])
            
            # To simplify, we'll check the move from the open of the first bar (13:00) to the close of the last bar (15:00).
            # This requires knowing the exact bar index relative to the current time.
            # A simpler approach: check if the previous 3 bars (13, 14, 15) fall within the 13-16 window.
            # This means the current bar (ctx.bar.timestamp.hour) is 16.
            # The previous 3 bars were 15, 14, 13.
            
            # We need price at 13:00 UTC (start of move) and price at 16:00 UTC (end of move).
            # Let's find the `open` of the bar that started at 13:00 and `close` of the bar that ended at 16:00.
            
            # Find the bar that started at 13:00 UTC.
            # We are currently at the 16:00 UTC bar. So `closes[-1]` is 15:00 close, `closes[-2]` is 14:00 close, `closes[-3]` is 13:00 close.
            # The `open` of the 13:00 bar would be `opens[-3]` if we consider `closes[-3]` as the 13:00 bar's close.
            # For simplicity, let's just use the `open` of 13:00 bar and `close` of 15:00 bar.
            
            # Let's define the "morning move" as the price change from the open of the 13:00 bar to the close of the 15:00 bar.
            # This requires looking at past bars' timestamps, which `ctx.closes()` doesn't directly provide in a time-indexed way.
            # We can approximate by checking the average price of the 3 bars before the current 16:00 bar.
            
            # To get the actual 13:00-16:00 window, we need to ensure the bars are indeed those specific hours.
            # Since we are on the 16:00 bar, the previous bars were 15:00, 14:00, 13:00.
            # The `closes` array gives us `closes[-1]` (15:00), `closes[-2]` (14:00), `closes[-3]` (13:00).
            
            # Price at the start of the 13:00 bar
            if len(opens) < 4: return None # Need open of 13:00 bar (opens[-4])
            morning_start_price = opens[-4] # Open of the bar that begins at 13:00 UTC

            # Price at the close of the 15:00 bar
            if len(closes) < 2: return None # Need close of 15:00 bar (closes[-2])
            morning_end_price = closes[-2] # Close of the bar that ends at 16:00 UTC (i.e. the 15:00 bar's close)

            morning_price_change = morning_end_price - morning_start_price
            morning_move_pct = (morning_price_change / morning_start_price) * 100

            # Check for "unusually one-sided" move
            if abs(morning_move_pct) > self.fade_threshold_pct:
                # Check for significant volume during the move (13:00 to 15:00 bars)
                morning_volumes = volumes[-4:-1] # Volumes for 13:00, 14:00, 15:00 bars
                avg_recent_volume = self._sma(volumes[:-4], 20) # Average volume before the move
                
                if avg_recent_volume is None or sum(morning_volumes) / len(morning_volumes) < avg_recent_volume * self.min_volume_ratio:
                    return None # Volume not high enough to confirm strong move

                signal_direction = None
                reason = ""
                
                # If morning move was up, we look to short (fade)
                if morning_move_pct > self.fade_threshold_pct:
                    signal_direction = "short"
                    reason = "fade_morning_up_move"
                # If morning move was down, we look to long (fade)
                elif morning_move_pct < -self.fade_threshold_pct:
                    signal_direction = "long"
                    reason = "fade_morning_down_move"

                if signal_direction:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        signal_direction,
                        confidence=0.65, # Moderate confidence
                        metadata={
                            "reason": reason,
                            "morning_start_price": morning_start_price,
                            "morning_end_price": morning_end_price,
                            "morning_move_pct": morning_move_pct,
                            "current_price": ctx.bar.close,
                            "current_hour": current_hour,
                            "fear_greed_index": fear_greed_index,
                        }
                    )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log or adapt strategy based on close
        pass