from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class GreedExhaustionShort(Strategy):
    METADATA = {
        "name": "Greed Exhaustion Short ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 4, # Roughly 4 hours intended hold
        "warmup_bars": 50, # Need enough for EMA and momentum calculations
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_period_short = 8
        self.ema_period_long = 21
        self.momentum_period = 5
        self.rsi_period = 14
        self.cooldown_bars = 12 # Wait 12 bars (12 hours) after a trade
        self.last_trade_bar = -self.cooldown_bars

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period # Simple average for initial EMA
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _rsi(self, closes, period=14):
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
            return 100.0 # Infinite RS means all gains
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_price = ctx.bar.close

        # Calculate indicators
        ema_short = self._ema(closes, self.ema_period_short)
        ema_long = self._ema(closes, self.ema_period_long)
        current_rsi = self._rsi(closes, self.rsi_period)

        if ema_short is None or ema_long is None or current_rsi is None:
            return None

        # Momentum calculation: price change over momentum_period
        momentum_prices = ctx.closes(self.momentum_period + 1)
        if len(momentum_prices) < self.momentum_period + 1:
            return None
        price_change_momentum = (momentum_prices[-1] - momentum_prices[0]) / momentum_prices[0] * 100

        fear_greed_index = ctx.features.get("fear_greed_index", 50)

        # Entry logic: Short when extreme greed and momentum stalls/reverses
        if not ctx.has_position():
            # Condition 1: Extreme Greed
            greed_threshold = 80

            # Condition 2: Price momentum stalling or reversing
            # Look for price below short EMA and short EMA below long EMA, indicating a potential downtrend start or stall
            # Also check if RSI is overbought (indicating exhaustion) but not extremely high (already reversed)
            # or if recent price change is flat/negative
            
            # Additional check: Price has recently been above the short EMA, now crossing below
            prev_closes = ctx.closes(2)
            if len(prev_closes) < 2:
                return None
            
            prev_ema_short = self._ema(ctx.closes(self.ema_period_short + 1), self.ema_period_short)
            
            if (
                fear_greed_index >= greed_threshold and
                current_rsi >= 65 and # Overbought but not necessarily extreme
                prev_closes[0] > prev_ema_short and # Price was above EMA
                current_price < ema_short and # Price now below EMA (cross)
                ema_short < ema_long # Short EMA starting to cross below long EMA or already below
            ):
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "greed_exhaustion_short_ema_cross",
                        "fear_greed_index": fear_greed_index,
                        "current_rsi": current_rsi,
                        "ema_short": ema_short,
                        "ema_long": ema_long,
                        "current_price": current_price,
                        "price_change_momentum_pct": price_change_momentum
                    }
                )

        # Exit logic: Exit if greed normalizes or momentum resumes against us
        elif ctx.position_direction() == "short":
            # Exit Condition 1: Fear & Greed normalizes (drops below a certain threshold)
            greed_normalize_threshold = 70

            # Exit Condition 2: Price recovers above short EMA or RSI drops too low
            if (
                fear_greed_index < greed_normalize_threshold or
                current_price > ema_short or # Price moves back above short EMA
                current_rsi < 40 # RSI drops significantly, indicating potential reversal of short momentum
            ):
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "greed_exhaustion_exit_normalization_or_recovery",
                        "fear_greed_index": fear_greed_index,
                        "current_rsi": current_rsi,
                        "ema_short": ema_short,
                        "current_price": current_price
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close details or adjust future behavior
        pass