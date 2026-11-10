from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PostPanicDrift(Strategy):
    METADATA = {
        "name": "Post Panic Drift",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours hold
        "warmup_bars": 60,
        "required_features": ["fear_greed_index", "eth_return_pct"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.drop_period = 10
        self.low_vol_period = 5
        self.stabilization_bars = 3 # bars of low volatility after drop
        self.min_drop_pct = -2.0 # minimum drop percentage over drop_period
        self.vol_threshold_multiplier = 0.5 # current volume must be below this multiple of avg vol
        self.rsi_period = 14
        self.cooldown_bars = 6 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars # Initialize to allow trade immediately

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def _rsi(self, closes, period):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        
        if len(gains) < period: # Ensure enough data for the initial average
            return None

        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        
        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0 # Avoid division by zero
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1)
        highs = ctx.highs(self.warmup_bars + 1)
        lows = ctx.lows(self.warmup_bars + 1)
        volumes = ctx.volumes(self.warmup_bars + 1)

        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        # Check for existing position
        if ctx.has_position():
            # If we're in a position and it's a long, but market turns extremely fearful, consider exiting
            # This adds a dynamic exit condition
            fear_greed_index = ctx.features.get("fear_greed_index", 50)
            if ctx.position_direction() == "long" and fear_greed_index < 20: # Extreme Fear
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "exit_due_to_extreme_fear",
                    "fear_greed_index": fear_greed_index,
                    "current_price": ctx.bar.close
                })
            return None # Hold position if no exit condition met

        # 1. Identify a sharp drop
        price_drop_start = closes[-self.drop_period - 1] if len(closes) > self.drop_period else None
        if price_drop_start is None:
            return None
        
        current_price = ctx.bar.close
        price_change_pct = ((current_price - price_drop_start) / price_drop_start) * 100
        
        recent_closes = closes[-self.drop_period:]
        if not (price_change_pct < self.min_drop_pct and current_price == min(recent_closes)):
            return None # Not a significant drop ending at current low

        # 2. Check for stabilization (low volatility and low volume) post-drop
        # We need data beyond the drop period to assess stabilization
        if len(closes) < self.drop_period + self.stabilization_bars + 1:
            return None

        # Check volatility in the stabilization period after the drop
        stabilization_closes = closes[-self.stabilization_bars:]
        if len(stabilization_closes) < self.stabilization_bars:
            return None
        
        stabilization_stdev = self._stddev(stabilization_closes, self.stabilization_bars)
        if stabilization_stdev is None:
            return None

        # Compare current volatility to historical average volatility (e.g., over 2x drop_period)
        long_term_stdev = self._stddev(closes, self.drop_period * 2)
        if long_term_stdev is None or long_term_stdev == 0:
            return None

        # Low volatility check: current stabilization volatility is significantly lower than recent average
        if stabilization_stdev / long_term_stdev > 0.7: # Adjust threshold as needed
            return None

        # Check for low volume during stabilization
        avg_volume_long_term = self._sma(volumes, self.drop_period * 2)
        if avg_volume_long_term is None or avg_volume_long_term == 0:
            return None

        current_volume = ctx.bar.volume
        if current_volume > avg_volume_long_term * self.vol_threshold_multiplier:
            return None # Volume not low enough

        # 3. Confirmation with RSI (oversold but not extremely so, suggesting stabilization)
        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        # RSI should be oversold but not in extreme panic territory, indicating a potential bottoming process
        # A value between 30 and 40 could indicate stabilization after a drop
        if not (30 < rsi < 40):
            return None

        # 4. Regime filter: Avoid trading into "Crisis" or "Meltdown" regimes
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ["CRISIS", "MELTDOWN"]:
            return None

        # 5. Sentiment filter: Avoid trading if fear is too high
        fear_greed_index = ctx.features.get("fear_greed_index", 50)
        if fear_greed_index < 25: # Avoid "Extreme Fear"
            return None

        # If all conditions are met, generate a long signal
        confidence = 0.7 + (40 - rsi) / 100.0 # Higher confidence if RSI is lower (closer to 30)
        confidence = min(0.9, max(0.5, confidence)) # Cap confidence

        self.last_trade_bar = ctx.bar_index
        return ctx.signal(
            "long",
            confidence=confidence,
            metadata={
                "reason": "post_panic_drift_entry",
                "price_drop_pct": round(price_change_pct, 2),
                "stabilization_stdev_ratio": round(stabilization_stdev / long_term_stdev, 2),
                "volume_ratio": round(current_volume / avg_volume_long_term, 2),
                "rsi": round(rsi, 2),
                "fear_greed_index": fear_greed_index,
                "market_regime": market_regime
            }
        )

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for this strategy
        pass