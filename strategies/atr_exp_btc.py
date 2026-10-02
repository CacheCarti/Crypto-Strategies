from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class VolatilityExpansionMomentum(Strategy):
    METADATA = {
        "name": "Volatility Expansion Momentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 260.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 70,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.atr_period = 14
        self.atr_baseline_period = 45
        self.momentum_lookback = 12
        self.expansion_threshold = 1.35
        self.move_atr_multiple = 1.40
        self.contraction_threshold = 1.02
        self.cooldown_bars = 4
        self.last_exit_bar = -999

    def _calculate_atr_series(self, highs: list, lows: list, closes: list, count: int) -> list:
        req_len = count + self.atr_period + 1
        if len(closes) < req_len:
            return []
        
        trs = []
        for i in range(1, len(closes)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1])
            )
            trs.append(tr)
        
        atrs = []
        for i in range(count):
            end_idx = len(trs) - count + 1 + i
            start_idx = end_idx - self.atr_period
            atrs.append(sum(trs[start_idx:end_idx]) / self.atr_period)
        return atrs

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        total_needed = self.atr_baseline_period + self.atr_period + self.momentum_lookback + 5
        closes = ctx.closes(total_needed)
        highs = ctx.highs(total_needed)
        lows = ctx.lows(total_needed)

        if len(closes) < total_needed:
            return None

        atr_series = self._calculate_atr_series(highs, lows, closes, self.atr_baseline_period)
        if len(atr_series) < self.atr_baseline_period:
            return None

        current_atr = atr_series[-1]
        baseline_atr = sum(atr_series) / len(atr_series)
        
        if baseline_atr <= 0:
            return None

        vol_ratio = current_atr / baseline_atr
        net_move = closes[-1] - closes[-self.momentum_lookback]
        move_ratio = abs(net_move) / current_atr if current_atr > 0 else 0.0

        # Position Management & Dynamic Exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            
            # Exit 1: Volatility contraction back to baseline
            if vol_ratio <= self.contraction_threshold:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "atr_contraction_exit",
                        "vol_ratio": round(vol_ratio, 3),
                        "current_atr": round(current_atr, 2),
                        "baseline_atr": round(baseline_atr, 2),
                        "price": closes[-1]
                    }
                )

            # Exit 2: Momentum reversal while in position
            if pos_dir == "long" and net_move < -0.5 * current_atr:
                return ctx.signal(
                    "flat",
                    confidence=0.65,
                    metadata={
                        "reason": "momentum_reversal_long_exit",
                        "net_move": round(net_move, 2),
                        "vol_ratio": round(vol_ratio, 3),
                        "price": closes[-1]
                    }
                )
            elif pos_dir == "short" and net_move > 0.5 * current_atr:
                return ctx.signal(
                    "flat",
                    confidence=0.65,
                    metadata={
                        "reason": "momentum_reversal_short_exit",
                        "net_move": round(net_move, 2),
                        "vol_ratio": round(vol_ratio, 3),
                        "price": closes[-1]
                    }
                )

            return None

        # Cooldown guard for new entries
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Entry Logic: Volatility expansion confirmed by clear multi-bar displacement
        if vol_ratio >= self.expansion_threshold and move_ratio >= self.move_atr_multiple:
            # Scale confidence by expansion magnitude
            confidence = min(0.60 + (vol_ratio - self.expansion_threshold) * 0.4 + (move_ratio - self.move_atr_multiple) * 0.05, 0.90)

            if net_move > 0:
                return ctx.signal(
                    "long",
                    confidence=round(confidence, 2),
                    metadata={
                        "reason": "volatility_expansion_bullish_momentum",
                        "vol_ratio": round(vol_ratio, 3),
                        "move_ratio": round(move_ratio, 3),
                        "net_move": round(net_move, 2),
                        "current_atr": round(current_atr, 2),
                        "price": closes[-1]
                    }
                )
            elif net_move < 0:
                return ctx.signal(
                    "short",
                    confidence=round(confidence, 2),
                    metadata={
                        "reason": "volatility_expansion_bearish_momentum",
                        "vol_ratio": round(vol_ratio, 3),
                        "move_ratio": round(move_ratio, 3),
                        "net_move": round(net_move, 2),
                        "current_atr": round(current_atr, 2),
                        "price": closes[-1]
                    }
                )

        return None