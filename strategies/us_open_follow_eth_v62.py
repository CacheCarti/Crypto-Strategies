from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsOpenContinuationEth(Strategy):
    METADATA = {
        "name": "US Open Continuation ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_day = -1
        self.entry_bar_index = -1
        self.cooldown_until_bar = 0

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

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
        self.cooldown_until_bar = ctx.bar_index + 4

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        highs = ctx.highs(40)
        lows = ctx.lows(40)

        if len(closes) < 30:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.toordinal()

        # Exit intraday trades by UTC 21:00
        if ctx.has_position():
            if current_hour >= 21 or current_hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_cutoff", "hour": current_hour, "bar_index": ctx.bar_index}
                )
            return None

        # Mandatory cooldown and 1-trade-per-day rule
        if ctx.bar_index < self.cooldown_until_bar:
            return None
        if self.last_traded_day == current_day:
            return None

        # Filter out extreme crisis regimes (prior failure avoided by gating)
        crisis_score = float(ctx.market.get("crisis_score", 0.0))
        market_regime = str(ctx.market.get("regime", "NORMAL")).upper()
        if crisis_score > 0.45 or market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Signal trigger at UTC 15:00 after observing the 13:00-15:00 2-hour window
        if current_hour != 15:
            return None

        atr = self._atr(highs, lows, closes, period=14)
        rsi = self._rsi(closes, period=14)
        if atr is None or rsi is None or atr <= 0:
            return None

        # 2-hour opening move: price at 15:00 close vs close 2 bars ago (13:00 open proxy)
        open_move = closes[-1] - closes[-3]
        open_move_abs = abs(open_move)

        # Require directional thrust exceeding recent median volatility (0.95 * ATR)
        # but not exhausted (> 2.8 * ATR)
        if open_move_abs < (0.95 * atr) or open_move_abs > (2.8 * atr):
            return None

        # Long breakout continuation
        if open_move > 0 and 42.0 <= rsi <= 68.0:
            self.last_traded_day = current_day
            self.entry_bar_index = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.72,
                stop_loss_bps=220.0,
                take_profit_bps=380.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_open_bull_continuation",
                    "open_move": open_move,
                    "atr": atr,
                    "rsi": rsi,
                    "hour": current_hour
                }
            )

        # Short breakdown continuation
        if open_move < 0 and 32.0 <= rsi <= 58.0:
            self.last_traded_day = current_day
            self.entry_bar_index = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.72,
                stop_loss_bps=220.0,
                take_profit_bps=380.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_open_bear_continuation",
                    "open_move": open_move,
                    "atr": atr,
                    "rsi": rsi,
                    "hour": current_hour
                }
            )

        return None