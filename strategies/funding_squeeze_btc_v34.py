from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcFundingSqueezeDivergence(Strategy):
    METADATA = {
        "name": "BtcFundingSqueezeDivergence",
        "domain": "btc_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 21600,  # ~6 hours target hold
        "warmup_bars": 40,
        "required_features": ["funding_rate_btcusdt", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 16
        self.last_exit_bar = -100
        self.entry_bar = -100

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
        closes = ctx.closes(self.lookback + 10)
        if len(closes) < self.lookback + 5:
            return None

        # Manage existing position exits
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            # Time-based hold horizon exit
            if bars_held >= 12:
                return ctx.signal("flat", confidence=0.5, metadata={
                    "reason": "horizon_exhaustion",
                    "bars_held": bars_held,
                    "close": ctx.bar.close
                })
            return None

        # Cooldown guard to avoid churn and friction death
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Skip high crisis / meltdown regimes where funding squeezes fail to maintain trend
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Volatility expansion state confirmation (ATR must expand vs rolling baseline)
        highs = ctx.highs(30)
        lows = ctx.lows(30)
        atr_now = self._atr(highs, lows, closes, period=14)
        atr_prev = self._atr(highs[:-6], lows[:-6], closes[:-6], period=14)
        if atr_now is None or atr_prev is None or atr_now <= atr_prev:
            return None

        # Price trajectory over the lookback window (24h)
        price_start = closes[-self.lookback]
        price_now = closes[-1]
        price_ret_pct = ((price_now - price_start) / price_start) * 100.0

        # Funding rate reading (negative = shorts pay longs, positive = longs pay shorts)
        funding = ctx.features.get("funding_rate_btcusdt", 0.0)

        # Cross-asset confirmation via ETH returns to ensure market-wide momentum
        eth_ret = ctx.features.get("eth_return_pct", 0.0)

        # 1. Long Squeeze: Price is up over 24h, ETH confirms, but funding is persistently negative (shorts trapped)
        if price_ret_pct >= 1.2 and funding <= -0.00003 and eth_ret > 0.0:
            confidence = min(0.9, 0.6 + abs(funding) * 2000.0)
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "short_squeeze_continuation",
                    "price_ret_pct": round(price_ret_pct, 3),
                    "funding": funding,
                    "eth_ret": round(eth_ret, 3),
                    "atr_now": round(atr_now, 2),
                    "atr_prev": round(atr_prev, 2),
                }
            )

        # 2. Short Squeeze (Long liquidation cascade): Price is down over 24h, ETH confirms, but funding remains positive (longs trapped)
        if price_ret_pct <= -1.2 and funding >= 0.00008 and eth_ret < 0.0:
            confidence = min(0.9, 0.6 + funding * 2000.0)
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "long_cascade_continuation",
                    "price_ret_pct": round(price_ret_pct, 3),
                    "funding": funding,
                    "eth_ret": round(eth_ret, 3),
                    "atr_now": round(atr_now, 2),
                    "atr_prev": round(atr_prev, 2),
                }
            )

        return None