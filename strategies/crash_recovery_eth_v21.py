from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthPanicFlushRecovery(Strategy):
    METADATA = {
        "name": "EthPanicFlushRecovery",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 21600,  # ~6 hours target recovery swing
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_bars = 24
        self.min_flush_pct = 0.05
        self.cooldown_period = 12
        self.last_exit_bar = -999
        self.flush_high = 0.0
        self.flush_low = 0.0
        self.target_exit_price = 0.0

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
        self.target_exit_price = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_bars + 2)
        highs = ctx.highs(self.lookback_bars + 2)
        lows = ctx.lows(self.lookback_bars + 2)

        if len(closes) < self.lookback_bars + 2:
            return None

        # Position Management
        if ctx.has_position():
            # Check midpoint target take profit or market regime breakdown
            if self.target_exit_price > 0.0 and ctx.bar.close >= self.target_exit_price:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "flush_midpoint_rebound_reached",
                        "close": ctx.bar.close,
                        "target_exit_price": self.target_exit_price,
                    },
                )
            return None

        # Post-trade cooldown
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_period:
            return None

        # Severe flush detection over last 24 bars
        window_high = max(highs[-(self.lookback_bars + 1):-1])
        recent_low = min(lows[-self.lookback_bars:])
        if window_high <= 0.0:
            return None

        drop_pct = (window_high - recent_low) / window_high
        if drop_pct < self.min_flush_pct:
            return None

        # Stabilization confirmation: current close breaks strictly above previous bar high
        prev_high = highs[-2]
        curr_close = ctx.bar.close
        stabilized = curr_close > prev_high

        if not stabilized:
            return None

        # Additional quality filter: RSI must show extreme oversold relief (washed out momentum)
        rsi_val = self._rsi(closes, period=14)
        if rsi_val is None or rsi_val > 44.0:
            return None

        # Macro fear filter: flush is authentic panic if sentiment isn't exuberant
        fear_greed = ctx.features.get("fear_greed_index", 50)
        if fear_greed > 55:
            return None

        # Calculate flush range midpoint target
        self.flush_high = window_high
        self.flush_low = recent_low
        flush_range = self.flush_high - self.flush_low
        self.target_exit_price = self.flush_low + (flush_range * 0.50)

        # Ensure realistic target gap exists
        if self.target_exit_price <= curr_close:
            return None

        # Dynamic confidence based on magnitude of drop
        confidence = min(0.95, max(0.55, 0.55 + (drop_pct - 0.05) * 5.0))

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=280.0,
            take_profit_bps=340.0,
            horizon_seconds=21600,
            metadata={
                "reason": "panic_drop_stabilization_rebound",
                "drop_pct": round(drop_pct, 4),
                "rsi": round(rsi_val, 2),
                "fear_greed": fear_greed,
                "target_exit": round(self.target_exit_price, 2),
                "curr_close": curr_close,
                "prev_high": prev_high,
            },
        )