from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcRelativeStrengthMomentum(Strategy):
    METADATA = {
        "name": "EthBtcRelativeStrengthMomentum",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
        "required_features": ["eth_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 4
        self.last_exit_bar = -100
        self.spread_threshold = 1.8
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
        highs = ctx.highs(20)
        lows = ctx.lows(20)

        if len(closes) < 35 or len(highs) < 20 or len(lows) < 20:
            return None

        # Feature inputs
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        # Volatility check via normalized ATR
        atr = self._atr(highs, lows, closes, 14)
        if atr is None or closes[-1] == 0:
            return None
        atr_pct = (atr / closes[-1]) * 100.0

        # Technical trend confirmation
        ema21 = self._ema(closes, self.ema_period)
        if ema21 is None:
            return None

        current_close = closes[-1]
        prev_close = closes[-2]

        # Manage open position: mean reversion / loss of divergence
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long":
                if spread < 0.2 or current_close < ema21:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_spread_reverted_or_ema_lost",
                            "spread": spread,
                            "close": current_close,
                            "ema21": ema21,
                        },
                    )
            elif pos_dir == "short":
                if spread > -0.2 or current_close > ema21:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_spread_reverted_or_ema_reclaimed",
                            "spread": spread,
                            "close": current_close,
                            "ema21": ema21,
                        },
                    )
            return None

        # Mandatory Cooldown Gate
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Avoid entry during extreme low-volatility dead markets or extreme runaway panic
        if atr_pct < 0.25 or atr_pct > 3.5:
            return None

        # Cross-asset momentum entries
        # Long: ETH outpacing BTC by threshold + confirming price above EMA and rising
        if spread >= self.spread_threshold and current_close > ema21 and current_close > prev_close:
            conf = min(0.85, 0.55 + (spread - self.spread_threshold) * 0.08)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_btc_rel_strength_breakout",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "atr_pct": atr_pct,
                    "ema21": ema21,
                    "close": current_close,
                },
            )

        # Short: ETH lagging BTC by threshold + confirming price below EMA and falling
        if spread <= -self.spread_threshold and current_close < ema21 and current_close < prev_close:
            conf = min(0.85, 0.55 + (abs(spread) - self.spread_threshold) * 0.08)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "eth_btc_rel_weakness_breakdown",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "atr_pct": atr_pct,
                    "ema21": ema21,
                    "close": current_close,
                },
            )

        return None