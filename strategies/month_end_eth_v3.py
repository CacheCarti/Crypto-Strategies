from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class MonthEndFlow(Strategy):
    METADATA = {
        "name": "Month End Flow",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 24 * 2,  # Up to 2 days hold
        "warmup_bars": 50,  # For SMA and volume
        "required_features": ["eth_return_pct", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.long_sma_period = 20
        self.short_sma_period = 5
        self.volume_sma_period = 20
        self.cooldown_bars = 48  # Cooldown for 2 days (48 hours) after a trade
        self.last_trade_bar = -self.cooldown_bars

        # State to track last month end/start for transition detection
        self.last_month_end_day = 0
        self.last_month_start_day = 0
        self.last_month_end_signal_bar = -self.cooldown_bars

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.long_sma_period + 1)
        volumes = ctx.volumes(self.volume_sma_period + 1)

        if len(closes) < self.long_sma_period or len(volumes) < self.volume_sma_period:
            return None

        current_close = ctx.bar.close
        current_volume = ctx.bar.volume
        eth_return_pct = ctx.features.get("eth_return_pct", 0.0)
        fear_greed_index = ctx.features.get("fear_greed_index", 50)

        # Calculate SMAs
        long_sma = self._sma(closes, self.long_sma_period)
        short_sma = self._sma(closes, self.short_sma_period)
        volume_sma = self._sma(volumes, self.volume_sma_period)

        if long_sma is None or short_sma is None or volume_sma is None:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_day = ctx.bar.timestamp.day
        current_month = ctx.bar.timestamp.month
        current_year = ctx.bar.timestamp.year

        # Determine last day of the month
        if current_month == 12:
            last_day_of_month = 31
        else:
            last_day_of_month = (ctx.bar.timestamp.replace(month=current_month + 1, day=1) - ctx.bar.timestamp.resolution).day

        is_month_end_window = (last_day_of_month - current_day <= 1) and (last_day_of_month - current_day >= 0)
        is_month_start_window = (current_day == 1)

        # Exit logic
        if ctx.has_position():
            # Exit on the first day of the new month OR if we've held too long
            if is_month_start_window and ctx.position_direction() == "long" and self.last_month_start_day != current_day:
                self.last_trade_bar = ctx.bar_index
                self.last_month_start_day = current_day
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "month_start_exit", "current_day": current_day, "month": current_month})
            elif is_month_start_window and ctx.position_direction() == "short" and self.last_month_start_day != current_day:
                self.last_trade_bar = ctx.bar_index
                self.last_month_start_day = current_day
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "month_start_exit", "current_day": current_day, "month": current_month})
            return None # Hold position

        # Entry logic (only if not in position and in month-end window)
        if not is_month_end_window:
            return None

        # Ensure signal fires only once per month-end window
        if self.last_month_end_signal_bar == ctx.bar_index:
            return None
        
        # Check if we are still in the same month-end window as the last signal
        if self.last_month_end_day == current_day and self.last_month_end_signal_bar >= ctx.bar_index - 24: # Within 24 hours of last signal
             return None

        # Price action and volume confirmation
        volume_confirm = current_volume > (volume_sma * 1.5) # 50% above average volume
        decisive_move_long = (current_close > long_sma) and (short_sma > long_sma) and (eth_return_pct > 0.5) # bullish momentum and strong daily return
        decisive_move_short = (current_close < long_sma) and (short_sma < long_sma) and (eth_return_pct < -0.5) # bearish momentum and strong daily return

        # Confidence scaling based on fear_greed_index
        base_confidence = 0.6
        if fear_greed_index <= 30: # Extreme Fear/Fear
            confidence_long = base_confidence * 0.8
            confidence_short = base_confidence * 1.2
        elif fear_greed_index >= 70: # Extreme Greed/Greed
            confidence_long = base_confidence * 1.2
            confidence_short = base_confidence * 0.8
        else: # Neutral
            confidence_long = base_confidence
            confidence_short = base_confidence


        if decisive_move_long and volume_confirm:
            self.last_trade_bar = ctx.bar_index
            self.last_month_end_signal_bar = ctx.bar_index
            self.last_month_end_day = current_day
            return ctx.signal("long", confidence=confidence_long, metadata={
                "reason": "month_end_long_flow_volume_confirm",
                "current_day": current_day,
                "month": current_month,
                "long_sma": long_sma,
                "short_sma": short_sma,
                "current_close": current_close,
                "current_volume": current_volume,
                "volume_sma": volume_sma,
                "eth_return_pct": eth_return_pct,
                "fear_greed_index": fear_greed_index
            })
        elif decisive_move_short and volume_confirm:
            self.last_trade_bar = ctx.bar_index
            self.last_month_end_signal_bar = ctx.bar_index
            self.last_month_end_day = current_day
            return ctx.signal("short", confidence=confidence_short, metadata={
                "reason": "month_end_short_flow_volume_confirm",
                "current_day": current_day,
                "month": current_month,
                "long_sma": long_sma,
                "short_sma": short_sma,
                "current_close": current_close,
                "current_volume": current_volume,
                "volume_sma": volume_sma,
                "eth_return_pct": eth_return_pct,
                "fear_greed_index": fear_greed_index
            })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No specific adaptive logic needed for this strategy on close.
        # Cooldown is handled in on_bar.
        pass