from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcSpreadMeanReversion(Strategy):
    METADATA = {
        "name": "EthBtcSpreadMeanReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 40,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.max_hold_bars = 16
        self.spread_threshold = 2.0
        self.rsi_period = 14
        self.entry_bar = -1

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
        closes = ctx.closes(40)
        highs = ctx.highs(40)
        lows = ctx.lows(40)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown guard after recent exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        rsi = self._rsi(closes, self.rsi_period)
        atr = self._atr(highs, lows, closes, 14)
        if rsi is None or atr is None:
            return None

        price = ctx.bar.close
        atr_pct = (atr / price) * 10000.0 if price > 0 else 250.0

        # Features: 24h rolling return %
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        spread = eth_ret - btc_ret  # positive means ETH outrunning BTC, negative means ETH lagging

        # Structural Risk Plan: Time-based decay exit and volatility-adjusted SL
        dynamic_sl = max(180.0, min(380.0, atr_pct * 1.5))
        dynamic_tp = max(240.0, min(500.0, atr_pct * 2.2))

        # Handle active position management (timed exit / mean-reversion exhaustion)
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar if self.entry_bar > 0 else 0
            pos_dir = ctx.position_direction()

            # Time stop to mitigate giveback / stagnation
            if bars_held >= self.max_hold_bars:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "timed_decay_exit", "bars_held": bars_held, "spread": spread})

            # Early spread normalization exit
            if pos_dir == "long" and spread >= -0.2:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "spread_normalized_long_exit", "spread": spread, "rsi": rsi})
            if pos_dir == "short" and spread <= 0.2:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "spread_normalized_short_exit", "spread": spread, "rsi": rsi})

            return None

        # Regime safety filter: skip entry during crisis meltdowns
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        # Entry logic: ETH lag with oversold bounce or ETH overshoot with overbought exhaustion
        if spread <= -self.spread_threshold and rsi < 44.0:
            confidence = min(0.9, 0.55 + min(abs(spread), 6.0) / 15.0)
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=dynamic_sl,
                take_profit_bps=dynamic_tp,
                metadata={
                    "reason": "eth_btc_lag_catchup_long",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "rsi": rsi,
                    "dynamic_sl": dynamic_sl,
                },
            )

        if spread >= self.spread_threshold and rsi > 56.0:
            confidence = min(0.9, 0.55 + min(abs(spread), 6.0) / 15.0)
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=dynamic_sl,
                take_profit_bps=dynamic_tp,
                metadata={
                    "reason": "eth_btc_overshoot_revert_short",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "rsi": rsi,
                    "dynamic_sl": dynamic_sl,
                },
            )

        return None