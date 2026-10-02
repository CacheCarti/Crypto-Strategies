from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any


class BtcRsiSnapbackScalp(Strategy):
    METADATA = {
        "name": "BTC RSI Snapback Scalp",
        "domain": "btc_usdc_scalp",
        "declared_sl_bps": 90.0,
        "declared_tp_bps": 160.0,
        "declared_hold_seconds": 1800,
        "warmup_bars": 30,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 7
        self.oversold_thresh = 15.0
        self.overbought_thresh = 85.0
        self.cooldown_bars = 36
        self.max_hold_bars = 10
        self.last_exit_bar = -100
        self.entry_bar = -100

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
        closes = ctx.closes(self.rsi_period + 5)
        if len(closes) < self.rsi_period + 1:
            return None

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        bar = ctx.bar
        pos_dir = ctx.position_direction()

        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar

            # Exit long when RSI normalizes to midpoint zone or time stop reached
            if pos_dir == "long":
                if rsi >= 50.0 or bars_held >= self.max_hold_bars:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "rsi_midpoint_long_exit" if rsi >= 50.0 else "max_hold_timeout",
                            "rsi": round(rsi, 2),
                            "bars_held": bars_held,
                        },
                    )

            # Exit short when RSI normalizes to midpoint zone or time stop reached
            elif pos_dir == "short":
                if rsi <= 50.0 or bars_held >= self.max_hold_bars:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "rsi_midpoint_short_exit" if rsi <= 50.0 else "max_hold_timeout",
                            "rsi": round(rsi, 2),
                            "bars_held": bars_held,
                        },
                    )
            return None

        # Mandatory multi-bar cooldown guard to eliminate overtrading
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Long entry: deep oversold exhaustion with a confirming green bar
        if rsi <= self.oversold_thresh and bar.close > bar.open:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.8,
                stop_loss_bps=90.0,
                take_profit_bps=160.0,
                horizon_seconds=1800,
                metadata={
                    "reason": "extreme_rsi_oversold_bounce",
                    "rsi": round(rsi, 2),
                    "close": bar.close,
                    "open": bar.open,
                },
            )

        # Short entry: extreme overbought exhaustion with a confirming red bar
        if rsi >= self.overbought_thresh and bar.close < bar.open:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.8,
                stop_loss_bps=90.0,
                take_profit_bps=160.0,
                horizon_seconds=1800,
                metadata={
                    "reason": "extreme_rsi_overbought_reversal",
                    "rsi": round(rsi, 2),
                    "close": bar.close,
                    "open": bar.open,
                },
            )

        return None