from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingSqueezeSelective(Strategy):
    METADATA = {
        "name": "FundingSqueezeSelective",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 12
        self.last_exit_bar = -100
        self.min_return_pct = 1.2
        self.neg_funding_thresh = -0.00005
        self.pos_funding_thresh = 0.00010

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
        closes = ctx.closes(self.lookback + 5)
        if len(closes) < self.lookback + 1:
            return None

        # Cooldown guard to avoid churn and friction bleed
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Avoid entries during absolute crisis
        if ctx.regime == "crisis":
            return None

        funding = ctx.features.get("funding_rate_btcusdt", 0.0)
        current_close = closes[-1]
        past_close = closes[-self.lookback]
        ret_pct = ((current_close - past_close) / past_close) * 100.0

        # Breakout / volatility confirmation using ATR
        highs = ctx.highs(20)
        lows = ctx.lows(20)
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr <= 0:
            return None

        # Manage open position
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            entry_p = ctx.position.get("entry_price", current_close) if ctx.position else current_close
            pnl_bps = ((current_close - entry_p) / entry_p) * 10000.0 if pos_dir == "long" else ((entry_p - current_close) / entry_p) * 10000.0

            # Early thesis invalidation if funding aligns heavily with momentum
            if pos_dir == "long" and funding > 0.00020:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "funding_normalized_long_exit", "funding": funding, "pnl_bps": pnl_bps}
                )
            if pos_dir == "short" and funding < -0.00015:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "funding_normalized_short_exit", "funding": funding, "pnl_bps": pnl_bps}
                )
            return None

        # Entry logic: Pure positioning divergence
        # Long squeeze: Price surging up strongly over 24h while funding remains persistently negative
        if ret_pct >= self.min_return_pct and funding <= self.neg_funding_thresh:
            # Confirm price is breaking out above recent resistance
            recent_high = max(highs[-10:-1]) if len(highs) >= 10 else current_close
            if current_close >= recent_high:
                conf = min(0.85, 0.55 + abs(funding) * 2000.0)
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "short_squeeze_divergence",
                        "ret_24h_pct": ret_pct,
                        "funding": funding,
                        "close": current_close,
                        "atr": atr
                    }
                )

        # Short squeeze: Price breaking down over 24h while funding remains persistently crowded positive
        if ret_pct <= -self.min_return_pct and funding >= self.pos_funding_thresh:
            recent_low = min(lows[-10:-1]) if len(lows) >= 10 else current_close
            if current_close <= recent_low:
                conf = min(0.85, 0.55 + funding * 1500.0)
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "long_liquidation_divergence",
                        "ret_24h_pct": ret_pct,
                        "funding": funding,
                        "close": current_close,
                        "atr": atr
                    }
                )

        return None