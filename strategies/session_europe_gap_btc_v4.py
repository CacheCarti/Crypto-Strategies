from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcEuMorningGapReversion(Strategy):
    METADATA = {
        "name": "BTC European Morning Gap Reversion",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # Intended hold time, but will exit by 12 UTC
        "warmup_bars": 50,
        "required_features": [], # No specific features required beyond OHLCV
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_close_utc7 = None
        self.traded_today = False
        self.last_trade_bar = -self.METADATA["warmup_bars"] # Initialize to allow trading after warmup

        # Parameters for regime filtering
        self.volatility_period = 20
        self.trend_period = 20
        self.min_atr = 20.0 # Minimum ATR in USD for a valid gap
        self.max_atr = 150.0 # Maximum ATR in USD for too volatile
        self.cooldown_bars = 5 # Cooldown period after a trade

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        if not trs:
            return None
        return sum(trs[-period:]) / period

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        opens = ctx.opens(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Reset daily trade flag at the start of a new "day" (e.g., after 12 UTC)
        if current_hour == 13 and self.traded_today:
            self.traded_today = False
            self.last_close_utc7 = None # Reset for next session

        # Capture the close price for the prior session (UTC 7 is the start of European morning)
        if current_hour == 7 and self.last_close_utc7 is None:
            # We need the close of the *previous* bar, which was 6 UTC
            if len(closes) >= 2:
                self.last_close_utc7 = closes[-2] # Close of the 6 UTC bar
            else:
                return None # Not enough data

        # Exit logic for existing positions
        if ctx.has_position():
            if current_hour >= 12: # Exit by 12 UTC
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "session_close_time", "current_hour": current_hour})
            return None # Hold position

        # Cooldown period check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Entry logic: Between 7 and 11 UTC (inclusive)
        if 7 <= current_hour <= 11 and not self.traded_today and self.last_close_utc7 is not None:
            current_open = ctx.bar.open
            gap_size_usd = current_open - self.last_close_utc7
            
            # Regime filter: Check ATR
            current_atr = self._atr(highs, lows, closes, self.volatility_period)
            if current_atr is None:
                return None

            # Filter out very low or very high volatility
            if current_atr < self.min_atr or current_atr > self.max_atr:
                return None # Market not in desired volatility range

            # Filter out strong trends (using SMA slope as a proxy)
            sma_short = self._sma(closes, 10)
            sma_long = self._sma(closes, 30)
            if sma_short is None or sma_long is None:
                return None
            
            # If short SMA is significantly above/below long SMA, it's trending
            trend_threshold = 0.005 # 0.5% difference
            if abs(sma_short - sma_long) / sma_long > trend_threshold:
                return None # Trending market, not good for gap reversion

            # Gap Reversion Logic
            gap_percentage = (gap_size_usd / self.last_close_utc7) * 100
            
            # Example: Look for a significant gap (e.g., > 0.3% of price)
            # and a lack of immediate continuation (current bar's close not extending the gap much)
            # and price is moving back towards the prior close
            
            # Long entry: price opened significantly lower and is moving up
            if gap_percentage < -0.3 and ctx.bar.close > current_open:
                # Check if the close is still below the prior session close, indicating a reversion opportunity
                if ctx.bar.close < self.last_close_utc7:
                    self.traded_today = True
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.75,
                        metadata={
                            "reason": "eu_morning_gap_reversion_long",
                            "gap_size_usd": gap_size_usd,
                            "gap_percentage": gap_percentage,
                            "prior_session_close": self.last_close_utc7,
                            "current_open": current_open,
                            "current_close": ctx.bar.close,
                            "current_atr": current_atr,
                            "sma_short": sma_short,
                            "sma_long": sma_long,
                        }
                    )

            # Short entry: price opened significantly higher and is moving down
            if gap_percentage > 0.3 and ctx.bar.close < current_open:
                # Check if the close is still above the prior session close, indicating a reversion opportunity
                if ctx.bar.close > self.last_close_utc7:
                    self.traded_today = True
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.75,
                        metadata={
                            "reason": "eu_morning_gap_reversion_short",
                            "gap_size_usd": gap_size_usd,
                            "gap_percentage": gap_percentage,
                            "prior_session_close": self.last_close_utc7,
                            "current_open": current_open,
                            "current_close": ctx.bar.close,
                            "current_atr": current_atr,
                            "sma_short": sma_short,
                            "sma_long": sma_long,
                        }
                    )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset specific state if needed, e.g., if a position closes before 12 UTC
        # For this strategy, we want to ensure only one trade per session, so traded_today
        # should only reset after the session window.
        pass