from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPctBReversion(Strategy):
    METADATA = {
        "name": "SolPctBReversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 20
        self.num_std = 2.0
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.max_hold_bars = 18

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
        closes = ctx.closes(self.period + 2)
        highs = ctx.highs(self.period + 2)
        lows = ctx.lows(self.period + 2)

        if len(closes) < self.period + 2:
            return None

        # Calculate current Bollinger Bands and prior bar Bands
        mid, upper, lower = self._bollinger(closes, self.period, self.num_std)
        prev_mid, prev_upper, prev_lower = self._bollinger(closes[:-1], self.period, self.num_std)

        if None in (mid, upper, lower, prev_mid, prev_upper, prev_lower):
            return None

        band_width = upper - lower
        prev_band_width = prev_upper - prev_lower
        if band_width <= 0 or prev_band_width <= 0:
            return None

        pct_b = (closes[-1] - lower) / band_width
        prev_pct_b = (closes[-2] - prev_lower) / prev_band_width

        # Filter out extreme crisis regimes to prevent toxic flow traps
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65 or regime == "MELTDOWN":
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_exit", "regime": regime})
            return None

        # Position management
        if ctx.has_position():
            direction = ctx.position_direction()
            pos_dict = ctx.position or {}
            entry_price = pos_dict.get("entry_price", closes[-1])
            bars_held = ctx.bar_index - self.last_exit_bar

            # 1. Target exit at mid-band
            if direction == "long" and closes[-1] >= mid:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "long_midband_reached", "close": closes[-1], "mid": mid, "pct_b": pct_b
                })
            elif direction == "short" and closes[-1] <= mid:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "short_midband_reached", "close": closes[-1], "mid": mid, "pct_b": pct_b
                })

            # 2. Time-decay exit
            if bars_held >= self.max_hold_bars:
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "time_stop_expired", "bars_held": bars_held
                })

            return None

        # Cooldown guard after trade exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Volatility check: require minimum band expansion to avoid dead chop
        atr = self._atr(highs, lows, closes, 14)
        if atr is None or closes[-1] == 0:
            return None
        atr_pct = (atr / closes[-1]) * 10000.0
        if atr_pct < 35.0:
            return None

        # Dynamic stop-loss based on structural band width (2.2x ATR equivalent in bps)
        dyn_sl_bps = max(180.0, min(420.0, atr_pct * 1.8))
        dyn_tp_bps = max(220.0, min(500.0, atr_pct * 2.2))

        # Failed break long: prior bar closed below lower band (%B < 0), current bar closes back inside (%B >= 0.05)
        if prev_pct_b < 0.0 and pct_b >= 0.05 and pct_b < 0.45:
            conf = min(0.85, max(0.55, 0.60 + (pct_b - 0.05)))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=dyn_sl_bps,
                take_profit_bps=dyn_tp_bps,
                horizon_seconds=21600,
                metadata={
                    "reason": "pctb_lower_rejection",
                    "prev_pct_b": prev_pct_b,
                    "pct_b": pct_b,
                    "lower": lower,
                    "mid": mid,
                    "atr_bps": atr_pct
                }
            )

        # Failed break short: prior bar closed above upper band (%B > 1), current bar closes back inside (%B <= 0.95)
        if prev_pct_b > 1.0 and pct_b <= 0.95 and pct_b > 0.55:
            conf = min(0.85, max(0.55, 0.60 + (0.95 - pct_b)))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=dyn_sl_bps,
                take_profit_bps=dyn_tp_bps,
                horizon_seconds=21600,
                metadata={
                    "reason": "pctb_upper_rejection",
                    "prev_pct_b": prev_pct_b,
                    "pct_b": pct_b,
                    "upper": upper,
                    "mid": mid,
                    "atr_bps": atr_pct
                }
            )

        return None