from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcSpreadMeanReversion(Strategy):
    METADATA = {
        "name": "EthBtcSpreadMeanReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 40,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.last_entry_bar = -999
        self.spread_threshold = 2.2
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
        closes = ctx.closes(40)
        if len(closes) < 40:
            return None

        # Check cooldown after position closure or recent entry
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None
        if (ctx.bar_index - self.last_entry_bar) < self.cooldown_bars:
            return None

        # Market regime guard: avoid turbulent meltdown or crisis
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        # Relative spread: ETH 24h return minus BTC 24h return
        spread = eth_ret - btc_ret

        rsi = self._rsi(closes, self.rsi_period)
        ema_fast = self._ema(closes, 9)
        ema_slow = self._ema(closes, 21)

        if rsi is None or ema_fast is None or ema_slow is None:
            return None

        current_price = ctx.bar.close

        # Long Catch-Up Setup: ETH substantially lags BTC, not heavily overbought
        # spread <= -2.2% indicates ETH lagging BTC by at least 220 bps in 24h
        if not ctx.has_position():
            if spread <= -self.spread_threshold and rsi < 58.0 and current_price >= ema_slow * 0.985:
                self.last_entry_bar = ctx.bar_index
                conf = min(0.85, 0.55 + abs(spread) * 0.05)
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "eth_lagging_btc_catchup_long",
                        "spread": round(spread, 3),
                        "eth_ret": round(eth_ret, 3),
                        "btc_ret": round(btc_ret, 3),
                        "rsi": round(rsi, 2),
                        "price": round(current_price, 2),
                    },
                )

            # Short Mean-Reversion Setup: ETH outran BTC materially, not oversold
            # spread >= +2.2% indicates ETH leads BTC by at least 220 bps in 24h
            elif spread >= self.spread_threshold and rsi > 42.0 and current_price <= ema_slow * 1.015:
                self.last_entry_bar = ctx.bar_index
                conf = min(0.85, 0.55 + spread * 0.05)
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "eth_outrunning_btc_reversion_short",
                        "spread": round(spread, 3),
                        "eth_ret": round(eth_ret, 3),
                        "btc_ret": round(btc_ret, 3),
                        "rsi": round(rsi, 2),
                        "price": round(current_price, 2),
                    },
                )

        # In-position early spread-neutralization exit
        else:
            pos_dir = ctx.position_direction()
            # If long and ETH has caught up / flipped to leading
            if pos_dir == "long" and spread >= 0.5:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "spread_neutralized_exit_long",
                        "spread": round(spread, 3),
                        "rsi": round(rsi, 2),
                    },
                )
            # If short and ETH premium has dissipated
            elif pos_dir == "short" and spread <= -0.5:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "spread_neutralized_exit_short",
                        "spread": round(spread, 3),
                        "rsi": round(rsi, 2),
                    },
                )

        return None