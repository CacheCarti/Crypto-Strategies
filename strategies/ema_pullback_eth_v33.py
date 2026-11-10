from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthEmaPullbackAdaptive(Strategy):
    METADATA = {
        "name": "EthEmaPullbackAdaptive",
        "domain": "eth_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 540.0,
        "declared_hold_seconds": 21600,  # ~6 hours average swing hold
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 20
        self.slow_period = 50
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.in_long_pullback = False
        self.in_short_pullback = False

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _atr(self, highs, lows, closes, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.in_long_pullback = False
        self.in_short_pullback = False

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.slow_period + 15)
        highs = ctx.highs(self.slow_period + 15)
        lows = ctx.lows(self.slow_period + 15)

        if len(closes) < self.slow_period + 5:
            return None

        ema20 = self._ema(closes, self.fast_period)
        ema50 = self._ema(closes, self.slow_period)
        if ema20 is None or ema50 is None:
            return None

        curr_close = ctx.bar.close
        curr_low = ctx.bar.low
        curr_high = ctx.bar.high

        # Manage open position exits
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and curr_close < ema20:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "exit_long_ema20_breakdown",
                    "close": curr_close,
                    "ema20": ema20,
                    "ema50": ema50
                })
            elif direction == "short" and curr_close > ema20:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "exit_short_ema20_breakout",
                    "close": curr_close,
                    "ema20": ema20,
                    "ema50": ema50
                })
            return None

        # Mandatory cooldown check
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Avoid entries in extreme crisis or meltdown
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            self.in_long_pullback = False
            self.in_short_pullback = False
            return None

        # Dynamic volatility estimate for confidence scaling
        atr = self._atr(highs, lows, closes, 14)
        vol_pct = (atr / curr_close) if (atr and curr_close > 0) else 0.015

        # Uptrend state: EMA20 above EMA50 and price structure intact
        is_uptrend = ema20 > ema50 and curr_close > ema50
        # Downtrend state: EMA20 below EMA50 and price structure intact
        is_downtrend = ema20 < ema50 and curr_close < ema50

        # State tracking: track pullback touch/dip
        if is_uptrend:
            self.in_short_pullback = False
            if curr_low <= ema20:
                self.in_long_pullback = True
            
            # Entry trigger: was in pullback and now closes firmly back above EMA20
            if self.in_long_pullback and curr_close > ema20:
                self.in_long_pullback = False
                self.last_exit_bar = ctx.bar_index
                conf = 0.65 if vol_pct > 0.01 else 0.75
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "ema_pullback_bullish_reclaim",
                        "close": curr_close,
                        "ema20": ema20,
                        "ema50": ema50,
                        "vol_pct": vol_pct
                    }
                )
        elif is_downtrend:
            self.in_long_pullback = False
            if curr_high >= ema20:
                self.in_short_pullback = True

            # Entry trigger: was in pullback and now closes firmly back below EMA20
            if self.in_short_pullback and curr_close < ema20:
                self.in_short_pullback = False
                self.last_exit_bar = ctx.bar_index
                conf = 0.65 if vol_pct > 0.01 else 0.75
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "ema_pullback_bearish_rejection",
                        "close": curr_close,
                        "ema20": ema20,
                        "ema50": ema50,
                        "vol_pct": vol_pct
                    }
                )
        else:
            # Trend invalidation: reset pending pullback memory
            self.in_long_pullback = False
            self.in_short_pullback = False

        return None