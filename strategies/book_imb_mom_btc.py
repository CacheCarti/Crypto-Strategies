from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class BookImbalanceBreakout(Strategy):
    METADATA = {
        "name": "Book Imbalance Momentum Breakout",
        "domain": "btc_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 35,
        "required_features": ["book_imbalance_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 20
        self.exit_ema_period = 9
        self.cooldown_bars = 10
        self.last_exit_bar = -100

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        highs = ctx.highs(35)
        lows = ctx.lows(35)
        volumes = ctx.volumes(35)

        if len(closes) < 30 or len(highs) < 30 or len(lows) < 30 or len(volumes) < 30:
            return None

        current_close = closes[-1]
        ema_exit = self._ema(closes, self.exit_ema_period)
        if ema_exit is None:
            return None

        # Position Management & Momentum Stall Exit
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # Require close cleanly crossing momentum EMA to exit
            if pos_dir == "long" and current_close < ema_exit and closes[-2] >= ema_exit:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "long_momentum_stalled_cross_below_ema",
                        "close": current_close,
                        "ema_exit": ema_exit,
                        "bar_index": ctx.bar_index,
                    },
                )
            elif pos_dir == "short" and current_close > ema_exit and closes[-2] <= ema_exit:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "short_momentum_stalled_cross_above_ema",
                        "close": current_close,
                        "ema_exit": ema_exit,
                        "bar_index": ctx.bar_index,
                    },
                )
            return None

        # Strict multi-bar cooldown guard to prevent overtrading & friction bleed
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Skip high-crisis market states
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # 20-bar Donchian breakout levels (excluding current bar)
        prev_high = max(highs[-self.lookback - 1 : -1])
        prev_low = min(lows[-self.lookback - 1 : -1])

        # Volume filter: breakout bar volume should be above 20-bar average
        avg_vol = self._sma(volumes[:-1], 20)
        current_vol = volumes[-1]
        if avg_vol is None or current_vol < avg_vol * 1.05:
            return None

        # Order-flow filter: require substantial imbalance conviction
        imbalance = ctx.features.get("book_imbalance_btcusdt", 0.0)

        # Long breakout trigger with solid positive orderbook imbalance
        if current_close > prev_high and imbalance >= 0.12:
            self.last_exit_bar = ctx.bar_index
            conf = min(0.9, max(0.6, 0.6 + abs(imbalance) * 0.3))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "donchian_high_breakout_confirmed_imbalance",
                    "close": current_close,
                    "prev_high": prev_high,
                    "imbalance": imbalance,
                    "vol_ratio": round(current_vol / avg_vol, 2),
                    "ema_exit": ema_exit,
                },
            )

        # Short breakdown trigger with solid negative orderbook imbalance
        if current_close < prev_low and imbalance <= -0.12:
            self.last_exit_bar = ctx.bar_index
            conf = min(0.9, max(0.6, 0.6 + abs(imbalance) * 0.3))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "donchian_low_breakdown_confirmed_imbalance",
                    "close": current_close,
                    "prev_low": prev_low,
                    "imbalance": imbalance,
                    "vol_ratio": round(current_vol / avg_vol, 2),
                    "ema_exit": ema_exit,
                },
            )

        return None