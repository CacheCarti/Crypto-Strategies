from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthPanicRecovery(Strategy):
    METADATA = {
        "name": "EthPanicRecovery",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 360.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.min_drop_pct = 0.05
        self.cooldown_bars = 16
        self.last_exit_bar = -100
        self.target_exit_price = None

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
        self.target_exit_price = None

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)
        if len(closes) < self.lookback + 1:
            return None

        current_close = closes[-1]

        # Manage existing long position: check if midpoint target achieved
        if ctx.has_position():
            if ctx.position_direction() == "long" and self.target_exit_price is not None:
                if current_close >= self.target_exit_price:
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "flush_midpoint_target_reached",
                            "exit_target": self.target_exit_price,
                            "close": current_close,
                        },
                    )
            return None

        # Cooldown guard after trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Avoid entries in unrecoverable crisis / meltdown regimes
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        recent_high = max(highs[-self.lookback:])
        recent_low = min(lows[-self.lookback:])
        if recent_high <= 0:
            return None

        drop_pct = (recent_high - current_close) / recent_high

        # Primary condition: genuine severe drop >= 5% over window
        if drop_pct < self.min_drop_pct:
            return None

        # Stabilization trigger: current close clears prior bar's high
        prev_high = highs[-2]
        if current_close <= prev_high:
            return None

        # Deep oversold / exhaustion confirmation to reject marginal shallow pullbacks
        rsi_val = self._rsi(closes, period=14)
        if rsi_val is None or rsi_val > 38.0:
            return None

        # Target midpoint of the flush range
        midpoint = (recent_high + recent_low) / 2.0
        if midpoint <= current_close:
            return None

        self.target_exit_price = midpoint
        potential_gain_bps = ((midpoint - current_close) / current_close) * 10000.0
        tp_bps = max(180.0, min(potential_gain_bps, 450.0))
        sl_bps = 280.0

        confidence = min(0.9, max(0.6, drop_pct * 10.0))

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=sl_bps,
            take_profit_bps=tp_bps,
            horizon_seconds=21600,
            metadata={
                "reason": "panic_flush_reversal_above_prior_high",
                "drop_pct": drop_pct,
                "rsi": rsi_val,
                "midpoint_target": midpoint,
                "recent_high": recent_high,
                "recent_low": recent_low,
                "regime": market_regime,
            },
        )