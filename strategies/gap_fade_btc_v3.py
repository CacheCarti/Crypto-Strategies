from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcWickReversionAdvanced(Strategy):
    METADATA = {
        "name": "BtcWickReversionAdvanced",
        "domain": "btc_usdc",
        "declared_sl_bps": 260.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.atr_period = 14
        self.vol_period = 24
        self.rsi_period = 14
        self.ema_period = 30
        
        # Tighter thresholds to hit 30-80 trades target
        self.wick_threshold = 0.65
        self.range_atr_multiplier = 1.35
        self.vol_multiplier = 1.45
        self.cooldown_bars = 10
        
        self.last_trade_bar = -999
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.target_mid_price = 0.0

    def _atr(self, highs, lows, closes, period: int = 14) -> Optional[float]:
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

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

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
        self.last_exit_bar = ctx.bar_index
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        n_bars = 45
        closes = ctx.closes(n_bars)
        highs = ctx.highs(n_bars)
        lows = ctx.lows(n_bars)
        volumes = ctx.volumes(n_bars)
        opens = ctx.opens(n_bars)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        atr = self._atr(highs, lows, closes, self.atr_period)
        vol_sma = self._sma(volumes, self.vol_period)
        rsi = self._rsi(closes, self.rsi_period)
        ema = self._ema(closes, self.ema_period)

        if atr is None or vol_sma is None or rsi is None or ema is None or atr <= 0.0 or vol_sma <= 0.0:
            return None

        current_close = ctx.bar.close
        current_open = ctx.bar.open
        current_high = ctx.bar.high
        current_low = ctx.bar.low
        current_vol = ctx.bar.volume

        bar_range = current_high - current_low
        if bar_range <= 0.0:
            return None

        body_top = max(current_open, current_close)
        body_bottom = min(current_open, current_close)
        upper_wick = current_high - body_top
        lower_wick = body_bottom - current_low

        upper_wick_ratio = upper_wick / bar_range
        lower_wick_ratio = lower_wick / bar_range
        vol_ratio = current_vol / vol_sma
        range_atr_ratio = bar_range / atr

        # Active position management: wick retracement target or time-stop
        if ctx.has_position():
            direction = ctx.position_direction()
            bars_in_pos = ctx.bar_index - self.entry_bar

            if direction == "long" and current_close >= self.target_mid_price and bars_in_pos >= 2:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.70,
                    metadata={
                        "reason": "wick_retrace_target_hit_long",
                        "target_mid": round(self.target_mid_price, 2),
                        "price": round(current_close, 2),
                        "bars_held": bars_in_pos
                    }
                )
            elif direction == "short" and current_close <= self.target_mid_price and bars_in_pos >= 2:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.70,
                    metadata={
                        "reason": "wick_retrace_target_hit_short",
                        "target_mid": round(self.target_mid_price, 2),
                        "price": round(current_close, 2),
                        "bars_held": bars_in_pos
                    }
                )
            return None

        # Hard cooldown after every entry or exit to prevent friction bleeding
        if (ctx.bar_index - self.last_trade_bar < self.cooldown_bars) or \
           (ctx.bar_index - self.last_exit_bar < self.cooldown_bars):
            return None

        # Regime & Crisis Filter
        crisis_score = ctx.market.get("crisis_score", 0.0)
        market_regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.50 or market_regime in ("CRISIS", "MELTDOWN") or ctx.regime == "crisis":
            return None

        is_large_range = range_atr_ratio >= self.range_atr_multiplier
        is_elevated_vol = vol_ratio >= self.vol_multiplier

        # Setup 1: Significant lower wick rejection on high volume, oversold-to-neutral RSI
        if (lower_wick_ratio >= self.wick_threshold and 
            is_large_range and 
            is_elevated_vol and 
            rsi <= 52.0 and 
            current_close < ema * 1.01):
            
            confidence = min(0.90, 0.60 + (lower_wick_ratio - self.wick_threshold) * 0.8 + (vol_ratio - 1.0) * 0.05)
            self.entry_bar = ctx.bar_index
            self.last_trade_bar = ctx.bar_index
            self.target_mid_price = current_low + (bar_range * 0.60)
            
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "lower_wick_rejection_long",
                    "lower_wick_ratio": round(lower_wick_ratio, 3),
                    "vol_ratio": round(vol_ratio, 2),
                    "range_atr_ratio": round(range_atr_ratio, 2),
                    "rsi": round(rsi, 2),
                    "target_mid": round(self.target_mid_price, 2)
                }
            )

        # Setup 2: Significant upper wick rejection on high volume, overbought-to-neutral RSI
        if (upper_wick_ratio >= self.wick_threshold and 
            is_large_range and 
            is_elevated_vol and 
            rsi >= 48.0 and 
            current_close > ema * 0.99):
            
            confidence = min(0.90, 0.60 + (upper_wick_ratio - self.wick_threshold) * 0.8 + (vol_ratio - 1.0) * 0.05)
            self.entry_bar = ctx.bar_index
            self.last_trade_bar = ctx.bar_index
            self.target_mid_price = current_high - (bar_range * 0.60)
            
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "upper_wick_rejection_short",
                    "upper_wick_ratio": round(upper_wick_ratio, 3),
                    "vol_ratio": round(vol_ratio, 2),
                    "range_atr_ratio": round(range_atr_ratio, 2),
                    "rsi": round(rsi, 2),
                    "target_mid": round(self.target_mid_price, 2)
                }
            )

        return None