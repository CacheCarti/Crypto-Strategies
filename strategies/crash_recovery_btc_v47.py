from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcPanicRecovery(Strategy):
    METADATA = {
        "name": "BtcPanicRecovery",
        "domain": "btc_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 43200,  # ~12 hours swing target
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.drop_thresh = -0.050  # 5.0% drop threshold over 24h
        self.last_exit_bar = -100
        self.cooldown_bars = 16
        self.entry_bar = 0
        self.max_hold_bars = 18

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
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

        current_close = ctx.bar.close
        ref_close = closes[-self.lookback - 1]
        rolling_return = (current_close - ref_close) / ref_close

        # Manage existing open long position
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            recent_high = max(highs[-self.lookback:])
            recent_low = min(lows[-self.lookback:])
            midpoint = (recent_high + recent_low) / 2.0

            # Exit condition 1: reached midpoint recovery target
            if current_close >= midpoint:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "midpoint_recovery_reached",
                        "midpoint": midpoint,
                        "close": current_close,
                        "bars_held": bars_held
                    }
                )

            # Exit condition 2: time-based holding limit reached
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "time_stop_expired",
                        "bars_held": bars_held,
                        "close": current_close
                    }
                )
            return None

        # Check cooldown to prevent overtrading
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Flush setup: price dropped >= 5.0% over 24 bars
        if rolling_return <= self.drop_thresh:
            prev_high = highs[-2]
            # Capitulation trigger: closing back above previous bar's high
            if current_close > prev_high:
                # Confirmation: oversold or extreme fear regime
                rsi = self._rsi(closes, period=14)
                fgi = ctx.features.get("fear_greed_index", 50)
                market_regime = ctx.market.get("regime", "NORMAL")

                # Filter out calm markets where drops lack capitulatory volatility
                if market_regime == "CALM":
                    return None

                # Require oversold confirmation via RSI or Fear & Greed sentiment
                is_oversold = (rsi is not None and rsi <= 38.0) or fgi <= 35

                if is_oversold:
                    self.entry_bar = ctx.bar_index
                    conf = 0.75 if fgi <= 25 else 0.65
                    return ctx.signal(
                        "long",
                        confidence=conf,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "crash_flush_reversal_confirmed",
                            "rolling_return_24h": rolling_return,
                            "prev_high": prev_high,
                            "close": current_close,
                            "rsi": rsi,
                            "fear_greed": fgi,
                            "market_regime": market_regime
                        }
                    )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index