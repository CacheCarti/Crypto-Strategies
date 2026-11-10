from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import statistics

class VolatilityCompressionBreakout(Strategy):
    METADATA = {
        "name": "Volatility Compression Breakout",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 100,
        "required_features": ["fear_greed_index", "funding_rate_solusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 30 # Period for historical volatility calculation
        self.vol_quartile_period = 100 # Period to determine vol quartile
        self.breakout_period = 20 # Period for Donchian Channels
        self.cooldown_bars = 5 # Bars to wait after a trade
        self.last_trade_bar = -self.cooldown_bars

        # For adaptive confidence/sizing
        self.win_streak = 0
        self.loss_streak = 0

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def _donchian_channels(self, highs, lows, period):
        if len(highs) < period or len(lows) < period:
            return None, None
        upper_band = max(highs[-period:])
        lower_band = min(lows[-period:])
        return upper_band, lower_band

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)
        volumes = ctx.volumes(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        # Calculate current volatility (e.g., ATR)
        current_atr = self._atr(highs, lows, closes, self.vol_period)
        if current_atr is None:
            return None

        # Calculate historical volatility for quartile determination
        historical_atrs = []
        for i in range(self.vol_period, len(closes)):
            h_slice = highs[i-self.vol_period:i]
            l_slice = lows[i-self.vol_period:i]
            c_slice = closes[i-self.vol_period:i]
            atr = self._atr(h_slice, l_slice, c_slice, self.vol_period)
            if atr is not None:
                historical_atrs.append(atr)

        if len(historical_atrs) < self.vol_quartile_period:
            return None

        # Get the lowest quartile of historical ATRs
        recent_atrs = sorted(historical_atrs[-self.vol_quartile_period:])
        first_quartile_atr = recent_atrs[int(len(recent_atrs) * 0.25)]

        # Check for volatility compression
        is_compressed = current_atr < first_quartile_atr * 0.9 # Require vol to be significantly in the lowest quartile
        
        # Donchian Channels for breakout
        upper_channel, lower_channel = self._donchian_channels(highs, lows, self.breakout_period)
        if upper_channel is None or lower_channel is None:
            return None

        # Regime filters
        fear_greed_index = ctx.features.get("fear_greed_index", 50)
        funding_rate = ctx.features.get("funding_rate_solusdt", 0.0)
        trend_regime = ctx.market.get("trend_regime", "neutral")

        # Exit existing position logic
        if ctx.has_position():
            # If the volatility has expanded significantly beyond the compression
            # Or if market regime becomes unfavorable
            if current_atr > first_quartile_atr * 1.5: # Volatility target for exit
                reason = "volatility_expansion_exit"
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={"reason": reason, "current_atr": current_atr, "first_quartile_atr": first_quartile_atr})
            
            # If funding rate turns heavily against current position
            if ctx.position_direction() == "long" and funding_rate < -0.0005: # Very negative funding
                reason = "unfavorable_funding_long_exit"
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.6, metadata={"reason": reason, "funding_rate": funding_rate})
            if ctx.position_direction() == "short" and funding_rate > 0.0005: # Very positive funding
                reason = "unfavorable_funding_short_exit"
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.6, metadata={"reason": reason, "funding_rate": funding_rate})
            
            return None # Hold position if no exit condition met

        # Entry logic
        signal_confidence = 0.6 + (self.win_streak * 0.1) - (self.loss_streak * 0.05)
        signal_confidence = min(0.9, max(0.5, signal_confidence)) # Cap confidence

        if is_compressed:
            # Long breakout
            if ctx.bar.close > upper_channel and trend_regime in ["bull", "neutral"] and fear_greed_index >= 40:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=signal_confidence,
                    metadata={
                        "reason": "vol_compression_long_breakout",
                        "current_atr": current_atr,
                        "first_quartile_atr": first_quartile_atr,
                        "upper_channel": upper_channel,
                        "close": ctx.bar.close,
                        "trend_regime": trend_regime,
                        "fear_greed_index": fear_greed_index,
                        "win_streak": self.win_streak,
                        "loss_streak": self.loss_streak
                    }
                )
            # Short breakout
            elif ctx.bar.close < lower_channel and trend_regime in ["bear", "neutral"] and fear_greed_index <= 60:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=signal_confidence,
                    metadata={
                        "reason": "vol_compression_short_breakout",
                        "current_atr": current_atr,
                        "first_quartile_atr": first_quartile_atr,
                        "lower_channel": lower_channel,
                        "close": ctx.bar.close,
                        "trend_regime": trend_regime,
                        "fear_greed_index": fear_greed_index,
                        "win_streak": self.win_streak,
                        "loss_streak": self.loss_streak
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        if position["return_bps"] > 0:
            self.win_streak += 1
            self.loss_streak = 0
        else:
            self.loss_streak += 1
            self.win_streak = 0
        # Optional: log position stats
        # ctx.log(f"Position closed: {position['direction']} return={position['return_bps']:.2f} bps, held={position['bars_held']} bars")