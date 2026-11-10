from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcPanicRecovery(Strategy):
    METADATA = {
        "name": "BtcPanicRecovery",
        "domain": "btc_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 28800,  # ~8 hours target hold
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 8
        self.last_exit_bar = -100
        self.entry_bar = -100
        self.max_hold_bars = 16

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return 50.0
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        current_close = ctx.bar.close
        prev_high = highs[-2]
        
        # In-position management
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            highest_24 = max(highs[-self.lookback:])
            lowest_24 = min(lows[-self.lookback:])
            midpoint = (highest_24 + lowest_24) / 2.0

            # Exit condition 1: reached 24-bar recovery midpoint
            if current_close >= midpoint:
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "midpoint_reached",
                        "close": current_close,
                        "midpoint": midpoint,
                        "bars_held": bars_held
                    }
                )

            # Exit condition 2: time-based holding exhaustion
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "max_hold_reached",
                        "close": current_close,
                        "bars_held": bars_held
                    }
                )
            return None

        # Mandatory cooldown between trades
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime safety filter: do not trade catastrophic unwinds
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime == "MELTDOWN":
            return None

        # Adaptive panic detection:
        # Instead of strict 6% drop, measure rolling drawdown from peak over lookback
        peak_24 = max(highs[-self.lookback - 1:-1])
        trough_recent = min(lows[-4:])
        drop_pct = (peak_24 - trough_recent) / peak_24 if peak_24 > 0 else 0.0

        # Dynamic flush threshold: 4.5% to 6.0% depending on Fear & Greed sentiment
        fng = ctx.features.get("fear_greed_index", 50.0)
        flush_threshold = 0.045 if fng < 35.0 else 0.055

        is_flushed = drop_pct >= flush_threshold

        # Capitulation reversal trigger: current close breaks above prior bar's high
        is_reversal_bar = (current_close > prev_high) and (closes[-2] < closes[-3])

        # RSI filter to ensure trade occurs on oversold/inflection regime
        rsi_val = self._rsi(closes, 14)
        is_oversold_or_inflecting = rsi_val < 42.0

        if is_flushed and is_reversal_bar and is_oversold_or_inflecting:
            self.entry_bar = ctx.bar_index
            confidence = min(0.9, max(0.55, 0.55 + (drop_pct - flush_threshold) * 5.0))

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "panic_flush_capitulation_bounce",
                    "drop_pct": round(drop_pct * 100, 2),
                    "rsi": round(rsi_val, 2),
                    "fear_greed": fng,
                    "prev_high": prev_high,
                    "close": current_close
                }
            )

        return None