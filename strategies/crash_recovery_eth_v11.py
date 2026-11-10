from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SelectivePanicRecoveryETH(Strategy):
    METADATA = {
        "name": "Selective Panic Recovery ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 350.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.min_drop_pct = 0.055
        self.cooldown_bars = 16
        self.last_exit_bar = -999
        self.flush_high = 0.0
        self.flush_low = 0.0
        self.entry_bar = 0

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.flush_high = 0.0
        self.flush_low = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 5)
        highs = ctx.highs(self.lookback + 5)
        lows = ctx.lows(self.lookback + 5)

        if len(closes) < self.lookback + 2:
            return None

        # Position management
        if ctx.has_position():
            if ctx.position_direction() == "long":
                current_price = ctx.bar.close
                # Dynamic midpoint exit: flush range midpoint
                if self.flush_high > self.flush_low > 0:
                    midpoint = (self.flush_high + self.flush_low) / 2.0
                    if current_price >= midpoint:
                        return ctx.signal(
                            "flat",
                            confidence=0.8,
                            metadata={
                                "reason": "flush_midpoint_reversion_target_hit",
                                "midpoint": round(midpoint, 2),
                                "price": round(current_price, 2),
                                "flush_high": round(self.flush_high, 2),
                                "flush_low": round(self.flush_low, 2)
                            }
                        )
                # Max hold time safeguard (14 bars ~ 14 hours)
                if (ctx.bar_index - self.entry_bar) >= 14:
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": "max_recovery_hold_reached",
                            "bars_held": ctx.bar_index - self.entry_bar,
                            "price": round(current_price, 2)
                        }
                    )
            return None

        # Cooldown guard
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Avoid extreme crisis / meltdown states where catch-the-knife fails persistently
        crisis_score = ctx.market.get("crisis_score", 0.0)
        market_regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.85 or market_regime == "MELTDOWN":
            return None

        # Flush identification: peak to trough over lookback bars
        window_highs = highs[-self.lookback:]
        window_lows = lows[-self.lookback:]
        peak_high = max(window_highs)
        trough_low = min(window_lows)

        if peak_high <= 0:
            return None

        drop_pct = (peak_high - trough_low) / peak_high
        if drop_pct < self.min_drop_pct:
            return None

        # Ensure the current low or recent bar is near the trough
        current_close = ctx.bar.close
        prev_high = highs[-2]
        prev_close = closes[-2]

        # Stabilization trigger: current close breaks distinctly above previous bar's high
        is_stabilizing = (current_close > prev_high) and (current_close > prev_close)
        if not is_stabilizing:
            return None

        # Exhaustion confirmation via RSI oversold recovery
        rsi_val = self._rsi(closes, period=14)
        if rsi_val is None or rsi_val > 42.0:
            return None

        # Ensure we are buying below the midpoint of the flush (favorable R:R)
        midpoint = (peak_high + trough_low) / 2.0
        if current_close >= midpoint:
            return None

        self.flush_high = peak_high
        self.flush_low = trough_low
        self.entry_bar = ctx.bar_index

        conf = min(0.9, 0.55 + (drop_pct * 3.0))

        return ctx.signal(
            "long",
            confidence=conf,
            stop_loss_bps=self.METADATA["declared_sl_bps"],
            take_profit_bps=self.METADATA["declared_tp_bps"],
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={
                "reason": "flush_exhaustion_stabilization_breakout",
                "drop_pct": round(drop_pct * 100, 2),
                "peak_high": round(peak_high, 2),
                "trough_low": round(trough_low, 2),
                "rsi": round(rsi_val, 2),
                "price": round(current_close, 2),
                "midpoint_target": round(midpoint, 2)
            }
        )