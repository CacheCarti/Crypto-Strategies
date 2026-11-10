from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcSpreadMeanReversion(Strategy):
    METADATA = {
        "name": "EthBtcSpreadMeanReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 40,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.rsi_period = 14
        self.bb_period = 20
        self.spread_threshold = 1.6

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

    def _bollinger_width(self, closes, period=20):
        if len(closes) < period:
            return None, None
        slice_c = closes[-period:]
        mean = sum(slice_c) / period
        variance = sum((x - mean) ** 2 for x in slice_c) / period
        std = math.sqrt(variance)
        bandwidth = (4.0 * std) / mean if mean > 0 else 0.0
        return mean, bandwidth

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.bb_period + 5)
        if len(closes) < self.bb_period + 1:
            return None

        # Guard against crisis or runaway meltdown
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.65:
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "risk_regime_exit", "regime": regime})
            return None

        # Position exit handling via mean-reversion normalization
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        spread = eth_ret - btc_ret

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        sma20, bandwidth = self._bollinger_width(closes, self.bb_period)
        if bandwidth is None:
            return None

        current_price = ctx.bar.close

        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # Mean reversion realized: spread flips sign or RSI normalizes
            if pos_dir == "long" and (spread > 0.4 or rsi > 58.0 or current_price > sma20):
                return ctx.signal("flat", confidence=0.65, metadata={
                    "reason": "long_spread_reverted",
                    "spread": spread,
                    "rsi": rsi,
                    "price": current_price
                })
            elif pos_dir == "short" and (spread < -0.4 or rsi < 42.0 or current_price < sma20):
                return ctx.signal("flat", confidence=0.65, metadata={
                    "reason": "short_spread_reverted",
                    "spread": spread,
                    "rsi": rsi,
                    "price": current_price
                })
            return None

        # Check mandatory cooldown
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Filter out extreme volatility compression (dead chop) or excessive blowout
        if bandwidth < 0.015 or bandwidth > 0.09:
            return None

        # Long Catch-Up: ETH heavily lags BTC, but ETH RSI shows local oversold/base building
        if spread <= -self.spread_threshold and rsi < 38.0 and current_price < sma20:
            confidence = min(0.9, 0.55 + abs(spread) / 10.0)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_lag_catchup_long",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "rsi": rsi,
                    "bandwidth": bandwidth
                }
            )

        # Short Overextension: ETH massively outruns BTC, but ETH RSI shows overbought exhaustion
        if spread >= self.spread_threshold and rsi > 62.0 and current_price > sma20:
            confidence = min(0.9, 0.55 + abs(spread) / 10.0)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_outrun_mean_revert_short",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "rsi": rsi,
                    "bandwidth": bandwidth
                }
            )

        return None