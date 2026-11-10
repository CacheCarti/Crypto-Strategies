from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingSqueezeCrossConfirm(Strategy):
    METADATA = {
        "name": "FundingSqueezeCrossConfirm",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 40,
        "required_features": ["funding_rate_btcusdt", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 16
        self.last_trade_bar = -100

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        if len(closes) < self.lookback + 1:
            return None

        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        highs = ctx.highs(15)
        lows = ctx.lows(15)
        recent_closes = ctx.closes(15)
        atr = self._atr(highs, lows, recent_closes, period=14)
        if atr is None or atr <= 0:
            return None

        current_close = ctx.bar.close
        price_24h_ago = closes[-self.lookback - 1]
        price_ret = (current_close - price_24h_ago) / price_24h_ago

        funding = ctx.features.get("funding_rate_btcusdt", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)

        # Cross-asset confirmation & volatility gate:
        # Move must exceed ~0.75x ATR over the 24h period to ensure active expansion
        min_move_pct = (atr * 0.75) / current_close

        # Long Squeeze: BTC price up, shorts trapped with negative funding, ETH confirms upward momentum
        if price_ret > min_move_pct and funding < -0.00003 and eth_ret > 0.003:
            if not ctx.has_position() or ctx.position_direction() != "long":
                self.last_trade_bar = ctx.bar_index
                conf = min(1.0, 0.55 + abs(funding) * 1500.0)
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "short_squeeze_continuation_cross_confirmed",
                        "price_ret_24h": round(price_ret, 4),
                        "funding": round(funding, 6),
                        "eth_return_pct": round(eth_ret, 4),
                        "atr": round(atr, 2),
                    }
                )

        # Short Squeeze: BTC price down, longs trapped with positive funding, ETH confirms downward momentum
        if price_ret < -min_move_pct and funding > 0.00008 and eth_ret < -0.003:
            if not ctx.has_position() or ctx.position_direction() != "short":
                self.last_trade_bar = ctx.bar_index
                conf = min(1.0, 0.55 + funding * 1500.0)
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "long_liquidation_continuation_cross_confirmed",
                        "price_ret_24h": round(price_ret, 4),
                        "funding": round(funding, 6),
                        "eth_return_pct": round(eth_ret, 4),
                        "atr": round(atr, 2),
                    }
                )

        return None