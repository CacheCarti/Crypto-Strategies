from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class RsiSnapbackScalp(Strategy):
    METADATA = {
        "name": "BTC RSI Snapback Scalp",
        "domain": "btc_usdc_scalp",
        "declared_sl_bps": 90.0,
        "declared_tp_bps": 180.0,
        "declared_hold_seconds": 900,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 10
        self.oversold_thresh = 18.0
        self.overbought_thresh = 82.0
        self.cooldown_bars = 45
        self.max_hold_bars = 6
        self.last_exit_bar = -999
        self.entry_bar = 0

    def _calculate_rsi(self, closes: list, period: int) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        gains = []
        losses = []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        
        recent_gains = gains[-period:]
        recent_losses = losses[-period:]
        avg_gain = sum(recent_gains) / period
        avg_loss = sum(recent_losses) / period
        
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.rsi_period + 5)
        if len(closes) < self.rsi_period + 1:
            return None

        rsi = self._calculate_rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        current_close = ctx.bar.close
        current_open = ctx.bar.open

        # Position management
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            pos_dir = ctx.position_direction()

            should_exit = False
            exit_reason = ""

            # Target zone exit (mean reversion) or maximum hold time stop
            if 45.0 <= rsi <= 55.0:
                should_exit = True
                exit_reason = "rsi_mean_target_reached"
            elif bars_held >= self.max_hold_bars:
                should_exit = True
                exit_reason = "time_stop_max_hold_bars"

            if should_exit:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": exit_reason,
                        "rsi": round(rsi, 2),
                        "bars_held": bars_held,
                        "close": current_close,
                        "position_direction": pos_dir,
                    }
                )
            return None

        # Hard multi-bar cooldown after exit to eliminate overtrading
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # High-conviction oversold snap-back (Long)
        if rsi <= self.oversold_thresh and current_close > current_open:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.8,
                stop_loss_bps=90.0,
                take_profit_bps=180.0,
                horizon_seconds=900,
                metadata={
                    "reason": "rsi_extreme_oversold_bullish_turn",
                    "rsi": round(rsi, 2),
                    "close": current_close,
                    "open": current_open,
                }
            )

        # High-conviction overbought snap-back (Short)
        if rsi >= self.overbought_thresh and current_close < current_open:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.8,
                stop_loss_bps=90.0,
                take_profit_bps=180.0,
                horizon_seconds=900,
                metadata={
                    "reason": "rsi_extreme_overbought_bearish_turn",
                    "rsi": round(rsi, 2),
                    "close": current_close,
                    "open": current_open,
                }
            )

        return None