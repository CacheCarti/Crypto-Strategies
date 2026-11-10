from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcFundingSqueeze(Strategy):
    METADATA = {
        "name": "BtcFundingSqueeze",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 40,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 8
        self.last_exit_bar = -999
        self.min_price_move_pct = 0.0075
        self.funding_threshold = 0.00004

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

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
        closes = ctx.closes(self.lookback + 15)
        if len(closes) < self.lookback + 10:
            return None

        # Check market regime to avoid crisis / extreme volatility
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_exit", "regime": market_regime})
            return None

        # Manage open position exit / trail via RSI extremes
        rsi = self._rsi(closes, 14) or 50.0
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and rsi > 78.0:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "rsi_overbought_exit", "rsi": rsi})
            if pos_dir == "short" and rsi < 22.0:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "rsi_oversold_exit", "rsi": rsi})
            return None

        # Cooldown guard after recent exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Funding rate & price movement over the lookback window
        funding_rate = ctx.features.get("funding_rate_btcusdt", 0.0)
        p_now = closes[-1]
        p_prior = closes[-self.lookback]
        if p_prior <= 0:
            return None

        price_ret = (p_now - p_prior) / p_prior

        # Short squeeze setup: Price surging while funding is persistently negative (shorts paying longs)
        # Momentum filter: RSI confirms upward push without being immediately blown out
        if price_ret > self.min_price_move_pct and funding_rate < -self.funding_threshold:
            if 48.0 < rsi < 72.0:
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "short_squeeze_continuation",
                        "price_ret_24h": round(price_ret, 4),
                        "funding_rate": funding_rate,
                        "rsi": round(rsi, 2),
                    },
                )

        # Long squeeze setup: Price dropping while funding remains stubbornly positive (longs paying shorts)
        if price_ret < -self.min_price_move_pct and funding_rate > self.funding_threshold:
            if 28.0 < rsi < 52.0:
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "long_squeeze_continuation",
                        "price_ret_24h": round(price_ret, 4),
                        "funding_rate": funding_rate,
                        "rsi": round(rsi, 2),
                    },
                )

        return None