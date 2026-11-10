from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingSqueezeMomentum(Strategy):
    METADATA = {
        "name": "FundingSqueezeMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 18000,  # ~5 hours average hold
        "warmup_bars": 40,
        "required_features": ["funding_rate_btcusdt"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.funding_history = []
        self.max_history = 100

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

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        highs = ctx.highs(40)
        lows = ctx.lows(40)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Track rolling funding rate
        cur_funding = ctx.features.get("funding_rate_btcusdt", 0.0)
        self.funding_history.append(cur_funding)
        if len(self.funding_history) > self.max_history:
            self.funding_history.pop(0)

        # Check regime filter: Avoid crisis meltdown regimes for squeeze trades
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_exit", "regime": market_regime})
            return None

        rsi = self._rsi(closes, period=14)
        if rsi is None:
            return None

        # Position exit logic based on momentum exhaustion
        if ctx.has_position():
            direction = ctx.position_direction()
            entry_price = ctx.position.get("entry_price", closes[-1]) if ctx.position else closes[-1]
            current_pnl_bps = ((closes[-1] - entry_price) / entry_price * 10000.0) if direction == "long" else ((entry_price - closes[-1]) / entry_price * 10000.0)

            if direction == "long" and rsi > 76.0:
                return ctx.signal("flat", confidence=0.75, metadata={"reason": "long_rsi_overbought_exit", "rsi": rsi, "pnl_bps": current_pnl_bps})
            elif direction == "short" and rsi < 24.0:
                return ctx.signal("flat", confidence=0.75, metadata={"reason": "short_rsi_oversold_exit", "rsi": rsi, "pnl_bps": current_pnl_bps})
            return None

        # Cooldown guard: enforce post-exit silence
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Need sufficient funding history for divergence verification
        if len(self.funding_history) < self.lookback:
            return None

        # Price momentum over 24 bars
        p_current = closes[-1]
        p_past = closes[-self.lookback]
        price_return_pct = (p_current - p_past) / p_past * 100.0

        # Mean funding over the squeeze accumulation window
        recent_funding = self.funding_history[-self.lookback:]
        avg_funding = sum(recent_funding) / len(recent_funding)

        # Volatility check to avoid trading in flat/dead conditions
        atr = self._atr(highs, lows, closes, 14)
        if atr is None or (atr / p_current) < 0.003:  # Min 30 bps per bar ATR
            return None

        # Divergence Entry 1: Squeeze Continuation (Price rising strongly while shorts pay / funding is persistently negative)
        # Shorts are trapped and forced to buy to cover
        if price_return_pct >= 1.2 and avg_funding < -0.00003 and cur_funding <= 0.0:
            if 48.0 < rsi < 68.0:  # Healthy momentum, not already peaked
                confidence = min(0.90, 0.55 + abs(avg_funding) * 500.0)
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=220.0,
                    take_profit_bps=440.0,
                    horizon_seconds=18000,
                    metadata={
                        "reason": "short_squeeze_continuation",
                        "price_return_pct": round(price_return_pct, 3),
                        "avg_funding": round(avg_funding, 6),
                        "cur_funding": round(cur_funding, 6),
                        "rsi": round(rsi, 2)
                    }
                )

        # Divergence Entry 2: Long Flush / Liquidation Continuation (Price falling while funding remains persistently positive)
        # Longs are trapped paying premium while underwater, triggering cascades
        if price_return_pct <= -1.2 and avg_funding > 0.00008 and cur_funding >= 0.0:
            if 32.0 < rsi < 52.0:  # Bearish momentum, not yet capitulated
                confidence = min(0.90, 0.55 + avg_funding * 400.0)
                return ctx.signal(
                    "short",
                    confidence=confidence,
                    stop_loss_bps=220.0,
                    take_profit_bps=440.0,
                    horizon_seconds=18000,
                    metadata={
                        "reason": "long_liquidation_continuation",
                        "price_return_pct": round(price_return_pct, 3),
                        "avg_funding": round(avg_funding, 6),
                        "cur_funding": round(cur_funding, 6),
                        "rsi": round(rsi, 2)
                    }
                )

        return None