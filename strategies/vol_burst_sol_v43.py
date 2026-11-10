from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolVolBurstMomentum(Strategy):
    METADATA = {
        "name": "SolVolBurstMomentum",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 18000,  # ~5 hours
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 30
        self.max_hold_bars = 6
        self.cooldown_bars = 6
        self.bars_in_pos = 0
        self.last_exit_bar = -100

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.bars_in_pos = 0
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)
        opens = ctx.opens(self.lookback + 2)
        volumes = ctx.volumes(self.lookback + 2)

        if len(closes) < self.lookback + 1:
            return None

        # Position management
        if ctx.has_position():
            self.bars_in_pos += 1
            direction = ctx.position_direction()
            
            # Time-based hold exit
            if self.bars_in_pos >= self.max_hold_bars:
                return ctx.signal("flat", confidence=0.5, metadata={
                    "reason": "max_hold_reached",
                    "bars_held": self.bars_in_pos
                })

            # Momentum stall exit: candle fails to advance past previous close
            if direction == "long" and closes[-1] < closes[-2] and closes[-2] < opens[-2]:
                return ctx.signal("flat", confidence=0.5, metadata={
                    "reason": "long_momentum_stall",
                    "bars_held": self.bars_in_pos
                })
            elif direction == "short" and closes[-1] > closes[-2] and closes[-2] > opens[-2]:
                return ctx.signal("flat", confidence=0.5, metadata={
                    "reason": "short_momentum_stall",
                    "bars_held": self.bars_in_pos
                })
            return None

        # Cooldown guard after trade exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Calculate percentile/rank metrics across lookback window to avoid fixed magic numbers
        hist_vols = volumes[-self.lookback:-1]
        cur_vol = volumes[-1]
        vol_rank = sum(1 for v in hist_vols if cur_vol > v) / len(hist_vols)

        hist_ranges = [highs[i] - lows[i] for i in range(-self.lookback, -1)]
        cur_range = highs[-1] - lows[-1]
        range_rank = sum(1 for r in hist_ranges if cur_range > r) / len(hist_ranges)

        # Requires top-decile volume and range expansion (statistical burst)
        if vol_rank < 0.90 or range_rank < 0.88:
            return None

        body = closes[-1] - opens[-1]
        if cur_range <= 0:
            return None

        body_ratio = abs(body) / cur_range

        # Filter out high-range indescision bars (wicks); require directional conviction
        if body_ratio < 0.55:
            return None

        # Market regime filter from engine
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Alignment with short-term trend filter (SMA 20)
        sma20 = sum(closes[-20:]) / 20.0
        
        # Bullish burst: strong green expansion bar closing near high, above SMA20
        if body > 0 and closes[-1] > sma20:
            confidence = min(0.9, 0.5 + 0.25 * vol_rank + 0.15 * body_ratio)
            self.bars_in_pos = 0
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "vol_burst_bullish_expansion",
                    "vol_rank": round(vol_rank, 3),
                    "range_rank": round(range_rank, 3),
                    "body_ratio": round(body_ratio, 3),
                    "price": closes[-1],
                    "sma20": round(sma20, 2)
                }
            )

        # Bearish burst: strong red expansion bar closing near low, below SMA20
        elif body < 0 and closes[-1] < sma20:
            confidence = min(0.9, 0.5 + 0.25 * vol_rank + 0.15 * body_ratio)
            self.bars_in_pos = 0
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "vol_burst_bearish_expansion",
                    "vol_rank": round(vol_rank, 3),
                    "range_rank": round(range_rank, 3),
                    "body_ratio": round(body_ratio, 3),
                    "price": closes[-1],
                    "sma20": round(sma20, 2)
                }
            )

        return None