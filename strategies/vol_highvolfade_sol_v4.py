from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolVolFade(Strategy):
    METADATA = {
        "name": "SOL Volatility Fade",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours average hold
        "warmup_bars": 60,
        "required_features": [], # No external features needed
    }

    def initialize(self, ctx: BarContext) -> None:
        self.zscore_period = 20
        self.vol_period = 20
        self.cooldown_bars = 5
        self.last_trade_bar_index = -self.cooldown_bars - 1 # Ensure no cooldown on first bar

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def _zscore(self, values, period):
        if len(values) < period:
            return None
        mean = sum(values[-period:]) / period
        std = self._stddev(values, period)
        if std is None or std == 0:
            return 0.0
        return (values[-1] - mean) / std

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar_index <= self.cooldown_bars:
            return None

        current_zscore = self._zscore(closes, self.zscore_period)
        if current_zscore is None:
            return None

        # Volatility filter: Use ATR to gauge volatility
        current_atr = self._atr(highs, lows, closes, self.vol_period)
        if current_atr is None:
            return None

        # Calculate historical ATRs to determine top quartile
        recent_atrs = []
        for i in range(self.vol_period, len(closes)):
            atr_val = self._atr(highs[:i+1], lows[:i+1], closes[:i+1], self.vol_period)
            if atr_val is not None:
                recent_atrs.append(atr_val)

        if len(recent_atrs) < self.vol_period: # Need enough history for quartile
            return None

        recent_atrs.sort()
        # Top quartile volatility check (75th percentile)
        volatility_threshold = recent_atrs[int(len(recent_atrs) * 0.75)]

        # Regime filter: Only trade in 'normal' or 'volatile' conditions, avoid 'crisis' and 'calm'
        # Also avoid trends (neutral trend_regime is preferred for fading)
        market_regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")

        if market_regime in ["CRISIS", "CALM"] or trend_regime != "neutral":
            return None # Stand down in crisis, calm, or trending markets

        # Entry conditions: Fade extreme z-score moves only if in top quartile volatility
        # and not in a strong trend
        if not ctx.has_position():
            if current_atr >= volatility_threshold: # Only fade in high volatility
                if current_zscore > 2.0: # Overbought, consider short
                    self.last_trade_bar_index = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.7,
                        metadata={
                            "reason": "zscore_fade_overbought_high_vol",
                            "zscore": current_zscore,
                            "atr": current_atr,
                            "volatility_threshold": volatility_threshold,
                            "market_regime": market_regime,
                            "trend_regime": trend_regime,
                            "price": ctx.bar.close
                        }
                    )
                elif current_zscore < -2.0: # Oversold, consider long
                    self.last_trade_bar_index = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.7,
                        metadata={
                            "reason": "zscore_fade_oversold_high_vol",
                            "zscore": current_zscore,
                            "atr": current_atr,
                            "volatility_threshold": volatility_threshold,
                            "market_regime": market_regime,
                            "trend_regime": trend_regime,
                            "price": ctx.bar.close
                        }
                    )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log position close for analysis
        ctx.log(f"Position closed: {position['direction']} {position['return_bps']:.2f} bps, held {position['bars_held']} bars.")
        # No adaptive logic for now, just reset cooldown tracking on the next bar via self.last_trade_bar_index