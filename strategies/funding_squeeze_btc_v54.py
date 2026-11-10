from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcFundingSqueezeDivergence(Strategy):
    METADATA = {
        "name": "BtcFundingSqueezeDivergence",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 18000,  # ~5 hours
        "warmup_bars": 35,
        "required_features": ["funding_rate_btcusdt", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.lookback = 24

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
        closes = ctx.closes(self.lookback + 2)
        if len(closes) < self.lookback + 1:
            return None

        # Cooldown guard to avoid friction bleed
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        if ctx.has_position():
            return None

        highs = ctx.highs(16)
        lows = ctx.lows(16)
        atr = self._atr(highs, lows, ctx.closes(16), period=14)
        if atr is None or closes[-1] == 0:
            return None

        # Filter out extreme crisis regimes
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # 24-bar cumulative return
        ret_24 = (closes[-1] - closes[-self.lookback]) / closes[-self.lookback]

        # Funding rate & cross-asset confirmation
        funding = ctx.features.get("funding_rate_btcusdt", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)

        # Volatility expansion check: current candle range vs ATR
        curr_bar_range = ctx.bar.high - ctx.bar.low
        vol_expansion = curr_bar_range > (atr * 0.8)

        # Bullish Squeeze Setup:
        # Price rising significantly (+1.2% over 24h), while funding is negative (shorts trapped).
        # Material difference confirmation: Cross-asset confirmation (ETH also positive) + Volatility active.
        if ret_24 > 0.012 and funding < -0.00003 and eth_ret > 0.2 and vol_expansion:
            conf = min(0.90, max(0.60, 0.65 + abs(funding) * 500))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=220.0,
                take_profit_bps=380.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "short_squeeze_continuation_cross_confirm",
                    "ret_24": round(ret_24, 4),
                    "funding": funding,
                    "eth_ret": eth_ret,
                    "atr": round(atr, 2),
                },
            )

        # Bearish Long-Liquidation / Squeeze Setup:
        # Price falling significantly (-1.2% over 24h), while funding is high positive (longs trapped).
        # Material difference confirmation: ETH also declining + Volatility active.
        if ret_24 < -0.012 and funding > 0.00008 and eth_ret < -0.2 and vol_expansion:
            conf = min(0.90, max(0.60, 0.65 + funding * 400))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=220.0,
                take_profit_bps=380.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "long_liquidation_squeeze_cross_confirm",
                    "ret_24": round(ret_24, 4),
                    "funding": funding,
                    "eth_ret": eth_ret,
                    "atr": round(atr, 2),
                },
            )

        return None