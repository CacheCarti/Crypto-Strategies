from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityBreakout(Strategy):
    METADATA = {
        "name": "Volatility Breakout ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,
        "warmup_bars": 100,
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 20  # Period for realized volatility calculation
        self.compression_threshold = 0.005 # Volatility below this indicates compression
        self.compression_duration = 5 # Number of consecutive bars below threshold for compression
        self.breakout_multiplier = 1.5 # Breakout above this multiple of recent average range
        self.cooldown_bars = 10 # Bars to wait after a trade
        self.last_trade_bar = -self.cooldown_bars

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.vol_period + 1)
        highs = ctx.highs(self.vol_period + 1)
        lows = ctx.lows(self.vol_period + 1)

        if len(closes) < self.vol_period + 1:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        # 1. Calculate realized volatility
        returns = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes))]
        realized_volatility = self._stddev(returns, self.vol_period) * math.sqrt(252) # Annualized daily vol, for comparison
        if realized_volatility is None:
            return None
        
        # Adjusting volatility to be comparable to current bar's range
        # Use average true range as a measure of recent volatility
        atr = self._atr(highs, lows, closes, self.vol_period)
        if atr is None:
            return None
        
        # Normalize ATR by price for percentage comparison
        normalized_atr = atr / closes[-1]

        # Check for volatility compression
        # We need enough history to check for `compression_duration` consecutive bars
        if len(ctx.closes(self.compression_duration + self.vol_period + 1)) < self.compression_duration + self.vol_period + 1:
            return None

        compressed_bars = 0
        for i in range(self.compression_duration):
            # Calculate volatility for the lookback window ending at bar_index - (i+1)
            lookback_closes = ctx.closes(self.vol_period + 1 + i)[-(self.vol_period + 1):]
            if len(lookback_closes) < self.vol_period + 1:
                break
            lookback_returns = [(lookback_closes[j] - lookback_closes[j-1]) / lookback_closes[j-1] for j in range(1, len(lookback_closes))]
            lookback_vol = self._stddev(lookback_returns, self.vol_period) * math.sqrt(252)
            if lookback_vol is None or lookback_vol > self.compression_threshold:
                break
            compressed_bars += 1

        is_compressed = (compressed_bars == self.compression_duration)

        # 2. Detect range breakout after compression
        if is_compressed:
            current_range = ctx.bar.high - ctx.bar.low
            
            # Use the current normalized ATR as a dynamic breakout threshold
            # Breakout if current bar's range is significantly larger than recent average true range
            if current_range > (normalized_atr * closes[-1] * self.breakout_multiplier):
                
                # Determine direction of breakout
                if ctx.bar.close > ctx.bar.open: # Bullish breakout
                    if not ctx.has_position():
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal("long", confidence=0.75, metadata={
                            "reason": "volatility_compression_bullish_breakout",
                            "realized_vol_annual": realized_volatility,
                            "normalized_atr": normalized_atr,
                            "current_range": current_range,
                            "compression_duration": self.compression_duration,
                            "breakout_multiplier": self.breakout_multiplier
                        })
                elif ctx.bar.close < ctx.bar.open: # Bearish breakout
                    if not ctx.has_position():
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal("short", confidence=0.75, metadata={
                            "reason": "volatility_compression_bearish_breakout",
                            "realized_vol_annual": realized_volatility,
                            "normalized_atr": normalized_atr,
                            "current_range": current_range,
                            "compression_duration": self.compression_duration,
                            "breakout_multiplier": self.breakout_multiplier
                        })
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for this strategy
        pass