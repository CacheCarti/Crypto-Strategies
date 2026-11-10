from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class LowVolTrendRider(Strategy):
    METADATA = {
        "name": "Low Volatility Trend Rider",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours average hold
        "warmup_bars": 60, # Need enough bars for ATR and EMA
        "required_features": [], # No specific features needed beyond OHLCV for this
    }

    def initialize(self, ctx: BarContext) -> None:
        self.atr_period = 20
        self.ema_period = 20
        self.vol_threshold_multiplier = 1.0 # Multiplier for ATR to define low vol
        self.cooldown_bars = 12 # Cooldown for 12 hours after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        # Simple moving average of TRs
        if len(trs) < period:
            return None
        return sum(trs[-period:]) / period

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period # Simple average for initial EMA
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        current_close = ctx.bar.close

        # Calculate ATR as a proxy for volatility
        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None:
            return None

        # Calculate EMA for trend detection
        ema = self._ema(closes, self.ema_period)
        if ema is None:
            return None

        # Define low volatility regime
        # We need a longer history of ATR to compare current ATR against
        all_atrs = []
        for i in range(self.atr_period, len(closes)):
            all_atrs.append(self._atr(highs[:i+1], lows[:i+1], closes[:i+1], self.atr_period))
        all_atrs = [a for a in all_atrs if a is not None]

        if not all_atrs:
            return None

        avg_atr = sum(all_atrs) / len(all_atrs)
        is_low_vol = atr < (avg_atr * self.vol_threshold_multiplier)

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            if ctx.has_position():
                return None # Hold position during cooldown
            else:
                return None # Skip new entries during cooldown

        # Entry logic: Only trade in low volatility regimes
        if not ctx.has_position():
            if is_low_vol:
                # Long entry: price crosses above EMA in low vol
                if current_close > ema and closes[-2] <= self._ema(closes[:-1], self.ema_period):
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.7,
                        metadata={
                            "reason": "low_vol_ema_cross_long",
                            "current_close": current_close,
                            "ema": ema,
                            "atr": atr,
                            "avg_atr": avg_atr,
                            "regime": ctx.regime,
                            "market_trend": ctx.market.get("trend_regime", "neutral"),
                        }
                    )
                # Short entry: price crosses below EMA in low vol
                elif current_close < ema and closes[-2] >= self._ema(closes[:-1], self.ema_period):
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.7,
                        metadata={
                            "reason": "low_vol_ema_cross_short",
                            "current_close": current_close,
                            "ema": ema,
                            "atr": atr,
                            "avg_atr": avg_atr,
                            "regime": ctx.regime,
                            "market_trend": ctx.market.get("trend_regime", "neutral"),
                        }
                    )
        else: # If we have a position
            # Exit logic: Exit if volatility expands
            if not is_low_vol:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "volatility_expansion_exit",
                        "current_close": current_close,
                        "atr": atr,
                        "avg_atr": avg_atr,
                        "regime": ctx.regime,
                        "market_trend": ctx.market.get("trend_regime", "neutral"),
                        "position_direction": ctx.position_direction(),
                    }
                )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No specific adaptive logic needed for this strategy on close
        pass