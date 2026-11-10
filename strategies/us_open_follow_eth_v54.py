from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsOpenContinuationEth(Strategy):
    METADATA = {
        "name": "US Open Session Continuation ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 40,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_trade_day = -1
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        highs = ctx.highs(40)
        lows = ctx.lows(40)
        if len(closes) < 35:
            return None

        current_time = ctx.bar.timestamp
        current_hour = current_time.hour
        current_day = current_time.toordinal()

        # Exit session trades by UTC 21
        if ctx.has_position():
            if current_hour >= 21 or current_hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "session_close_utc21",
                        "hour": current_hour,
                        "price": ctx.bar.close,
                    }
                )
            return None

        # Guard: cooldown and one trade per day
        if ctx.bar_index < self.cooldown_until_bar:
            return None
        if current_day == self.last_trade_day:
            return None

        # Entry window: trigger at UTC 15 bar close (after evaluating 13:00-15:00 movement)
        if current_hour != 15:
            return None

        # Session opening displacement over the 2-hour window (13:00-15:00)
        # closes[-1] is close of 15:00 bar, closes[-3] is open/close around start of 13:00
        open_move = closes[-1] - closes[-3]
        open_move_pct = (open_move / closes[-3]) * 100.0

        # Dynamic range threshold: 2-hour move must exceed ATR
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr == 0:
            return None
        min_move = atr * 0.85

        # Check for exhaustion via RSI
        rsi = self._rsi(closes, period=14)
        if rsi is None:
            return None

        # Cross-asset confirmation from BTC 24h return to avoid ETH idiosyncratic traps
        btc_perf = ctx.features.get("btc_return_pct", 0.0)

        # Macro trend alignment via EMA24
        ema24 = sum(closes[-24:]) / 24.0

        # Bullish continuation setup
        if open_move > min_move and rsi < 68.0 and btc_perf > -1.5 and closes[-1] > ema24:
            self.last_trade_day = current_day
            self.cooldown_until_bar = ctx.bar_index + 6
            confidence = min(0.85, 0.6 + (open_move / (atr * 2.5)) * 0.2)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "open_move_pct": open_move_pct,
                    "atr": atr,
                    "rsi": rsi,
                    "btc_return_pct": btc_perf,
                    "price": ctx.bar.close,
                }
            )

        # Bearish continuation setup
        if open_move < -min_move and rsi > 32.0 and btc_perf < 1.5 and closes[-1] < ema24:
            self.last_trade_day = current_day
            self.cooldown_until_bar = ctx.bar_index + 6
            confidence = min(0.85, 0.6 + (abs(open_move) / (atr * 2.5)) * 0.2)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "open_move_pct": open_move_pct,
                    "atr": atr,
                    "rsi": rsi,
                    "btc_return_pct": btc_perf,
                    "price": ctx.bar.close,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.cooldown_until_bar = ctx.bar_index + 3