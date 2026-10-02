from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class SolBollingerSqueezeBreakout(Strategy):
    METADATA = {
        "name": "SOL Bollinger Squeeze Breakout",
        "domain": "sol_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 125,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.bb_period = 20
        self.bb_std = 2.0
        self.squeeze_lookback = 100
        self.squeeze_percentile = 0.15  # Tighter: bottom 15% bandwidth
        self.min_vol_ratio = 1.30       # Require clear volume surge on breakout
        self.cooldown_bars = 10         # Stricter cooldown after any exit
        self.last_exit_bar = -999

    def _calc_bb(self, slice_c: list[float], num_std: float = 2.0) -> tuple[float, float, float, float]:
        n = len(slice_c)
        mean = sum(slice_c) / n
        var = sum((x - mean) ** 2 for x in slice_c) / n
        std = math.sqrt(var)
        upper = mean + num_std * std
        lower = mean - num_std * std
        width = (upper - lower) / mean if mean > 0 else 0.0
        return mean, upper, lower, width

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        needed_bars = self.squeeze_lookback + self.bb_period
        closes = ctx.closes(needed_bars + 1)
        if len(closes) < needed_bars + 1:
            return None

        # Filter out extreme crisis regimes to prevent whipsaws
        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.70 or regime in ("CRISIS", "MELTDOWN"):
            return None

        # Calculate historical Bollinger Bandwidth over the lookback window
        bandwidths = []
        for i in range(self.squeeze_lookback):
            idx_end = len(closes) - self.squeeze_lookback + i
            sub_slice = closes[idx_end - self.bb_period:idx_end]
            _, _, _, bw = self._calc_bb(sub_slice, self.bb_std)
            bandwidths.append(bw)

        sorted_bw = sorted(bandwidths)
        q_idx = int(len(sorted_bw) * self.squeeze_percentile)
        squeeze_threshold = sorted_bw[q_idx]

        # Prior squeeze requirement: must have been in tight squeeze within the last 5 bars
        was_squeezed = any(bw <= squeeze_threshold for bw in bandwidths[-6:-1])

        # Current and previous BB
        current_slice = closes[-self.bb_period:]
        mid, upper, lower, current_bw = self._calc_bb(current_slice, self.bb_std)

        prev_slice = closes[-self.bb_period - 1:-1]
        _, prev_upper, prev_lower, _ = self._calc_bb(prev_slice, self.bb_std)

        current_close = closes[-1]
        prev_close = closes[-2]

        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Exit management for active positions
        if has_pos:
            if pos_dir == "long":
                # Exit long if price drops decisively back below mid band
                if current_close < mid:
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "long_momentum_fade_below_mid",
                            "close": current_close,
                            "mid_band": round(mid, 2),
                            "bandwidth": round(current_bw, 4),
                        },
                    )
            elif pos_dir == "short":
                # Exit short if price rallies decisively back above mid band
                if current_close > mid:
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "short_momentum_fade_above_mid",
                            "close": current_close,
                            "mid_band": round(mid, 2),
                            "bandwidth": round(current_bw, 4),
                        },
                    )
            return None

        # Hard post-exit cooldown guard to prevent overtrading and friction loss
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        if not was_squeezed:
            return None

        # Volume confirmation
        vols = ctx.volumes(20)
        if len(vols) < 20 or sum(vols) <= 0:
            return None

        avg_vol = sum(vols) / len(vols)
        vol_ratio = vols[-1] / avg_vol if avg_vol > 0 else 0.0

        if vol_ratio < self.min_vol_ratio:
            return None

        # Long Entry: Clean breakout above upper band following tight squeeze with volume confirmation
        if prev_close <= prev_upper and current_close > upper:
            confidence = min(0.90, 0.65 + 0.10 * min(vol_ratio - 1.0, 2.0))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "tight_squeeze_vol_breakout_long",
                    "close": current_close,
                    "upper_band": round(upper, 2),
                    "mid_band": round(mid, 2),
                    "bandwidth": round(current_bw, 4),
                    "squeeze_threshold": round(squeeze_threshold, 4),
                    "vol_ratio": round(vol_ratio, 2),
                },
            )

        # Short Entry: Clean breakout below lower band following tight squeeze with volume confirmation
        if prev_close >= prev_lower and current_close < lower:
            confidence = min(0.90, 0.65 + 0.10 * min(vol_ratio - 1.0, 2.0))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "tight_squeeze_vol_breakout_short",
                    "close": current_close,
                    "lower_band": round(lower, 2),
                    "mid_band": round(mid, 2),
                    "bandwidth": round(current_bw, 4),
                    "squeeze_threshold": round(squeeze_threshold, 4),
                    "vol_ratio": round(vol_ratio, 2),
                },
            )

        return None