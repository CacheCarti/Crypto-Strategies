from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPanicRecovery(Strategy):
    METADATA = {
        "name": "SolPanicRecovery",
        "domain": "sol_usdc",
        "declared_sl_bps": 450.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 35,
        "required_features": ["btc_return_pct", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.drop_thresh = -0.065
        self.cooldown_bars = 10
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.target_price = 0.0
        self.stop_price = 0.0

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return 50.0
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
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        current_close = ctx.bar.close
        current_low = ctx.bar.low
        prev_high = highs[-2]

        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if current_close >= self.target_price and self.target_price > 0.0:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "reached_rebound_target", "target": self.target_price, "close": current_close}
                )
            if current_close <= self.stop_price and self.stop_price > 0.0:
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={"reason": "panic_low_breached", "stop": self.stop_price, "close": current_close}
                )
            if bars_held >= 24:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "time_stop_rebound_fizzled", "bars_held": bars_held}
                )
            return None

        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        ref_close = closes[-self.lookback - 1]
        rolling_return = (current_close - ref_close) / ref_close

        if rolling_return <= self.drop_thresh:
            stabilization = current_close > prev_high
            btc_ret = ctx.features.get("btc_return_pct", 0.0)
            btc_stabilizing = btc_ret > -3.5

            rsi_val = self._rsi(closes, period=14)
            rsi_oversold_or_hooking = rsi_val < 38.0

            if stabilization and btc_stabilizing and rsi_oversold_or_hooking:
                flush_high = max(highs[-self.lookback:])
                flush_low = min(lows[-self.lookback:])
                rebound_target = (flush_high + flush_low) / 2.0

                sl_distance_bps = max(((current_close - flush_low) / current_close) * 10000.0, 200.0)
                sl_distance_bps = min(sl_distance_bps, 600.0)

                tp_distance_bps = max(((rebound_target - current_close) / current_close) * 10000.0, 300.0)
                tp_distance_bps = min(tp_distance_bps, 750.0)

                self.entry_bar = ctx.bar_index
                self.target_price = rebound_target
                self.stop_price = flush_low

                confidence = 0.75 if btc_ret >= 0.0 else 0.65

                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=sl_distance_bps,
                    take_profit_bps=tp_distance_bps,
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "sol_flush_reversal_cross_asset_confirmed",
                        "rolling_return": rolling_return,
                        "btc_ret": btc_ret,
                        "rsi": rsi_val,
                        "flush_low": flush_low,
                        "rebound_target": rebound_target
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.target_price = 0.0
        self.stop_price = 0.0