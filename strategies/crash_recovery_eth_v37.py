from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthPanicFlushRecovery(Strategy):
    METADATA = {
        "name": "EthPanicFlushRecovery",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 360.0,
        "declared_hold_seconds": 21600,  # ~6 hours hold horizon
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.drop_thresh = -0.045
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.entry_price = 0.0
        self.target_midpoint = 0.0
        self.highest_pre_flush = 0.0
        self.lowest_flush = 0.0

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
        self.entry_price = 0.0
        self.target_midpoint = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        # Manage open position
        if ctx.has_position():
            curr_price = ctx.bar.close
            # Dynamic midpoint or target take-profit
            if self.target_midpoint > 0.0 and curr_price >= self.target_midpoint:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "flush_midpoint_rebound_reached",
                        "target_midpoint": round(self.target_midpoint, 2),
                        "price": round(curr_price, 2),
                    },
                )
            return None

        # Mandatory cooldown check
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Flush calculation over the lookback window
        window_high = max(highs[-self.lookback:])
        current_close = closes[-1]
        prior_high = highs[-2]
        prior_close = closes[-2]

        drop_pct = (current_close - window_high) / window_high

        # 1. Look for significant flush (-4.5% or worse from recent high)
        if drop_pct > self.drop_thresh:
            return None

        # 2. Stabilization trigger: current bar closes above previous bar's high
        stabilization = (current_close > prior_high) and (current_close > prior_close)
        if not stabilization:
            return None

        # 3. Filter: RSI confirmation of oversold turning up (avoid falling knives without momentum shift)
        rsi = self._rsi(closes, 14)
        if rsi is None or rsi > 46.0:
            return None

        # 4. Filter: Avoid structural meltdown regimes
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime == "MELTDOWN":
            return None

        # Calculate bounce target (midpoint of the flush range)
        window_low = min(lows[-self.lookback:])
        flush_range = window_high - window_low
        midpoint = window_low + (flush_range * 0.50)

        # Ensure reward to midpoint is at least 80 bps to clear friction
        if (midpoint - current_close) / current_close < 0.0080:
            return None

        self.highest_pre_flush = window_high
        self.lowest_flush = window_low
        self.target_midpoint = midpoint
        self.entry_price = current_close

        confidence = 0.75 if drop_pct < -0.065 else 0.65

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=self.METADATA["declared_sl_bps"],
            take_profit_bps=self.METADATA["declared_tp_bps"],
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={
                "reason": "panic_flush_stabilization_bounce",
                "drop_pct": round(drop_pct * 100, 2),
                "rsi": round(rsi, 2),
                "flush_high": round(window_high, 2),
                "flush_low": round(window_low, 2),
                "target_midpoint": round(midpoint, 2),
                "regime": market_regime,
            },
        )