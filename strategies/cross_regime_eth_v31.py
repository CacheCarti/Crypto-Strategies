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
        self.cooldown_bars = 4
        self.last_exit_bar = -100
        self.spread_threshold = 1.4  # Minimum 1.4% return spread divergence
        self.max_hold_bars = 8

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
        if avg_loss == 0:
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
        closes = ctx.closes(35)
        if len(closes) < 35:
            return None

        # Cooldown guard: mandate pauses between trades to maintain trade count target
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Cross-asset spread calculation: spread = ETH 24h return - BTC 24h return
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        rsi = self._rsi(closes, 14)
        ema20 = self._ema(closes, 20)
        if rsi is None or ema20 is None:
            return None

        # In-position management
        if ctx.has_position():
            pos = ctx.position
            pos_dir = ctx.position_direction()
            entry_price = pos.get("entry_price", ctx.bar.close) if pos else ctx.bar.close

            # Mean-reversion exit when spread normalizes towards parity
            if pos_dir == "long":
                if spread >= -0.2 or rsi >= 60.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_spread_reverted",
                            "spread": spread,
                            "rsi": rsi,
                            "price": ctx.bar.close,
                        },
                    )
            elif pos_dir == "short":
                if spread <= 0.2 or rsi <= 40.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_spread_reverted",
                            "spread": spread,
                            "rsi": rsi,
                            "price": ctx.bar.close,
                        },
                    )
            return None

        # Market regime filter: avoid entering against panic/meltdowns
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Long Setup: BTC leads, ETH lags significantly, and ETH is not severely overbought
        # spread <= -1.4% means ETH return is materially lower than BTC return
        if spread <= -self.spread_threshold and rsi < 55.0 and ctx.bar.close > ema20 * 0.96:
            conf = min(0.9, 0.6 + abs(spread) * 0.05)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_lagging_btc_catchup",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "rsi": rsi,
                    "ema20": ema20,
                },
            )

        # Short Setup: ETH outruns BTC by wide margin, and ETH is not severely oversold
        # spread >= 1.4% means ETH return outpaced BTC
        if spread >= self.spread_threshold and rsi > 45.0 and ctx.bar.close < ema20 * 1.04:
            conf = min(0.9, 0.6 + abs(spread) * 0.05)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_overextended_vs_btc_mean_revert",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "rsi": rsi,
                    "ema20": ema20,
                },
            )

        return None