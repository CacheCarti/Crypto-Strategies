from typing import Optional, Dict, Any
from domains.strategy_contract import Strategy, BarContext, Signal


class StochasticReversionScalp(Strategy):
    METADATA = {
        "name": "Stochastic Reversion Scalp",
        "domain": "eth_usdc_scalp",
        "declared_sl_bps": 90.0,
        "declared_tp_bps": 160.0,
        "declared_hold_seconds": 1800,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 20
        self.oversold = 8.0
        self.overbought = 92.0
        self.cooldown_bars = 60
        self.last_trade_bar = -100

    def _stochastic_k(self, highs, lows, closes, period: int) -> Optional[float]:
        if len(closes) < period:
            return None
        h_slice = highs[-period:]
        l_slice = lows[-period:]
        highest = max(h_slice)
        lowest = min(l_slice)
        if highest == lowest:
            return 50.0
        return ((closes[-1] - lowest) / (highest - lowest)) * 100.0

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        req_bars = self.period + 3
        closes = ctx.closes(req_bars)
        highs = ctx.highs(req_bars)
        lows = ctx.lows(req_bars)

        if len(closes) < req_bars:
            return None

        stoch_curr = self._stochastic_k(highs, lows, closes, self.period)
        stoch_prev = self._stochastic_k(highs[:-1], lows[:-1], closes[:-1], self.period)

        if stoch_curr is None or stoch_prev is None:
            return None

        # Manage open position exits at midline reversion
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and stoch_curr >= 50.0:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "long_stoch_midline_exit",
                        "stoch": round(stoch_curr, 2),
                        "price": ctx.bar.close
                    }
                )
            elif pos_dir == "short" and stoch_curr <= 50.0:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "short_stoch_midline_exit",
                        "stoch": round(stoch_curr, 2),
                        "price": ctx.bar.close
                    }
                )
            return None

        # Mandatory multi-bar cooldown guard to prevent overtrading
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # High-conviction entry logic: extreme oversold hook up / overbought hook down
        if stoch_prev <= self.oversold and stoch_curr > stoch_prev:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.8,
                stop_loss_bps=90.0,
                take_profit_bps=160.0,
                horizon_seconds=1800,
                metadata={
                    "reason": "stoch_extreme_oversold_turnup",
                    "stoch_curr": round(stoch_curr, 2),
                    "stoch_prev": round(stoch_prev, 2),
                    "price": ctx.bar.close
                }
            )

        if stoch_prev >= self.overbought and stoch_curr < stoch_prev:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.8,
                stop_loss_bps=90.0,
                take_profit_bps=160.0,
                horizon_seconds=1800,
                metadata={
                    "reason": "stoch_extreme_overbought_turndown",
                    "stoch_curr": round(stoch_curr, 2),
                    "stoch_prev": round(stoch_prev, 2),
                    "price": ctx.bar.close
                }
            )

        return None