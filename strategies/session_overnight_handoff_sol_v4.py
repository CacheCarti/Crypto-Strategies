from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSessionHandoff(Strategy):
    METADATA = {
        "name": "SOL Session Handoff",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 6, # Intended hold for a session, up to 6 hours
        "warmup_bars": 50,
        "required_features": ["fear_greed_index", "funding_rate_solusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_cooldown_bars = 12 # Cooldown for 12 hours after an entry attempt
        self.last_entry_bar = -self.entry_cooldown_bars
        self.session_length_hours = 6 # Overnight session is 0-6 UTC
        self.exit_hour = 7 # Exit before 7 UTC to avoid US session open volatility
        self.fast_ema_period = 10
        self.slow_ema_period = 30
        self.rsi_period = 14
        self.vol_sma_period = 20

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        # Calculate initial SMA for the first 'period' values
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes, period):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))

        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period

        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0 # Handle division by zero
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1) # Need one more for RSI diff
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)
        volumes = ctx.volumes(self.warmup_bars)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_close = ctx.bar.close

        # Calculate indicators
        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)
        rsi = self._rsi(closes, self.rsi_period)
        avg_volume = self._sma(volumes, self.vol_sma_period)

        if fast_ema is None or slow_ema is None or rsi is None or avg_volume is None:
            return None

        # Feature-based regime filters
        fear_greed = ctx.features.get("fear_greed_index", 50)
        funding_rate = ctx.features.get("funding_rate_solusdt", 0.0)
        market_regime = ctx.market.get("regime", "NORMAL")

        # Cooldown logic
        if ctx.bar_index - self.last_entry_bar < self.entry_cooldown_bars:
            if ctx.has_position():
                return ctx.signal("hold", confidence=0.0, metadata={"reason": "cooldown_hold"})
            return None # Skip entry attempts during cooldown

        # Exit logic
        if ctx.has_position():
            if current_hour == self.exit_hour:
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "session_handoff_exit",
                    "current_hour": current_hour,
                    "entry_hour": ctx.position["metadata"].get("entry_hour", "N/A")
                })
            return ctx.signal("hold", confidence=0.0, metadata={"reason": "holding_position"})

        # Entry logic - only during overnight session (0-6 UTC)
        if 0 <= current_hour < self.session_length_hours:
            # Check for sufficient volatility and neutral sentiment
            if market_regime == "CRISIS" or market_regime == "MELTDOWN" or fear_greed < 30 or fear_greed > 70:
                return None # Stand down in extreme conditions

            # Long entry conditions
            if (fast_ema > slow_ema and # Bullish EMA crossover or trend
                rsi < 45 and # Price pulls back into value (oversold in short term, but trend is up)
                current_close > fast_ema and # Current price confirms above fast EMA
                volumes[-1] > avg_volume * 1.2 and # Volume confirmation
                funding_rate > -0.0001 # Avoid negative funding rates indicating strong short bias
            ):
                self.last_entry_bar = ctx.bar_index
                return ctx.signal("long", confidence=0.75, metadata={
                    "reason": "sol_overnight_long_setup",
                    "fast_ema": fast_ema,
                    "slow_ema": slow_ema,
                    "rsi": rsi,
                    "volume_ratio": volumes[-1] / avg_volume,
                    "funding_rate": funding_rate,
                    "fear_greed": fear_greed,
                    "market_regime": market_regime,
                    "entry_hour": current_hour
                })

            # Short entry conditions
            if (fast_ema < slow_ema and # Bearish EMA crossover or trend
                rsi > 55 and # Price pushes into overbought (but trend is down)
                current_close < fast_ema and # Current price confirms below fast EMA
                volumes[-1] > avg_volume * 1.2 and # Volume confirmation
                funding_rate < 0.0001 # Avoid positive funding rates indicating strong long bias
            ):
                self.last_entry_bar = ctx.bar_index
                return ctx.signal("short", confidence=0.75, metadata={
                    "reason": "sol_overnight_short_setup",
                    "fast_ema": fast_ema,
                    "slow_ema": slow_ema,
                    "rsi": rsi,
                    "volume_ratio": volumes[-1] / avg_volume,
                    "funding_rate": funding_rate,
                    "fear_greed": fear_greed,
                    "market_regime": market_regime,
                    "entry_hour": current_hour
                })
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close for analysis or adaptive sizing
        pass