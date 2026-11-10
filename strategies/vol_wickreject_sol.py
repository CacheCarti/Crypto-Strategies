from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolWickRejectSol(Strategy):
    METADATA = {
        "name": "VolWickRejectSol",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Intended hold time of 4 hours
        "warmup_bars": 50,  # Need enough bars for ATR and volume calculations
        "required_features": [], # No external features needed for this pure OHLCV strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.atr_period = 20
        self.volume_period = 20
        self.cooldown_bars = 10  # Cooldown period after a trade
        self.last_signal_bar_index = -self.cooldown_bars - 1 # Initialize to allow immediate trade

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
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)
        opens = ctx.opens(self.warmup_bars)
        volumes = ctx.volumes(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        # Cooldown check
        if ctx.bar_index - self.last_signal_bar_index < self.cooldown_bars:
            return None

        current_close = ctx.bar.close
        current_open = ctx.bar.open
        current_high = ctx.bar.high
        current_low = ctx.bar.low
        current_volume = ctx.bar.volume

        prev_close = closes[-2] if len(closes) >= 2 else None
        prev_open = opens[-2] if len(opens) >= 2 else None
        prev_high = highs[-2] if len(highs) >= 2 else None
        prev_low = lows[-2] if len(lows) >= 2 else None
        prev_volume = volumes[-2] if len(volumes) >= 2 else None

        if prev_close is None or prev_open is None or prev_high is None or prev_low is None or prev_volume is None:
            return None

        current_atr = self._atr(highs, lows, closes, self.atr_period)
        avg_volume = self._sma(volumes, self.volume_period)

        if current_atr is None or avg_volume is None:
            return None

        # Volatility filter: Only consider when market is not in crisis
        if ctx.market.get("crisis_score", 0.0) > 0.5:
             return None

        # Check for previous bar's large wick and high volume
        # Long wick (rejection from above) for a potential short
        # A large upper wick means high - max(open, close) is significant
        # A large lower wick means min(open, close) - low is significant
        
        # Upper wick for short signal: large upper wick on previous bar
        prev_upper_wick = prev_high - max(prev_open, prev_close)
        prev_body_size = abs(prev_open - prev_close)
        
        # Lower wick for long signal: large lower wick on previous bar
        prev_lower_wick = min(prev_open, prev_close) - prev_low

        # Condition 1: Significant wick relative to ATR and body size
        # Condition 2: High volume on the previous bar
        # Condition 3: Current bar confirms the rejection

        # Short Signal Logic
        # Previous bar has a large upper wick (rejection from highs)
        # and current bar closes lower, confirming rejection
        if (prev_upper_wick > 1.5 * current_atr # Large upper wick
            and prev_upper_wick > 2 * prev_body_size # Wick is significantly larger than body
            and prev_volume > 1.5 * avg_volume # High volume on wick bar
            and current_close < prev_close # Current bar closes lower
            and current_close < current_open # Current bar is bearish
            and not ctx.has_position()):

            self.last_signal_bar_index = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                metadata={
                    "reason": "vol_wick_rejection_short_confirm",
                    "prev_upper_wick_atr_ratio": prev_upper_wick / current_atr,
                    "prev_volume_avg_ratio": prev_volume / avg_volume,
                    "current_close": current_close,
                    "prev_close": prev_close,
                }
            )

        # Long Signal Logic
        # Previous bar has a large lower wick (rejection from lows)
        # and current bar closes higher, confirming rejection
        if (prev_lower_wick > 1.5 * current_atr # Large lower wick
            and prev_lower_wick > 2 * prev_body_size # Wick is significantly larger than body
            and prev_volume > 1.5 * avg_volume # High volume on wick bar
            and current_close > prev_close # Current bar closes higher
            and current_close > current_open # Current bar is bullish
            and not ctx.has_position()):

            self.last_signal_bar_index = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                metadata={
                    "reason": "vol_wick_rejection_long_confirm",
                    "prev_lower_wick_atr_ratio": prev_lower_wick / current_atr,
                    "prev_volume_avg_ratio": prev_volume / avg_volume,
                    "current_close": current_close,
                    "prev_close": prev_close,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for this strategy, simply log the close
        pass