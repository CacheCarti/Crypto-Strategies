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
        lows = ctx.lows(5)
        highs = ctx.highs(5)

        if len(closes) < self.slow_period + 2:
            return None

        # Check market regime to fix prior negative expectancy in choppy ranges
        trend_regime = ctx.market.get("trend_regime", "neutral")
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "exit_crisis_regime", "regime": market_regime}
                )
            return None

        ema20_curr = self._ema(closes, self.fast_period)
        ema50_curr = self._ema(closes, self.slow_period)
        ema20_prev = self._ema(closes[:-1], self.fast_period)

        if ema20_curr is None or ema50_curr is None or ema20_prev is None:
            return None

        curr_close = closes[-1]
        prev_close = closes[-2]
        prev_low = lows[-2]
        prev_high = highs[-2]

        pos_dir = ctx.position_direction()

        # Handle active positions: exit on clear violation of EMA20
        if ctx.has_position():
            if pos_dir == "long" and curr_close < ema20_curr:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "ema20_cross_below_exit",
                        "close": curr_close,
                        "ema20": ema20_curr
                    }
                )
            elif pos_dir == "short" and curr_close > ema20_curr:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "ema20_cross_above_exit",
                        "close": curr_close,
                        "ema20": ema20_curr
                    }
                )
            return None

        # Cooldown guard to avoid churn
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Long Setup: Trend must be up, regime supportive, pullback dipped to/below EMA20 and reclaimed it
        is_uptrend = ema20_curr > ema50_curr and curr_close > ema50_curr
        pulled_back_long = prev_low <= ema20_prev and curr_close > ema20_curr and prev_close <= ema20_prev * 1.002
        if is_uptrend and pulled_back_long and trend_regime in ("bull", "neutral"):
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=250.0,
                take_profit_bps=500.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "bull_ema_pullback_reclaim",
                    "close": curr_close,
                    "ema20": ema20_curr,
                    "ema50": ema50_curr,
                    "trend_regime": trend_regime
                }
            )

        # Short Setup: Trend must be down, regime supportive, bounce tapped/exceeded EMA20 and rejected back below
        is_downtrend = ema20_curr < ema50_curr and curr_close < ema50_curr
        pulled_back_short = prev_high >= ema20_prev and curr_close < ema20_curr and prev_close >= ema20_prev * 0.998
        if is_downtrend and pulled_back_short and trend_regime in ("bear", "neutral"):
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=250.0,
                take_profit_bps=500.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "bear_ema_pullback_reject",
                    "close": curr_close,
                    "ema20": ema20_curr,
                    "ema50": ema50_curr,
                    "trend_regime": trend_regime
                }
            )

        return None