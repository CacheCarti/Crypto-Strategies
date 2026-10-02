from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any


class PanicRecoveryFlush(Strategy):
    METADATA = {
        "name": "PanicRecoveryFlush",
        "domain": "btc_usdc",
        "declared_sl_bps": 260.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 14400,  # 4 hours
        "warmup_bars": 30,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_bars = 20
        self.min_flush_pct = 0.022  # 2.2% drop from recent high in lookback
        self.cooldown_bars = 4
        self.max_hold_bars = 10
        self.sl_bps = 260.0
        self.tp_bps = 420.0
        self.hold_seconds = 14400

        self.last_exit_bar = -999
        self.entry_bar = -999
        self.target_midpoint = 0.0

    def _rsi(self, closes: list, period: int = 14) -> Optional[float]:
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
        self.target_midpoint = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_bars + 5)
        highs = ctx.highs(self.lookback_bars + 5)
        lows = ctx.lows(self.lookback_bars + 5)

        if len(closes) < self.lookback_bars + 2:
            return None

        curr_close = ctx.bar.close
        curr_open = ctx.bar.open
        prev_high = highs[-2]
        prev_close = closes[-2]

        rsi_val = self._rsi(closes, 14)
        if rsi_val is None:
            rsi_val = 50.0

        # Position Management & Exit Rules
        if ctx.has_position():
            bars_in_pos = ctx.bar_index - self.entry_bar

            # Midpoint profit target hit
            if self.target_midpoint > 0 and curr_close >= self.target_midpoint:
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "flush_midpoint_target_hit",
                        "close": curr_close,
                        "midpoint": round(self.target_midpoint, 2),
                        "bars_held": bars_in_pos,
                    },
                )

            # RSI overbought bounce exit
            if rsi_val >= 68.0:
                return ctx.signal(
                    "flat",
                    confidence=0.65,
                    metadata={
                        "reason": "recovery_rsi_overbought",
                        "rsi": round(rsi_val, 2),
                        "close": curr_close,
                        "bars_held": bars_in_pos,
                    },
                )

            # Max hold duration exit
            if bars_in_pos >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.50,
                    metadata={
                        "reason": "max_hold_duration_reached",
                        "close": curr_close,
                        "bars_held": bars_in_pos,
                    },
                )

            return None

        # Cooldown check
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Flush calculation: drop from the peak within the lookback window
        window_highs = highs[-self.lookback_bars:]
        window_lows = lows[-self.lookback_bars:]
        peak_high = max(window_highs)
        trough_low = min(window_lows)

        if peak_high <= 0:
            return None

        # Check total drop depth from peak to recent trough/close
        flush_depth = (peak_high - trough_low) / peak_high

        # Entry conditions:
        # 1. Lookback experienced a significant drop (>= min_flush_pct)
        # 2. Reversal confirmation: green bar closing above prev high OR strong green engulfing prev close
        # 3. RSI not already overbought (<= 60)
        reversal_candle = (curr_close > curr_open) and (curr_close > prev_high or (curr_close > prev_close * 1.004))
        
        if flush_depth >= self.min_flush_pct and reversal_candle and rsi_val <= 60.0:
            midpoint = (peak_high + trough_low) / 2.0
            self.target_midpoint = midpoint if midpoint > curr_close else curr_close * 1.025
            self.entry_bar = ctx.bar_index

            confidence = min(0.85, 0.55 + (flush_depth - self.min_flush_pct) * 5.0)

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.sl_bps,
                take_profit_bps=self.tp_bps,
                horizon_seconds=self.hold_seconds,
                metadata={
                    "reason": "flush_capitulation_reversal",
                    "flush_depth_pct": round(flush_depth * 100.0, 2),
                    "peak_high": round(peak_high, 2),
                    "trough_low": round(trough_low, 2),
                    "target_midpoint": round(self.target_midpoint, 2),
                    "rsi": round(rsi_val, 2),
                    "close": curr_close,
                },
            )

        return None