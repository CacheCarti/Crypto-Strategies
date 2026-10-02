from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPanicRecovery(Strategy):
    METADATA = {
        "name": "SolPanicRecovery",
        "domain": "sol_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 25,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 18
        self.min_drop_pct = 0.038
        self.cooldown_bars = 4
        self.last_trade_bar = -999

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
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        current_close = closes[-1]
        prev_high = highs[-2]
        rsi = self._rsi(closes, 14)

        if ctx.has_position():
            # Exit when momentum overheats
            if rsi is not None and rsi > 68.0:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "rsi_rebound_exit", "rsi": round(rsi, 2), "close": current_close}
                )
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Measure flush over recent lookback
        flush_highs = highs[-self.lookback:]
        flush_lows = lows[-self.lookback:]
        max_high = max(flush_highs)
        min_low = min(flush_lows)

        if max_high <= 0:
            return None

        drop_pct = (max_high - min_low) / max_high

        # Entry logic:
        # 1. SOL experienced a rapid flush (>= 3.8% drop within lookback window)
        # 2. Reversal confirmation: current close breaks above previous bar's high
        if drop_pct >= self.min_drop_pct and current_close > prev_high:
            flush_range = max_high - min_low
            target_mid = min_low + (flush_range * 0.5)

            # Target roughly the midpoint of the flush
            tp_bps = max(250.0, min(650.0, ((target_mid - current_close) / current_close) * 10000.0)) if target_mid > current_close else 350.0
            sl_dist = max(current_close - min_low, current_close * 0.018)
            sl_bps = max(220.0, min(480.0, (sl_dist / current_close) * 10000.0))

            fgi = ctx.features.get("fear_greed_index", 50.0)
            conf = min(0.90, 0.60 + (drop_pct * 3.0) + (0.05 if fgi < 40.0 else 0.0))

            self.last_trade_bar = ctx.bar_index

            return ctx.signal(
                "long",
                confidence=round(conf, 2),
                stop_loss_bps=round(sl_bps, 1),
                take_profit_bps=round(tp_bps, 1),
                metadata={
                    "reason": "sol_flush_stabilization_long",
                    "drop_pct": round(drop_pct * 100.0, 2),
                    "rsi": round(rsi, 2) if rsi is not None else 0.0,
                    "prev_high": prev_high,
                    "close": current_close,
                    "flush_low": min_low,
                    "target_mid": round(target_mid, 2),
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index