from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class ThreeDriveExhaustion(Strategy):
    METADATA = {
        "name": "Three Drive Exhaustion",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4, # Roughly 4 hours
        "warmup_bars": 50,
        "required_features": ["fear_greed_index", "funding_rate_solusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 10
        self.slow_ema_period = 30
        self.rsi_period = 7
        self.atr_period = 14
        self.cooldown_bars = 5 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period # Simple average for the initial EMA
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

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.slow_ema_period + self.rsi_period + self.atr_period)
        highs = ctx.highs(self.slow_ema_period + self.rsi_period + self.atr_period)
        lows = ctx.lows(self.slow_ema_period + self.rsi_period + self.atr_period)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown period
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        current_price = ctx.bar.close

        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)
        rsi = self._rsi(closes, self.rsi_period)
        atr = self._atr(highs, lows, closes, self.atr_period)

        if fast_ema is None or slow_ema is None or rsi is None or atr is None:
            return None

        # Regime filters
        fear_greed = ctx.features.get("fear_greed_index", 50)
        funding_rate = ctx.features.get("funding_rate_solusdt", 0.0)
        market_regime = ctx.market.get("regime", "NORMAL")

        # Only trade in normal or calm regimes for mean reversion, avoid crisis
        if market_regime in ["CRISIS", "MELTDOWN"]:
            return None

        # Three-Drive Exhaustion Logic
        # Look for 3 consecutive bars in the same direction, with decreasing momentum
        # Momentum approximated by RSI and candle body size
        
        # We need at least 3 bars for this logic
        if len(closes) < 4:
            return None

        # Long Entry: Three drives down, weakening momentum
        # 1. Price is below slow EMA (downtrend)
        # 2. RSI is oversold or moving towards it but showing divergence (weakening momentum)
        # 3. Last three bars are down bars (close < open), with decreasing body size
        
        # Check for 3 down bars
        is_down_bar = [closes[i] < closes[i-1] for i in range(1, len(closes))]
        if len(is_down_bar) >= 3 and is_down_bar[-1] and is_down_bar[-2] and is_down_bar[-3]:
            # Check for decreasing momentum (e.g., smaller candle bodies)
            body_sizes = [abs(closes[i] - opens[i]) for i in range(len(closes))]
            if body_sizes[-1] < body_sizes[-2] and body_sizes[-2] < body_sizes[-3]:
                # Check for oversold RSI and potential divergence (RSI not making new lows with price)
                if rsi < 40 and closes[-1] < slow_ema:
                    # Additional check: funding rate negative (bearish sentiment, good for reversal)
                    if funding_rate < -0.0001: # e.g., -0.01% per 8h
                        # Confidence based on how strong the signals are
                        confidence = 0.6 + (0.1 if rsi < 30 else 0) + (0.1 if fear_greed > 60 else 0) # Higher confidence if more oversold or fearful
                        if not ctx.has_position():
                            self.last_trade_bar = ctx.bar_index
                            return ctx.signal(
                                "long",
                                confidence=confidence,
                                stop_loss_bps=self.METADATA["declared_sl_bps"],
                                take_profit_bps=self.METADATA["declared_tp_bps"],
                                horizon_seconds=self.METADATA["declared_hold_seconds"],
                                metadata={
                                    "reason": "three_drive_down_exhaustion",
                                    "current_price": current_price,
                                    "rsi": rsi,
                                    "fast_ema": fast_ema,
                                    "slow_ema": slow_ema,
                                    "funding_rate": funding_rate,
                                    "fear_greed": fear_greed,
                                    "market_regime": market_regime,
                                }
                            )

        # Short Entry: Three drives up, weakening momentum
        # 1. Price is above slow EMA (uptrend)
        # 2. RSI is overbought or moving towards it but showing divergence (weakening momentum)
        # 3. Last three bars are up bars (close > open), with decreasing body size

        # Check for 3 up bars
        is_up_bar = [closes[i] > closes[i-1] for i in range(1, len(closes))]
        if len(is_up_bar) >= 3 and is_up_bar[-1] and is_up_bar[-2] and is_up_bar[-3]:
            # Check for decreasing momentum (e.g., smaller candle bodies)
            body_sizes = [abs(closes[i] - opens[i]) for i in range(len(closes))]
            if body_sizes[-1] < body_sizes[-2] and body_sizes[-2] < body_sizes[-3]:
                # Check for overbought RSI and potential divergence
                if rsi > 60 and closes[-1] > slow_ema:
                    # Additional check: funding rate positive (bullish sentiment, good for reversal)
                    if funding_rate > 0.0001: # e.g., +0.01% per 8h
                        # Confidence based on how strong the signals are
                        confidence = 0.6 + (0.1 if rsi > 70 else 0) + (0.1 if fear_greed < 40 else 0) # Higher confidence if more overbought or greedy
                        if not ctx.has_position():
                            self.last_trade_bar = ctx.bar_index
                            return ctx.signal(
                                "short",
                                confidence=confidence,
                                stop_loss_bps=self.METADATA["declared_sl_bps"],
                                take_profit_bps=self.METADATA["declared_tp_bps"],
                                horizon_seconds=self.METADATA["declared_hold_seconds"],
                                metadata={
                                    "reason": "three_drive_up_exhaustion",
                                    "current_price": current_price,
                                    "rsi": rsi,
                                    "fast_ema": fast_ema,
                                    "slow_ema": slow_ema,
                                    "funding_rate": funding_rate,
                                    "fear_greed": fear_greed,
                                    "market_regime": market_regime,
                                }
                            )

        # Exit logic (if position open, but no new signal generated)
        # We hold until SL/TP, but can add early exit conditions if needed
        # For this strategy, we rely on the engine's SL/TP and horizon.
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Not strictly necessary for this strategy, but good practice
        # For example, one could adapt self.cooldown_bars based on return_bps
        pass