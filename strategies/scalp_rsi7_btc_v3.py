from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcRsiSnapbackScalp(Strategy):
    METADATA = {
        "name": "BTC RSI Snapback Scalp",
        "domain": "btc_usdc_scalp",
        "declared_sl_bps": 80.0,
        "declared_tp_bps": 130.0,
        "declared_hold_seconds": 1500,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 8
        self.rsi_oversold = 13.0
        self.rsi_overbought = 87.0
        self.rsi_exit_long = 50.0
        self.rsi_exit_short = 50.0
        self.max_hold_bars = 8
        self.cooldown_bars = 36
        self.last_exit_bar = -999
        self.entry_bar = 0

    def _rsi(self, closes, period: int) -> Optional[float]:
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.rsi_period + 2)
        if len(closes) < self.rsi_period + 1:
            return None

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        bar = ctx.bar
        prev_close = closes[-2]

        # Position management and exit logic
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar

            if pos_dir == "long":
                if rsi >= self.rsi_exit_long:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": "rsi_mean_reversion_long_exit",
                            "rsi": round(rsi, 2),
                            "bars_held": bars_held,
                            "close": bar.close
                        }
                    )
            elif pos_dir == "short":
                if rsi <= self.rsi_exit_short:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": "rsi_mean_reversion_short_exit",
                            "rsi": round(rsi, 2),
                            "bars_held": bars_held,
                            "close": bar.close
                        }
                    )

            if bars_held >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "time_stop_exit",
                        "rsi": round(rsi, 2),
                        "bars_held": bars_held,
                        "close": bar.close
                    }
                )

            return None

        # Mandatory cooldown filter to prevent overtrading and friction drag
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Extreme oversold condition with strong bullish bounce candle
        is_bullish_reversal = (bar.close > bar.open) and (bar.close > prev_close)
        if rsi <= self.rsi_oversold and is_bullish_reversal:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.85,
                stop_loss_bps=80.0,
                take_profit_bps=130.0,
                horizon_seconds=1500,
                metadata={
                    "reason": "extreme_rsi_oversold_bounce",
                    "rsi": round(rsi, 2),
                    "close": bar.close,
                    "open": bar.open,
                    "prev_close": prev_close
                }
            )

        # Extreme overbought condition with strong bearish rejection candle
        is_bearish_reversal = (bar.close < bar.open) and (bar.close < prev_close)
        if rsi >= self.rsi_overbought and is_bearish_reversal:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.85,
                stop_loss_bps=80.0,
                take_profit_bps=130.0,
                horizon_seconds=1500,
                metadata={
                    "reason": "extreme_rsi_overbought_rejection",
                    "rsi": round(rsi, 2),
                    "close": bar.close,
                    "open": bar.open,
                    "prev_close": prev_close
                }
            )

        return None