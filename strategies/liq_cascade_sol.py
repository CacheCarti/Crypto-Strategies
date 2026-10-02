from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class LiquidationCascadeExhaustion(Strategy):
    METADATA = {
        "name": "SOL Liquidation Cascade Exhaustion",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.atr_period = 20
        self.vol_period = 20
        self.rsi_period = 14
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.last_entry_bar = -999
        
        # State tracking for multi-bar cascade sequence
        self.cascade_pending = False
        self.cascade_bar_idx = -999
        self.cascade_low = 0.0
        self.cascade_high = 0.0
        self.cascade_range = 0.0

    def _atr(self, highs, lows, closes, period=20) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _sma(self, values, period=20) -> Optional[float]:
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _rsi(self, closes, period=14) -> Optional[float]:
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
        self.cascade_pending = False

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        warmup = max(self.atr_period, self.vol_period, self.rsi_period) + 2
        closes = ctx.closes(warmup + 5)
        highs = ctx.highs(warmup + 5)
        lows = ctx.lows(warmup + 5)
        volumes = ctx.volumes(warmup + 5)

        if len(closes) < warmup:
            return None

        # Indicator calculations
        atr = self._atr(highs, lows, closes, self.atr_period)
        avg_vol = self._sma(volumes[:-1], self.vol_period)
        rsi = self._rsi(closes, self.rsi_period)

        if atr is None or avg_vol is None or rsi is None or avg_vol <= 0 or atr <= 0:
            return None

        current_bar = ctx.bar
        fear_greed = ctx.features.get("fear_greed_index", 50.0)

        # 1. POSITION MANAGEMENT / EXIT LOGIC
        if ctx.has_position():
            # Profit take or exhaustion exit on overbought RSI bounce
            if rsi > 68.0:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "rsi_overbought_exhaustion_exit",
                        "rsi": round(rsi, 2),
                        "price": current_bar.close,
                        "bars_held": ctx.bar_index - self.last_entry_bar,
                    },
                )
            return None

        # Cooldown guard after recent exit or entry
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None
        if ctx.bar_index - self.last_entry_bar < self.cooldown_bars:
            return None

        # 2. CHECK FOR COMPLETED CASCADE FROM PREVIOUS BAR (ENTRY TRIGGER)
        if self.cascade_pending and ctx.bar_index == self.cascade_bar_idx + 1:
            # Evaluate if current bar held the panic low (exhaustion confirmation)
            held_panic_low = current_bar.low >= (self.cascade_low * 0.998)
            bullish_response = current_bar.close > current_bar.open or current_bar.close > closes[-2]

            if held_panic_low and bullish_response:
                # Dynamic stop-loss based on distance below the panic low
                dist_to_low_bps = ((current_bar.close - self.cascade_low) / current_bar.close) * 10000.0
                dynamic_sl = max(180.0, min(360.0, dist_to_low_bps + 60.0))
                dynamic_tp = max(380.0, dynamic_sl * 1.8)

                # Confidence calculation based on Fear & Greed and RSI stretch
                base_conf = 0.65
                if fear_greed < 30.0:
                    base_conf += 0.10
                if rsi < 32.0:
                    base_conf += 0.10
                confidence = min(0.95, base_conf)

                self.cascade_pending = False
                self.last_entry_bar = ctx.bar_index

                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=dynamic_sl,
                    take_profit_bps=dynamic_tp,
                    horizon_seconds=14400,
                    metadata={
                        "reason": "liquidation_cascade_bottom_confirmed",
                        "cascade_low": round(self.cascade_low, 2),
                        "entry_price": current_bar.close,
                        "rsi": round(rsi, 2),
                        "fear_greed": fear_greed,
                        "stop_loss_bps": round(dynamic_sl, 1),
                        "take_profit_bps": round(dynamic_tp, 1),
                    },
                )
            else:
                # Failed to hold low, cancel setup
                self.cascade_pending = False

        # 3. DETECT NEW LIQUIDATION CASCADE BAR
        bar_range = current_bar.high - current_bar.low
        is_bearish = current_bar.close < current_bar.open
        vol_multiple = current_bar.volume / avg_vol
        range_multiple = bar_range / atr

        # Closed in lower 35% of the bar's full range
        closed_near_low = (
            (current_bar.close - current_bar.low) <= (0.35 * bar_range)
            if bar_range > 0
            else False
        )

        # Cascade criteria: Sharp wide-range drop on >2.1x volume and >2.1x ATR, closing near low
        is_cascade = (
            is_bearish
            and range_multiple >= 2.1
            and vol_multiple >= 2.1
            and closed_near_low
            and rsi < 42.0
        )

        if is_cascade:
            self.cascade_pending = True
            self.cascade_bar_idx = ctx.bar_index
            self.cascade_low = current_bar.low
            self.cascade_high = current_bar.high
            self.cascade_range = bar_range
        else:
            if ctx.bar_index > self.cascade_bar_idx + 1:
                self.cascade_pending = False

        return None