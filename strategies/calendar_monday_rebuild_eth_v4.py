from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CalendarMondayRebuildETH(Strategy):
    METADATA = {
        "name": "CalendarMondayRebuildETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # Intended hold for 2 hours, allowing for some Monday morning volatility
        "warmup_bars": 72, # Need enough bars for 24h data + some buffer (24 * 3 = 72 bars for 3 days)
        "required_features": ["fear_greed_index", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_trade_bar = -self.METADATA["warmup_bars"] # Ensure no trade at start
        self.cooldown_bars = 12 # Cooldown for 12 hours after a trade
        self.entry_cooldown_bars = 6 # Cooldown for 6 hours after a potential entry signal, to avoid re-entering too quickly
        self.last_entry_signal_bar = -self.METADATA["warmup_bars"]
        self.weekend_return_threshold = 0.015 # 1.5% move over the weekend
        self.monday_confirmation_threshold = 0.005 # 0.5% move in the first US hours for confirmation
        self.confirmation_period_hours = 4 # How many hours into US Monday for confirmation
        self.short_ema_period = 12 # For Monday direction confirmation
        self.long_ema_period = 24 # For Monday direction confirmation
        self.regime_filter_ema_period = 50 # For overall trend filter
        self.min_confidence = 0.65
        self.max_confidence = 0.85

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

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None
        if ctx.bar_index - self.last_entry_signal_bar < self.entry_cooldown_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_weekday = ctx.bar.timestamp.weekday() # Monday is 0, Sunday is 6

        # Regime filter: Only trade in "NORMAL" or "HIGH_VOL" regimes, avoid "CRISIS" or "MELTDOWN"
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ["CRISIS", "MELTDOWN"]:
            return None
        
        # Overall trend filter: Price above/below a 50-period EMA
        ema_50 = self._ema(closes, self.regime_filter_ema_period)
        if ema_50 is None:
            return None

        # Logic for Monday trading
        if current_weekday == 0: # It's Monday
            # US market hours for confirmation (e.g., 13:00 UTC to 17:00 UTC for 9 AM - 1 PM EST)
            # This covers the first few hours of US trading when volume picks up
            if 13 <= current_hour < (13 + self.confirmation_period_hours):
                if ctx.has_position():
                    return None # Already in a position, wait for it to close

                # Calculate weekend return (Friday close to Sunday close)
                # Need at least 48 bars for 2 days + Friday close
                if len(closes) < 72: # 3 days (72 bars) to ensure we have Friday, Saturday, Sunday data
                    return None

                friday_close_idx = -48 - current_hour # Assuming current_hour is Monday's bar, -48 is Sunday, -48-24= -72 is Saturday, -48-24-24 = Friday
                if friday_close_idx >= 0: # Ensure index is valid
                    friday_close = closes[friday_close_idx]
                    sunday_close = closes[-25 - current_hour] # Close of the last full bar on Sunday UTC
                    
                    if friday_close is None or sunday_close is None:
                        return None

                    weekend_return = (sunday_close - friday_close) / friday_close

                    # Check for significant weekend move
                    if abs(weekend_return) > self.weekend_return_threshold:
                        # Confirm direction with Monday's early US hours price action
                        short_ema = self._ema(closes, self.short_ema_period)
                        long_ema = self._ema(closes, self.long_ema_period)

                        if short_ema is None or long_ema is None:
                            return None

                        signal_direction = None
                        reason = ""
                        # Long setup: weekend dump, Monday rebuild
                        if weekend_return < 0 and (ctx.bar.close > short_ema and short_ema > long_ema): # Price above short EMA, short EMA above long EMA
                            # Price must also be above the overall trend EMA
                            if ctx.bar.close > ema_50:
                                signal_direction = "long"
                                reason = "weekend_dump_monday_rebuild_long"
                        # Short setup: weekend pump, Monday fade
                        elif weekend_return > 0 and (ctx.bar.close < short_ema and short_ema < long_ema): # Price below short EMA, short EMA below long EMA
                            # Price must also be below the overall trend EMA
                            if ctx.bar.close < ema_50:
                                signal_direction = "short"
                                reason = "weekend_pump_monday_fade_short"

                        if signal_direction:
                            # Scale confidence based on fear_greed_index and weekend return magnitude
                            fear_greed = ctx.features.get("fear_greed_index", 50)
                            confidence = self.min_confidence + (abs(weekend_return) / (self.weekend_return_threshold * 2)) * (self.max_confidence - self.min_confidence)
                            confidence = min(self.max_confidence, max(self.min_confidence, confidence))
                            
                            # Adjust confidence based on market regime and fear/greed
                            if market_regime == "HIGH_VOL":
                                confidence *= 0.9 # Reduce confidence slightly in high vol
                            if fear_greed < 30 and signal_direction == "long": # Extreme fear for long
                                confidence *= 1.1
                            elif fear_greed > 70 and signal_direction == "short": # Extreme greed for short
                                confidence *= 1.1

                            self.last_trade_bar = ctx.bar_index
                            self.last_entry_signal_bar = ctx.bar_index
                            
                            return ctx.signal(
                                signal_direction,
                                confidence=confidence,
                                metadata={
                                    "reason": reason,
                                    "weekend_return_pct": round(weekend_return * 100, 2),
                                    "current_price": ctx.bar.close,
                                    "short_ema": round(short_ema, 2),
                                    "long_ema": round(long_ema, 2),
                                    "ema50": round(ema_50, 2),
                                    "fear_greed_index": fear_greed,
                                    "market_regime": market_regime
                                }
                            )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown on close
        self.last_trade_bar = ctx.bar_index
        # Log position details for analysis
        # print(f"Position closed: {position['direction']} {position['return_bps']} bps in {position['bars_held']} bars.")
        pass