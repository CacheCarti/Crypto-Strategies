from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SessionFadeStrategy(Strategy):
    METADATA = {
        "name": "BTC Late US Session Fade",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 3,  # Up to 3 hours
        "warmup_bars": 50,
        "required_features": ["fear_greed_index", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_open_price = None
        self.session_high = -math.inf
        self.session_low = math.inf
        self.entry_bar_index = -1
        self.trade_cooldown = 10  # bars to wait after a trade
        self.min_session_stretch_pct = 0.5  # Min % move from session open to consider fading
        self.max_session_stretch_pct = 2.0 # Max % move to consider fading (avoid extreme trends)
        self.entry_hour_start = 18 # UTC 18:00 (late US session)
        self.entry_hour_end = 23 # UTC 23:00 (end of late US session)
        self.exit_hour = 0 # UTC 00:00 (exit before new day)

        # Indicator periods
        self.ema_period_fast = 20
        self.ema_period_slow = 50
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

    def _atr(self, highs, lows, closes, period):
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
        current_close = ctx.bar.close

        # Reset session data at the start of a new day (UTC 0)
        if current_hour == self.exit_hour and self.session_open_price is not None:
            self.session_open_price = None
            self.session_high = -math.inf
            self.session_low = math.inf
            # If position is open, close it before the new session begins
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "session_close_time", "current_hour": current_hour})

        # Track session high/low and open price
        if self.session_open_price is None and current_hour >= self.entry_hour_start:
            self.session_open_price = ctx.bar.open
            self.session_high = ctx.bar.high
            self.session_low = ctx.bar.low
        elif self.session_open_price is not None:
            self.session_high = max(self.session_high, ctx.bar.high)
            self.session_low = min(self.session_low, ctx.bar.low)

        # Cooldown logic
        if ctx.bar_index < self.entry_bar_index + self.trade_cooldown:
            return None

        # Calculate indicators
        ema_fast = self._ema(closes, self.ema_period_fast)
        ema_slow = self._ema(closes, self.ema_period_slow)
        rsi = self._rsi(closes, self.rsi_period)
        atr = self._atr(highs, lows, closes, self.atr_period)

        if ema_fast is None or ema_slow is None or rsi is None or atr is None:
            return None

        # Regime filters
        fear_greed_index = ctx.features.get("fear_greed_index", 50)
        btc_return_pct = ctx.features.get("btc_return_pct", 0.0)
        
        # Avoid trading in crisis or strong trend regimes
        if ctx.market.get("regime", "NORMAL") in ["CRISIS", "MELTDOWN"] or \
           abs(btc_return_pct) > 3.0: # Avoid fading extremely strong 24h moves
            if ctx.has_position(): # Exit if regime turns bad while in position
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "regime_filter_exit", "market_regime": ctx.market.get("regime"), "btc_return_pct": btc_return_pct})
            return None

        # If a position is open, check for stop/profit or time-based exit
        if ctx.has_position():
            if current_hour == self.exit_hour:
                return ctx.signal("flat", confidence=0.9, metadata={"reason": "session_end_exit", "current_hour": current_hour})
            return None # Hold position

        # Entry logic: Only in late US session and if no position is open
        if self.entry_hour_start <= current_hour <= self.entry_hour_end and self.session_open_price is not None:
            
            session_range = self.session_high - self.session_low
            mid_session_price = self.session_open_price # Simple midpoint is session open for now

            current_move_from_open_pct = ((current_close - self.session_open_price) / self.session_open_price) * 100.0

            # Check for stretched move and stall
            if abs(current_move_from_open_pct) >= self.min_session_stretch_pct and abs(current_move_from_open_pct) <= self.max_session_stretch_pct:
                
                # Check for stall/reversal conditions
                is_stalled_high = current_close >= self.session_high - 0.5 * atr and rsi >= 65 and current_close > ema_fast and current_close > ema_slow
                is_stalled_low = current_close <= self.session_low + 0.5 * atr and rsi <= 35 and current_close < ema_fast and current_close < ema_slow

                # Long signal: Stretched down, fading towards midpoint
                if current_move_from_open_pct < -self.min_session_stretch_pct and current_close < mid_session_price:
                    if rsi < 40 and current_close < ema_fast and ema_fast > ema_slow: # Price is low, potentially bouncing
                        self.entry_bar_index = ctx.bar_index
                        return ctx.signal(
                            "long",
                            confidence=0.7,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "session_fade_long_oversold",
                                "session_open": self.session_open_price,
                                "current_close": current_close,
                                "move_from_open_pct": current_move_from_open_pct,
                                "rsi": rsi,
                                "ema_fast": ema_fast,
                                "ema_slow": ema_slow,
                                "mid_session_price": mid_session_price,
                                "fear_greed_index": fear_greed_index,
                            }
                        )

                # Short signal: Stretched up, fading towards midpoint
                elif current_move_from_open_pct > self.min_session_stretch_pct and current_close > mid_session_price:
                    if rsi > 60 and current_close > ema_fast and ema_fast < ema_slow: # Price is high, potentially pulling back
                        self.entry_bar_index = ctx.bar_index
                        return ctx.signal(
                            "short",
                            confidence=0.7,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "session_fade_short_overbought",
                                "session_open": self.session_open_price,
                                "current_close": current_close,
                                "move_from_open_pct": current_move_from_open_pct,
                                "rsi": rsi,
                                "ema_fast": ema_fast,
                                "ema_slow": ema_slow,
                                "mid_session_price": mid_session_price,
                                "fear_greed_index": fear_greed_index,
                            }
                        )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown on position close
        self.entry_bar_index = ctx.bar_index
        # Log for adaptive sizing or state reset if needed
        # print(f"Position closed: {position['direction']} {position['return_bps']:.2f} bps, held for {position['bars_held']} bars.")
        pass