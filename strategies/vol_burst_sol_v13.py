from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolVolumeBurstMomentum(Strategy):
    METADATA = {
        "name": "SolVolumeBurstMomentum",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.max_hold_bars = 5
        self.cooldown_bars = 4
        self.entry_bar_idx = -1
        self.last_exit_idx = -999

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_idx = ctx.bar_index
        self.entry_bar_idx = -1

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 5)
        highs = ctx.highs(self.lookback + 5)
        lows = ctx.lows(self.lookback + 5)
        volumes = ctx.volumes(self.lookback + 5)

        if len(closes) < self.lookback + 2:
            return None

        # Manage open position
        if ctx.has_position():
            bars_in_trade = ctx.bar_index - self.entry_bar_idx if self.entry_bar_idx > 0 else 1
            pos_dir = ctx.position_direction()

            # Exit on momentum stall or max hold horizon
            if bars_in_trade >= self.max_hold_bars:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "max_bars_exceeded", "bars": bars_in_trade})

            # Check momentum stall (bar closes opposite to trade direction with fading volume)
            bar_change = ctx.bar.close - ctx.bar.open
            if pos_dir == "long" and bar_change < 0 and ctx.bar.volume < volumes[-2]:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "long_momentum_stalled", "bars": bars_in_trade})
            elif pos_dir == "short" and bar_change > 0 and ctx.bar.volume < volumes[-2]:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "short_momentum_stalled", "bars": bars_in_trade})

            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_idx < self.cooldown_bars:
            return None

        # Avoid crisis breakdown regimes
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Compute volume and range stats over rolling baseline
        prior_vols = volumes[-(self.lookback + 1):-1]
        prior_ranges = [highs[i] - lows[i] for i in range(-self.lookback - 1, -1)]

        mean_vol = sum(prior_vols) / len(prior_vols)
        vol_std = math.sqrt(sum((v - mean_vol) ** 2 for v in prior_vols) / len(prior_vols))
        vol_zscore = (ctx.bar.volume - mean_vol) / vol_std if vol_std > 0 else 0.0

        current_range = ctx.bar.high - ctx.bar.low
        mean_range = sum(prior_ranges) / len(prior_ranges)
        range_ratio = current_range / mean_range if mean_range > 0 else 1.0

        # High-volume expansion setup: volume z-score >= 1.75 (~96th percentile) & range expansion >= 1.5x
        if vol_zscore < 1.75 or range_ratio < 1.5:
            return None

        body = abs(ctx.bar.close - ctx.bar.open)
        if current_range == 0 or (body / current_range) < 0.55:
            return None

        atr = self._atr(highs, lows, closes, 14)
        if atr is None or atr == 0:
            return None

        # Dynamic stop loss and take profit based on ATR volatility
        atr_bps = (atr / ctx.bar.close) * 10000.0
        sl_bps = max(180.0, min(380.0, atr_bps * 1.5))
        tp_bps = max(300.0, min(750.0, atr_bps * 3.0))

        # Long burst
        if ctx.bar.close > ctx.bar.open and ctx.bar.close > highs[-2]:
            self.entry_bar_idx = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=min(0.9, 0.65 + (vol_zscore - 1.75) * 0.1),
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bullish_volume_burst_breakout",
                    "vol_zscore": round(vol_zscore, 2),
                    "range_ratio": round(range_ratio, 2),
                    "close": ctx.bar.close,
                    "sl_bps": round(sl_bps, 1),
                }
            )

        # Short burst
        if ctx.bar.close < ctx.bar.open and ctx.bar.close < lows[-2]:
            self.entry_bar_idx = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=min(0.9, 0.65 + (vol_zscore - 1.75) * 0.1),
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bearish_volume_burst_breakout",
                    "vol_zscore": round(vol_zscore, 2),
                    "range_ratio": round(range_ratio, 2),
                    "close": ctx.bar.close,
                    "sl_bps": round(sl_bps, 1),
                }
            )

        return None