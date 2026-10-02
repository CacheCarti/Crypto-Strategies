from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingSqueeze(Strategy):
    METADATA = {
        "name": "BTC Funding Squeeze Divergence",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 30,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 16
        self.rsi_period = 14
        self.cooldown_bars = 3
        self.last_exit_bar = -999
        self.min_return_threshold = 0.004
        self.funding_neutral = 0.00010
        self.funding_low_thresh = 0.00004
        self.funding_high_thresh = 0.00008

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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + self.rsi_period + 5)
        if len(closes) < self.lookback + self.rsi_period:
            return None

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        ret_lookback = (closes[-1] - closes[-self.lookback]) / closes[-self.lookback]
        funding = ctx.features.get("funding_rate_btcusdt", 0.0)
        curr_price = ctx.bar.close

        # Position exit management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long":
                if rsi > 78.0 or ret_lookback < -0.010 or funding > 0.00025:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_exhaustion_or_reversal",
                            "rsi": round(rsi, 2),
                            "ret_lookback": round(ret_lookback, 4),
                            "funding": round(funding, 6),
                            "price": curr_price,
                        },
                    )
            elif pos_dir == "short":
                if rsi < 22.0 or ret_lookback > 0.010 or funding < -0.00005:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_exhaustion_or_reversal",
                            "rsi": round(rsi, 2),
                            "ret_lookback": round(ret_lookback, 4),
                            "funding": round(funding, 6),
                            "price": curr_price,
                        },
                    )
            return None

        # Post-exit cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Bullish Squeeze: Price rising while funding is depressed / negative (shorts trapped or offsides)
        if (
            ret_lookback >= self.min_return_threshold
            and funding <= self.funding_low_thresh
            and rsi < 72.0
        ):
            conf = min(0.85, 0.55 + abs(ret_lookback) * 8.0)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "funding_short_squeeze_divergence",
                    "ret_lookback": round(ret_lookback, 4),
                    "funding_rate": round(funding, 6),
                    "rsi": round(rsi, 2),
                    "price": curr_price,
                },
            )

        # Bearish Squeeze: Price falling while funding remains elevated / positive (longs trapped)
        if (
            ret_lookback <= -self.min_return_threshold
            and funding >= self.funding_high_thresh
            and rsi > 28.0
        ):
            conf = min(0.85, 0.55 + abs(ret_lookback) * 8.0)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "funding_long_squeeze_divergence",
                    "ret_lookback": round(ret_lookback, 4),
                    "funding_rate": round(funding, 6),
                    "rsi": round(rsi, 2),
                    "price": curr_price,
                },
            )

        return None