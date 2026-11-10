from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PanicCapitulationRecovery(Strategy):
    METADATA = {
        "name": "Panic Capitulation Recovery BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 28800,  # ~8 hours hold window
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.drop_thresh = -0.06  # 6% flush over 24 bars
        self.cooldown_period = 12  # Wait 12 bars after exit to prevent churning
        self.last_exit_bar = -999
        self.bars_in_trade = 0
        self.entry_midpoint = 0.0

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        # Position management
        if ctx.has_position():
            self.bars_in_trade += 1
            current_price = closes[-1]

            # Midpoint take profit trigger
            if self.entry_midpoint > 0 and current_price >= self.entry_midpoint:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "flush_midpoint_reversion_target_hit",
                        "price": current_price,
                        "midpoint": self.entry_midpoint,
                        "bars_held": self.bars_in_trade
                    }
                )

            # Max hold time exit (~10 bars for 1h swing)
            if self.bars_in_trade >= 10:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "max_recovery_hold_timeout",
                        "price": current_price,
                        "bars_held": self.bars_in_trade
                    }
                )
            return None

        # Mandatory post-exit cooldown
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_period:
            return None

        # Crash detector: check cumulative drop over the last 24 bars
        recent_high = max(highs[-(self.lookback + 1):-1])
        prior_close = closes[-2]
        current_close = closes[-1]
        prev_high = highs[-2]

        drop_from_high = (prior_close - recent_high) / recent_high

        # Only trigger if we had a genuine >=6% drop from recent high in the 24-bar window
        if drop_from_high > self.drop_thresh:
            return None

        # Capitulation reversal candle: current close takes out prior candle's high
        reversal_candle = current_close > prev_high

        # Avoid entries in catastrophic meltdowns unless momentum confirms a bounce
        regime = ctx.market.get("regime", "NORMAL")
        if regime == "MELTDOWN":
            return None

        rsi = self._rsi(closes, period=14)
        fg_index = ctx.features.get("fear_greed_index", 50)

        # High-conviction entry: Capitulation break + oversold / fear confirmation
        if reversal_candle and rsi < 42.0:
            window_low = min(lows[-self.lookback:])
            midpoint = (recent_high + window_low) / 2.0
            self.entry_midpoint = midpoint
            self.bars_in_trade = 0

            # Scale confidence based on extremity of the crash and fear sentiment
            conf = 0.70
            if fg_index < 30:
                conf += 0.15
            if drop_from_high < -0.08:
                conf += 0.10
            conf = min(conf, 0.95)

            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=280.0,
                take_profit_bps=450.0,
                horizon_seconds=28800,
                metadata={
                    "reason": "crash_flush_capitulation_engulf",
                    "drop_pct": round(drop_from_high * 100, 2),
                    "rsi": round(rsi, 2),
                    "fg_index": fg_index,
                    "target_midpoint": round(midpoint, 2),
                    "price": current_close
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.bars_in_trade = 0
        self.entry_midpoint = 0.0