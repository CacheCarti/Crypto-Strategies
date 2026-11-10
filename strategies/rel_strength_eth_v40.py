from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcRelativeMomentum(Strategy):
    METADATA = {
        "name": "EthBtcRelativeMomentum",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
        "required_features": ["eth_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.prev_spread = 0.0
        self.spread_threshold = 2.2
        self.reversion_threshold = 0.5
        self.bars_in_trade = 0

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
        self.bars_in_trade = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        highs = ctx.highs(20)
        lows = ctx.lows(20)
        if len(closes) < 35 or len(highs) < 20 or len(lows) < 20:
            return None

        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        # Track spread state boundary crossing
        prev_s = self.prev_spread
        self.prev_spread = spread

        if ctx.has_position():
            self.bars_in_trade += 1
            pos_dir = ctx.position_direction()

            # Mean-reversion exit or max duration exit
            if pos_dir == "long" and spread <= self.reversion_threshold:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "long_spread_reverted",
                    "spread": spread,
                    "bars_held": self.bars_in_trade
                })
            elif pos_dir == "short" and spread >= -self.reversion_threshold:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "short_spread_reverted",
                    "spread": spread,
                    "bars_held": self.bars_in_trade
                })
            return None

        # Check cooldown period
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Market regime filter: avoid meltdowns or hyper-volatile shocks
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        ema20 = self._ema(closes, 20)
        if ema20 is None:
            return None

        curr_close = ctx.bar.close

        # Stricter event-boundary trigger: fresh cross of threshold + price alignment
        # Long entry: Spread breaks above threshold this bar and price confirms trend
        if prev_s <= self.spread_threshold and spread > self.spread_threshold:
            if curr_close > ema20:
                conf = min(0.9, 0.55 + abs(spread) * 0.05)
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "relative_strength_cross_over",
                        "spread": spread,
                        "prev_spread": prev_s,
                        "eth_ret": eth_ret,
                        "btc_ret": btc_ret,
                        "ema20": ema20,
                        "close": curr_close
                    }
                )

        # Short entry: Spread breaks below negative threshold this bar and price confirms downtrend
        if prev_s >= -self.spread_threshold and spread < -self.spread_threshold:
            if curr_close < ema20:
                conf = min(0.9, 0.55 + abs(spread) * 0.05)
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "relative_weakness_cross_under",
                        "spread": spread,
                        "prev_spread": prev_s,
                        "eth_ret": eth_ret,
                        "btc_ret": btc_ret,
                        "ema20": ema20,
                        "close": curr_close
                    }
                )

        return None