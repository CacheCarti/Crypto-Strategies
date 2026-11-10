from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthPanicRecovery(Strategy):
    METADATA = {
        "name": "EthPanicRecovery",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 350.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.drop_thresh_pct = -0.045
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.entry_price = 0.0
        self.target_midpoint = 0.0
        self.flush_low = 0.0

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
        closes = ctx.closes(self.lookback + 5)
        highs = ctx.highs(self.lookback + 5)
        lows = ctx.lows(self.lookback + 5)

        if len(closes) < self.lookback + 2:
            return None

        current_idx = ctx.bar_index
        c_price = ctx.bar.close

        # Manage existing long position
        if ctx.has_position():
            bars_held = current_idx - self.entry_bar
            # Midpoint mean-reversion exit or max hold time safeguard
            if c_price >= self.target_midpoint or bars_held >= 12:
                exit_reason = "target_midpoint_reached" if c_price >= self.target_midpoint else "time_limit_reached"
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": exit_reason,
                        "close": c_price,
                        "target_midpoint": self.target_midpoint,
                        "bars_held": bars_held
                    }
                )
            return None

        # Cooldown guard after prior trade
        if (current_idx - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Check market regime to avoid entering in continuous collapse/meltdown
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime == "MELTDOWN":
            return None

        # Calculate 24-bar drop
        start_price = closes[-self.lookback]
        recent_low = min(lows[-self.lookback:])
        drop_pct = (recent_low - start_price) / start_price

        # Must have dropped at least 4.5% over the window
        if drop_pct > self.drop_thresh_pct:
            return None

        # Stabilization confirmation: current close breaks above previous bar's high
        prev_high = highs[-2]
        is_stabilized = ctx.bar.close > prev_high

        if not is_stabilized:
            return None

        # Additional momentum sanity: RSI must not be overbought
        rsi = self._rsi(closes, 14)
        if rsi is None or rsi > 58.0:
            return None

        # Midpoint of the flush
        local_high = max(highs[-self.lookback:])
        self.flush_low = recent_low
        self.target_midpoint = (local_high + self.flush_low) * 0.5

        # Ensure reasonable room to midpoint (at least 70 bps)
        potential_bps = ((self.target_midpoint - c_price) / c_price) * 10000.0
        if potential_bps < 70.0:
            return None

        self.entry_bar = current_idx
        self.entry_price = c_price

        # Dynamic stop-loss placed just below the flush low
        risk_bps = ((c_price - self.flush_low) / c_price) * 10000.0
        sl_bps = max(180.0, min(risk_bps + 40.0, 320.0))
        tp_bps = max(200.0, min(potential_bps * 1.05, 450.0))

        confidence = 0.65 if rsi < 40.0 else 0.55

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=sl_bps,
            take_profit_bps=tp_bps,
            horizon_seconds=18000,
            metadata={
                "reason": "flush_stabilization_rebound",
                "drop_pct": drop_pct,
                "rsi": rsi,
                "target_midpoint": self.target_midpoint,
                "flush_low": self.flush_low,
                "sl_bps": sl_bps,
                "tp_bps": tp_bps
            }
        )

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_bar = -999
        self.entry_price = 0.0
        self.target_midpoint = 0.0
        self.flush_low = 0.0