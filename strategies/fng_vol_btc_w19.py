from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FearGreedPanicBuy(Strategy):
    METADATA = {
        "name": "Fear Greed Panic Buy",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 60, # Need enough bars for ATR and EMA
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.atr_period = 20
        self.ema_fast_period = 10
        self.ema_slow_period = 30
        self.rsi_period = 14
        self.cooldown_bars = 5 # Wait 5 bars after a trade before entering again
        self.last_trade_bar = -self.cooldown_bars # Initialize to allow immediate trading

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

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
            return 100.0 if avg_gain > 0 else 50.0 # Handle flat market
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        current_price = ctx.bar.close

        # Calculate indicators
        atr = self._atr(highs, lows, closes, self.atr_period)
        ema_fast = self._ema(closes, self.ema_fast_period)
        ema_slow = self._ema(closes, self.ema_slow_period)
        rsi = self._rsi(closes, self.rsi_period)

        if any(x is None for x in [atr, ema_fast, ema_slow, rsi]):
            return None

        # Market Context / Features
        fear_greed_index = ctx.features.get("fear_greed_index", 50)
        
        # Volatility check: Compare current ATR to a rolling average of ATR (simple average for simplicity)
        # We need a longer history of ATR for this. Let's assume we have it.
        # For this example, we'll use a simpler proxy: current ATR is significantly higher than recent average.
        # This requires more history than self.warmup_bars. Let's use `max(closes)` as a proxy for price range.
        # A more robust solution would compute a rolling average of ATR itself.
        
        # Simple proxy for "elevated volatility" and "weakness"
        # Check if current ATR is > 1.5 * average ATR of the last X periods.
        # To avoid computing average ATR, let's just check if current ATR is above a certain threshold relative to price.
        # This is a bit of a shortcut due to performance/complexity constraints.
        # A 1-hour bar, 20-period ATR of 0.5% of price could indicate panic.
        
        atr_percent = (atr / current_price) * 100 if current_price > 0 else 0
        elevated_volatility = atr_percent > 0.5 # Example threshold: ATR is more than 0.5% of price
        
        # Entry Logic: Buy weakness when fear is high and volatility is elevated (panic)
        if not ctx.has_position():
            if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
                return None # Cooldown in effect

            # Conditions for LONG entry
            # 1. Low Fear & Greed Index (Fear / Extreme Fear)
            fear_condition = fear_greed_index <= 30 # Extreme Fear or Fear
            
            # 2. Elevated Volatility (panic)
            volatility_condition = elevated_volatility

            # 3. Price weakness - RSI oversold OR price below fast EMA (short-term weakness)
            weakness_condition = rsi < 35 or current_price < ema_fast # RSI oversold or below short-term EMA

            # 4. Trend context: price is generally above slow EMA (buying dips in an uptrend)
            # Or at least not a strong downtrend (EMA fast > EMA slow, or EMAs converging/flat)
            # For "buy weakness", we want to catch falling knives, so we allow price below slow EMA
            # but ensure fast EMA is not too far below slow EMA (avoiding strong downtrends)
            trend_context = ema_fast > ema_slow * 0.995 # Fast EMA not significantly below slow EMA

            if fear_condition and volatility_condition and weakness_condition and trend_context:
                # Add a confidence based on how extreme the fear/volatility is
                confidence = max(0.5, (30 - fear_greed_index) / 30.0 + (atr_percent - 0.5) / 0.5) # Scale to 0-1
                confidence = min(1.0, confidence) # Cap confidence at 1.0

                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    metadata={
                        "reason": "fear_greed_panic_buy_long",
                        "fear_greed_index": fear_greed_index,
                        "atr_percent": atr_percent,
                        "rsi": rsi,
                        "ema_fast": ema_fast,
                        "ema_slow": ema_slow,
                        "current_price": current_price
                    }
                )

        # Exit Logic: Exit on volatility normalization or sentiment recovery
        elif ctx.has_position() and ctx.position_direction() == "long":
            # Conditions for FLAT exit
            # 1. Volatility Normalization: ATR drops significantly from elevated levels
            #    We use a threshold for ATR percent, if it drops below, consider volatility normalized.
            volatility_normalization = atr_percent < 0.3 # Example: ATR below 0.3% of price

            # 2. Sentiment Recovery: Fear & Greed Index rises significantly
            sentiment_recovery = fear_greed_index > 40 # Neutral or Greed

            # 3. Price recovery: RSI moves out of oversold, or price above fast EMA
            price_recovery = rsi > 50 or current_price > ema_fast * 1.005 # Above EMA by a small margin

            # Exit if volatility normalized OR sentiment recovered OR price recovered
            if (volatility_normalization and price_recovery) or sentiment_recovery:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7, # Moderate confidence for exit
                    metadata={
                        "reason": "volatility_sentiment_recovery_exit",
                        "fear_greed_index": fear_greed_index,
                        "atr_percent": atr_percent,
                        "rsi": rsi,
                        "ema_fast": ema_fast,
                        "current_price": current_price
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log or adapt behavior based on trade outcome
        pass