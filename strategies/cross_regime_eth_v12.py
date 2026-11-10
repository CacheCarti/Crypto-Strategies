from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcSpreadMeanReversion(Strategy):
    METADATA = {
        "name": "EthBtcSpreadMeanReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 14400,  # 4 hours average hold
        "warmup_bars": 35,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_exit_bar = -999
        self.cooldown_bars = 4
        self.spread_threshold = 1.8  # Material spread difference in %
        self.rsi_period = 14

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Regime gate to avoid poor OOS performance: skip extreme market crisis
        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if market_regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.65:
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "regime_risk_exit", "crisis_score": crisis_score})
            return None

        # Cross-asset returns (24h % return)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        spread = eth_ret - btc_ret  # Positive when ETH outruns BTC, negative when ETH lags

        rsi = self._rsi(closes, self.rsi_period)
        ema_trend = self._ema(closes, 30)
        if rsi is None or ema_trend is None:
            return None

        curr_close = closes[-1]

        # Manage existing position exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # Mean reversion achieved: spread has normalized
            if pos_dir == "long" and (spread > -0.2 or rsi > 62.0):
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "long_mean_reversion_done", "spread": spread, "rsi": rsi})
            elif pos_dir == "short" and (spread < 0.2 or rsi < 38.0):
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "short_mean_reversion_done", "spread": spread, "rsi": rsi})
            return None

        # Cooldown guard to avoid overtrading
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Setup 1: ETH lags BTC materially -> Expect catch-up (Long ETH)
        # Filtered by RSI not overbought, and not in severe persistent structural breakdown
        if spread <= -self.spread_threshold and rsi < 55.0 and btc_ret > -2.0:
            confidence = min(0.9, 0.55 + abs(spread) * 0.08)
            return ctx.signal(
                "long",
                confidence=round(confidence, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_lagging_btc_catchup",
                    "spread": round(spread, 3),
                    "btc_ret": round(btc_ret, 3),
                    "eth_ret": round(eth_ret, 3),
                    "rsi": round(rsi, 2),
                    "close": curr_close,
                }
            )

        # Setup 2: ETH outruns BTC materially -> Expect mean reversion (Short ETH)
        # Filtered by RSI not oversold, and price extended relative to local EMA
        if spread >= self.spread_threshold and rsi > 45.0:
            confidence = min(0.9, 0.55 + spread * 0.08)
            return ctx.signal(
                "short",
                confidence=round(confidence, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_overextended_vs_btc_mean_revert",
                    "spread": round(spread, 3),
                    "btc_ret": round(btc_ret, 3),
                    "eth_ret": round(eth_ret, 3),
                    "rsi": round(rsi, 2),
                    "close": curr_close,
                }
            )

        return None