from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolVolQuietTrend(Strategy):
    METADATA = {
        "name": "SOL Volatility Quiet Trend",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Approx 4 hours
        "warmup_bars": 60,  # Need enough bars for ATR and long EMAs
    }

    def initialize(self, ctx: BarContext) -> None:
        self.atr_period = 20
        self.ema_fast_period = 10
        self.ema_slow_period = 30
        self.vol_ema_period = 20 # EMA of ATR for volatility smoothing
        self.last_signal_bar_index = -self.warmup_bars
        self.cooldown_bars = 12 # Cooldown for 12 hours after a trade

        # Volatility thresholds (tuned for 1h SOL_USDC)
        self.low_vol_threshold_multiplier = 0.8
        self.high_vol_threshold_multiplier = 1.2

        self.last_atr_value = 0.0

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        # Cooldown check
        if ctx.bar_index - self.last_signal_bar_index < self.cooldown_bars:
            return None

        current_atr = self._atr(highs, lows, closes, self.atr_period)
        if current_atr is None:
            return None
        
        # Calculate EMA of ATR for smoothed volatility
        atr_history = []
        for i in range(self.atr_period, len(closes)):
            atr_val = self._atr(highs[:i+1], lows[:i+1], closes[:i+1], self.atr_period)
            if atr_val is not None:
                atr_history.append(atr_val)
        
        vol_ema = self._ema(atr_history, self.vol_ema_period)
        if vol_ema is None or vol_ema == 0:
            return None

        # Volatility regime detection
        is_low_vol = current_atr < vol_ema * self.low_vol_threshold_multiplier
        is_high_vol = current_atr > vol_ema * self.high_vol_threshold_multiplier

        # Trend detection
        ema_fast = self._ema(closes, self.ema_fast_period)
        ema_slow = self._ema(closes, self.ema_slow_period)

        if ema_fast is None or ema_slow is None:
            return None

        current_price = ctx.bar.close

        # Entry conditions: Low Volatility + Trend Confirmation
        if not ctx.has_position():
            # Long entry
            if is_low_vol and ema_fast > ema_slow and current_price > ema_fast:
                self.last_signal_bar_index = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.7,
                    metadata={
                        "reason": "vol_quiet_trend_long_entry",
                        "current_atr": current_atr,
                        "vol_ema": vol_ema,
                        "ema_fast": ema_fast,
                        "ema_slow": ema_slow,
                        "price": current_price,
                        "regime": ctx.regime,
                    },
                )

            # Short entry
            elif is_low_vol and ema_fast < ema_slow and current_price < ema_fast:
                self.last_signal_bar_index = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.7,
                    metadata={
                        "reason": "vol_quiet_trend_short_entry",
                        "current_atr": current_atr,
                        "vol_ema": vol_ema,
                        "ema_fast": ema_fast,
                        "ema_slow": ema_slow,
                        "price": current_price,
                        "regime": ctx.regime,
                    },
                )
        
        # Exit conditions: First sign of volatility expansion
        elif ctx.has_position():
            if is_high_vol:
                self.last_signal_bar_index = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={
                        "reason": "vol_expansion_exit",
                        "current_atr": current_atr,
                        "vol_ema": vol_ema,
                        "price": current_price,
                        "regime": ctx.regime,
                    },
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic implemented for this exploration strategy.
        pass