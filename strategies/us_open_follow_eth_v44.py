from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsOpenContinuationEth(Strategy):
    METADATA = {
        "name": "US Open Session Continuation ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,  # up to 6 hours hold
        "warmup_bars": 30,
        "required_features": ["btc_return_pct"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_trade_day = -1
        self.bars_since_exit = 10

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
        self.bars_since_exit = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        self.bars_since_exit += 1
        closes = ctx.closes(30)
        if len(closes) < 30:
            return None

        current_time = ctx.bar.timestamp
        current_hour = current_time.hour
        current_day = current_time.toordinal()

        # Mandatory time-based exit: close intraday session trades by UTC 21:00
        if ctx.has_position():
            if current_hour >= 21:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_utc_21", "hour": current_hour, "price": ctx.bar.close}
                )
            return None

        # Restrict entries strictly to UTC 15:00 (right after the 13:00-15:00 US open 2-hour window)
        if current_hour != 15:
            return None

        # Guard: strictly one entry per calendar day and respect exit cooldown
        if current_day == self.last_trade_day or self.bars_since_exit < 4:
            return None

        # Avoid extreme crisis / meltdown states
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Calculate opening move over the prior 2 completed hours (13:00 to 15:00 UTC)
        # closes[-1] is 15:00 close; closes[-3] is 13:00 open/close reference
        open_ref = closes[-3]
        curr_price = closes[-1]
        move = curr_price - open_ref
        move_pct = (move / open_ref) * 100.0

        highs = ctx.highs(30)
        lows = ctx.lows(30)
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr <= 0:
            return None

        # Volatility condition: opening 2-hour move must exceed 0.8 * ATR(14)
        # but avoid overextension (> 2.5 * ATR(14))
        abs_move = abs(move)
        if abs_move < 0.8 * atr or abs_move > 2.5 * atr:
            return None

        # RSI filter: ensure the move has momentum but is not exhausted
        rsi = self._rsi(closes, period=14)
        if rsi is None:
            return None

        # Cross-asset confirmation using BTC 24h trend to avoid false breakouts
        btc_perf = ctx.features.get("btc_return_pct", 0.0)

        # Bullish continuation entry
        if move > 0 and 52.0 <= rsi <= 68.0 and btc_perf > -1.5:
            self.last_trade_day = current_day
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "move_pct": round(move_pct, 2),
                    "atr": round(atr, 2),
                    "rsi": round(rsi, 2),
                    "btc_return_pct": round(btc_perf, 2),
                    "hour": current_hour
                }
            )

        # Bearish continuation entry
        if move < 0 and 32.0 <= rsi <= 48.0 and btc_perf < 1.5:
            self.last_trade_day = current_day
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "move_pct": round(move_pct, 2),
                    "atr": round(atr, 2),
                    "rsi": round(rsi, 2),
                    "btc_return_pct": round(btc_perf, 2),
                    "hour": current_hour
                }
            )

        return None