from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcRelativeStrengthMomentum(Strategy):
    METADATA = {
        "name": "EthBtcRelativeStrengthMomentum",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
        "required_features": ["eth_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.spread_threshold = 2.0
        self.ema_period = 21

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

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
        closes = ctx.closes(35)
        highs = ctx.highs(35)
        lows = ctx.lows(35)
        
        if len(closes) < 35:
            return None

        current_close = ctx.bar.close
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Exit conditions: spread mean-reversion
        if has_pos:
            if pos_dir == "long" and spread <= 0.2:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "eth_outperformance_dissipated", "spread": spread, "close": current_close}
                )
            elif pos_dir == "short" and spread >= -0.2:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "eth_underperformance_dissipated", "spread": spread, "close": current_close}
                )
            return None

        # Mandatory cooldown to avoid churn
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Volatility & Trend Confirmation (avoid chop & crisis noise)
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        ema21 = self._ema(closes, self.ema_period)
        if ema21 is None:
            return None

        atr14 = self._atr(highs, lows, closes, 14)
        if atr14 is None or atr14 <= 0:
            return None

        # Volatility expansion state: ensure market is moving, not flatlining
        atr_pct = (atr14 / current_close) * 10000.0
        if atr_pct < 25.0:  # Avoid ultra-low volatility false breakouts
            return None

        # Entry logic: ETH breaking away from BTC with price confirming above/below local trend
        if spread > self.spread_threshold and current_close > ema21:
            confidence = min(0.5 + (spread - self.spread_threshold) * 0.1, 0.95)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=280.0,
                take_profit_bps=450.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "eth_leads_btc_trend_aligned",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "ema21": ema21,
                    "close": current_close,
                    "atr_bps": atr_pct
                }
            )

        if spread < -self.spread_threshold and current_close < ema21:
            confidence = min(0.5 + (abs(spread) - self.spread_threshold) * 0.1, 0.95)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=280.0,
                take_profit_bps=450.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "eth_lags_btc_trend_aligned",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "ema21": ema21,
                    "close": current_close,
                    "atr_bps": atr_pct
                }
            )

        return None