from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcPanicRecovery(Strategy):
    METADATA = {
        "name": "BtcPanicRecovery",
        "domain": "btc_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 21600,  # ~6 hours target hold
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.flush_threshold_pct = -4.5  # Realistic 1h 24-bar flush threshold (~4.5% to 6%)
        self.cooldown_bars = 8
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 10
        self.target_midpoint = 0.0

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return 0.0
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 1:
            return None

        current_idx = ctx.bar_index
        current_close = ctx.bar.close
        current_high = ctx.bar.high

        # Manage open position
        if ctx.has_position():
            bars_held = current_idx - self.entry_bar
            
            # Midpoint take profit or time horizon exit
            if self.target_midpoint > 0 and current_close >= self.target_midpoint:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "target_midpoint_hit",
                        "price": current_close,
                        "midpoint": self.target_midpoint,
                        "bars_held": bars_held
                    }
                )

            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "max_bars_timeout",
                        "price": current_close,
                        "bars_held": bars_held
                    }
                )

            return None

        # Cooldown guard after prior trade
        if (current_idx - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Lookback flush calculation (past 24 bars)
        ref_high = max(highs[-self.lookback - 1:-1])
        ref_low = min(lows[-self.lookback - 1:-1])
        drop_pct = ((current_close - ref_high) / ref_high) * 100.0

        # Capitulation reversal trigger: current close > previous bar's high
        prev_high = highs[-2]
        reversal_candle = current_close > prev_high

        # Regime and sentiment check
        fear_greed = ctx.features.get("fear_greed_index", 50.0)
        market_regime = ctx.market.get("regime", "NORMAL")

        # Must have flushed, formed reversal candle, and market showing fear/volatility
        if drop_pct <= self.flush_threshold_pct and reversal_candle and fear_greed <= 55 and market_regime != "MELTDOWN":
            self.entry_bar = current_idx
            self.target_midpoint = (ref_high + ref_low) / 2.0
            
            # Dynamic confidence based on flush depth
            severity = min(abs(drop_pct) / 10.0, 1.0)
            confidence = round(0.55 + 0.35 * severity, 2)

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "panic_flush_reversal_bounce",
                    "drop_pct": round(drop_pct, 2),
                    "target_midpoint": round(self.target_midpoint, 2),
                    "fear_greed": fear_greed,
                    "prev_high": prev_high,
                    "price": current_close
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.target_midpoint = 0.0