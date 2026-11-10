from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityCompressionBreakout(Strategy):
    METADATA = {
        "name": "Volatility Compression Breakout",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours
        "warmup_bars": 100,
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 50 # Period for realizing volatility
        self.compression_lookback = 100 # Period to evaluate if current vol is low
        self.breakout_period = 5 # Period for breakout confirmation
        self.min_compression_bars = 5 # Minimum bars volatility must be low
        self.cooldown_bars = 10 # Bars to wait after a trade
        self.last_trade_bar = -self.cooldown_bars

        # Store historical volatility for quantile calculation
        self.historical_volatilities = []
        self.compression_start_bar = -1

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        # Use simple moving average for ATR
        return sum(trs[-period:]) / period

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.compression_lookback)
        highs = ctx.highs(self.compression_lookback)
        lows = ctx.lows(self.compression_lookback)

        if len(closes) < self.compression_lookback or len(highs) < self.compression_lookback or len(lows) < self.compression_lookback:
            return None

        # Calculate current realized volatility (using ATR or StdDev of returns)
        # Using StdDev of log returns for more precise volatility
        returns = []
        for i in range(1, len(closes)):
            returns.append(math.log(closes[i] / closes[i-1]))
        
        current_volatility = self._stddev(returns[-self.vol_period:], self.vol_period)
        if current_volatility is None:
            return None

        # Maintain a history of volatilities for quantile calculation
        self.historical_volatilities.append(current_volatility)
        if len(self.historical_volatilities) > self.compression_lookback:
            self.historical_volatilities.pop(0)

        if len(self.historical_volatilities) < self.compression_lookback:
            return None

        # Determine the volatility threshold for compression (lowest quartile)
        sorted_vols = sorted(self.historical_volatilities)
        compression_threshold = sorted_vols[len(sorted_vols) // 4] # First quartile

        # Check for compression
        is_compressed = current_volatility < compression_threshold

        if is_compressed:
            if self.compression_start_bar == -1:
                self.compression_start_bar = ctx.bar_index
        else:
            self.compression_start_bar = -1 # Reset if compression breaks

        # Only look for breakouts if compression has persisted for enough bars
        if self.compression_start_bar != -1 and (ctx.bar_index - self.compression_start_bar) >= self.min_compression_bars:
            # Check for breakout after compression
            if ctx.bar_index <= self.last_trade_bar + self.cooldown_bars:
                return None

            if ctx.has_position():
                return None

            # Breakout condition: current close breaks above/below the highest/lowest of the last few bars
            # excluding the current bar to avoid lookahead
            lookback_highs = highs[-(self.breakout_period + 1):-1]
            lookback_lows = lows[-(self.breakout_period + 1):-1]

            if not lookback_highs or not lookback_lows:
                return None

            highest_high = max(lookback_highs)
            lowest_low = min(lookback_lows)
            
            current_close = ctx.bar.close

            # Long breakout
            if current_close > highest_high:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.8,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "volatility_compression_long_breakout",
                        "current_volatility": current_volatility,
                        "compression_threshold": compression_threshold,
                        "highest_high_breakout": highest_high,
                        "current_close": current_close,
                        "compression_duration": ctx.bar_index - self.compression_start_bar
                    }
                )

            # Short breakout
            elif current_close < lowest_low:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.8,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "volatility_compression_short_breakout",
                        "current_volatility": current_volatility,
                        "compression_threshold": compression_threshold,
                        "lowest_low_breakout": lowest_low,
                        "current_close": current_close,
                        "compression_duration": ctx.bar_index - self.compression_start_bar
                    }
                )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset compression state after a trade to ensure fresh detection
        self.compression_start_bar = -1
        pass