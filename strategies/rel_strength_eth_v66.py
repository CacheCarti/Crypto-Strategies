from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcRelativeStrength(Strategy):
    METADATA = {
        "name": "EthBtcRelativeStrength",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 18000,  # ~5 hours average hold for 1h swing
        "warmup_bars": 50,
        "required_features": ["eth_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_exit_bar = -100
        self.spread_threshold = 2.0  # 2.0% spread trigger
        self.max_hold_bars = 24

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        if len(closes) < 50:
            return None

        # Fetch returns
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        # Calculate indicators
        ema20 = self._ema(closes, 20)
        rsi14 = self._rsi(closes, 14)
        if ema20 is None or rsi14 is None:
            return None

        current_price = ctx.bar.close

        # Manage existing position (mean reversion exit or time-stop)
        if ctx.has_position():
            pos = ctx.position
            direction = pos.get("direction") if pos else ctx.position_direction()
            
            # Exit if spread has mean-reverted towards neutral
            if direction == "long" and spread <= 0.2:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "long_spread_reverted",
                    "spread": round(spread, 3),
                    "rsi": round(rsi14, 2),
                    "price": current_price
                })
            elif direction == "short" and spread >= -0.2:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "short_spread_reverted",
                    "spread": round(spread, 3),
                    "rsi": round(rsi14, 2),
                    "price": current_price
                })
            return None

        # Cooldown check after previous trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Crisis filter: do not open new swing positions during severe market stress
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        # Directional entry triggers:
        # Long: ETH outperforms BTC by >= 2.0%, price confirmed above EMA20, and RSI not overbought (< 68)
        if spread >= self.spread_threshold and current_price > ema20 and rsi14 < 68.0:
            conf = min(0.9, 0.6 + (spread - self.spread_threshold) * 0.05)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "rel_strength_eth_leading_breakout",
                    "spread": round(spread, 3),
                    "eth_ret": round(eth_ret, 3),
                    "btc_ret": round(btc_ret, 3),
                    "rsi": round(rsi14, 2),
                    "ema20": round(ema20, 2),
                    "price": current_price
                }
            )

        # Short: ETH lags BTC by <= -2.0%, price confirmed below EMA20, and RSI not oversold (> 32)
        if spread <= -self.spread_threshold and current_price < ema20 and rsi14 > 32.0:
            conf = min(0.9, 0.6 + abs(spread + self.spread_threshold) * 0.05)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "rel_weakness_eth_lagging_breakdown",
                    "spread": round(spread, 3),
                    "eth_ret": round(eth_ret, 3),
                    "btc_ret": round(btc_ret, 3),
                    "rsi": round(rsi14, 2),
                    "ema20": round(ema20, 2),
                    "price": current_price
                }
            )

        return None