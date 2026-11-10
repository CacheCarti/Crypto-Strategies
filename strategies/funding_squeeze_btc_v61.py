from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingSqueezeMomentum(Strategy):
    METADATA = {
        "name": "Funding Squeeze Momentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 18000,  # ~5 hours target
        "warmup_bars": 35,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_period = 8
        self.last_exit_bar = -999
        self.funding_history = []
        self.max_history = 50

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
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
        closes = ctx.closes(self.lookback + 10)
        if len(closes) < self.lookback + 5:
            return None

        # Track funding rate locally
        cur_funding = ctx.features.get("funding_rate_btcusdt", 0.0)
        self.funding_history.append(cur_funding)
        if len(self.funding_history) > self.max_history:
            self.funding_history.pop(0)

        # In-position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            pos_bars = ctx.bar_index - ctx.position.get("entry_bar", ctx.bar_index) if ctx.position else 0
            
            # Dynamic exit on clear momentum exhaustion
            rsi = self._rsi(closes, 14)
            if rsi is not None:
                if pos_dir == "long" and rsi > 78.0:
                    return ctx.signal("flat", confidence=0.7, metadata={"reason": "rsi_overbought_exit", "rsi": rsi})
                elif pos_dir == "short" and rsi < 22.0:
                    return ctx.signal("flat", confidence=0.7, metadata={"reason": "rsi_oversold_exit", "rsi": rsi})

            # Time stop past 15 bars (~15 hours)
            if pos_bars >= 15:
                return ctx.signal("flat", confidence=0.5, metadata={"reason": "time_exhaustion", "bars": pos_bars})
            return None

        # Post-exit cooldown gate
        if ctx.bar_index - self.last_exit_bar < self.cooldown_period:
            return None

        # Market stress gate
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Price momentum over 24h
        price_ret = (closes[-1] - closes[-self.lookback]) / closes[-self.lookback]

        # Median funding over last 12 bars (approx 12h) to avoid single-tick spikes
        recent_funding = self.funding_history[-12:] if len(self.funding_history) >= 12 else self.funding_history
        avg_funding = sum(recent_funding) / len(recent_funding) if recent_funding else cur_funding

        # Indicators
        ema20 = self._ema(closes, 20)
        rsi = self._rsi(closes, 14)
        if ema20 is None or rsi is None:
            return None

        # Long Squeeze: Price strongly advancing (> +1.5%) while funding remains discounted/negative (< -0.00005)
        # Shorts are trapped and paying to hold against a rising price.
        if price_ret > 0.015 and avg_funding < -0.00005 and closes[-1] > ema20 and 45.0 < rsi < 68.0:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "short_funding_squeeze_continuation",
                    "price_ret_24h": round(price_ret, 4),
                    "avg_funding": round(avg_funding, 6),
                    "rsi": round(rsi, 2),
                    "ema20": round(ema20, 2),
                }
            )

        # Short Squeeze / Long Liquidation trap: Price tumbling (< -1.5%) while funding stays persistently positive (> +0.00015)
        # Bullish retail is stubbornly long, getting squeezed as price drops through key levels.
        if price_ret < -0.015 and avg_funding > 0.00015 and closes[-1] < ema20 and 32.0 < rsi < 55.0:
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "long_funding_trap_breakdown",
                    "price_ret_24h": round(price_ret, 4),
                    "avg_funding": round(avg_funding, 6),
                    "rsi": round(rsi, 2),
                    "ema20": round(ema20, 2),
                }
            )

        return None