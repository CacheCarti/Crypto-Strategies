from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class TokyoHandoffBTC(Strategy):
    METADATA = {
        "name": "TokyoHandoffBTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2-3 hours for handoff
        "warmup_bars": 30,
        "required_features": ["fear_greed_index", "funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 4 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars
        self.tokyo_period = 8 # UTC 0-8 is 8 hours
        self.min_move_pct = 0.3 # Minimum move percentage in Tokyo session (0.3%)
        self.max_move_pct_exhaustion = 1.5 # Max move % before considering exhausted (1.5%)
        self.rsi_period = 14
        self.rsi_oversold = 35
        self.rsi_overbought = 65
        self.ema_fast_period = 10
        self.ema_slow_period = 20

    def _rsi(self, closes, period):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))

        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period

        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + max(self.tokyo_period, self.rsi_period, self.ema_slow_period))
        if len(closes) < self.warmup_bars + max(self.tokyo_period, self.rsi_period, self.ema_slow_period):
            return None

        if ctx.bar_index <= self.last_trade_bar + self.cooldown_bars:
            return None

        # Tokyo session is UTC 0-8. Europe session starts around UTC 7-8.
        # We want to detect a move in Tokyo and trade the handoff to Europe.
        # Look for the handoff around UTC 7-9.
        if not (7 <= ctx.bar.timestamp.hour < 9):
            return None

        # Check for sufficient bars for Tokyo session analysis
        if len(closes) < self.tokyo_period:
            return None

        # Get Tokyo session data (previous 8 hours, ending at current bar's start)
        tokyo_session_closes = ctx.closes(self.tokyo_period + 1)[-(self.tokyo_period + 1):]
        tokyo_open = tokyo_session_closes[0]
        tokyo_close = tokyo_session_closes[-1]

        tokyo_move_pct = ((tokyo_close - tokyo_open) / tokyo_open) * 100

        # Current market conditions
        current_rsi = self._rsi(closes, self.rsi_period)
        ema_fast = self._ema(closes, self.ema_fast_period)
        ema_slow = self._ema(closes, self.ema_slow_period)

        if current_rsi is None or ema_fast is None or ema_slow is None:
            return None

        # Gating conditions
        fear_greed_index = ctx.features.get("fear_greed_index", 50)
        funding_rate = ctx.features.get("funding_rate_btcusdt", 0.0)

        confidence = 0.0

        if not ctx.has_position():
            # Long signal: Tokyo moved up decisively, not exhausted, and current RSI is not overbought
            if (tokyo_move_pct > self.min_move_pct and
                tokyo_move_pct < self.max_move_pct_exhaustion and
                current_rsi < self.rsi_overbought and
                ema_fast > ema_slow): # Confirm uptrend
                
                # Further refine with Fear & Greed: don't long into extreme greed
                if fear_greed_index < 75:
                    confidence = min(0.8, (tokyo_move_pct / self.max_move_pct_exhaustion) * 0.7 + (100 - current_rsi) / 100 * 0.1)
                    if confidence > 0.4:
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal(
                            "long",
                            confidence=confidence,
                            metadata={
                                "reason": "tokyo_handoff_long_continuation",
                                "tokyo_move_pct": tokyo_move_pct,
                                "current_rsi": current_rsi,
                                "ema_fast": ema_fast,
                                "ema_slow": ema_slow,
                                "fear_greed_index": fear_greed_index,
                                "funding_rate": funding_rate
                            }
                        )

            # Short signal: Tokyo moved down decisively, not exhausted, and current RSI is not oversold
            elif (tokyo_move_pct < -self.min_move_pct and
                  tokyo_move_pct > -self.max_move_pct_exhaustion and
                  current_rsi > self.rsi_oversold and
                  ema_fast < ema_slow): # Confirm downtrend

                # Further refine with Fear & Greed: don't short into extreme fear
                if fear_greed_index > 25:
                    confidence = min(0.8, (abs(tokyo_move_pct) / self.max_move_pct_exhaustion) * 0.7 + (current_rsi / 100) * 0.1)
                    if confidence > 0.4:
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal(
                            "short",
                            confidence=confidence,
                            metadata={
                                "reason": "tokyo_handoff_short_continuation",
                                "tokyo_move_pct": tokyo_move_pct,
                                "current_rsi": current_rsi,
                                "ema_fast": ema_fast,
                                "ema_slow": ema_slow,
                                "fear_greed_index": fear_greed_index,
                                "funding_rate": funding_rate
                            }
                        )
        elif ctx.has_position():
            # Exit before next Asia session starts (around UTC 23-0)
            # Or if the trend reverses significantly
            if ctx.bar.timestamp.hour >= 23 or ctx.bar.timestamp.hour < 0: # Exit before UTC 0
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "time_based_exit_before_asia", "current_hour": ctx.bar.timestamp.hour})

            if ctx.position_direction() == "long":
                if ema_fast < ema_slow or current_rsi > self.rsi_overbought + 5: # Overbought exit
                     return ctx.signal("flat", confidence=0.6, metadata={"reason": "long_exit_ema_cross_or_overbought", "current_rsi": current_rsi, "ema_fast": ema_fast, "ema_slow": ema_slow})
            elif ctx.position_direction() == "short":
                if ema_fast > ema_slow or current_rsi < self.rsi_oversold - 5: # Oversold exit
                     return ctx.signal("flat", confidence=0.6, metadata={"reason": "short_exit_ema_cross_or_oversold", "current_rsi": current_rsi, "ema_fast": ema_fast, "ema_slow": ema_slow})

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log position close details for analysis
        ctx.log(f"Position closed: {position['direction']} at {position['exit_price']} (Return: {position['return_bps']} bps, Held: {position['bars_held']} bars)")
        # No adaptive sizing in this version, simple cooldown is sufficient.