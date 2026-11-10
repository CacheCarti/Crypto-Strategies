from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPanicRecovery(Strategy):
    METADATA = {
        "name": "SolPanicRecovery",
        "domain": "sol_usdc",
        "declared_sl_bps": 450.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 36000,
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.drop_thresh = -0.07
        self.cooldown_bars = 12
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.panic_low = 0.0
        self.target_midpoint = 0.0

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
        self.panic_low = 0.0
        self.target_midpoint = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 1:
            return None

        # Manage open position exits
        if ctx.has_position():
            bars_in_trade = ctx.bar_index - self.entry_bar
            curr_close = ctx.bar.close

            # Target midpoint reached
            if self.target_midpoint > 0.0 and curr_close >= self.target_midpoint:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "flush_midpoint_target_reached",
                        "target_midpoint": self.target_midpoint,
                        "close": curr_close,
                        "bars_held": bars_in_trade,
                    },
                )

            # Close under panic low
            if self.panic_low > 0.0 and curr_close < self.panic_low:
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={
                        "reason": "stop_under_panic_low",
                        "panic_low": self.panic_low,
                        "close": curr_close,
                        "bars_held": bars_in_trade,
                    },
                )

            # Time stop: exit after 36 bars if stagnant
            if bars_in_trade >= 36:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "time_stop_expired",
                        "close": curr_close,
                        "bars_held": bars_in_trade,
                    },
                )

            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime gating: prior failure evidence showed poor performance in high-sentiment bull traps.
        # Edge is concentrated in severe macro panic/fear conditions without full market meltdown.
        fear_greed = ctx.features.get("fear_greed_index", 50)
        market_regime = ctx.market.get("regime", "NORMAL")

        # Avoid entry during total MELTDOWN, but insist on market fear / distressed sentiment (< 45)
        if fear_greed > 45 or market_regime == "MELTDOWN":
            return None

        # Check 24-bar drop
        ref_close = closes[-self.lookback]
        curr_close = closes[-1]
        pct_return_24 = (curr_close - ref_close) / ref_close

        if pct_return_24 > self.drop_thresh:
            return None

        # Stabilization confirmation: current bar closes above previous bar's high
        prev_high = highs[-2]
        if curr_close <= prev_high:
            return None

        # Momentum filter: RSI should be coming out of oversold territory but not overextended
        rsi_val = self._rsi(closes, period=14)
        if rsi_val is None or rsi_val > 45.0:
            return None

        # Define flush range and midpoint target
        flush_high = max(highs[-self.lookback:])
        flush_low = min(lows[-self.lookback:])
        if flush_high <= flush_low:
            return None

        midpoint = flush_low + (flush_high - flush_low) * 0.5
        if curr_close >= midpoint:
            return None

        # Calculate dynamic stop and take profit in bps
        sl_bps = max(150.0, min(500.0, ((curr_close - flush_low) / curr_close) * 10000.0 + 30.0))
        tp_bps = max(200.0, min(650.0, ((midpoint - curr_close) / curr_close) * 10000.0))

        self.panic_low = flush_low
        self.target_midpoint = midpoint
        self.entry_bar = ctx.bar_index

        confidence = 0.70
        if fear_greed < 25:
            confidence += 0.15

        return ctx.signal(
            "long",
            confidence=min(1.0, confidence),
            stop_loss_bps=sl_bps,
            take_profit_bps=tp_bps,
            horizon_seconds=36000,
            metadata={
                "reason": "sol_panic_flush_reversal_confirmation",
                "drop_24h_pct": round(pct_return_24 * 100, 2),
                "rsi": round(rsi_val, 2),
                "fear_greed": fear_greed,
                "panic_low": round(flush_low, 2),
                "midpoint_tp": round(midpoint, 2),
                "prev_high": round(prev_high, 2),
            },
        )