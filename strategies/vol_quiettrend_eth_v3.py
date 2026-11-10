from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityQuietTrendETH(Strategy):
    METADATA = {
        "name": "VolatilityQuietTrendETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,
        "warmup_bars": 50, # ATR 14 + EMA 20
        "required_features": [], # No explicit features needed, using OHLCV and market context
    }

    def initialize(self, ctx: BarContext) -> None:
        self.atr_period = 14
        self.ema_period = 20
        self.vol_threshold_multiplier = 1.5 # How many ATRs above normal to consider high vol
        self.min_quiet_bars = 5 # Number of bars to confirm quiet regime
        self.cooldown_bars = 10 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade if conditions met

        # Store previous bar's ATR for transition detection
        self.prev_atr = None
        self.prev_regime_is_quiet = False

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        # Simple Moving Average of True Range
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

        # Calculate indicators
        current_atr = self._atr(highs, lows, closes, self.atr_period)
        ema_20 = self._ema(closes, self.ema_period)

        if current_atr is None or ema_20 is None:
            return None

        # Determine quiet regime
        # A simple way to determine "normal" volatility is to take an average of recent ATRs.
        # Here we'll use the current ATR and check if it's below a threshold.
        # For a more robust "normal", we might need a longer-term ATR average.
        # For this strategy, we'll consider "quiet" if current_atr is below a certain multiple of its recent average.
        # Or, simpler, if the market regime is 'calm' or 'normal' and not 'volatile' or 'crisis'.
        # Let's use ctx.market.get("regime") as a primary filter.
        market_regime = ctx.market.get("regime", "NORMAL")
        is_quiet_regime = market_regime in ["CALM", "NORMAL"]

        # Trend detection using EMA slope or price relative to EMA
        is_uptrend = closes[-1] > ema_20 and closes[-1] > closes[-2] # Price above EMA and rising
        is_downtrend = closes[-1] < ema_20 and closes[-1] < closes[-2] # Price below EMA and falling

        # Check for cooldown
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Logic for event boundary detection:
        # We want to enter after a quiet period following some volatility, or after a sustained quiet period.
        # The key is to detect a *transition* from non-quiet to quiet, or sustained quiet.
        # We need to accumulate quiet bars.
        # Let's track a counter for consecutive quiet bars.
        if not hasattr(self, 'consecutive_quiet_bars'):
            self.consecutive_quiet_bars = 0

        if is_quiet_regime:
            self.consecutive_quiet_bars += 1
        else:
            self.consecutive_quiet_bars = 0

        # Entry conditions
        signal = None
        if not ctx.has_position():
            # Long entry: Trend up, market is quiet, and we have enough consecutive quiet bars
            # Also, we should ensure we just *entered* a quiet period or sustained it.
            # To avoid continuous signals, we trigger only if we *just* crossed min_quiet_bars
            # or if we are in a sustained quiet trend.
            if is_uptrend and self.consecutive_quiet_bars >= self.min_quiet_bars:
                # To prevent re-entry on every bar during a long quiet trend,
                # we only trigger if we *just* crossed the threshold or if the trend strengthened
                # and we haven't traded recently.
                # A simple check: if we were not quiet enough last bar, but are now.
                # Or if the previous regime was not quiet, and now it is.
                if (self.consecutive_quiet_bars == self.min_quiet_bars) or \
                   (not self.prev_regime_is_quiet and is_quiet_regime and self.consecutive_quiet_bars >= self.min_quiet_bars):
                    signal = ctx.signal(
                        "long",
                        confidence=0.75,
                        metadata={
                            "reason": "quiet_uptrend_entry",
                            "regime": market_regime,
                            "consecutive_quiet_bars": self.consecutive_quiet_bars,
                            "current_atr": current_atr,
                            "ema_20": ema_20,
                            "close": closes[-1],
                        }
                    )
            # Short entry: Trend down, market is quiet, and we have enough consecutive quiet bars
            elif is_downtrend and self.consecutive_quiet_bars >= self.min_quiet_bars:
                if (self.consecutive_quiet_bars == self.min_quiet_bars) or \
                   (not self.prev_regime_is_quiet and is_quiet_regime and self.consecutive_quiet_bars >= self.min_quiet_bars):
                    signal = ctx.signal(
                        "short",
                        confidence=0.75,
                        metadata={
                            "reason": "quiet_downtrend_entry",
                            "regime": market_regime,
                            "consecutive_quiet_bars": self.consecutive_quiet_bars,
                            "current_atr": current_atr,
                            "ema_20": ema_20,
                            "close": closes[-1],
                        }
                    )
        elif ctx.has_position():
            # Exit condition: volatility expansion (regime changes to volatile/crisis)
            # Or if the trend reverses against the position
            position_direction = ctx.position_direction()
            
            if market_regime in ["VOLATILE", "CRISIS"]: # Volatility expansion
                signal = ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={
                        "reason": "volatility_expansion_exit",
                        "regime": market_regime,
                        "current_atr": current_atr,
                        "close": closes[-1],
                        "position_direction": position_direction
                    }
                )
            elif position_direction == "long" and is_downtrend: # Trend reversal for long
                 signal = ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "long_trend_reversal_exit",
                        "regime": market_regime,
                        "current_atr": current_atr,
                        "ema_20": ema_20,
                        "close": closes[-1],
                        "position_direction": position_direction
                    }
                )
            elif position_direction == "short" and is_uptrend: # Trend reversal for short
                 signal = ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "short_trend_reversal_exit",
                        "regime": market_regime,
                        "current_atr": current_atr,
                        "ema_20": ema_20,
                        "close": closes[-1],
                        "position_direction": position_direction
                    }
                )

        # Update state for next bar
        self.prev_atr = current_atr
        self.prev_regime_is_quiet = is_quiet_regime

        if signal:
            self.last_trade_bar = ctx.bar_index
        
        return signal

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic needed for this exploration hypothesis.
        pass