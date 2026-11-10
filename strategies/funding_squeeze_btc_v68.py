from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcFundingSqueezeEvent(Strategy):
    METADATA = {
        "name": "BtcFundingSqueezeEvent",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 28800,  # ~8 hour target swing
        "warmup_bars": 35,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_bars = 24
        self.cooldown_bars = 16
        self.last_exit_bar = -100
        self.entry_bar = -100
        self.max_hold_bars = 24
        self.consecutive_funding_thresh = 4
        self.price_move_min_pct = 0.015  # 1.5% clear cumulative displacement

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
        closes = ctx.closes(self.lookback_bars + 2)
        if len(closes) < self.lookback_bars + 1:
            return None

        # Check existing position time stop
        if ctx.has_position():
            if ctx.bar_index - self.entry_bar >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "time_stop_squeeze_decay",
                        "bars_held": ctx.bar_index - self.entry_bar,
                        "close": ctx.bar.close,
                    },
                )
            return None

        # Mandatory cooldown check
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime safety filter: skip during severe market dislocation
        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.65 or regime in ["CRISIS", "MELTDOWN"]:
            return None

        current_funding = ctx.features.get("funding_rate_btcusdt", 0.0)

        # Cumulative price displacement over 24 bars
        price_start = closes[-self.lookback_bars]
        price_now = closes[-1]
        price_pct_chg = (price_now - price_start) / price_start

        rsi = self._rsi(closes, period=14)
        if rsi is None:
            return None

        # Short Squeeze setup:
        # Price has pushed significantly UP (+1.5%), but funding remains persistently negative.
        # Shorts are offside and funding incentivizes liquidation / continuation.
        # Gate RSI to avoid top-ticking blowoffs (> 78).
        if price_pct_chg >= self.price_move_min_pct and current_funding < -0.00008:
            if 48.0 <= rsi <= 76.0:
                self.entry_bar = ctx.bar_index
                conf = min(0.85, 0.55 + abs(current_funding) * 1500)
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "short_squeeze_continuation",
                        "price_pct_chg": round(price_pct_chg * 100, 2),
                        "funding_rate": current_funding,
                        "rsi": round(rsi, 2),
                        "bar_index": ctx.bar_index,
                    },
                )

        # Long Squeeze (flush) setup:
        # Price has dropped significantly (-1.5%), but funding remains persistently positive / stubborn.
        # Overleveraged longs are trapped in cascade. Gate RSI to avoid oversold bounces (< 24).
        if price_pct_chg <= -self.price_move_min_pct and current_funding > 0.00015:
            if 24.0 <= rsi <= 52.0:
                self.entry_bar = ctx.bar_index
                conf = min(0.85, 0.55 + current_funding * 1200)
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "long_liquidation_squeeze",
                        "price_pct_chg": round(price_pct_chg * 100, 2),
                        "funding_rate": current_funding,
                        "rsi": round(rsi, 2),
                        "bar_index": ctx.bar_index,
                    },
                )

        return None