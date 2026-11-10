from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityWickRejectionBTC(Strategy):
    METADATA = {
        "name": "Volatility Wick Rejection BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # 1 hour bar, aiming for short swing
        "warmup_bars": 30,
        "required_features": [], # No external features used for this strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 5 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        highs = ctx.highs(30)
        lows = ctx.lows(30)
        opens = ctx.opens(30)
        volumes = ctx.volumes(30)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        current_close = ctx.bar.close
        current_open = ctx.bar.open
        current_high = ctx.bar.high
        current_low = ctx.bar.low
        current_volume = ctx.bar.volume

        prev_close = closes[-2]
        prev_open = opens[-2]
        prev_high = highs[-2]
        prev_low = lows[-2]
        prev_volume = volumes[-2]

        atr = self._atr(highs, lows, closes, period=14)
        if atr is None:
            return None

        # Calculate average volume for comparison
        avg_volume_period = 20
        if len(volumes) < avg_volume_period:
            return None
        avg_volume = sum(volumes[-avg_volume_period:]) / avg_volume_period

        # Condition for a significant rejection wick (previous bar)
        # Long rejection wick (bearish signal for next bar)
        long_rejection_wick_prev = False
        if prev_close > prev_open: # Bullish candle
            upper_wick = prev_high - prev_close
            body_size = prev_close - prev_open
            # Upper wick is significantly larger than body
            if body_size > 0 and upper_wick / body_size > 1.5 and upper_wick > 0.5 * atr:
                long_rejection_wick_prev = True
        elif prev_open > prev_close: # Bearish candle
            upper_wick = prev_high - prev_open
            body_size = prev_open - prev_close
            # Upper wick is significantly larger than body
            if body_size > 0 and upper_wick / body_size > 1.5 and upper_wick > 0.5 * atr:
                long_rejection_wick_prev = True
        else: # Doji
            upper_wick = prev_high - prev_close
            if upper_wick > 0.5 * atr:
                long_rejection_wick_prev = True


        # Short rejection wick (bullish signal for next bar)
        short_rejection_wick_prev = False
        if prev_close > prev_open: # Bullish candle
            lower_wick = prev_open - prev_low
            body_size = prev_close - prev_open
            # Lower wick is significantly larger than body
            if body_size > 0 and lower_wick / body_size > 1.5 and lower_wick > 0.5 * atr:
                short_rejection_wick_prev = True
        elif prev_open > prev_close: # Bearish candle
            lower_wick = prev_close - prev_low
            body_size = prev_open - prev_close
            # Lower wick is significantly larger than body
            if body_size > 0 and lower_wick / body_size > 1.5 and lower_wick > 0.5 * atr:
                short_rejection_wick_prev = True
        else: # Doji
            lower_wick = prev_open - prev_low
            if lower_wick > 0.5 * atr:
                short_rejection_wick_prev = True

        # Volume confirmation for the previous bar's wick
        volume_confirm_prev = prev_volume > 1.5 * avg_volume

        # Current bar confirmation for the rejection
        # Short entry: previous bar had long rejection wick, current bar confirms bearish movement
        if long_rejection_wick_prev and volume_confirm_prev and current_close < prev_close and ctx.position_direction() != "short":
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.7,
                metadata={
                    "reason": "bearish_wick_rejection_confirmed",
                    "prev_high": prev_high,
                    "prev_low": prev_low,
                    "prev_close": prev_close,
                    "prev_volume": prev_volume,
                    "current_close": current_close,
                    "atr": atr,
                    "avg_volume": avg_volume
                }
            )

        # Long entry: previous bar had short rejection wick, current bar confirms bullish movement
        if short_rejection_wick_prev and volume_confirm_prev and current_close > prev_close and ctx.position_direction() != "long":
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.7,
                metadata={
                    "reason": "bullish_wick_rejection_confirmed",
                    "prev_high": prev_high,
                    "prev_low": prev_low,
                    "prev_close": prev_close,
                    "prev_volume": prev_volume,
                    "current_close": current_close,
                    "atr": atr,
                    "avg_volume": avg_volume
                }
            )

        # Exit logic (if any specific exit condition besides SL/TP is desired)
        # This strategy relies on declared SL/TP for exits, but a simple exit could be added here.
        # For example, if a strong reversal candle forms against the position.
        # For simplicity, we'll rely on the declared SL/TP as per problem statement.

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optionally log or adapt based on position close
        pass