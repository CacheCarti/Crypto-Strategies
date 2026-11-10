from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcFundingSqueeze(Strategy):
    METADATA = {
        "name": "BtcFundingSqueeze",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 21600,  # ~6 hours
        "warmup_bars": 50,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 8
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 16

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        if len(closes) < 50:
            return None

        # Manage existing open positions
        if ctx.has_position():
            bars_in_trade = ctx.bar_index - self.entry_bar
            if bars_in_trade >= self.max_hold_bars:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "max_hold_reached", "bars_held": bars_in_trade})
            return None

        # Mandatory cooldown guard to prevent overtrading and friction drag
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Feature extraction
        funding_rate = ctx.features.get("funding_rate_btcusdt", 0.0)
        regime = ctx.market.get("regime", "NORMAL")

        # Avoid extreme breakdown regimes
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Price momentum over lookback
        price_now = closes[-1]
        price_start = closes[-self.lookback]
        ret_lookback = (price_now - price_start) / price_start

        ema_trend = self._ema(closes, 48)
        if ema_trend is None:
            return None

        # Volatility normalization
        highs = ctx.highs(30)
        lows = ctx.lows(30)
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr <= 0:
            return None

        norm_ret = (ret_lookback * price_now) / atr

        # Setup conditions:
        # Long Squeeze: Price pushed up significantly while perpetual funding stays negative (aggressive shorts trapped into trend).
        # Short Squeeze: Price broke down significantly while perpetual funding stays elevated positive (longs trapped into downtrend).
        funding_thresh_long = -0.00003  # Slightly negative or deeply negative
        funding_thresh_short = 0.00010  # Excessively positive funding during dump

        min_momentum = 1.0  # Move exceeds 1x ATR

        is_long_squeeze = (
            ret_lookback > 0.008
            and norm_ret > min_momentum
            and price_now > ema_trend
            and funding_rate < funding_thresh_long
        )

        is_short_squeeze = (
            ret_lookback < -0.008
            and norm_ret < -min_momentum
            and price_now < ema_trend
            and funding_rate > funding_thresh_short
        )

        if is_long_squeeze:
            self.entry_bar = ctx.bar_index
            conf = min(0.85, 0.55 + abs(funding_rate) * 3000)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=220.0,
                take_profit_bps=440.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "funding_short_squeeze_continuation",
                    "funding_rate": funding_rate,
                    "ret_lookback": ret_lookback,
                    "price": price_now,
                    "ema_trend": ema_trend,
                },
            )

        if is_short_squeeze:
            self.entry_bar = ctx.bar_index
            conf = min(0.85, 0.55 + abs(funding_rate) * 2000)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=220.0,
                take_profit_bps=440.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "funding_long_trapped_breakdown",
                    "funding_rate": funding_rate,
                    "ret_lookback": ret_lookback,
                    "price": price_now,
                    "ema_trend": ema_trend,
                },
            )

        return None