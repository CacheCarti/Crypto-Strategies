from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CascadeExhaustionBottom(Strategy):
    METADATA = {
        "name": "CascadeExhaustionBottom",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.atr_period = 18
        self.vol_period = 18
        self.rsi_period = 14
        self.range_mult = 2.2
        self.vol_mult = 2.0
        self.cooldown_bars = 6
        self.last_trade_bar = -999
        self.pending_panic: Optional[Dict[str, float]] = None

    def _atr(self, highs, lows, closes, period: int) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _sma(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _rsi(self, closes, period: int = 14) -> Optional[float]:
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
        self.last_trade_bar = ctx.bar_index
        self.pending_panic = None

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(40)
        highs = ctx.highs(40)
        lows = ctx.lows(40)
        volumes = ctx.volumes(40)

        atr = self._atr(highs, lows, closes, self.atr_period)
        vol_sma = self._sma(volumes, self.vol_period)
        rsi = self._rsi(closes, self.rsi_period)

        if atr is None or vol_sma is None or rsi is None or vol_sma <= 0:
            return None

        current_close = ctx.bar.close
        current_open = ctx.bar.open
        current_high = ctx.bar.high
        current_low = ctx.bar.low
        current_vol = ctx.bar.volume
        fear_greed = ctx.features.get("fear_greed_index", 50.0)

        # 1. Manage active position exit via exhaustion target or momentum rollover
        if ctx.has_position():
            if ctx.position_direction() == "long":
                if rsi > 72.0:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "rsi_overbought_exhaustion_exit",
                            "rsi": round(rsi, 2),
                            "price": round(current_close, 2),
                        },
                    )
            return None

        # 2. Check Cooldown
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            self.pending_panic = None
            return None

        # 3. Check for Confirmation on the bar immediately following a panic bar
        if self.pending_panic is not None:
            panic = self.pending_panic
            if ctx.bar_index == int(panic["bar_index"]) + 1:
                panic_low = panic["low"]
                # Holds the panic low (does not make a lower low, buffer of 0.2%)
                if current_low >= panic_low * 0.998:
                    # Confirmation bounce: bar closes green or above panic close
                    if current_close > current_open or current_close > panic["close"]:
                        sl_dist = current_close - panic_low
                        sl_bps = max(150.0, min(350.0, (sl_dist / current_close) * 10000.0 + 30.0))
                        tp_bps = sl_bps * 2.0

                        conf = 0.65
                        if rsi < 35.0:
                            conf += 0.15
                        if fear_greed < 30.0:
                            conf += 0.10
                        conf = min(0.95, conf)

                        self.last_trade_bar = ctx.bar_index
                        self.pending_panic = None

                        return ctx.signal(
                            "long",
                            confidence=round(conf, 2),
                            stop_loss_bps=round(sl_bps, 1),
                            take_profit_bps=round(tp_bps, 1),
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "cascade_reversal_confirmed",
                                "panic_low": round(panic_low, 2),
                                "current_close": round(current_close, 2),
                                "rsi": round(rsi, 2),
                                "fear_greed": float(fear_greed),
                                "vol_ratio": round(panic["vol_ratio"], 2),
                                "range_ratio": round(panic["range_ratio"], 2),
                            },
                        )
            self.pending_panic = None

        # 4. Detect New Panic Cascade Bar
        bar_range = current_high - current_low
        bar_drop = current_open - current_close
        vol_ratio = current_vol / vol_sma
        range_ratio = bar_range / atr if atr > 0 else 0.0

        is_bearish = current_close < current_open
        closed_near_low = (current_close - current_low) <= (bar_range * 0.32) if bar_range > 0 else False
        significant_cascade = range_ratio >= self.range_mult and vol_ratio >= self.vol_mult and bar_drop > (atr * 0.8)

        if is_bearish and closed_near_low and significant_cascade:
            self.pending_panic = {
                "bar_index": float(ctx.bar_index),
                "low": current_low,
                "high": current_high,
                "close": current_close,
                "vol_ratio": vol_ratio,
                "range_ratio": range_ratio,
            }

        return None