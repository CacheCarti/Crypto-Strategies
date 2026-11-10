from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SessionEuropeFadeSOL(Strategy):
    METADATA = {
        "name": "Session Europe Fade SOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 3,  # Up to 3 hours for fade
        "warmup_bars": 50,  # Need enough bars for 20-period indicators
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.long_period = 20  # For price deviation
        self.rsi_period = 14
        self.cooldown_bars = 5  # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars
        self.traded_today = False # Track if a trade has been made in the session

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

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
            return 100.0 if avg_gain > 0 else 50.0
        
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.long_period + 1) # +1 for RSI
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_close = ctx.bar.close

        # Reset daily trade flag at the start of a new day (UTC 0-6)
        if 0 <= current_hour < 7 and self.traded_today:
            self.traded_today = False

        # Session boundaries: European morning (UTC 7-12)
        is_trading_session = 7 <= current_hour < 12

        # Cooldown period check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Regime filters
        if ctx.market.get("regime", "NORMAL") in ["CRISIS", "MELTDOWN"]:
            return None # Avoid trading in crisis regimes

        # Only consider entries if we don't have a position and are in the session
        if not ctx.has_position() and is_trading_session and not self.traded_today:
            # Calculate mean and std dev for the session's price action
            session_closes = ctx.closes(self.long_period)
            if len(session_closes) < self.long_period:
                return None

            session_mean = self._sma(session_closes, self.long_period)
            session_stddev = self._stddev(session_closes, self.long_period)

            if session_mean is None or session_stddev is None or session_stddev == 0:
                return None

            # Calculate Z-score of current price relative to session mean
            z_score = (current_close - session_mean) / session_stddev

            # Calculate RSI
            current_rsi = self._rsi(closes, self.rsi_period)
            if current_rsi is None:
                return None

            # Fading logic: If price is significantly stretched from the mean
            # And RSI indicates overbought/oversold, but not extreme (avoiding strong trends)
            # FADE LONG: Price is high (z_score > 1.5) but RSI is not extremely overbought (70-80 range)
            if z_score > 1.5 and 70 <= current_rsi < 80:
                # Check for strong trend (e.g., if price has been consistently above mean for too long)
                # Simple check: if current price is highest in last few bars
                if current_close == max(session_closes[-5:]): # If still making new highs, don't fade short
                     return None

                # Ensure Fear & Greed is not "Extreme Greed" (too bullish to fade)
                if ctx.features.get("fear_greed_class", "Neutral") != "Extreme Greed":
                    self.last_trade_bar = ctx.bar_index
                    self.traded_today = True
                    return ctx.signal(
                        "short",
                        confidence=0.65,
                        metadata={
                            "reason": "europe_session_fade_short",
                            "current_hour": current_hour,
                            "z_score": z_score,
                            "rsi": current_rsi,
                            "session_mean": session_mean,
                            "session_stddev": session_stddev,
                            "price": current_close,
                        },
                    )

            # FADE SHORT: Price is low (z_score < -1.5) but RSI is not extremely oversold (20-30 range)
            elif z_score < -1.5 and 20 < current_rsi <= 30:
                # Check for strong trend (e.g., if price has been consistently below mean for too long)
                # Simple check: if current price is lowest in last few bars
                if current_close == min(session_closes[-5:]): # If still making new lows, don't fade long
                    return None

                # Ensure Fear & Greed is not "Extreme Fear" (too bearish to fade)
                if ctx.features.get("fear_greed_class", "Neutral") != "Extreme Fear":
                    self.last_trade_bar = ctx.bar_index
                    self.traded_today = True
                    return ctx.signal(
                        "long",
                        confidence=0.65,
                        metadata={
                            "reason": "europe_session_fade_long",
                            "current_hour": current_hour,
                            "z_score": z_score,
                            "rsi": current_rsi,
                            "session_mean": session_mean,
                            "session_stddev": session_stddev,
                            "price": current_close,
                        },
                    )

        # Exit logic: if we have a position and it's approaching session end or a new day
        if ctx.has_position():
            # If current hour is 12 UTC or later, close the position
            if current_hour >= 12:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "session_end_exit",
                        "current_hour": current_hour,
                        "entry_price": ctx.position["entry_price"],
                        "exit_price": current_close,
                    },
                )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log or adjust confidence based on performance
        pass