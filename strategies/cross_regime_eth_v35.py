from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcCatchupReversion(Strategy):
    METADATA = {
        "name": "EthBtcCatchupReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 40,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_exit_bar = -100
        self.spread_threshold = 2.4
        self.rsi_period = 14
        self.ema_period = 30

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
            ema = v * k + ema * (1 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(45)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Check cooldown after prior exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Do not enter if in extreme crisis
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.65:
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_exit", "regime": regime})
            return None

        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        
        # Spread is ETH performance relative to BTC
        # Positive spread: ETH outrunning BTC. Negative spread: ETH lagging BTC.
        spread = eth_ret - btc_ret

        rsi = self._rsi(closes, self.rsi_period)
        ema = self._ema(closes, self.ema_period)
        if rsi is None or ema is None:
            return None

        current_price = ctx.bar.close

        # Position management / exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # Mean reversion of spread achieved
            if pos_dir == "long" and (spread >= 0.2 or rsi >= 65.0):
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "long_tp_spread_reverted", "spread": spread, "rsi": rsi}
                )
            elif pos_dir == "short" and (spread <= -0.2 or rsi <= 35.0):
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "short_tp_spread_reverted", "spread": spread, "rsi": rsi}
                )
            return None

        # Entries: Rare and high-conviction
        # Setup 1: BTC is strong/positive, ETH materially lags (spread < -2.4%), ETH RSI is not overbought (< 52)
        if spread <= -self.spread_threshold and btc_ret > 0.5 and rsi < 52.0 and current_price >= ema * 0.96:
            conf = min(0.85, 0.60 + abs(spread) * 0.04)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_lagging_btc_catchup",
                    "spread": spread,
                    "btc_ret": btc_ret,
                    "eth_ret": eth_ret,
                    "rsi": rsi,
                    "ema": ema
                }
            )

        # Setup 2: ETH outran BTC excessively (spread > 2.4%), ETH RSI stretched (> 60), ETH not in strong structural discount
        if spread >= self.spread_threshold and eth_ret > 0.5 and rsi > 60.0 and current_price <= ema * 1.04:
            conf = min(0.85, 0.60 + spread * 0.04)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_outrunning_btc_reversion",
                    "spread": spread,
                    "btc_ret": btc_ret,
                    "eth_ret": eth_ret,
                    "rsi": rsi,
                    "ema": ema
                }
            )

        return None