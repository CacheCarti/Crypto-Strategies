from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMondayRebuild(Strategy):
    METADATA = {
        "name": "SolMondayRebuild",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours intended hold
        "warmup_bars": 50,
        "required_features": ["btc_return_pct", "eth_return_pct", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 12  # Wait 12 hours after a trade
        self.last_trade_bar = -self.cooldown_bars
        self.weekend_period = 24 * 2 # 2 days of 1-hour bars for weekend
        self.ma_period = 20 # for trend filter
        self.rsi_period = 14 # for entry confirmation
        self.entry_threshold = 0.005 # 0.5% move for weekend measure
        self.monday_entry_hour_start = 12 # 12 UTC (US morning)
        self.monday_entry_hour_end = 20 # 20 UTC (US afternoon)

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        
        # Calculate initial averages
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period

        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 0.0 # Handle division by zero
        
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        # Market regime filter
        current_regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")
        fear_greed_index = ctx.features.get("fear_greed_index", 50)

        # Avoid trading in extreme crisis or meltdown
        if current_regime in ["CRISIS", "MELTDOWN"]:
            return None
        
        # Avoid trading if market sentiment is extremely fearful
        if fear_greed_index < 20: # Extreme Fear
            return None

        # Only consider entries on Monday within specific US trading hours (UTC)
        if ctx.bar.timestamp.weekday() != 0: # Monday is 0
            return None
        
        if not (self.monday_entry_hour_start <= ctx.bar.timestamp.hour < self.monday_entry_hour_end):
            return None

        # Calculate weekend move (Friday close to Sunday close)
        # We need enough bars to cover the weekend plus some buffer
        if len(closes) < self.weekend_period + 24: # Roughly Friday close to Monday open
            return None

        # Find Friday close (approx 24 bars before Sunday close)
        # Assuming Friday is 4, Saturday 5, Sunday 6, Monday 0
        # We want to find the close of the last bar on Friday
        friday_closes = []
        for i in range(len(closes) - 1, -1, -1):
            bar_time = ctx.bar.timestamp - (ctx.bar_index - i) * (ctx.bar.timestamp - (ctx.bar.timestamp.replace(hour=0, minute=0, second=0, microsecond=0))) # Approximate previous bar timestamp
            # This timestamp approximation is tricky. Simpler to just look at the last N bars before Monday.
            # Let's simplify: look at the price change over the last weekend_period bars ending Sunday close.
            # The current bar is Monday. So `closes[-1]` is the current close.
            # `closes[-self.weekend_period - 1]` would be the close from `self.weekend_period` bars ago (roughly Friday)
            pass

        # Simplified weekend move: compare price at the start of the weekend to end of weekend
        # For a Monday bar, closes[-1] is current price.
        # closes[-self.weekend_period -1] would be roughly 48 bars ago (Friday close)
        # closes[-self.weekend_period // 2 -1] would be roughly 24 bars ago (Saturday/Sunday mid)
        
        # Let's define weekend as the period before the current Monday bar.
        # We need the close from `self.weekend_period` bars ago.
        # This is roughly Friday's close if `self.weekend_period` is 48 hours for 1h bars.
        friday_close = closes[-self.weekend_period - 1] if len(closes) > self.weekend_period else None
        
        if friday_close is None:
            return None

        # Sunday close (last bar before Monday's open) would be closes[-2] from the perspective of current bar
        # No, closes[-1] is current Monday bar. closes[-2] is Sunday's last bar.
        sunday_close = closes[-2] 

        if sunday_close is None: # Should not happen if friday_close is not None
            return None

        weekend_move_pct = (sunday_close - friday_close) / friday_close

        # Calculate Monday's initial direction (rebuild)
        # Compare current price to Sunday's close
        monday_rebuild_pct = (closes[-1] - sunday_close) / sunday_close

        # Moving average for trend filter
        ma = self._sma(closes, self.ma_period)
        if ma is None:
            return None

        # RSI for confirmation
        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None
        
        signal_direction: Optional[str] = None
        confidence = 0.0
        reason = ""
        
        # Long condition: Weekend down, Monday rebuilding up, price above MA, RSI confirming
        if weekend_move_pct < -self.entry_threshold and \
           monday_rebuild_pct > 0 and \
           closes[-1] > ma and \
           rsi > 50: # RSI above 50 for bullish momentum
            
            # Additional trend filter: only long if overall trend is not bearish
            if trend_regime in ["bull", "neutral"]:
                signal_direction = "long"
                confidence = min(0.7 + abs(weekend_move_pct) * 10, 1.0) # Higher confidence for bigger weekend drops
                reason = "monday_rebuild_long_weekend_dip_above_ma_rsi_confirm"

        # Short condition: Weekend up, Monday rebuilding down, price below MA, RSI confirming
        elif weekend_move_pct > self.entry_threshold and \
             monday_rebuild_pct < 0 and \
             closes[-1] < ma and \
             rsi < 50: # RSI below 50 for bearish momentum
            
            # Additional trend filter: only short if overall trend is not bullish
            if trend_regime in ["bear", "neutral"]:
                signal_direction = "short"
                confidence = min(0.7 + abs(weekend_move_pct) * 10, 1.0) # Higher confidence for bigger weekend rallies
                reason = "monday_rebuild_short_weekend_rally_below_ma_rsi_confirm"
        
        # If we have a position, check for exit conditions or hold
        if ctx.has_position():
            if ctx.position_direction() == "long" and signal_direction == "short":
                # Flip from long to short
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("short", confidence=confidence, metadata={
                    "reason": f"flip_from_long_to_short_{reason}",
                    "weekend_move_pct": weekend_move_pct,
                    "monday_rebuild_pct": monday_rebuild_pct,
                    "close": closes[-1],
                    "ma": ma,
                    "rsi": rsi,
                    "regime": current_regime,
                    "trend_regime": trend_regime,
                    "fear_greed": fear_greed_index,
                })
            elif ctx.position_direction() == "short" and signal_direction == "long":
                # Flip from short to long
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("long", confidence=confidence, metadata={
                    "reason": f"flip_from_short_to_long_{reason}",
                    "weekend_move_pct": weekend_move_pct,
                    "monday_rebuild_pct": monday_rebuild_pct,
                    "close": closes[-1],
                    "ma": ma,
                    "rsi": rsi,
                    "regime": current_regime,
                    "trend_regime": trend_regime,
                    "fear_greed": fear_greed_index,
                })
            else:
                return ctx.signal("hold") # Hold current position if no opposing signal

        # If no position and a signal is generated
        if signal_direction:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(signal_direction, confidence=confidence, metadata={
                "reason": reason,
                "weekend_move_pct": weekend_move_pct,
                "monday_rebuild_pct": monday_rebuild_pct,
                "close": closes[-1],
                "ma": ma,
                "rsi": rsi,
                "regime": current_regime,
                "trend_regime": trend_regime,
                "fear_greed": fear_greed_index,
            })
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log position close for analysis or adaptive sizing (not implemented here)
        pass