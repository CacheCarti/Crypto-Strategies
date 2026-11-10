from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthEmaPullbackRegime(Strategy):
    METADATA = {
        "name": "EthEmaPullbackRegime",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 20
        self.slow_period = 50
        self.cooldown_bars = 6
        self.last_trade_bar = -999

    def _ema(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.slow_period + 5)
        lows = ctx.lows(self.slow_period + 5)
        highs = ctx.highs(self.slow_period + 5)

        if len(closes) < self.slow_period + 2:
            return None

        # Regime filter to address prior OOS failures: avoid entering in crisis or volatile chop
        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if market_regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.4:
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "regime_risk_exit", "regime": market_regime, "crisis_score": crisis_score})
            return None

        ema20_curr = self._ema(closes, self.fast_period)
        ema50_curr = self._ema(closes, self.slow_period)
        ema20_prev = self._ema(closes[:-1], self.fast_period)
        ema50_prev = self._ema(closes[:-1], self.slow_period)

        if ema20_curr is None or ema50_curr is None or ema20_prev is None or ema50_prev is None:
            return None

        curr_close = closes[-1]
        prev_close = closes[-2]
        curr_low = lows[-1]
        prev_low = lows[-2]
        curr_high = highs[-1]
        prev_high = highs[-2]

        # Manage existing position exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and curr_close < ema20_curr:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "exit_long_below_ema20", "close": curr_close, "ema20": ema20_curr}
                )
            elif pos_dir == "short" and curr_close > ema20_curr:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "exit_short_above_ema20", "close": curr_close, "ema20": ema20_curr}
                )
            return None

        # Mandatory cooldown check
        if (ctx.bar_index - self.last_trade_bar) < self.cooldown_bars:
            return None

        # Long pullback: Trend is up (EMA20 > EMA50, price > EMA50).
        # Previous bar touched/dipped below EMA20 (or closed below), current bar closes firmly back above EMA20.
        bull_trend = ema20_curr > ema50_curr and curr_close > ema50_curr
        long_pullback = (prev_low <= ema20_prev or prev_close <= ema20_prev) and curr_close > ema20_curr and prev_close < curr_close

        if bull_trend and long_pullback:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "ema_pullback_bullish_reclaim",
                    "close": curr_close,
                    "ema20": ema20_curr,
                    "ema50": ema50_curr,
                    "regime": market_regime
                }
            )

        # Short pullback: Trend is down (EMA20 < EMA50, price < EMA50).
        # Previous bar touched/spiked above EMA20 (or closed above), current bar closes back below EMA20.
        bear_trend = ema20_curr < ema50_curr and curr_close < ema50_curr
        short_pullback = (prev_high >= ema20_prev or prev_close >= ema20_prev) and curr_close < ema20_curr and prev_close > curr_close

        if bear_trend and short_pullback:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "ema_pullback_bearish_reclaim",
                    "close": curr_close,
                    "ema20": ema20_curr,
                    "ema50": ema50_curr,
                    "regime": market_regime
                }
            )

        return None