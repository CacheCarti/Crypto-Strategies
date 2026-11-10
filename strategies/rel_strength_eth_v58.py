from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthRelativeStrengthEvent(Strategy):
    METADATA = {
        "name": "EthRelativeStrengthEvent",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 21600,  # ~6 hours hold expectation
        "warmup_bars": 35,
        "required_features": ["eth_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 8
        self.last_exit_bar = -999
        self.spread_threshold = 2.0  # 2% return spread threshold
        self.prev_spread = 0.0

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
        closes = ctx.closes(30)
        if len(closes) < 30:
            return None

        # Fetch returns
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        # Market trend filter
        trend_regime = ctx.market.get("trend_regime", "neutral")
        regime = ctx.market.get("regime", "NORMAL")

        # Trend baseline confirmation
        ema20 = self._ema(closes, 20)
        current_close = ctx.bar.close

        # Check existing position for mean-reversion exit
        if ctx.has_position():
            direction = ctx.position_direction()
            # Mean reversion exit: spread closes back toward zero
            if direction == "long" and spread < 0.4:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "eth_outperformance_reverted", "spread": spread, "eth_ret": eth_ret, "btc_ret": btc_ret}
                )
            elif direction == "short" and spread > -0.4:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "eth_underperformance_reverted", "spread": spread, "eth_ret": eth_ret, "btc_ret": btc_ret}
                )
            self.prev_spread = spread
            return None

        # Mandatory cooldown post-exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            self.prev_spread = spread
            return None

        # Avoid extreme crises
        if regime in ("CRISIS", "MELTDOWN"):
            self.prev_spread = spread
            return None

        # Event trigger: Cross threshold strictly on this bar (event boundary formulation)
        long_cross = (self.prev_spread <= self.spread_threshold) and (spread > self.spread_threshold)
        short_cross = (self.prev_spread >= -self.spread_threshold) and (spread < -self.spread_threshold)

        self.prev_spread = spread

        if ema20 is None:
            return None

        # Long: ETH breaks out in relative strength and holds above its 20 EMA
        if long_cross and current_close > ema20 and trend_regime != "bear":
            conf = min(0.85, 0.55 + (spread - self.spread_threshold) * 0.05)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=280.0,
                take_profit_bps=480.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "eth_relative_strength_breakout",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "ema20": ema20,
                    "price": current_close
                }
            )

        # Short: ETH breaks down in relative strength and holds below its 20 EMA
        if short_cross and current_close < ema20 and trend_regime != "bull":
            conf = min(0.85, 0.55 + (abs(spread) - self.spread_threshold) * 0.05)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=280.0,
                take_profit_bps=480.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "eth_relative_weakness_breakdown",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "ema20": ema20,
                    "price": current_close
                }
            )

        return None