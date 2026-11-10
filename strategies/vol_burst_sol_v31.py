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
        "warmup_bars": 35,
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 20
        self.atr_period = 14
        self.bars_in_trade = 0
        self.max_hold_bars = 5
        self.cooldown_bars = 4
        self.last_exit_bar = -999

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.bars_in_trade = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        # Position management & timed/momentum stall exit
        if ctx.has_position():
            self.bars_in_trade += 1
            pos_dir = ctx.position_direction()
            
            # Timed exit: after max_hold_bars
            if self.bars_in_trade >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                self.bars_in_trade = 0
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "timed_exit_hold_cap", "bars_held": self.bars_in_trade}
                )

            # Momentum stall exit: counter bar opposing entry thrust
            bar = ctx.bar
            if pos_dir == "long" and bar.close < bar.open and (bar.open - bar.close) > 0.6 * (bar.high - bar.low):
                self.last_exit_bar = ctx.bar_index
                self.bars_in_trade = 0
                return ctx.signal(
                    "flat",
                    confidence=0.55,
                    metadata={"reason": "long_momentum_stall_reversal_bar", "close": bar.close}
                )
            elif pos_dir == "short" and bar.close > bar.open and (bar.close - bar.open) > 0.6 * (bar.high - bar.low):
                self.last_exit_bar = ctx.bar_index
                self.bars_in_trade = 0
                return ctx.signal(
                    "flat",
                    confidence=0.55,
                    metadata={"reason": "short_momentum_stall_reversal_bar", "close": bar.close}
                )
            return None

        # Mandatory cooldown check
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Regime safety filter
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)
        volumes = ctx.volumes(self.warmup_bars)

        # Baseline indicators
        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        avg_vol = sum(volumes[-(self.vol_period + 1):-1]) / self.vol_period
        if avg_vol <= 0:
            return None

        curr_bar = ctx.bar
        curr_range = curr_bar.high - curr_bar.low
        body = abs(curr_bar.close - curr_bar.open)
        vol_ratio = curr_bar.volume / avg_vol
        range_ratio = curr_range / atr

        # Setup conditions:
        # 1. Volume > 2.0x 20-bar baseline
        # 2. Bar range > 1.6x ATR (unusually wide)
        # 3. High body-to-range ratio (decisive thrust, not high-vol indecision/doji)
        is_volume_burst = vol_ratio >= 2.0
        is_wide_range = range_ratio >= 1.6
        is_decisive = (body / curr_range) >= 0.65 if curr_range > 0 else False

        if not (is_volume_burst and is_wide_range and is_decisive):
            return None

        # Volatility-based adaptive stop loss (structure stop based on current ATR)
        sl_bps = max(180.0, min(380.0, (1.8 * atr / curr_bar.close) * 10000.0))
        tp_bps = sl_bps * 2.0

        if curr_bar.close > curr_bar.open:
            self.bars_in_trade = 0
            return ctx.signal(
                "long",
                confidence=min(0.85, 0.55 + 0.1 * min(vol_ratio - 2.0, 3.0)),
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=18000,
                metadata={
                    "reason": "bullish_volume_burst_expansion",
                    "vol_ratio": round(vol_ratio, 2),
                    "range_ratio": round(range_ratio, 2),
                    "atr": round(atr, 4),
                    "close": curr_bar.close
                }
            )
        elif curr_bar.close < curr_bar.open:
            self.bars_in_trade = 0
            return ctx.signal(
                "short",
                confidence=min(0.85, 0.55 + 0.1 * min(vol_ratio - 2.0, 3.0)),
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=18000,
                metadata={
                    "reason": "bearish_volume_burst_expansion",
                    "vol_ratio": round(vol_ratio, 2),
                    "range_ratio": round(range_ratio, 2),
                    "atr": round(atr, 4),
                    "close": curr_bar.close
                }
            )

        return None