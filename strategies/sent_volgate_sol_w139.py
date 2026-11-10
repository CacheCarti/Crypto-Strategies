from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentVolConfirmation(Strategy):
    METADATA = {
        "name": "Sentiment Vol Confirmation",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 30
        self.fear_threshold = 30
        self.greed_threshold = 70
        self.min_vol_std_dev = 1.5 # Volatility must be significantly above average
        self.cooldown_bars = 6
        self.last_trade_bar = -self.cooldown_bars

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.vol_period * 2) # Need more for stddev of ATR
        highs = ctx.highs(self.vol_period * 2)
        lows = ctx.lows(self.vol_period * 2)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_fear_greed = ctx.features.get("fear_greed_index", 50)

        # Calculate ATR for volatility proxy
        atr_values = []
        for i in range(self.vol_period, len(closes)):
            atr_val = self._atr(highs[:i+1], lows[:i+1], closes[:i+1], period=14)
            if atr_val is not None:
                atr_values.append(atr_val)

        if len(atr_values) < self.vol_period:
            return None

        current_atr = atr_values[-1]
        atr_std = self._stddev(atr_values[:-1], self.vol_period - 1) # Stddev of past ATRs
        atr_mean = sum(atr_values[:-1]) / (self.vol_period - 1)

        if atr_std is None or atr_std == 0:
            return None

        # Check for significant volatility confirmation (panic/euphoria)
        volatility_z_score = (current_atr - atr_mean) / atr_std

        if ctx.has_position():
            # If in position, just hold. No exit logic here, rely on SL/TP.
            return None

        # Long entry: Extreme Fear + High Volatility
        if current_fear_greed <= self.fear_threshold and volatility_z_score >= self.min_vol_std_dev:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "extreme_fear_high_volatility_buy",
                    "fear_greed_index": current_fear_greed,
                    "volatility_z_score": volatility_z_score,
                    "current_atr": current_atr,
                    "atr_mean": atr_mean,
                    "atr_std": atr_std,
                    "price": ctx.bar.close,
                }
            )

        # Short entry: Extreme Greed + High Volatility
        elif current_fear_greed >= self.greed_threshold and volatility_z_score >= self.min_vol_std_dev:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "extreme_greed_high_volatility_sell",
                    "fear_greed_index": current_fear_greed,
                    "volatility_z_score": volatility_z_score,
                    "current_atr": current_atr,
                    "atr_mean": atr_mean,
                    "atr_std": atr_std,
                    "price": ctx.bar.close,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for now, simply log or reset if needed.
        pass