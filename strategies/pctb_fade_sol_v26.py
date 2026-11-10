from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPctBReversion(Strategy):
    METADATA = {
        "name": "SolPctBReversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 20
        self.num_std = 2.0
        self.atr_period = 14
        self.cooldown_bars = 4
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 12

    def _bollinger(self, closes: list, period: int = 20, num_std: float = 2.0):
        if len(closes) < period:
            return None, None, None
        slice_c = closes[-period:]
        mean = sum(slice_c) / period
        variance = sum((x - mean) ** 2 for x in slice_c) / period
        std = math.sqrt(variance)
        return mean, mean + num_std * std, mean - num_std * std

    def _atr(self, highs: list, lows: list, closes: list, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.period + 5)
        highs = ctx.highs(self.period + 5)
        lows = ctx.lows(self.period + 5)

        if len(closes) < self.period + 2:
            return None

        mid, upper, lower = self._bollinger(closes, self.period, self.num_std)
        if mid is None or upper is None or lower is None or upper == lower:
            return None

        # Prior bar bands
        prev_closes = closes[:-1]
        p_mid, p_upper, p_lower = self._bollinger(prev_closes, self.period, self.num_std)
        if p_mid is None or p_upper is None or p_lower is None or p_upper == p_lower:
            return None

        curr_close = closes[-1]
        prev_close = closes[-2]

        pct_b = (curr_close - lower) / (upper - lower)
        prev_pct_b = (prev_close - p_lower) / (p_upper - p_lower)

        # In-position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar

            # Timed volatility exit or mid-band touch exit
            if pos_dir == "long":
                if curr_close >= mid or bars_held >= self.max_hold_bars:
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={"reason": "long_mid_or_timed_exit", "pct_b": pct_b, "bars_held": bars_held}
                    )
            elif pos_dir == "short":
                if curr_close <= mid or bars_held >= self.max_hold_bars:
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={"reason": "short_mid_or_timed_exit", "pct_b": pct_b, "bars_held": bars_held}
                    )
            return None

        # Cooldown guard
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Regime protection: skip entries during extreme crisis
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.65:
            return None

        # Dynamic ATR-based structure stop/target calculation
        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or curr_close <= 0:
            return None

        # Express ATR in basis points
        atr_bps = (atr / curr_close) * 10000.0
        sl_bps = max(180.0, min(500.0, atr_bps * 1.5))
        tp_bps = max(250.0, min(650.0, atr_bps * 2.0))

        # Long Entry: Previous close fell outside (below lower band), current close reclaims inside
        if prev_pct_b < 0.0 and pct_b >= 0.0:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                metadata={
                    "reason": "pct_b_failed_breakdown_reclaim",
                    "pct_b": pct_b,
                    "prev_pct_b": prev_pct_b,
                    "lower_band": lower,
                    "mid_band": mid,
                    "atr_bps": atr_bps
                }
            )

        # Short Entry: Previous close pierced above upper band, current close drops back inside
        if prev_pct_b > 1.0 and pct_b <= 1.0:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                metadata={
                    "reason": "pct_b_failed_breakout_rejection",
                    "pct_b": pct_b,
                    "prev_pct_b": prev_pct_b,
                    "upper_band": upper,
                    "mid_band": mid,
                    "atr_bps": atr_bps
                }
            )

        return None