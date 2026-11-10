from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolVolQuietTrend(Strategy):
    METADATA = {
        "name": "SOL Volatility Quiet Trend",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours
        "warmup_bars": 60,
        "required_features": [], # No external features needed for this core logic
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 20
        self.trend_period = 30
        self.cooldown_bars = 5
        self.bars_since_last_trade = 0
        self.entry_volatility = 0.0 # Store volatility at entry
        self.vol_threshold = 0.005 # 0.5% average true range for "low volatility"
        self.trend_threshold = 0.0005 # 0.05% relative change for "persistent trend"

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        self.bars_since_last_trade += 1

        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        current_atr = self._atr(highs, lows, closes, self.vol_period)
        sma_closes = self._sma(closes, self.trend_period)

        if current_atr is None or sma_closes is None:
            return None

        # Normalize ATR by price for comparison
        normalized_atr = current_atr / closes[-1]

        # Calculate trend strength from SMA slope
        # Using simple percent change over the last bar relative to SMA
        trend_strength = (closes[-1] - closes[-2]) / sma_closes if sma_closes != 0 else 0.0

        is_low_volatility = normalized_atr < self.vol_threshold
        is_uptrend = trend_strength > self.trend_threshold
        is_downtrend = trend_strength < -self.trend_threshold

        # Entry logic
        if not ctx.has_position():
            if self.bars_since_last_trade < self.cooldown_bars:
                return None

            # Long entry: Low volatility and clear uptrend
            if is_low_volatility and is_uptrend:
                self.bars_since_last_trade = 0
                self.entry_volatility = normalized_atr
                return ctx.signal(
                    "long",
                    confidence=0.7,
                    metadata={
                        "reason": "low_vol_uptrend_entry",
                        "normalized_atr": normalized_atr,
                        "trend_strength": trend_strength,
                        "close": closes[-1]
                    }
                )
            # Short entry: Low volatility and clear downtrend
            elif is_low_volatility and is_downtrend:
                self.bars_since_last_trade = 0
                self.entry_volatility = normalized_atr
                return ctx.signal(
                    "short",
                    confidence=0.7,
                    metadata={
                        "reason": "low_vol_downtrend_entry",
                        "normalized_atr": normalized_atr,
                        "trend_strength": trend_strength,
                        "close": closes[-1]
                    }
                )
        # Exit logic
        else:
            # Exit on first significant volatility expansion
            # Volatility has increased by more than 50% from entry, or current volatility is high
            vol_expansion_threshold = self.entry_volatility * 1.5
            if normalized_atr > vol_expansion_threshold and normalized_atr > self.vol_threshold * 1.5:
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={
                        "reason": "volatility_expansion_exit",
                        "entry_volatility": self.entry_volatility,
                        "current_normalized_atr": normalized_atr,
                        "close": closes[-1]
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown on position close, if not already reset by an entry signal
        self.bars_since_last_trade = 0
        pass