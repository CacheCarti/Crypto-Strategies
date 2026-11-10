from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthSessionGapReversion(Strategy):
    METADATA = {
        "name": "ETH Session Gap Reversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # 1 hour bar, aim for 1-4 bars hold
        "warmup_bars": 50, # Need prior day's close and 20-bar SMA
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_bar_day = -1
        self.last_us_session_close = None
        self.last_trade_bar_index = -self.METADATA["warmup_bars"] # Allow trading immediately after warmup

        # Indicator periods
        self.sma_period = 20
        self.rsi_period = 14
        self.atr_period = 14
        self.cooldown_bars = 4 # Cooldown after a trade

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
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day

        # Update last_us_session_close
        # US session ends around 00:00 UTC (prev day's close)
        # So, the close of the 23:00 UTC bar is the US session close
        if current_hour == 23 and current_day != self.last_bar_day:
            self.last_us_session_close = ctx.bar.close
            self.last_bar_day = current_day

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar_index < self.cooldown_bars:
            return None

        # Trading window: Late US session (18-23 UTC for entry)
        # Entry only if we have a recorded last_us_session_close from the previous day
        if self.last_us_session_close is None or not (18 <= current_hour <= 23):
            return None

        current_price = ctx.bar.close
        sma = self._sma(closes, self.sma_period)
        rsi = self._rsi(closes, self.rsi_period)
        atr_val = self._atr(highs, lows, closes, self.atr_period)

        if sma is None or rsi is None or atr_val is None:
            return None

        # Calculate gap from previous US session close
        price_gap = current_price - self.last_us_session_close
        gap_percent = (price_gap / self.last_us_session_close) * 100
        
        # Define a significant gap threshold (e.g., 0.2% of ATR)
        # Using ATR to make the threshold adaptive to volatility
        min_gap_for_trade_abs = 0.5 * atr_val # Example: 0.5 ATR
        
        # Exit logic
        if ctx.has_position():
            # Exit before 00:00 UTC (start of next day) if position is still open
            if current_hour == 23:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "end_of_session_exit",
                    "current_hour": current_hour,
                    "price": current_price
                })
            # Or if price crosses back past the SMA, indicating trend continuation
            if ctx.position_direction() == "long" and current_price < sma:
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "long_position_sma_cross_exit",
                    "current_price": current_price,
                    "sma": sma
                })
            if ctx.position_direction() == "short" and current_price > sma:
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "short_position_sma_cross_exit",
                    "current_price": current_price,
                    "sma": sma
                })
            return None # Hold position

        # Entry logic: Look for reversal of a significant gap
        # Condition 1: Significant gap in price vs last US session close
        # Condition 2: Price has moved away from the last_us_session_close but shows signs of reversal (e.g., RSI extreme)
        # Condition 3: Current price is on the "wrong" side of the SMA, suggesting a deviation that might revert

        signal_direction = None
        confidence = 0.0
        reason = ""

        # Long entry scenario: Price gapped down, then moved below SMA, and is oversold, suggesting reversion up
        if price_gap < -min_gap_for_trade_abs and current_price < sma and rsi < 35:
            # We want to buy if the price has gapped down significantly, moved lower than the SMA,
            # and is oversold, expecting it to revert towards the last_us_session_close and/or SMA.
            signal_direction = "long"
            confidence = 0.7 + ctx.rng.uniform(0, 0.1) # Add a touch of randomness for exploration
            reason = "eth_gap_down_reversion_oversold"

        # Short entry scenario: Price gapped up, then moved above SMA, and is overbought, suggesting reversion down
        elif price_gap > min_gap_for_trade_abs and current_price > sma and rsi > 65:
            # We want to sell if the price has gapped up significantly, moved higher than the SMA,
            # and is overbought, expecting it to revert towards the last_us_session_close and/or SMA.
            signal_direction = "short"
            confidence = 0.7 + ctx.rng.uniform(0, 0.1)
            reason = "eth_gap_up_reversion_overbought"

        if signal_direction:
            self.last_trade_bar_index = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "current_price": current_price,
                    "last_us_session_close": self.last_us_session_close,
                    "price_gap": price_gap,
                    "gap_percent": gap_percent,
                    "sma": sma,
                    "rsi": rsi,
                    "current_hour": current_hour,
                    "atr": atr_val
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last trade bar index to enforce cooldown after any position close
        # (even if closed by SL/TP/engine)
        self.last_trade_bar_index = ctx.bar_index
        pass