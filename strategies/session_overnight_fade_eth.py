from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthOvernightFade(Strategy):
    METADATA = {
        "name": "ETH Overnight Fade",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 2, # ~2 hours hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index", "funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.overnight_start_hour = 0  # UTC 00:00
        self.overnight_end_hour = 6    # UTC 06:00
        self.session_mid_hour = 3      # UTC 03:00
        self.entry_cooldown_bars = 12  # Cooldown for 12 hours after a trade
        self.last_trade_bar_index = -self.entry_cooldown_bars * 2 # Initialize far in the past

        # Indicator periods
        self.ema_short_period = 10
        self.ema_long_period = 30
        self.rsi_period = 14
        self.atr_period = 20

        # Thresholds
        self.stretch_multiplier = 1.0 # How many ATRs move is considered stretched
        self.rsi_fade_threshold_low = 30
        self.rsi_fade_threshold_high = 70
        self.min_confidence = 0.6

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
            return 100.0 if avg_gain > 0 else 50.0 # Handle flat market
        
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
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar_index < self.entry_cooldown_bars:
            return None

        # Calculate indicators
        ema_short = self._ema(closes, self.ema_short_period)
        ema_long = self._ema(closes, self.ema_long_period)
        rsi = self._rsi(closes, self.rsi_period)
        atr = self._atr(highs, lows, closes, self.atr_period)

        if ema_short is None or ema_long is None or rsi is None or atr is None:
            return None

        # Regime filters
        fear_greed = ctx.features.get("fear_greed_index", 50)
        regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")
        funding_rate = ctx.features.get("funding_rate_ethusdt", 0.0)

        # Do not trade in crisis or strong confirmed trends
        if regime in ["CRISIS", "MELTDOWN"] or ctx.market.get("crisis_score", 0.0) > 0.5:
            return None
        if trend_regime in ["bull", "bear"] and ctx.market.get("trend_regime_confidence", 0.0) > 0.7:
            return None

        # Overnight Session Logic
        if self.overnight_start_hour <= current_hour < self.overnight_end_hour:
            # Only consider entry if price has moved significantly from the start of the session
            # and is showing signs of reversal (e.g., extreme RSI, stalling around EMA)

            # Find the opening price of the current overnight session (00:00 UTC)
            session_open_price = None
            for i in range(len(ctx.opens(self.overnight_end_hour + 1)) -1, -1, -1):
                bar_time = ctx.bar.timestamp.replace(hour=current_hour - i, minute=0, second=0, microsecond=0)
                if bar_time.hour == self.overnight_start_hour:
                    session_open_price = ctx.opens(i+1)[0] # This is a bit hacky, need to ensure correct bar
                    break

            # A more robust way to get session open price
            session_closes = ctx.closes(self.overnight_end_hour + 1)
            session_opens = ctx.opens(self.overnight_end_hour + 1)
            session_highs = ctx.highs(self.overnight_end_hour + 1)
            session_lows = ctx.lows(self.overnight_end_hour + 1)
            
            # Find the bar corresponding to the session start hour
            for i in range(len(session_opens)):
                # This assumes ctx.bar.timestamp is the current bar's timestamp
                # and ctx.opens() etc return ordered data.
                # A better way would be to store the session open bar on the first bar of the session.
                # For simplicity, we'll try to find the start of the session in the recent history.
                # This needs to be robust for the backtester.
                # Let's simplify: look for the first bar in the current overnight window.
                if current_hour == self.overnight_start_hour and ctx.bar.timestamp.minute == 0:
                    session_open_price = ctx.bar.open
                    break
                elif i > 0 and (ctx.bar.timestamp.hour - i) % 24 == self.overnight_start_hour and ctx.bar.timestamp.minute == 0:
                    session_open_price = session_opens[-i-1]
                    break
            
            if session_open_price is None: # If we haven't found the session open price for this session yet
                return None

            price_change_from_open = (ctx.bar.close - session_open_price) / session_open_price * 10000 # in bps
            
            # Condition: price is stretched from session open (e.g., > 1 ATR from open)
            is_stretched_long = price_change_from_open > self.stretch_multiplier * atr / ctx.bar.close * 10000
            is_stretched_short = price_change_from_open < -self.stretch_multiplier * atr / ctx.bar.close * 10000

            # Signal generation (fade the stretch towards the session midpoint)
            # Long entry: price is stretched down, RSI oversold, and price is below EMA short
            if is_stretched_short and rsi < self.rsi_fade_threshold_low and ctx.bar.close < ema_short:
                if not ctx.has_position():
                    self.last_trade_bar_index = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=self.min_confidence,
                        metadata={
                            "reason": "overnight_fade_long_stretch_rsi_oversold",
                            "current_hour": current_hour,
                            "session_open_price": session_open_price,
                            "price_change_bps": price_change_from_open,
                            "rsi": rsi,
                            "ema_short": ema_short,
                            "current_price": ctx.bar.close,
                            "funding_rate": funding_rate,
                            "fear_greed": fear_greed,
                        }
                    )
            
            # Short entry: price is stretched up, RSI overbought, and price is above EMA short
            elif is_stretched_long and rsi > self.rsi_fade_threshold_high and ctx.bar.close > ema_short:
                if not ctx.has_position():
                    self.last_trade_bar_index = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=self.min_confidence,
                        metadata={
                            "reason": "overnight_fade_short_stretch_rsi_overbought",
                            "current_hour": current_hour,
                            "session_open_price": session_open_price,
                            "price_change_bps": price_change_from_open,
                            "rsi": rsi,
                            "ema_short": ema_short,
                            "current_price": ctx.bar.close,
                            "funding_rate": funding_rate,
                            "fear_greed": fear_greed,
                        }
                    )

        # Exit logic: if we are approaching the end of the overnight session or midpoint
        # Or if we are past the midpoint and the trade hasn't worked out
        if ctx.has_position():
            # Exit if current hour is past session midpoint or close to end of session
            # The engine will handle declared_sl_bps and declared_tp_bps automatically.
            # We add an additional time-based exit.
            if current_hour >= self.session_mid_hour:
                return ctx.signal(
                    "flat",
                    confidence=0.7, # Higher confidence to ensure exit
                    metadata={
                        "reason": "session_midpoint_exit",
                        "current_hour": current_hour,
                        "position_direction": ctx.position_direction(),
                        "entry_price": ctx.position["entry_price"],
                        "current_price": ctx.bar.close,
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown on position close
        self.last_trade_bar_index = ctx.bar_index
        # You could add adaptive logic here, e.g., adjust confidence based on recent performance
        pass