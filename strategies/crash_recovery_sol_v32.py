from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPanicRecovery(Strategy):
    METADATA = {
        "name": "SolPanicRecovery",
        "domain": "sol_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 600.0,
        "declared_hold_seconds": 28800,  # ~8 hours
        "warmup_bars": 35,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.min_drop_pct = 0.065       # 6.5% drop over 24h
        self.cooldown_bars = 6          # mandatory wait after exit
        self.last_exit_bar = -999
        self.entry_bar = 0
        self.target_midpoint = 0.0
        self.panic_low = 0.0

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
        self.target_midpoint = 0.0
        self.panic_low = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        # Position management
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            current_price = ctx.bar.close

            # Target midpoint reached
            if self.target_midpoint > 0 and current_price >= self.target_midpoint:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "flush_range_midpoint_hit",
                        "price": current_price,
                        "target_midpoint": self.target_midpoint,
                        "bars_held": bars_held,
                    }
                )

            # Volatility-adjusted timed exhaustion exit (max 18h hold to prevent dead-cat bleeding)
            if bars_held >= 18:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "timed_exhaustion_exit",
                        "price": current_price,
                        "bars_held": bars_held,
                    }
                )

            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Calculate 24-bar drop
        anchor_high = max(highs[-self.lookback:])
        current_close = ctx.bar.close
        drop_pct = (anchor_high - current_close) / anchor_high

        # Require meaningful capitulation flush
        if drop_pct < self.min_drop_pct:
            return None

        # Trigger condition: first stabilization bar that closes above previous bar's high
        prev_high = highs[-2]
        if current_close <= prev_high:
            return None

        # Structural low during the panic window
        flush_low = min(lows[-self.lookback:])
        range_span = anchor_high - flush_low
        if range_span <= 0:
            return None

        # Midpoint of the entire panic liquidation wave
        midpoint = flush_low + (range_span * 0.50)
        if midpoint <= current_close:
            return None

        # Structural stop: just below the panic low with ATR buffer
        atr = self._atr(highs, lows, closes, 14)
        atr_val = atr if atr else (current_close * 0.01)
        stop_price = flush_low - (0.5 * atr_val)

        # Calculate exact stop-loss and take-profit in basis points
        sl_bps = ((current_close - stop_price) / current_close) * 10000.0
        tp_bps = ((midpoint - current_close) / current_close) * 10000.0

        # Boundary safety for execution contract
        sl_bps = max(180.0, min(550.0, sl_bps))
        tp_bps = max(220.0, min(800.0, tp_bps))

        # Risk-to-reward filter: setup must offer attractive recovery potential
        if tp_bps / sl_bps < 1.05:
            return None

        # Confidence based on depth of flush and stabilization impulse
        confidence = min(0.90, max(0.55, 0.50 + (drop_pct * 3.0)))

        self.entry_bar = ctx.bar_index
        self.target_midpoint = midpoint
        self.panic_low = flush_low

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=sl_bps,
            take_profit_bps=tp_bps,
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={
                "reason": "sol_panic_flush_reversal",
                "drop_pct": drop_pct,
                "anchor_high": anchor_high,
                "panic_low": flush_low,
                "target_midpoint": midpoint,
                "sl_bps": sl_bps,
                "tp_bps": tp_bps,
                "btc_24h_return": ctx.features.get("btc_return_pct", 0.0),
            }
        )