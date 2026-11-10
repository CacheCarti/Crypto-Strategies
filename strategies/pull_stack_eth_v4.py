from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class StructuralPullback(Strategy):
    METADATA = {
        "name": "Structural Pullback",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 100,
        "required_features": ["fear_greed_index", "eth_return_pct", "twitter_sentiment"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_fast_period = 20
        self.ema_slow_period = 50
        self.rsi_period = 14
        self.atr_period = 14
        self.cooldown_bars = 5 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

        # For structural analysis
        self.lookback_structure = 30 # Bars to look for HH/HL
        self.swing_threshold = 0.005 # 0.5% price movement for a swing point
        self.structure_break_atr_mult = 1.5 # ATR multiple for structure break

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
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
            return 100.0 if avg_gain > 0 else 50.0 # Avoid division by zero
        
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _is_higher_high(self, highs, idx1, idx2, threshold):
        return highs[idx2] > highs[idx1] * (1 + threshold)

    def _is_higher_low(self, lows, idx1, idx2, threshold):
        return lows[idx2] > lows[idx1] * (1 + threshold)

    def _find_swing_points(self, highs, lows, threshold_pct):
        if len(highs) < 3:
            return [], []

        swing_highs = []
        swing_lows = []

        # Find swing highs
        for i in range(1, len(highs) - 1):
            if highs[i] > highs[i-1] and highs[i] > highs[i+1]:
                # Check for significant move
                if (highs[i] - min(highs[i-1], highs[i+1])) / highs[i] >= threshold_pct:
                    swing_highs.append(i)

        # Find swing lows
        for i in range(1, len(lows) - 1):
            if lows[i] < lows[i-1] and lows[i] < lows[i+1]:
                # Check for significant move
                if (min(lows[i-1], lows[i+1]) - lows[i]) / lows[i] >= threshold_pct:
                    swing_lows.append(i)
        
        return swing_highs, swing_lows

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])
        volumes = ctx.volumes(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown period
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_price = ctx.bar.close

        # Indicators
        ema_fast = self._ema(closes, self.ema_fast_period)
        ema_slow = self._ema(closes, self.ema_slow_period)
        current_rsi = self._rsi(closes, self.rsi_period)
        current_atr = self._atr(highs, lows, closes, self.atr_period)

        if ema_fast is None or ema_slow is None or current_rsi is None or current_atr is None:
            return None

        # Regime filters
        fear_greed = ctx.features.get("fear_greed_index", 50)
        eth_return_pct = ctx.features.get("eth_return_pct", 0.0)
        twitter_sentiment = ctx.features.get("twitter_sentiment", 0.0)
        market_regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")

        # Long Entry Logic
        if not ctx.has_position():
            # 1. Bullish Trend Confirmation (EMA crossover and market trend)
            is_uptrend = ema_fast > ema_slow and trend_regime == "bull" and closes[-1] > ema_fast

            if is_uptrend:
                # 2. Structural Pullback Detection (Higher Highs, Higher Lows)
                # Look for recent swing points within the lookback window
                recent_highs = highs[-self.lookback_structure:]
                recent_lows = lows[-self.lookback_structure:]
                recent_closes = closes[-self.lookback_structure:]

                swing_high_indices, swing_low_indices = self._find_swing_points(
                    recent_highs, recent_lows, self.swing_threshold
                )

                # Need at least two swing highs and two swing lows to confirm structure
                if len(swing_high_indices) >= 2 and len(swing_low_indices) >= 2:
                    # Check for recent higher high and higher low
                    last_sh_idx = swing_high_indices[-1]
                    second_last_sh_idx = swing_high_indices[-2]
                    last_sl_idx = swing_low_indices[-1]
                    second_last_sl_idx = swing_low_indices[-2]

                    is_hh = recent_highs[last_sh_idx] > recent_highs[second_last_sh_idx]
                    is_hl = recent_lows[last_sl_idx] > recent_lows[second_last_sl_idx]

                    # Ensure the current price is a pullback (below recent high, above recent low)
                    # and price is turning up (current close > previous close)
                    is_pullback = (current_price < recent_highs[last_sh_idx] and
                                   current_price > recent_lows[last_sl_idx])
                    
                    price_turning_up = closes[-1] > closes[-2]

                    # RSI condition for pullback (not overbought, suggesting room to move up)
                    rsi_ok_for_pullback = current_rsi < 60

                    # Additional regime filter: avoid extreme volatility or fear
                    calm_regime = market_regime in ["NORMAL", "calm"]
                    not_extreme_fear = fear_greed > 30

                    if (is_hh and is_hl and is_pullback and price_turning_up and rsi_ok_for_pullback and
                        calm_regime and not_extreme_fear):

                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal(
                            "long",
                            confidence=0.75,
                            metadata={
                                "reason": "structural_pullback_long",
                                "ema_fast": ema_fast,
                                "ema_slow": ema_slow,
                                "current_rsi": current_rsi,
                                "current_price": current_price,
                                "fear_greed": fear_greed,
                                "market_regime": market_regime,
                                "trend_regime": trend_regime,
                            }
                        )

        # Position Management (Exit Logic)
        if ctx.has_position():
            pos = ctx.position
            entry_price = pos["entry_price"]
            direction = pos["direction"]

            # Exit on structure break (for long positions)
            if direction == "long":
                # A break below the last swing low could indicate structure breakdown
                recent_lows_for_exit = lows[-self.lookback_structure:]
                swing_high_indices_exit, swing_low_indices_exit = self._find_swing_points(
                    highs[-self.lookback_structure:], recent_lows_for_exit, self.swing_threshold
                )

                if len(swing_low_indices_exit) >= 1:
                    last_sl_idx_abs = len(lows) - self.lookback_structure + swing_low_indices_exit[-1]
                    last_swing_low = lows[last_sl_idx_abs]

                    # Structure break if price closes significantly below the last swing low
                    # using ATR as a dynamic threshold
                    if current_price < last_swing_low - (self.structure_break_atr_mult * current_atr):
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal(
                            "flat",
                            confidence=0.9,
                            metadata={
                                "reason": "structure_break_exit_long",
                                "entry_price": entry_price,
                                "current_price": current_price,
                                "last_swing_low": last_swing_low,
                                "atr_threshold": self.structure_break_atr_mult * current_atr,
                            }
                        )
                
                # Exit at prior high (a form of take profit or resistance)
                # Find a significant prior high that could act as resistance
                if len(highs) > self.ema_slow_period: # Ensure enough history
                    prior_high_candidate = max(highs[-self.ema_slow_period:-1]) # Exclude current bar
                    
                    if current_price >= prior_high_candidate * (1 - 0.001) and current_price > entry_price: # Close to prior high and profitable
                        # Add a condition to ensure it's not still in a strong uptrend
                        if current_rsi > 70 or ema_fast < ema_slow: # Overbought or trend weakening
                            self.last_trade_bar = ctx.bar_index
                            return ctx.signal(
                                "flat",
                                confidence=0.8,
                                metadata={
                                    "reason": "prior_high_resistance_exit_long",
                                    "entry_price": entry_price,
                                    "current_price": current_price,
                                    "prior_high_candidate": prior_high_candidate,
                                    "current_rsi": current_rsi,
                                }
                            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last trade bar to prevent immediate re-entry
        self.last_trade_bar = ctx.bar_index