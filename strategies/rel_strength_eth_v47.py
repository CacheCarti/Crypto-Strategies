from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcRelativeStrengthMomentum(Strategy):
    METADATA = {
        "name": "EthBtcRelativeStrengthMomentum",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
        "required_features": ["eth_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 4
        self.last_exit_bar = -100
        self.max_hold_bars = 16
        self.entry_bar = -100
        self.min_spread_threshold = 2.2
        self.reversion_threshold = 0.4

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

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
        highs = ctx.highs(35)
        lows = ctx.lows(35)

        if len(closes) < 30:
            return None

        # Fetch cross-asset 24h returns
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        # Dynamic volatility tracking
        atr = self._atr(highs, lows, closes, period=14)
        ema20 = self._ema(closes, 20)
        close = ctx.bar.close

        # Manage active position via structural & timed exits
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            pos_dir = ctx.position_direction()

            # 1. Timed exit to prevent capital lockup and degradation
            if bars_held >= self.max_hold_bars:
                return ctx.signal("flat", confidence=0.5, metadata={
                    "reason": "timed_horizon_exit",
                    "bars_held": bars_held,
                    "spread": spread
                })

            # 2. Reversion exit: exit when spread mean-reverts back near parity
            if pos_dir == "long" and spread <= self.reversion_threshold:
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "spread_mean_reverted_long_exit",
                    "spread": spread,
                    "bars_held": bars_held
                })
            elif pos_dir == "short" and spread >= -self.reversion_threshold:
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "spread_mean_reverted_short_exit",
                    "spread": spread,
                    "bars_held": bars_held
                })

            # 3. Volatility stop: trailing breaker based on 2x ATR against local price
            if atr is not None and ctx.position:
                entry_price = ctx.position.get("entry_price", close)
                if pos_dir == "long" and (entry_price - close) > (atr * 2.2):
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "volatility_stop_long",
                        "price": close,
                        "atr": atr
                    })
                elif pos_dir == "short" and (close - entry_price) > (atr * 2.2):
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "volatility_stop_short",
                        "price": close,
                        "atr": atr
                    })

            return None

        # Check entry cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Avoid entries in extreme crisis regimes
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        # Dynamic stop calculation based on ATR
        sl_bps = 280.0
        tp_bps = 480.0
        if atr is not None and close > 0:
            atr_bps = (atr / close) * 10000.0
            sl_bps = max(180.0, min(420.0, atr_bps * 1.8))
            tp_bps = max(280.0, min(650.0, atr_bps * 3.0))

        # Relative strength long: ETH decisively leading BTC + confirmed by price above local EMA
        if spread >= self.min_spread_threshold and ema20 is not None and close >= ema20:
            self.entry_bar = ctx.bar_index
            conf = min(0.9, 0.55 + (abs(spread) / 10.0))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=14400,
                metadata={
                    "reason": "rel_strength_momentum_long",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "atr": atr,
                    "close": close,
                    "ema20": ema20
                }
            )

        # Relative weakness short: ETH decisively lagging BTC + confirmed by price below local EMA
        elif spread <= -self.min_spread_threshold and ema20 is not None and close <= ema20:
            self.entry_bar = ctx.bar_index
            conf = min(0.9, 0.55 + (abs(spread) / 10.0))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=14400,
                metadata={
                    "reason": "rel_weakness_momentum_short",
                    "spread": spread,
                    "eth_ret": eth_ret,
                    "btc_ret": btc_ret,
                    "atr": atr,
                    "close": close,
                    "ema20": ema20
                }
            )

        return None