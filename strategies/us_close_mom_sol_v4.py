from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import statistics

class UsSessionCloseMomentum(Strategy):
    METADATA = {
        "name": "US Session Close Momentum",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 2,  # Hold for up to 2 hours
        "warmup_bars": 50,
        "required_features": ["fear_greed_index", "funding_rate_solusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_period = 4  # 4 hours for momentum calculation
        self.range_vol_lookback = 20 # for median range/volume
        self.cooldown_bars = 5
        self.last_trade_bar_index = -self.cooldown_bars

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        if len(trs) < period:
            return None
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)
        volumes = ctx.volumes(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar_index < self.cooldown_bars:
            return None

        # Exit logic: Flat position by UTC 0 (24:00) or if regime changes adversely
        if ctx.has_position():
            if ctx.bar.timestamp.hour == 0: # Exit at UTC 0
                self.last_trade_bar_index = ctx.bar_index
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "time_based_exit_utc0", "current_hour": ctx.bar.timestamp.hour})
            
            # Exit on adverse regime change
            current_regime = ctx.market.get("regime", "NORMAL")
            current_trend_regime = ctx.market.get("trend_regime", "neutral")
            
            if ctx.position_direction() == "long" and current_trend_regime == "bear":
                self.last_trade_bar_index = ctx.bar_index
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "adverse_trend_regime_exit", "current_trend": current_trend_regime})
            if ctx.position_direction() == "short" and current_trend_regime == "bull":
                self.last_trade_bar_index = ctx.bar_index
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "adverse_trend_regime_exit", "current_trend": current_trend_regime})

            # Stay in position if conditions are still favorable
            return ctx.signal("hold")

        # Entry logic: Only consider entries around US session close (UTC 21:00 to 22:00)
        if not (ctx.bar.timestamp.hour == 21): # Only trigger on the 21:00 UTC bar (US close)
            return None

        # Regime filters
        fear_greed = ctx.features.get("fear_greed_index", 50)
        funding_rate = ctx.features.get("funding_rate_solusdt", 0.0)
        market_regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")

        # Stand down in crisis or extreme volatility regimes
        if market_regime in ["CRISIS", "HIGH_VOL"]:
            return None

        # Check for sufficient lookback for momentum and range/volume
        if len(closes) < self.lookback_period + self.range_vol_lookback:
            return None

        # Calculate momentum over the last `lookback_period` hours
        momentum_closes = closes[-self.lookback_period:]
        if len(momentum_closes) < self.lookback_period:
            return None
        
        # Simple momentum: price change over the period
        price_change = momentum_closes[-1] - momentum_closes[0]
        
        # Calculate recent ranges and volumes for comparison
        recent_ranges = [h - l for h, l in zip(highs[-self.range_vol_lookback:], lows[-self.range_vol_lookback:])]
        recent_volumes = volumes[-self.range_vol_lookback:]

        if len(recent_ranges) < self.range_vol_lookback or len(recent_volumes) < self.range_vol_lookback:
            return None

        median_range = statistics.median(recent_ranges)
        median_volume = statistics.median(recent_volumes)

        current_bar_range = ctx.bar.high - ctx.bar.low
        current_bar_volume = ctx.bar.volume

        # Determine strong close criteria
        is_strong_close_long = (price_change > 0 and 
                                current_bar_range > median_range * 1.0 and # Current range above median
                                current_bar_volume > median_volume * 1.0) # Current volume above median

        is_strong_close_short = (price_change < 0 and 
                                 current_bar_range > median_range * 1.0 and # Current range above median
                                 current_bar_volume > median_volume * 1.0) # Current volume above median

        confidence = 0.0
        signal_direction = None
        reason = ""

        # Long entry conditions
        if is_strong_close_long:
            # Only go long if trend is not bearish
            if trend_regime != "bear":
                confidence = 0.7
                signal_direction = "long"
                reason = "us_close_momentum_long"
                # Adjust confidence based on fear_greed and funding
                if fear_greed > 60: # Greed
                    confidence += 0.1
                if funding_rate < 0: # Negative funding (shorts paying longs)
                    confidence += 0.1
                if trend_regime == "bull":
                    confidence += 0.1

        # Short entry conditions
        elif is_strong_close_short:
            # Only go short if trend is not bullish
            if trend_regime != "bull":
                confidence = 0.7
                signal_direction = "short"
                reason = "us_close_momentum_short"
                # Adjust confidence based on fear_greed and funding
                if fear_greed < 40: # Fear
                    confidence += 0.1
                if funding_rate > 0: # Positive funding (longs paying shorts)
                    confidence += 0.1
                if trend_regime == "bear":
                    confidence += 0.1

        if signal_direction:
            # Cap confidence at 1.0
            confidence = min(confidence, 1.0)
            self.last_trade_bar_index = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "price_change_4h": price_change,
                    "current_bar_range": current_bar_range,
                    "median_range_20h": median_range,
                    "current_bar_volume": current_bar_volume,
                    "median_volume_20h": median_volume,
                    "fear_greed_index": fear_greed,
                    "funding_rate": funding_rate,
                    "market_regime": market_regime,
                    "trend_regime": trend_regime,
                },
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown on close
        self.last_trade_bar_index = ctx.bar_index
        # You could add adaptive sizing logic here, e.g., if return_bps was very good,
        # increase confidence slightly for next trade, or decrease after a loss.
        pass