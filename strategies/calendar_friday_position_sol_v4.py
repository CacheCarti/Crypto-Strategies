from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolFridayEvent(Strategy):
    METADATA = {
        "name": "Solana Friday Event Strategy",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200,  # 2 hours, allowing for some price action post-event
        "warmup_bars": 50,
        "required_features": [], # No external features needed for this calendar-based strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_fast_period = 10
        self.ema_slow_period = 30
        self.rsi_period = 14
        self.trade_cooldown_bars = 24 # Cooldown for 24 hours (24 bars) after a trade
        self.last_trade_bar_index = -self.trade_cooldown_bars # Initialize to allow trading immediately

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

        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period

        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar_index < self.trade_cooldown_bars:
            return None

        # Only consider trading if no position is open
        if ctx.has_position():
            return None

        # Calculate indicators
        ema_fast = self._ema(closes, self.ema_fast_period)
        ema_slow = self._ema(closes, self.ema_slow_period)
        rsi = self._rsi(closes, self.rsi_period)

        if ema_fast is None or ema_slow is None or rsi is None:
            return None

        current_price = ctx.bar.close
        
        # Regime filter: only trade in 'normal' or 'volatile' regimes
        # And ensure the market is not in a strong crisis
        if ctx.market.get("regime", "NORMAL") == "CRISIS" or ctx.market.get("crisis_score", 0.0) > 0.5:
            return None

        # Check for Friday 22:00 UTC (end of day, before Sunday starts)
        # We are looking for the last few hours of Friday UTC
        is_friday_late = ctx.bar.timestamp.weekday() == 4 and ctx.bar.timestamp.hour >= 20 and ctx.bar.timestamp.hour <= 23

        if is_friday_late:
            # Look for decisive move in the last 6 hours (6 bars for 1h timeframe)
            # This means a significant directional move confirmed by EMA crossover and RSI
            
            # Check for recent price action. If the price has moved significantly in one direction,
            # and EMAs confirm, and RSI is not extreme, we can consider an entry.
            recent_closes = ctx.closes(6)
            if len(recent_closes) < 6:
                return None

            # Check if there's a strong trend in the last 6 bars
            # Long condition: price has moved up significantly AND EMA fast > EMA slow AND RSI is not overbought
            if (current_price > recent_closes[0] * 1.005 and # 0.5% up in 6 hours
                ema_fast > ema_slow and
                closes[-2] <= ema_slow and # EMA cross just happened or recently
                rsi < 70): # Not extremely overbought

                self.last_trade_bar_index = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    metadata={
                        "reason": "friday_late_long_positioning",
                        "current_price": current_price,
                        "ema_fast": ema_fast,
                        "ema_slow": ema_slow,
                        "rsi": rsi,
                        "recent_price_change_pct": (current_price / recent_closes[0] - 1) * 100,
                        "bar_timestamp": str(ctx.bar.timestamp)
                    }
                )

            # Short condition: price has moved down significantly AND EMA fast < EMA slow AND RSI is not oversold
            elif (current_price < recent_closes[0] * 0.995 and # 0.5% down in 6 hours
                  ema_fast < ema_slow and
                  closes[-2] >= ema_slow and # EMA cross just happened or recently
                  rsi > 30): # Not extremely oversold

                self.last_trade_bar_index = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    metadata={
                        "reason": "friday_late_short_positioning",
                        "current_price": current_price,
                        "ema_fast": ema_fast,
                        "ema_slow": ema_slow,
                        "rsi": rsi,
                        "recent_price_change_pct": (current_price / recent_closes[0] - 1) * 100,
                        "bar_timestamp": str(ctx.bar.timestamp)
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No specific adaptive logic needed for this strategy on position close,
        # but we use it to log the last trade bar index.
        # The cooldown is handled in on_bar.
        pass