from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcPanicRecovery(Strategy):
    METADATA = {
        "name": "BtcPanicRecovery",
        "domain": "btc_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 18000,  # ~5 hours (5 x 1h bars)
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.flush_threshold_pct = -5.5
        self.max_hold_bars = 6
        self.cooldown_period = 8
        self.last_exit_bar = -999
        self.entry_bar_idx = -999
        self.midpoint_target = 0.0

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

        current_close = ctx.bar.close
        current_bar = ctx.bar_index

        # Position Management: exit logic
        if ctx.has_position():
            bars_held = current_bar - self.entry_bar_idx
            
            # Target 1: Midpoint of the prior crash range reached
            if self.midpoint_target > 0.0 and current_close >= self.midpoint_target:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "exit_midpoint_reversion_target",
                        "close": current_close,
                        "midpoint": self.midpoint_target,
                        "bars_held": bars_held,
                    }
                )

            # Target 2: Time-based hold horizon for mean-reversion bounce
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "exit_max_hold_expired",
                        "close": current_close,
                        "bars_held": bars_held,
                    }
                )
            return None

        # Cooldown guard after trade exit
        if current_bar - self.last_exit_bar < self.cooldown_period:
            return None

        # Flush Detection: 24-bar cumulative return
        ref_price = closes[-(self.lookback + 1)]
        prior_close = closes[-2]
        rolling_return_pct = ((prior_close - ref_price) / ref_price) * 100.0

        # Primary Condition: Severe flush occurred
        if rolling_return_pct <= self.flush_threshold_pct:
            prev_high = highs[-2]
            
            # Trigger: First capitulation recovery candle closing above previous bar's high
            if current_close > prev_high and prior_close <= prev_high:
                high_24 = max(highs[-(self.lookback + 1):-1])
                low_24 = min(lows[-(self.lookback + 1):-1])
                
                # Calculate recovery midpoint target
                self.midpoint_target = low_24 + 0.5 * (high_24 - low_24)
                self.entry_bar_idx = current_bar

                rsi = self._rsi(closes, period=14)
                fgi = ctx.features.get("fear_greed_index", 50.0)

                # Confidence scaling: stronger recovery conviction if heavily oversold
                conf = 0.70
                if rsi < 30.0:
                    conf += 0.15
                if fgi < 25.0:
                    conf += 0.10

                return ctx.signal(
                    "long",
                    confidence=min(conf, 1.0),
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "flush_capitulation_breakout",
                        "drop_pct": rolling_return_pct,
                        "midpoint_target": self.midpoint_target,
                        "rsi": round(rsi, 2),
                        "fear_greed": fgi,
                        "close": current_close,
                        "prev_high": prev_high,
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_bar_idx = -999
        self.midpoint_target = 0.0