from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcFundingSqueezeDivergence(Strategy):
    METADATA = {
        "name": "BtcFundingSqueezeDivergence",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 28800,  # ~8 hours swing hold
        "warmup_bars": 35,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.last_signal_bar = -999
        self.funding_history = []

    def _rsi(self, closes, period=14):
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

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 10)
        if len(closes) < self.lookback + 10:
            return None

        # Track funding history
        funding = ctx.features.get("funding_rate_btcusdt", 0.0)
        self.funding_history.append(funding)
        if len(self.funding_history) > self.lookback + 10:
            self.funding_history.pop(0)

        # Regulate trade pace & enforce cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None
        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        # Calculate price momentum over 24 bars
        current_close = closes[-1]
        past_close = closes[-self.lookback]
        pct_return_24h = (current_close - past_close) / past_close

        # Calculate average funding over last 12 bars to confirm persistent positioning
        recent_funding = self.funding_history[-12:]
        avg_funding_12 = sum(recent_funding) / len(recent_funding) if recent_funding else funding

        # Momentum & trend confirmation
        rsi = self._rsi(closes, 14)
        ema20 = self._ema(closes, 20)
        if rsi is None or ema20 is None:
            return None

        # Exit/Flat logic if in position
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # If trapped conditions normalize or reverse sharply
            if pos_dir == "long" and (rsi > 78.0 or avg_funding_12 > 0.0003):
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "exit_long_overbought_or_funding_flipped", "rsi": round(rsi, 2), "funding": avg_funding_12}
                )
            elif pos_dir == "short" and (rsi < 22.0 or avg_funding_12 < -0.0003):
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "exit_short_oversold_or_funding_flipped", "rsi": round(rsi, 2), "funding": avg_funding_12}
                )
            return None

        # SQUEEZE LONG SETUP:
        # Price rising strongly (+1.2% over 24h) while aggregate funding remains negative (< -0.00005)
        # Shorts are offside and trapped against steady spot buying.
        # RSI must be in active expansion (> 50) and price above EMA20, but not blow-off extreme (< 72).
        if pct_return_24h > 0.012 and avg_funding_12 < -0.00005:
            if 50.0 < rsi < 72.0 and current_close > ema20:
                self.last_signal_bar = ctx.bar_index
                conf = min(0.9, 0.65 + abs(pct_return_24h) * 5.0)
                return ctx.signal(
                    "long",
                    confidence=round(conf, 2),
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "short_squeeze_continuation",
                        "pct_return_24h": round(pct_return_24h, 4),
                        "avg_funding_12": round(avg_funding_12, 6),
                        "rsi": round(rsi, 2),
                        "close": round(current_close, 2),
                        "ema20": round(ema20, 2)
                    }
                )

        # LONG SQUEEZE / FLUSH SHORT SETUP:
        # Price falling (-1.2% over 24h) while aggregate funding remains elevated (> +0.00008)
        # Over-levered longs are trapped and facing liquidation cascading.
        # RSI in bearish momentum (< 50) and price below EMA20, not oversold flush bottom (> 28).
        if pct_return_24h < -0.012 and avg_funding_12 > 0.00008:
            if 28.0 < rsi < 50.0 and current_close < ema20:
                self.last_signal_bar = ctx.bar_index
                conf = min(0.9, 0.65 + abs(pct_return_24h) * 5.0)
                return ctx.signal(
                    "short",
                    confidence=round(conf, 2),
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "long_liquidation_squeeze",
                        "pct_return_24h": round(pct_return_24h, 4),
                        "avg_funding_12": round(avg_funding_12, 6),
                        "rsi": round(rsi, 2),
                        "close": round(current_close, 2),
                        "ema20": round(ema20, 2)
                    }
                )

        return None