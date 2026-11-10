from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcPanicRecovery(Strategy):
    METADATA = {
        "name": "BtcPanicRecovery",
        "domain": "btc_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 28800,  # ~8 hours hold expectation
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.drop_thresh_pct = 5.0      # calibrated from 6.0% to ensure enough valid events over history
        self.cooldown_bars = 12         # wait at least 12 bars (~12 hours) between trades
        self.max_hold_bars = 16         # time stop after 16 bars (~16h)
        self.last_trade_bar = -100
        self.entry_bar = -100
        self.entry_price = 0.0
        self.target_price = 0.0

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 1:
            return None

        # Position Management
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            current_close = ctx.bar.close

            # Target hit: 24-bar bounce midpoint reached
            if self.target_price > 0 and current_close >= self.target_price:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "midpoint_target_reached",
                        "target_price": self.target_price,
                        "close": current_close,
                        "bars_held": bars_held,
                    },
                )

            # Max hold duration exit
            if bars_held >= self.max_hold_bars:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "max_hold_expired",
                        "close": current_close,
                        "bars_held": bars_held,
                    },
                )

            return None

        # Cooldown guard to avoid clustering entries and dying to friction
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Check market regime to avoid entering in ongoing liquidity meltdown
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime == "MELTDOWN":
            return None

        recent_high = max(highs[-self.lookback:])
        recent_low = min(lows[-self.lookback:])
        curr_close = closes[-1]
        prev_high = highs[-2]

        # Calculate percentage drop over the rolling window
        drop_pct = ((recent_high - curr_close) / recent_high) * 100.0

        # Capitulation reversal trigger:
        # 1. Rolling window saw at least a 5% drop from peak
        # 2. Reversal confirmation: current close breaks back above the prior bar's high
        # 3. Oversold / stretched condition verified via RSI < 42
        rsi_val = self._rsi(closes, period=14)
        if rsi_val is None:
            return None

        fg_index = ctx.features.get("fear_greed_index", 50.0)

        if drop_pct >= self.drop_thresh_pct and curr_close > prev_high and rsi_val < 42.0:
            midpoint = (recent_high + recent_low) / 2.0

            # Midpoint must offer at least meaningful upside above friction
            if midpoint <= curr_close * 1.008:
                return None

            self.entry_bar = ctx.bar_index
            self.entry_price = curr_close
            self.target_price = midpoint

            # Confidence scaled by magnitude of oversold capitulation
            conf = min(0.90, max(0.55, 0.55 + (drop_pct - self.drop_thresh_pct) * 0.04))

            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "capitulation_reversal_long",
                    "drop_pct": round(drop_pct, 2),
                    "rsi": round(rsi_val, 2),
                    "fear_greed": fg_index,
                    "target_midpoint": round(midpoint, 2),
                    "prev_high": round(prev_high, 2),
                },
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index
        self.entry_price = 0.0
        self.target_price = 0.0