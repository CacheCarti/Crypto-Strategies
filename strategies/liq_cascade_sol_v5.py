from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolLiquidationCascadeBottom(Strategy):
    METADATA = {
        "name": "SolLiquidationCascadeBottom",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.atr_period = 18
        self.vol_period = 18
        self.rsi_period = 14
        self.cooldown_bars = 8
        self.last_trade_bar = -100

    def _atr(self, highs: list, lows: list, closes: list, period: int) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _sma(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _rsi(self, closes: list, period: int) -> Optional[float]:
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.atr_period + 5)
        highs = ctx.highs(self.atr_period + 5)
        lows = ctx.lows(self.atr_period + 5)
        opens = ctx.opens(self.atr_period + 5)
        volumes = ctx.volumes(self.vol_period + 5)

        if len(closes) < self.atr_period + 3:
            return None

        # Check existing position management
        if ctx.has_position():
            rsi_val = self._rsi(closes, self.rsi_period)
            if rsi_val is not None and rsi_val >= 68.0:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "exhaustion_bounce_rsi_target_hit",
                        "rsi": rsi_val,
                        "close": ctx.bar.close,
                    }
                )
            return None

        # Enforce cooldown to prevent friction bleed
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Calculate indicators prior to the current bar
        atr = self._atr(highs[:-1], lows[:-1], closes[:-1], self.atr_period)
        vol_sma = self._sma(volumes[:-1], self.vol_period)
        rsi = self._rsi(closes, self.rsi_period)

        if atr is None or vol_sma is None or rsi is None or atr == 0.0 or vol_sma == 0.0:
            return None

        # Cascade candle metrics (previous bar: index -2)
        prev_open = opens[-2]
        prev_close = closes[-2]
        prev_high = highs[-2]
        prev_low = lows[-2]
        prev_vol = volumes[-2]

        prev_range = prev_high - prev_low
        is_bearish = prev_close < prev_open
        vol_multiple = prev_vol / vol_sma
        range_multiple = prev_range / atr

        # Close position within the panic candle (0.0 = low, 1.0 = high)
        close_pos_ratio = (prev_close - prev_low) / (prev_range + 1e-8) if prev_range > 0 else 0.5

        # Cascade qualification: high volume, wide range, closed in bottom 35% of bar
        is_cascade_bar = (
            is_bearish and
            range_multiple >= 2.2 and
            vol_multiple >= 1.9 and
            close_pos_ratio <= 0.35
        )

        # Current confirmation candle (index -1)
        curr_open = opens[-1]
        curr_close = closes[-1]
        curr_low = lows[-1]

        # Confirmation: holds panic low (no lower low) and shows stabilization/buying
        holds_panic_low = curr_low >= (prev_low * 0.9985)
        bullish_reaction = curr_close > curr_open or curr_close > prev_close

        if is_cascade_bar and holds_panic_low and bullish_reaction and rsi < 48.0:
            # Measure distance to panic low for dynamic stop-loss
            dist_to_low_bps = ((curr_close - prev_low) / curr_close) * 10000.0
            sl_bps = max(180.0, min(420.0, dist_to_low_bps + 40.0))
            tp_bps = max(300.0, sl_bps * 1.7)

            fgi = ctx.features.get("fear_greed_index", 50.0)
            confidence = 0.75 if fgi < 40 else 0.65

            self.last_trade_bar = ctx.bar_index

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=14400,
                metadata={
                    "reason": "liquidation_cascade_exhaustion_hold",
                    "range_multiple": round(range_multiple, 2),
                    "vol_multiple": round(vol_multiple, 2),
                    "rsi": round(rsi, 2),
                    "fear_greed": fgi,
                    "panic_low": prev_low,
                    "entry_price": curr_close,
                    "calculated_sl_bps": round(sl_bps, 1),
                }
            )

        return None