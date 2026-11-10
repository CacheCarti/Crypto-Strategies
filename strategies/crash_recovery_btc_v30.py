from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcPanicFlushRecovery(Strategy):
    METADATA = {
        "name": "BtcPanicFlushRecovery",
        "domain": "btc_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 43200,  # up to 12 hours max swing
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.flush_threshold = -0.055  # 5.5% drop over 24h
        self.cooldown_bars = 6
        self.last_exit_bar = -100
        self.entry_bar_idx = 0
        self.target_midpoint = 0.0

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        # Check existing position management
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar_idx
            current_close = ctx.bar.close

            # Midpoint take profit or timed regime exit
            if current_close >= self.target_midpoint and self.target_midpoint > 0:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "flush_midpoint_reversion_target_hit",
                        "price": current_close,
                        "target_midpoint": self.target_midpoint,
                        "bars_held": bars_held
                    }
                )

            # Max holding guard: exit after 14 bars if momentum stalled
            if bars_held >= 14:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "flush_recovery_timeout",
                        "price": current_close,
                        "bars_held": bars_held
                    }
                )
            return None

        # Cooldown guard after prior trade
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Filter severe meltdown contexts
        regime = ctx.market.get("regime", "NORMAL")
        if regime == "MELTDOWN":
            return None

        # 24-bar window drop calculation
        past_close = closes[-(self.lookback + 1)]
        prev_close = closes[-2]
        window_return = (prev_close - past_close) / past_close

        # Capitulation trigger: deep flush followed by reversal candle breaking previous bar's high
        if window_return <= self.flush_threshold:
            prev_high = highs[-2]
            curr_close = closes[-1]

            if curr_close > prev_high:
                window_high = max(highs[-(self.lookback + 1):-1])
                window_low = min(lows[-(self.lookback + 1):])
                midpoint = (window_high + window_low) / 2.0

                # Ensure meaningful upside to the midpoint
                if midpoint > curr_close * 1.008:
                    atr = self._atr(highs, lows, closes, 14)
                    if atr is None or atr == 0:
                        vol_sl_bps = 350.0
                    else:
                        vol_sl_bps = max(200.0, min(500.0, (2.2 * atr / curr_close) * 10000.0))

                    tp_bps = min(600.0, max(250.0, ((midpoint - curr_close) / curr_close) * 10000.0))

                    self.entry_bar_idx = ctx.bar_index
                    self.target_midpoint = midpoint

                    return ctx.signal(
                        "long",
                        confidence=0.85,
                        stop_loss_bps=vol_sl_bps,
                        take_profit_bps=tp_bps,
                        horizon_seconds=43200,
                        metadata={
                            "reason": "panic_flush_capitulation_recovery",
                            "window_return_pct": round(window_return * 100, 2),
                            "prev_high": prev_high,
                            "curr_close": curr_close,
                            "midpoint": round(midpoint, 2),
                            "atr": round(atr, 2) if atr else 0.0,
                            "vol_sl_bps": round(vol_sl_bps, 1),
                            "tp_bps": round(tp_bps, 1)
                        }
                    )

        return None