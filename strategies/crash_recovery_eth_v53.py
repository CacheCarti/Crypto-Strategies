from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthPanicFlushRecovery(Strategy):
    METADATA = {
        "name": "EthPanicFlushRecovery",
        "domain": "eth_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 28800,  # ~8 hours target hold
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.drop_thresh = -0.05
        self.cooldown_bars = 16
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.target_price = 0.0
        self.structure_stop_price = 0.0

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        highs = ctx.highs(35)
        lows = ctx.lows(35)

        if len(closes) < 30:
            return None

        # Manage existing position
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            current_close = ctx.bar.close

            # Target reached (midpoint recovery) or structure stop violated
            hit_tp = self.target_price > 0 and current_close >= self.target_price
            hit_sl = self.structure_stop_price > 0 and current_close <= self.structure_stop_price
            time_stop = bars_held >= 20  # Timed exit: thesis expires if recovery stalls

            if hit_tp or hit_sl or time_stop:
                exit_reason = "take_profit_midpoint" if hit_tp else ("structure_stop" if hit_sl else "timed_exit")
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": exit_reason,
                        "close": current_close,
                        "bars_held": bars_held,
                        "target_price": self.target_price,
                        "stop_price": self.structure_stop_price,
                    }
                )
            return None

        # Cooldown guard
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Regime / Crisis filter: avoid entering during active meltdown/crisis collapses
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Calculate 24-bar return
        current_close = closes[-1]
        prior_close = closes[-self.lookback]
        return_24b = (current_close - prior_close) / prior_close

        # Panic drop condition: drop of 5%+ over last 24 bars
        if return_24b <= self.drop_thresh:
            # Stabilization trigger: current close breaks above previous bar high
            prev_high = highs[-2]
            if current_close > prev_high:
                flush_high = max(highs[-self.lookback:])
                flush_low = min(lows[-self.lookback:])
                range_span = flush_high - flush_low

                if range_span <= 0:
                    return None

                # Midpoint of the flush range as profit target
                midpoint = flush_low + 0.5 * range_span

                # Only enter if there is sufficient upside to midpoint (> 1.2%)
                if midpoint > current_close * 1.012:
                    atr = self._atr(highs, lows, closes, period=14)
                    atr_val = atr if atr is not None else (current_close * 0.015)

                    # Structural volatility-anchored stop: below flush low with ATR buffer
                    self.structure_stop_price = flush_low - (0.5 * atr_val)
                    self.target_price = midpoint
                    self.entry_bar = ctx.bar_index

                    # Distance metrics in bps
                    sl_bps = max(150.0, min(500.0, ((current_close - self.structure_stop_price) / current_close) * 10000.0))
                    tp_bps = max(200.0, min(650.0, ((self.target_price - current_close) / current_close) * 10000.0))

                    # Scale confidence by depth of flush
                    confidence = min(0.9, max(0.55, 0.55 + abs(return_24b) * 2.0))

                    return ctx.signal(
                        "long",
                        confidence=confidence,
                        stop_loss_bps=sl_bps,
                        take_profit_bps=tp_bps,
                        horizon_seconds=28800,
                        metadata={
                            "reason": "panic_recovery_flush_breakout",
                            "return_24b": round(return_24b, 4),
                            "flush_high": flush_high,
                            "flush_low": flush_low,
                            "midpoint_target": midpoint,
                            "structure_stop": self.structure_stop_price,
                            "current_close": current_close,
                        }
                    )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.target_price = 0.0
        self.structure_stop_price = 0.0