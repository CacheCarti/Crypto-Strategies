from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolLiquidationCascadeReversal(Strategy):
    METADATA = {
        "name": "SOL Liquidation Cascade Reversal",
        "domain": "sol_usdc",
        "declared_sl_bps": 260.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.atr_period = 20
        self.vol_period = 20
        self.range_mult = 1.35
        self.vol_mult = 1.30
        self.cooldown_bars = 4
        self.last_trade_bar = -999
        self.armed_flush = False
        self.flush_bar_idx = -999
        self.flush_low = 0.0
        self.flush_close = 0.0

    def _calc_atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _calc_sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _calc_rsi(self, closes, period=14):
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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index
        self.armed_flush = False

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.atr_period + 5)
        highs = ctx.highs(self.atr_period + 5)
        lows = ctx.lows(self.atr_period + 5)
        volumes = ctx.volumes(self.vol_period + 5)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_bar = ctx.bar
        current_idx = ctx.bar_index
        atr = self._calc_atr(highs, lows, closes, self.atr_period)
        avg_vol = self._calc_sma(volumes, self.vol_period)
        rsi = self._calc_rsi(closes, 14)

        if atr is None or avg_vol is None or atr <= 0.0 or avg_vol <= 0.0:
            return None

        # Manage open position exit
        if ctx.has_position():
            if rsi > 72.0:
                return ctx.signal(
                    "flat",
                    confidence=0.65,
                    metadata={
                        "reason": "rsi_overbought_exit",
                        "rsi": round(rsi, 2),
                        "close": round(current_bar.close, 3),
                    }
                )
            return None

        # Cooldown guard
        if (current_idx - self.last_trade_bar) < self.cooldown_bars:
            return None

        bar_range = current_bar.high - current_bar.low
        is_bearish = current_bar.close < current_bar.open
        close_dist_pct = (current_bar.close - current_bar.low) / bar_range if bar_range > 0 else 0.5

        # Cascade flush condition: enlarged range and elevated volume on down move or dip
        is_flush_bar = (
            bar_range >= (self.range_mult * atr)
            and current_bar.volume >= (self.vol_mult * avg_vol)
            and (is_bearish or close_dist_pct <= 0.50 or rsi < 38.0)
        )

        if is_flush_bar:
            self.armed_flush = True
            self.flush_bar_idx = current_idx
            self.flush_low = current_bar.low
            self.flush_close = current_bar.close

            # Immediate hammer / strong absorption bounce entry
            if close_dist_pct >= 0.40 and current_bar.close > current_bar.low:
                self.armed_flush = False
                self.last_trade_bar = current_idx
                return ctx.signal(
                    "long",
                    confidence=0.72,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "cascade_absorption_pinbar",
                        "rsi": round(rsi, 2),
                        "bar_range": round(bar_range, 3),
                        "atr": round(atr, 3),
                        "vol_ratio": round(current_bar.volume / avg_vol, 2),
                    }
                )
            return None

        # Rebound / exhaustion confirmation on subsequent 1-3 bars
        if self.armed_flush:
            bars_since_flush = current_idx - self.flush_bar_idx

            if 1 <= bars_since_flush <= 3:
                # Holds above flush low or shows green recovery
                held_low = current_bar.low >= (self.flush_low * 0.992)
                recovering = current_bar.close > current_bar.open or current_bar.close > self.flush_close

                if held_low and recovering:
                    self.armed_flush = False
                    self.last_trade_bar = current_idx

                    fg = ctx.features.get("fear_greed_index", 50.0)
                    confidence = 0.70
                    if fg < 40.0:
                        confidence += 0.08

                    return ctx.signal(
                        "long",
                        confidence=min(confidence, 0.90),
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "liquidation_cascade_exhaustion_entry",
                            "bars_since_flush": bars_since_flush,
                            "flush_low": round(self.flush_low, 3),
                            "close": round(current_bar.close, 3),
                            "rsi": round(rsi, 2),
                            "fear_greed": fg,
                        }
                    )
            else:
                self.armed_flush = False

        return None