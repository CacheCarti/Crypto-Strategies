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
        self.cooldown_bars = 4
        self.last_trade_bar = -999
        self.bars_in_pos = 0

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
        self.bars_in_pos = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 15)
        highs = ctx.highs(self.lookback + 15)
        lows = ctx.lows(self.lookback + 15)

        if len(closes) < self.lookback + 15:
            return None

        # Position time management: hold up to 6 bars max
        if ctx.has_position():
            self.bars_in_pos += 1
            if self.bars_in_pos >= 6:
                return ctx.signal("flat", confidence=0.5, metadata={
                    "reason": "time_stop_reversion_horizon",
                    "bars_held": self.bars_in_pos,
                    "close": ctx.bar.close
                })
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Regime gate to address prior failure (oos_reject from chopping during trending/crisis periods)
        # Reversion works best in ranging/normal environments, avoid crisis or extreme momentum regimes
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.40:
            return None

        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr <= 0:
            return None

        # Exclude the current active bar to get true prior reference levels
        prior_highs = highs[-(self.lookback + 1):-1]
        prior_lows = lows[-(self.lookback + 1):-1]

        donchian_high = max(prior_highs)
        donchian_low = min(prior_lows)

        current_high = ctx.bar.high
        current_low = ctx.bar.low
        current_close = ctx.bar.close
        min_penetration = 0.25 * atr

        # Setup 1: Bear Trap (Bullish Reversal)
        # Price pokes meaningfully below prior 20-bar low but closes back inside range
        if (current_low < donchian_low - min_penetration) and (current_close > donchian_low):
            wick_depth = donchian_low - current_low
            stop_distance = max(wick_depth * 1.2, atr * 0.8)
            sl_bps = min(350.0, max(120.0, (stop_distance / current_close) * 10000.0))
            tp_bps = sl_bps * 1.6

            self.last_trade_bar = ctx.bar_index
            self.bars_in_pos = 0
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=21600,
                metadata={
                    "reason": "failed_breakout_bear_trap",
                    "donchian_low": donchian_low,
                    "current_low": current_low,
                    "close": current_close,
                    "atr": atr,
                    "penetration_atr": wick_depth / atr
                }
            )

        # Setup 2: Bull Trap (Bearish Reversal)
        # Price pokes meaningfully above prior 20-bar high but closes back inside range
        if (current_high > donchian_high + min_penetration) and (current_close < donchian_high):
            wick_height = current_high - donchian_high
            stop_distance = max(wick_height * 1.2, atr * 0.8)
            sl_bps = min(350.0, max(120.0, (stop_distance / current_close) * 10000.0))
            tp_bps = sl_bps * 1.6

            self.last_trade_bar = ctx.bar_index
            self.bars_in_pos = 0
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=21600,
                metadata={
                    "reason": "failed_breakout_bull_trap",
                    "donchian_high": donchian_high,
                    "current_high": current_high,
                    "close": current_close,
                    "atr": atr,
                    "penetration_atr": wick_height / atr
                }
            )

        return None