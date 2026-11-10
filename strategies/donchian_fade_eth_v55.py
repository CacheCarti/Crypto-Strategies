from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class DonchianTrapFade(Strategy):
    METADATA = {
        "name": "DonchianTrapFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 18000,  # ~5 hours target hold
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 20
        self.atr_period = 14
        self.min_wick_atr_mult = 0.35  # Meaningful wick beyond range
        self.cooldown_bars = 6
        self.max_hold_bars = 6
        self.last_trade_bar = -999
        self.entry_bar = -999

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        req_len = max(self.lookback + 2, self.atr_period + 2)
        closes = ctx.closes(req_len)
        if len(closes) < req_len:
            return None

        highs = ctx.highs(req_len)
        lows = ctx.lows(req_len)
        cur_bar = ctx.bar
        idx = ctx.bar_index

        # Time-based exit for swing fade
        if ctx.has_position():
            if idx - self.entry_bar >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "max_bars_timeout", "bars_held": idx - self.entry_bar}
                )
            return None

        # Mandatory post-exit cooldown
        if idx - self.last_trade_bar < self.cooldown_bars:
            return None

        # Avoid extreme meltdown regimes
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # 20-bar Donchian channel strictly PRIOR to current bar
        prior_highs = highs[-self.lookback - 1 : -1]
        prior_lows = lows[-self.lookback - 1 : -1]
        prior_hh = max(prior_highs)
        prior_ll = min(prior_lows)

        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        min_wick = atr * self.min_wick_atr_mult
        c_price = cur_bar.close

        # Bear Trap / Liquidity Sweep Below: Poked below prior LL but closed back inside
        if cur_bar.low < prior_ll and c_price > prior_ll:
            wick_depth = prior_ll - cur_bar.low
            if wick_depth >= min_wick:
                wick_bps = (wick_depth / c_price) * 10000.0
                sl_bps = max(140.0, min(300.0, wick_bps + 40.0))
                tp_bps = sl_bps * 1.6
                self.last_trade_bar = idx
                self.entry_bar = idx
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    metadata={
                        "reason": "bear_trap_donchian_low_fade",
                        "prior_ll": prior_ll,
                        "low": cur_bar.low,
                        "close": c_price,
                        "wick_depth": wick_depth,
                        "atr": atr,
                    }
                )

        # Bull Trap / Liquidity Sweep Above: Poked above prior HH but closed back inside
        if cur_bar.high > prior_hh and c_price < prior_hh:
            wick_depth = cur_bar.high - prior_hh
            if wick_depth >= min_wick:
                wick_bps = (wick_depth / c_price) * 10000.0
                sl_bps = max(140.0, min(300.0, wick_bps + 40.0))
                tp_bps = sl_bps * 1.6
                self.last_trade_bar = idx
                self.entry_bar = idx
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    metadata={
                        "reason": "bull_trap_donchian_high_fade",
                        "prior_hh": prior_hh,
                        "high": cur_bar.high,
                        "close": c_price,
                        "wick_depth": wick_depth,
                        "atr": atr,
                    }
                )

        return None