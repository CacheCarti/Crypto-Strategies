from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class SolPanicRecovery(Strategy):
    METADATA = {
        "name": "SolPanicRecovery",
        "domain": "sol_usdc",
        "declared_sl_bps": 380.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 30,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 20
        self.rsi_period = 14
        self.cooldown_bars = 5
        self.last_trade_bar = -100
        self.target_midpoint = 0.0
        self.panic_low = 0.0

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
        closes = ctx.closes(self.lookback + 10)
        highs = ctx.highs(self.lookback + 10)
        lows = ctx.lows(self.lookback + 10)

        if len(closes) < self.lookback + 5:
            return None

        current_close = ctx.bar.close
        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        # Position exit management
        if ctx.has_position():
            # Exit on reaching the flush midpoint target or strong overbought level
            if self.target_midpoint > 0 and current_close >= self.target_midpoint:
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "flush_midpoint_target_reached",
                        "close": current_close,
                        "target_midpoint": round(self.target_midpoint, 2),
                        "rsi": round(rsi, 2),
                    },
                )
            if rsi >= 70.0:
                return ctx.signal(
                    "flat",
                    confidence=0.70,
                    metadata={
                        "reason": "rsi_overbought_exit",
                        "close": current_close,
                        "rsi": round(rsi, 2),
                    },
                )
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Rolling lookback high/low
        h_slice = highs[-self.lookback:]
        l_slice = lows[-self.lookback:]

        max_high = max(h_slice)
        min_low = min(l_slice)

        if max_high <= 0:
            return None

        flush_depth_pct = (max_high - min_low) / max_high
        prev_bar_high = highs[-2]
        prev_bar_close = closes[-2]

        # Loosened, robust entry conditions:
        # 1. Flush of >= 4.0% over recent window
        # 2. Stabilization bar: close above prior bar's high OR close above prior close with bullish candle
        # 3. RSI not already severely overbought (< 58.0)
        is_flush = flush_depth_pct >= 0.040
        is_stabilizing = (current_close > prev_bar_high) or (
            current_close > prev_bar_close and ctx.bar.close > ctx.bar.open and current_close > min_low
        )
        is_rsi_valid = rsi < 58.0

        if is_flush and is_stabilizing and is_rsi_valid:
            midpoint = (max_high + min_low) / 2.0
            
            # Dynamic SL based on panic low distance with safety bounds
            dist_to_low_bps = ((current_close - min_low) / current_close) * 10000.0
            sl_bps = min(max(dist_to_low_bps + 40.0, 200.0), 450.0)

            # Dynamic TP targeting the flush recovery
            if midpoint > current_close:
                tp_bps = min(max(((midpoint - current_close) / current_close) * 10000.0, 250.0), 550.0)
            else:
                tp_bps = 350.0

            confidence = min(0.60 + (flush_depth_pct * 2.5), 0.90)

            self.panic_low = min_low
            self.target_midpoint = midpoint
            self.last_trade_bar = ctx.bar_index

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=14400,
                metadata={
                    "reason": "sol_panic_flush_recovery_entry",
                    "flush_depth_pct": round(flush_depth_pct * 100, 2),
                    "panic_high": round(max_high, 2),
                    "panic_low": round(min_low, 2),
                    "target_midpoint": round(midpoint, 2),
                    "rsi": round(rsi, 2),
                    "close": current_close,
                },
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index
        self.target_midpoint = 0.0