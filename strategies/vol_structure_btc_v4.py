from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityStructure(Strategy):
    METADATA = {
        "name": "Volatility Structure BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 50,
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_period = 20
        self.min_hh_hl_sequence = 3 # Minimum number of higher highs/lows for an uptrend
        self.min_ll_lh_sequence = 3 # Minimum number of lower lows/highs for a downtrend
        self.cooldown_bars = 10 # Cooldown period after a trade
        self.last_trade_bar = -self.cooldown_bars

    def _is_uptrend(self, highs, lows):
        if len(highs) < self.min_hh_hl_sequence or len(lows) < self.min_hh_hl_sequence:
            return False, 0

        hh_count = 0
        for i in range(1, self.min_hh_hl_sequence):
            if highs[-i] > highs[-(i+1)]:
                hh_count += 1
            else:
                break
        
        hl_count = 0
        for i in range(1, self.min_hh_hl_sequence):
            if lows[-i] > lows[-(i+1)]:
                hl_count += 1
            else:
                break

        return hh_count >= self.min_hh_hl_sequence -1 and hl_count >= self.min_hh_hl_sequence -1, min(hh_count, hl_count)

    def _is_downtrend(self, highs, lows):
        if len(highs) < self.min_ll_lh_sequence or len(lows) < self.min_ll_lh_sequence:
            return False, 0

        ll_count = 0
        for i in range(1, self.min_ll_lh_sequence):
            if lows[-i] < lows[-(i+1)]:
                ll_count += 1
            else:
                break

        lh_count = 0
        for i in range(1, self.min_ll_lh_sequence):
            if highs[-i] < highs[-(i+1)]:
                lh_count += 1
            else:
                break
        
        return ll_count >= self.min_ll_lh_sequence -1 and lh_count >= self.min_ll_lh_sequence -1, min(ll_count, lh_count)

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_period + 1)
        highs = ctx.highs(self.lookback_period + 1)
        lows = ctx.lows(self.lookback_period + 1)

        if len(closes) < self.lookback_period + 1:
            return None

        current_close = ctx.bar.close

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Regime filter: Only trade in normal or volatile conditions, avoid crisis
        if ctx.market.get("regime", "NORMAL") == "CRISIS":
             return None
        
        # Volatility filter: Use historical volatility (standard deviation of returns)
        # to ensure there's enough movement for the strategy
        returns = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes))]
        volatility = self._stddev(returns, self.lookback_period)
        if volatility is None or volatility < 0.005: # Minimum volatility threshold (0.5% std dev)
            return None

        # Trend detection based on Higher Highs/Higher Lows or Lower Lows/Lower Highs sequence
        is_uptrend, uptrend_strength = self._is_uptrend(highs[:-1], lows[:-1]) # Check previous bar's trend
        is_downtrend, downtrend_strength = self._is_downtrend(highs[:-1], lows[:-1]) # Check previous bar's trend

        # Look for a pullback that preserves the trend structure
        if is_uptrend and uptrend_strength >= self.min_hh_hl_sequence -1:
            # Price pulled back but didn't break the last higher low
            last_higher_low = lows[-2] # Second to last low
            if current_close < highs[-1] and current_close > last_higher_low:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.7 + (uptrend_strength * 0.05), # Confidence scales with trend strength
                    metadata={
                        "reason": "uptrend_pullback_higher_low_preserved",
                        "current_close": current_close,
                        "last_high": highs[-1],
                        "last_higher_low": last_higher_low,
                        "uptrend_strength": uptrend_strength,
                        "market_regime": ctx.market.get("regime", "NORMAL"),
                        "volatility": volatility
                    }
                )

        elif is_downtrend and downtrend_strength >= self.min_ll_lh_sequence -1:
            # Price pulled back but didn't break the last lower high
            last_lower_high = highs[-2] # Second to last high
            if current_close > lows[-1] and current_close < last_lower_high:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.7 + (downtrend_strength * 0.05), # Confidence scales with trend strength
                    metadata={
                        "reason": "downtrend_pullback_lower_high_preserved",
                        "current_close": current_close,
                        "last_low": lows[-1],
                        "last_lower_high": last_lower_high,
                        "downtrend_strength": downtrend_strength,
                        "market_regime": ctx.market.get("regime", "NORMAL"),
                        "volatility": volatility
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive sizing for this strategy, just logging
        pass