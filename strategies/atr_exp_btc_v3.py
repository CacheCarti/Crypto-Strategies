from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class VolatilityExpansionMomentum(Strategy):
    METADATA = {
        "name": "Volatility Expansion Momentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 640.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 75,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.atr_period = 14
        self.atr_ma_period = 40
        self.expansion_mult = 1.60
        self.contraction_mult = 0.85
        self.mom_lookback = 12
        self.mom_atr_mult = 2.8
        self.cooldown_bars = 10
        self.last_exit_bar = -999

    def _calc_atr_history(self, highs: list, lows: list, closes: list, period: int, count: int) -> Optional[list]:
        if len(closes) < period + count:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        if len(trs) < period + count - 1:
            return None
        atrs = []
        for k in range(count):
            start = len(trs) - count + k - period + 1
            end = len(trs) - count + k + 1
            atrs.append(sum(trs[start:end]) / period)
        return atrs

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Filter extreme crisis regimes
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.85:
            return None

        atr_hist = self._calc_atr_history(highs, lows, closes, self.atr_period, self.atr_ma_period)
        if atr_hist is None or len(atr_hist) < self.atr_ma_period:
            return None

        curr_atr = atr_hist[-1]
        atr_ma = sum(atr_hist) / len(atr_hist)
        if atr_ma <= 0:
            return None

        expansion_ratio = curr_atr / atr_ma

        # Manage open position
        if ctx.has_position():
            # Exit when volatility contracts significantly back to normal
            if curr_atr < atr_ma * self.contraction_mult:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "atr_contraction_exit",
                        "curr_atr": round(curr_atr, 2),
                        "atr_ma": round(atr_ma, 2),
                        "expansion_ratio": round(expansion_ratio, 3),
                        "price": ctx.bar.close,
                    },
                )
            return None

        # Strict post-exit cooldown to avoid overtrading
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Require significant volatility expansion
        if expansion_ratio < self.expansion_mult:
            return None

        # Require strong directional thrust over the lookback window
        net_move = closes[-1] - closes[-1 - self.mom_lookback]
        move_threshold = self.mom_atr_mult * curr_atr

        # Bullish expansion breakout
        if net_move > move_threshold:
            conf = min(0.85, 0.60 + (expansion_ratio - self.expansion_mult) * 0.25)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "vol_expansion_bull_momentum",
                    "curr_atr": round(curr_atr, 2),
                    "atr_ma": round(atr_ma, 2),
                    "expansion_ratio": round(expansion_ratio, 3),
                    "net_move": round(net_move, 2),
                    "move_threshold": round(move_threshold, 2),
                    "price": ctx.bar.close,
                },
            )

        # Bearish expansion breakout
        if net_move < -move_threshold:
            conf = min(0.85, 0.60 + (expansion_ratio - self.expansion_mult) * 0.25)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "vol_expansion_bear_momentum",
                    "curr_atr": round(curr_atr, 2),
                    "atr_ma": round(atr_ma, 2),
                    "expansion_ratio": round(expansion_ratio, 3),
                    "net_move": round(net_move, 2),
                    "move_threshold": round(-move_threshold, 2),
                    "price": ctx.bar.close,
                },
            )

        return None