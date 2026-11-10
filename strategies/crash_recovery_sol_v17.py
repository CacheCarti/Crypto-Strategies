from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPanicRecovery(Strategy):
    METADATA = {
        "name": "SolPanicRecovery",
        "domain": "sol_usdc",
        "declared_sl_bps": 450.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 18000,  # 5 hours target hold on 1h bars
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.drop_thresh = -0.07  # -7% flush over 24h
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.target_price = 0.0
        self.stop_price = 0.0

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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.target_price = 0.0
        self.stop_price = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 1:
            return None

        # Position management
        if ctx.has_position():
            curr_price = ctx.bar.close
            bars_held = ctx.bar_index - self.entry_bar
            
            # Midpoint target reached or stop breached or time limit exceeded
            if (self.target_price > 0.0 and curr_price >= self.target_price) or \
               (self.stop_price > 0.0 and curr_price <= self.stop_price) or \
               bars_held >= 12:
                reason = "take_profit_midpoint" if (self.target_price > 0 and curr_price >= self.target_price) else \
                         ("stop_loss_low" if (self.stop_price > 0 and curr_price <= self.stop_price) else "time_horizon_exit")
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": reason, "curr_price": curr_price, "target": self.target_price, "stop": self.stop_price, "bars_held": bars_held}
                )
            return None

        # Cooldown guard after trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime gate: Do not catch knives in ongoing crisis/meltdown
        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if market_regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.75:
            return None

        # Panic flush calculation over last 24 bars
        prev_close_24 = closes[-self.lookback - 1]
        last_close = closes[-2]
        rolling_return = (last_close - prev_close_24) / prev_close_24

        if rolling_return > self.drop_thresh:
            return None

        # Confirmation: First stabilization bar closing above previous bar's high
        prev_high = highs[-2]
        curr_close = ctx.bar.close
        if curr_close <= prev_high:
            return None

        # Additional exhaustion filter: RSI was deeply oversold recently
        rsi_val = self._rsi(closes, period=14)
        if rsi_val is None or rsi_val > 45.0:
            return None

        # Structural levels: flush range high, low, and midpoint target
        recent_high = max(highs[-self.lookback:])
        recent_low = min(lows[-self.lookback:])
        midpoint = (recent_high + recent_low) / 2.0

        if midpoint <= curr_close:
            return None

        # Set trade execution parameters
        self.entry_bar = ctx.bar_index
        self.target_price = midpoint
        # Place stop just below the flush low
        self.stop_price = recent_low * 0.992

        stop_distance_bps = max(100.0, min(600.0, ((curr_close - self.stop_price) / curr_close) * 10000.0))
        target_distance_bps = max(150.0, min(800.0, ((self.target_price - curr_close) / curr_close) * 10000.0))

        confidence = 0.75
        fg = ctx.features.get("fear_greed_index", 50)
        if fg < 25:
            confidence = 0.85

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=stop_distance_bps,
            take_profit_bps=target_distance_bps,
            horizon_seconds=18000,
            metadata={
                "reason": "sol_panic_flush_reversal_confirmation",
                "rolling_return_24": rolling_return,
                "rsi": rsi_val,
                "midpoint_target": midpoint,
                "stop_price": self.stop_price,
                "fear_greed": fg
            }
        )