from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class OvernightHandoffBTC(Strategy):
    METADATA = {
        "name": "Overnight Handoff BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 6, # Roughly 6 hours
        "warmup_bars": 50,
        "required_features": ["btc_return_pct", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_hour_start = 0  # UTC 00:00
        self.entry_hour_end = 6    # UTC 06:00
        self.exit_hour = 12        # Exit before US session (UTC 12:00)
        self.min_volatility_score = 0.3 # Example threshold for volatility regime
        self.min_trend_confidence = 0.4 # Example threshold for trend confidence
        self.cooldown_bars = 5
        self.last_signal_bar_index = -self.cooldown_bars

        # Indicator periods
        self.ema_short_period = 10
        self.ema_long_period = 30
        self.rsi_period = 14
        self.atr_period = 14

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        
        # Calculate initial averages
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        
        # Apply smoothing
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0 # Handle division by zero
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        
        # Calculate initial ATR
        atr_val = sum(trs[:period]) / period
        
        # Apply smoothing for subsequent ATR values
        for i in range(period, len(trs)):
            atr_val = (atr_val * (period - 1) + trs[i]) / period
        
        return atr_val


    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)
        volumes = ctx.volumes(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        
        # Calculate indicators
        ema_short = self._ema(closes, self.ema_short_period)
        ema_long = self._ema(closes, self.ema_long_period)
        rsi = self._rsi(closes, self.rsi_period)
        atr = self._atr(highs, lows, closes, self.atr_period)

        if ema_short is None or ema_long is None or rsi is None or atr is None:
            return None

        # Regime filters
        market_regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")
        trend_confidence = ctx.market.get("trend_regime_confidence", 0.0)
        crisis_score = ctx.market.get("crisis_score", 0.0)
        fear_greed_index = ctx.features.get("fear_greed_index", 50)

        # Volatility filter: Stand down in crisis or very low volatility
        if crisis_score > 0.5: # Or if market_regime == "CRISIS" or "MELTDOWN"
            return None
        
        # Trend filter: Only trade if there's a clear trend and confidence
        if trend_confidence < self.min_trend_confidence:
            return None

        # Exit logic
        if ctx.has_position():
            # Close position if it's past the exit hour
            if current_hour >= self.exit_hour:
                exit_reason = "session_handoff_exit"
                if ctx.position["direction"] == "long":
                    entry_price = ctx.position["entry_price"]
                    current_price = ctx.bar.close
                    return_bps = (current_price - entry_price) / entry_price * 10000
                    return ctx.signal("flat", confidence=1.0, metadata={"reason": exit_reason, "current_hour": current_hour, "return_bps": return_bps})
                elif ctx.position["direction"] == "short":
                    entry_price = ctx.position["entry_price"]
                    current_price = ctx.bar.close
                    return_bps = (entry_price - current_price) / entry_price * 10000
                    return ctx.signal("flat", confidence=1.0, metadata={"reason": exit_reason, "current_hour": current_hour, "return_bps": return_bps})
            return None # Hold position until exit hour

        # Entry logic (only within entry window and if not on cooldown)
        if self.entry_hour_start <= current_hour < self.entry_hour_end and \
           ctx.bar_index >= self.last_signal_bar_index + self.cooldown_bars:

            # Determine overnight impulse direction
            # Using EMA crossover for directional bias within the session
            # And RSI for overbought/oversold confirmation
            
            # Long condition
            if ema_short > ema_long and rsi < 50 and trend_regime == "bull":
                # Ensure price is above the short EMA for confirmation
                if ctx.bar.close > ema_short:
                    self.last_signal_bar_index = ctx.bar_index
                    # Confidence based on RSI and trend confidence
                    confidence = 0.5 + (1 - rsi / 50) * 0.2 + trend_confidence * 0.3
                    confidence = min(1.0, confidence) # Cap confidence at 1.0

                    # Adjust SL/TP dynamically based on ATR
                    dynamic_sl_bps = max(self.METADATA["declared_sl_bps"], atr / ctx.bar.close * 10000 * 1.5)
                    dynamic_tp_bps = max(self.METADATA["declared_tp_bps"], atr / ctx.bar.close * 10000 * 2.5)

                    return ctx.signal(
                        "long",
                        confidence=confidence,
                        stop_loss_bps=dynamic_sl_bps,
                        take_profit_bps=dynamic_tp_bps,
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "overnight_long_impulse_ema_rsi",
                            "ema_short": ema_short,
                            "ema_long": ema_long,
                            "rsi": rsi,
                            "atr": atr,
                            "trend_regime": trend_regime,
                            "trend_confidence": trend_confidence,
                            "current_hour": current_hour,
                            "fear_greed_index": fear_greed_index
                        }
                    )
            
            # Short condition
            elif ema_short < ema_long and rsi > 50 and trend_regime == "bear":
                # Ensure price is below the short EMA for confirmation
                if ctx.bar.close < ema_short:
                    self.last_signal_bar_index = ctx.bar_index
                    # Confidence based on RSI and trend confidence
                    confidence = 0.5 + (rsi / 50 - 1) * 0.2 + trend_confidence * 0.3
                    confidence = min(1.0, confidence) # Cap confidence at 1.0

                    # Adjust SL/TP dynamically based on ATR
                    dynamic_sl_bps = max(self.METADATA["declared_sl_bps"], atr / ctx.bar.close * 10000 * 1.5)
                    dynamic_tp_bps = max(self.METADATA["declared_tp_bps"], atr / ctx.bar.close * 10000 * 2.5)

                    return ctx.signal(
                        "short",
                        confidence=confidence,
                        stop_loss_bps=dynamic_sl_bps,
                        take_profit_bps=dynamic_tp_bps,
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "overnight_short_impulse_ema_rsi",
                            "ema_short": ema_short,
                            "ema_long": ema_long,
                            "rsi": rsi,
                            "atr": atr,
                            "trend_regime": trend_regime,
                            "trend_confidence": trend_confidence,
                            "current_hour": current_hour,
                            "fear_greed_index": fear_greed_index
                        }
                    )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # You can add logging or adaptive sizing logic here if needed.
        # For this strategy, we mainly care about the cooldown, which is handled in on_bar.
        pass