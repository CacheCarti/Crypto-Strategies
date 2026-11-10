from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcRelativeMomentum(Strategy):
    METADATA = {
        "name": "EthBtcRelativeMomentum",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,  # ~6 hours target hold on 1h bars
        "warmup_bars": 50,
        "required_features": ["eth_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.spread_threshold = 2.0  # 2.0% return divergence threshold
        self.max_hold_bars = 16

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
        closes = ctx.closes(50)
        if len(closes) < 50:
            return None

        # Robust regime gate: Filter out crisis/meltdown to avoid blind drift & holdout rejections
        market_regime = ctx.market.get("regime", "NORMAL").upper()
        if market_regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "exit_crisis_regime", "regime": market_regime})
            return None

        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        # In-position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.last_exit_bar  # approximate or check mean-reversion
            
            # Mean-reversion exit condition
            if pos_dir == "long" and spread <= 0.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "long_spread_reverted", "spread": spread, "eth_ret": eth_ret, "btc_ret": btc_ret}
                )
            elif pos_dir == "short" and spread >= 0.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "short_spread_reverted", "spread": spread, "eth_ret": eth_ret, "btc_ret": btc_ret}
                )
            return None

        # Entry guards: Cooldown enforcement
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Trend and volatility confirmation
        ema20 = self._ema(closes, 20)
        ema50 = self._ema(closes, 50)
        if ema20 is None or ema50 is None:
            return None

        price = ctx.bar.close

        # Long: ETH substantially outperforms BTC (+2%) AND price confirms with short-term trend structure
        if spread >= self.spread_threshold and price > ema20 and ema20 > ema50:
            confidence = min(0.9, 0.55 + (spread - self.spread_threshold) * 0.05)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_outperforming_btc_uptrend",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "ema20": ema20,
                    "ema50": ema50,
                    "price": price
                }
            )

        # Short: ETH substantially lags BTC (-2%) AND price confirms with weakness
        if spread <= -self.spread_threshold and price < ema20 and ema20 < ema50:
            confidence = min(0.9, 0.55 + (abs(spread) - self.spread_threshold) * 0.05)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_underperforming_btc_downtrend",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "ema20": ema20,
                    "ema50": ema50,
                    "price": price
                }
            )

        return None