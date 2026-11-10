from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthRelativeStrengthMomentum(Strategy):
    METADATA = {
        "name": "EthRelativeStrengthMomentum",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 18000,  # ~5 hours average hold
        "warmup_bars": 40,
        "required_features": ["eth_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_threshold = 2.2      # 2.2% 24h spread requirement
        self.cooldown_bars = 6          # 6 bars (~6 hours) cooldown to prevent overtrading
        self.last_exit_bar = -100
        self.prev_spread = 0.0

    def _ema(self, values, period: int):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _atr(self, highs, lows, closes, period: int = 14):
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
        closes = ctx.closes(40)
        if len(closes) < 40:
            return None

        # Feature inputs
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        # Avoid trading during extreme crisis/meltdown regimes
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "risk_off_regime_exit", "regime": market_regime, "spread": spread}
                )
            return None

        # Position management: exit on mean reversion of spread
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and spread <= 0.3:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "eth_momentum_reverted_flat", "spread": spread, "eth_ret": eth_ret, "btc_ret": btc_ret}
                )
            elif pos_dir == "short" and spread >= -0.3:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "eth_underperformance_reverted_flat", "spread": spread, "eth_ret": eth_ret, "btc_ret": btc_ret}
                )
            return None

        # Mandatory cooldown check
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            self.prev_spread = spread
            return None

        # Price confirmation indicators
        highs = ctx.highs(40)
        lows = ctx.lows(40)
        ema20 = self._ema(closes, 20)
        atr = self._atr(highs, lows, closes, 14)
        curr_price = ctx.bar.close

        if ema20 is None or atr is None:
            self.prev_spread = spread
            return None

        # Clean event-boundary trigger: fresh crossover of spread threshold
        long_event = (self.prev_spread <= self.spread_threshold) and (spread > self.spread_threshold)
        short_event = (self.prev_spread >= -self.spread_threshold) and (spread < -self.spread_threshold)

        self.prev_spread = spread

        # Dynamic stop loss based on volatility (ATR bps) clamped to safe range
        atr_bps = (atr / curr_price) * 10000.0
        sl_bps = max(180.0, min(350.0, atr_bps * 1.8))
        tp_bps = sl_bps * 1.6

        # Go Long ETH: leading BTC with breakout cross above threshold + price confirming above EMA20
        if long_event and curr_price > ema20:
            confidence = min(0.9, 0.60 + min(abs(spread) / 10.0, 0.25))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=18000,
                metadata={
                    "reason": "eth_outperformance_event_cross",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "ema20": ema20,
                    "price": curr_price,
                    "atr_bps": atr_bps
                }
            )

        # Go Short ETH: lagging BTC with cross below -threshold + price confirming below EMA20
        if short_event and curr_price < ema20:
            confidence = min(0.9, 0.60 + min(abs(spread) / 10.0, 0.25))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=18000,
                metadata={
                    "reason": "eth_underperformance_event_cross",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "ema20": ema20,
                    "price": curr_price,
                    "atr_bps": atr_bps
                }
            )

        return None