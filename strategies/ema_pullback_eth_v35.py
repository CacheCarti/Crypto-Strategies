from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthPullbackTrendAdaptive(Strategy):
    METADATA = {
        "name": "EthPullbackTrendAdaptive",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 20
        self.slow_period = 50
        self.atr_period = 14
        self.cooldown_bars = 6
        self.last_trade_bar = -999

    def _ema(self, values, period: int):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _atr(self, highs, lows, closes, period: int = 14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.slow_period + 5)
        highs = ctx.highs(self.slow_period + 5)
        lows = ctx.lows(self.slow_period + 5)

        if len(closes) < self.slow_period + 2:
            return None

        # Precompute key EMAs for current bar and previous bar
        ema20_curr = self._ema(closes, self.fast_period)
        ema50_curr = self._ema(closes, self.slow_period)
        ema20_prev = self._ema(closes[:-1], self.fast_period)
        ema50_prev = self._ema(closes[:-1], self.slow_period)

        if ema20_curr is None or ema50_curr is None or ema20_prev is None or ema50_prev is None:
            return None

        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or closes[-1] == 0:
            return None

        atr_bps = (atr / closes[-1]) * 10000.0
        current_close = closes[-1]
        prev_close = closes[-2]
        current_low = lows[-1]
        prev_low = lows[-2]
        current_high = highs[-1]
        prev_high = highs[-2]

        # Manage existing position exits
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long":
                # Exit if close crosses back decisively below EMA20
                if current_close < ema20_curr and prev_close >= ema20_prev:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_exit_cross_below_ema20",
                            "close": current_close,
                            "ema20": ema20_curr,
                        }
                    )
            elif direction == "short":
                # Exit if close crosses back decisively above EMA20
                if current_close > ema20_curr and prev_close <= ema20_prev:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_exit_cross_above_ema20",
                            "close": current_close,
                            "ema20": ema20_curr,
                        }
                    )
            return None

        # Post-exit cooldown gate
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Regime & Crisis Filter (avoid choppy/crisis environments)
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Dynamic SL & TP sizing based on ATR volatility
        dynamic_sl = max(150.0, min(350.0, atr_bps * 1.5))
        dynamic_tp = max(280.0, min(650.0, atr_bps * 2.8))

        # Long Setup:
        # 1. Structural uptrend: EMA20 > EMA50 and current close > EMA50
        # 2. Pullback trigger: prior bar dipped into or below EMA20 (or current low penetrated EMA20),
        #    but current close reclaimed EMA20 cleanly (bullish confirmation).
        bull_trend = (ema20_curr > ema50_curr) and (current_close > ema50_curr)
        bull_pullback = (prev_low <= ema20_prev or current_low <= ema20_curr)
        bull_reclaim = (current_close > ema20_curr) and (prev_close <= ema20_prev or current_close > prev_close)

        if bull_trend and bull_pullback and bull_reclaim:
            trend_margin = (current_close - ema50_curr) / ema50_curr
            confidence = min(0.9, max(0.55, 0.55 + trend_margin * 5.0))
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=dynamic_sl,
                take_profit_bps=dynamic_tp,
                metadata={
                    "reason": "ema_pullback_uptrend_rebound",
                    "close": current_close,
                    "ema20": ema20_curr,
                    "ema50": ema50_curr,
                    "atr_bps": atr_bps,
                }
            )

        # Short Setup:
        # 1. Structural downtrend: EMA20 < EMA50 and current close < EMA50
        # 2. Pullback trigger: prior bar touched or rose above EMA20 (or current high penetrated EMA20),
        #    but current close fell cleanly back below EMA20 (bearish confirmation).
        bear_trend = (ema20_curr < ema50_curr) and (current_close < ema50_curr)
        bear_pullback = (prev_high >= ema20_prev or current_high >= ema20_curr)
        bear_reclaim = (current_close < ema20_curr) and (prev_close >= ema20_prev or current_close < prev_close)

        if bear_trend and bear_pullback and bear_reclaim:
            trend_margin = (ema50_curr - current_close) / ema50_curr
            confidence = min(0.9, max(0.55, 0.55 + trend_margin * 5.0))
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=dynamic_sl,
                take_profit_bps=dynamic_tp,
                metadata={
                    "reason": "ema_pullback_downtrend_rebound",
                    "close": current_close,
                    "ema20": ema20_curr,
                    "ema50": ema50_curr,
                    "atr_bps": atr_bps,
                }
            )

        return None