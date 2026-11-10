from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcRelativeStrength(Strategy):
    METADATA = {
        "name": "ETH-BTC Relative Strength Regime Momentum",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 50,
        "required_features": ["eth_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_threshold = 2.0
        self.reversion_threshold = 0.5
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.trend_ema_period = 34

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.trend_ema_period + 5)
        if len(closes) < self.trend_ema_period:
            return None

        # Feature readings
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        # Market regime filter to address holdout failure in crisis/extreme vol
        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.4 or regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "regime_risk_off_exit", "regime": regime, "crisis_score": crisis_score}
                )
            return None

        # Position management: exit on relative strength mean-reversion
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and spread < self.reversion_threshold:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "spread_mean_reverted_long_exit", "spread": spread, "eth_ret": eth_ret, "btc_ret": btc_ret}
                )
            elif pos_dir == "short" and spread > -self.reversion_threshold:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "spread_mean_reverted_short_exit", "spread": spread, "eth_ret": eth_ret, "btc_ret": btc_ret}
                )
            return None

        # Cooldown guard to avoid overtrading
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Trend alignment via fast-acting trend filter
        ema_trend = self._ema(closes, self.trend_ema_period)
        if ema_trend is None:
            return None

        price = ctx.bar.close

        # Entry logic: ETH leading vs lagging with price confirmation
        if spread >= self.spread_threshold and price > ema_trend:
            conf = min(0.9, 0.55 + (spread - self.spread_threshold) * 0.08)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_outperformance_momentum_long",
                    "spread": round(spread, 3),
                    "eth_ret": round(eth_ret, 3),
                    "btc_ret": round(btc_ret, 3),
                    "ema_trend": round(ema_trend, 2),
                    "price": round(price, 2)
                }
            )

        elif spread <= -self.spread_threshold and price < ema_trend:
            conf = min(0.9, 0.55 + (abs(spread) - self.spread_threshold) * 0.08)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_underperformance_momentum_short",
                    "spread": round(spread, 3),
                    "eth_ret": round(eth_ret, 3),
                    "btc_ret": round(btc_ret, 3),
                    "ema_trend": round(ema_trend, 2),
                    "price": round(price, 2)
                }
            )

        return None