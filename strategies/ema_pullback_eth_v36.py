from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthEmaPullbackRegime(Strategy):
    METADATA = {
        "name": "EthEmaPullbackRegime",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 20
        self.slow_period = 50
        self.rsi_period = 14
        self.cooldown_bars = 6
        self.last_trade_bar = -999

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _rsi(self, closes, period: int = 14) -> Optional[float]:
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
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.slow_period + 10)
        lows = ctx.lows(5)
        highs = ctx.highs(5)

        if len(closes) < self.slow_period + 5:
            return None

        ema20 = self._ema(closes, self.fast_period)
        ema50 = self._ema(closes, self.slow_period)
        rsi = self._rsi(closes, self.rsi_period)

        if ema20 is None or ema50 is None or rsi is None:
            return None

        curr_close = closes[-1]
        prev_close = closes[-2]
        curr_low = lows[-1]
        prev_low = lows[-2]
        curr_high = highs[-1]
        prev_high = highs[-2]

        # Manage open positions
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and curr_close < ema20:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "exit_long_ema20_cross_under", "close": curr_close, "ema20": ema20}
                )
            elif pos_dir == "short" and curr_close > ema20:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "exit_short_ema20_cross_over", "close": curr_close, "ema20": ema20}
                )
            return None

        # Cooldown guard after trade
        if (ctx.bar_index - self.last_trade_bar) < self.cooldown_bars:
            return None

        # Market regime filter
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        trend_regime = ctx.market.get("trend_regime", "neutral")

        # Long Setup:
        # 1. Structural uptrend: EMA20 > EMA50, Trend Regime not bear
        # 2. Pullback & reclaim: previous bar dipped below EMA20 (or current low penetrated it), current close reclaimed above EMA20
        # 3. RSI sanity check (not severely overbought, showing upward momentum from pullback)
        is_uptrend = (ema20 > ema50) and (curr_close > ema50) and (trend_regime != "bear")
        long_pullback = (prev_low <= ema20 or curr_low <= ema20) and (prev_close <= ema20) and (curr_close > ema20)
        long_rsi_valid = 40.0 <= rsi <= 65.0

        if is_uptrend and long_pullback and long_rsi_valid:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "ema_pullback_reclaim_long",
                    "close": curr_close,
                    "ema20": ema20,
                    "ema50": ema50,
                    "rsi": rsi,
                    "regime": regime,
                    "trend_regime": trend_regime
                }
            )

        # Short Setup:
        # 1. Structural downtrend: EMA20 < EMA50, Trend Regime not bull
        # 2. Pullback & rejection: previous bar or current high pierced EMA20, current close fell back below EMA20
        # 3. RSI sanity check (not severely oversold)
        is_downtrend = (ema20 < ema50) and (curr_close < ema50) and (trend_regime != "bull")
        short_pullback = (prev_high >= ema20 or curr_high >= ema20) and (prev_close >= ema20) and (curr_close < ema20)
        short_rsi_valid = 35.0 <= rsi <= 60.0

        if is_downtrend and short_pullback and short_rsi_valid:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "ema_pullback_rejection_short",
                    "close": curr_close,
                    "ema20": ema20,
                    "ema50": ema50,
                    "rsi": rsi,
                    "regime": regime,
                    "trend_regime": trend_regime
                }
            )

        return None