from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcPanicRecovery(Strategy):
    METADATA = {
        "name": "BtcPanicRecovery",
        "domain": "btc_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 28800,  # ~8 hours target hold
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.flush_drop_pct = 0.045     # 4.5% drop over 24 bars (~1 day)
        self.cooldown_bars = 6          # mandatory cooldown between trades
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 16         # max 16 1h bars hold
        self.target_midpoint = 0.0

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return 50.0
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
        closes = ctx.closes(self.lookback + 5)
        highs = ctx.highs(self.lookback + 5)
        lows = ctx.lows(self.lookback + 5)

        if len(closes) < self.lookback + 2:
            return None

        current_close = ctx.bar.close
        current_bar = ctx.bar_index

        # Position management
        if ctx.has_position():
            bars_in_trade = current_bar - self.entry_bar
            
            # Midpoint take profit exit
            if self.target_midpoint > 0.0 and current_close >= self.target_midpoint:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "flush_midpoint_reached",
                        "close": current_close,
                        "target_midpoint": self.target_midpoint,
                        "bars_held": bars_in_trade
                    }
                )

            # Max duration time-stop
            if bars_in_trade >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "max_hold_expired",
                        "close": current_close,
                        "bars_held": bars_in_trade
                    }
                )
            return None

        # Cooldown guard after recent exit
        if current_bar - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime Gate: Prior failures in crash_recovery family suffered from entering ongoing meltdowns.
        # Filter: Avoid Meltdown, ensure sentiment reflects extreme fear (where recovery bounce has edge).
        regime = ctx.market.get("regime", "NORMAL")
        if regime == "MELTDOWN":
            return None

        fear_greed = ctx.features.get("fear_greed_index", 50.0)
        if fear_greed > 45.0:
            return None

        # Check 24-bar drop (from max high in the window to current previous close)
        window_high = max(highs[-self.lookback:])
        prev_close = closes[-2]
        drop = (window_high - prev_close) / window_high

        # 1. Significant drop must be present
        if drop < self.flush_drop_pct:
            return None

        # 2. Capitulation bounce confirmation: current bar closes above previous bar's high
        prev_high = highs[-2]
        if current_close <= prev_high:
            return None

        # 3. RSI check: deeply oversold bounce (RSI < 40)
        rsi = self._rsi(closes, period=14)
        if rsi > 42.0:
            return None

        # Valid setup: set midpoint target of the flush leg
        window_low = min(lows[-self.lookback:])
        self.target_midpoint = (window_high + window_low) / 2.0
        self.entry_bar = current_bar

        # Confidence scaled by magnitude of drop and fear
        conf = min(0.9, 0.5 + (drop * 4.0))

        return ctx.signal(
            "long",
            confidence=conf,
            stop_loss_bps=self.METADATA["declared_sl_bps"],
            take_profit_bps=self.METADATA["declared_tp_bps"],
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={
                "reason": "capitulation_engulf_bounce",
                "drop_pct": round(drop * 100, 2),
                "rsi": round(rsi, 2),
                "fear_greed": fear_greed,
                "prev_high": prev_high,
                "target_midpoint": round(self.target_midpoint, 2),
                "close": current_close
            }
        )

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.target_midpoint = 0.0