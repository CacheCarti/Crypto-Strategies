from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcRelativeStrength(Strategy):
    METADATA = {
        "name": "EthBtcRelativeStrength",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 18000,  # ~5 hours target hold on 1h bars
        "warmup_bars": 40,
        "required_features": ["eth_return_pct", "btc_return_pct"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 16
        self.spread_threshold = 2.0  # 2.0% divergence
        self.ema_period = 20
        self.atr_period = 14

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
        if len(trs) < period:
            return None
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Feature spread: ETH 24h return vs BTC 24h return
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        # Calculate structural baseline and dynamic ATR
        ema20 = self._ema(closes, self.ema_period)
        highs = ctx.highs(40)
        lows = ctx.lows(40)
        atr = self._atr(highs, lows, closes, self.atr_period)
        current_close = ctx.bar.close

        # Manage open position
        if ctx.has_position():
            direction = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar
            
            # Structural exit: mean reversion of spread or timed exit to prevent drawdowns
            mean_reverted = (direction == "long" and spread <= 0.2) or (direction == "short" and spread >= -0.2)
            time_expired = bars_held >= self.max_hold_bars

            if mean_reverted or time_expired:
                reason = "spread_mean_reverted" if mean_reverted else "timed_exit_expired"
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": reason,
                    "spread": round(spread, 3),
                    "bars_held": bars_held,
                    "price": current_close
                })
            return None

        # Entry cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Structural market regime filter (avoid entering crisis melt-down chop)
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Dynamic volatility stop calculation based on ATR
        atr_val = atr if (atr is not None and current_close > 0) else (current_close * 0.015)
        atr_bps = (atr_val / current_close) * 10000.0
        dyn_sl_bps = max(180.0, min(380.0, atr_bps * 1.5))
        dyn_tp_bps = max(280.0, dyn_sl_bps * 1.5)

        # Long: ETH leading BTC strongly and price confirmed above trend
        if spread >= self.spread_threshold and (ema20 is not None and current_close > ema20):
            self.entry_bar = ctx.bar_index
            confidence = min(0.9, 0.55 + (abs(spread) - self.spread_threshold) * 0.1)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=dyn_sl_bps,
                take_profit_bps=dyn_tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_outperformance_trend_confirm",
                    "spread": round(spread, 3),
                    "eth_ret": round(eth_ret, 3),
                    "btc_ret": round(btc_ret, 3),
                    "atr_bps": round(atr_bps, 1),
                    "ema20": round(ema20, 2)
                }
            )

        # Short: ETH lagging BTC strongly and price confirmed below trend
        if spread <= -self.spread_threshold and (ema20 is not None and current_close < ema20):
            self.entry_bar = ctx.bar_index
            confidence = min(0.9, 0.55 + (abs(spread) - self.spread_threshold) * 0.1)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=dyn_sl_bps,
                take_profit_bps=dyn_tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_underperformance_trend_confirm",
                    "spread": round(spread, 3),
                    "eth_ret": round(eth_ret, 3),
                    "btc_ret": round(btc_ret, 3),
                    "atr_bps": round(atr_bps, 1),
                    "ema20": round(ema20, 2)
                }
            )

        return None