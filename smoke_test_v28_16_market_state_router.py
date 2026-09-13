from pathlib import Path

from app_src.market_state_v2816 import identify_analysis_map, identify_market_state, load_market_state_policy


def analysis(**overrides):
    features = {
        "close": 100.0,
        "trend_regime_score": 60.0,
        "range_regime_score": 25.0,
        "squeeze_regime_score": 20.0,
        "panic_regime_score": 10.0,
        "adx_14": 25.0,
        "rsi_14": 56.0,
        "atr_pct": 0.015,
        "bb_width_pct": 0.04,
        "vwap_distance_pct": 0.005,
        "range_position_20": 0.50,
        "trend_distance_pct": 0.01,
        "breakout_above_n_bar_high": False,
        "breakout_below_n_bar_low": False,
        "liquidity_sweep_high": False,
        "liquidity_sweep_low": False,
        "volume_spike": False,
        "ma_stack_state": "bullish",
        "local_trend": "up",
        "global_trend": "up",
        "htf_alignment": "bullish",
        "slope_pct_10": 0.25,
        "ema_20_50_spread_pct": 0.01,
        "market_structure": "HH_HL bullish",
        "bearish_divergence": False,
        "bullish_divergence": False,
        "choch_bearish": False,
        "choch_bullish": False,
    }
    features.update(overrides)
    return {"features": features, "htf_context": {"htf_alignment": features.get("htf_alignment")}}


policy = load_market_state_policy()
assert policy["version"].startswith("28.16")
assert policy["range_edges"] == {"lower": 0.20, "upper": 0.80}

pullback = identify_market_state(analysis(), symbol="BTCUSDT", analysis_timeframe="4h", policy=policy)
assert pullback.lifecycle_state == "trend_pullback_entry"
assert pullback.preferred_strategy_family == "trend_pullback"
assert pullback.router_action == "TRADE_CANDIDATE"
assert pullback.route_direction == "LONG"
assert pullback.risk_multiplier > 0
assert pullback.htf_alignment == "ALIGNED"
assert pullback.structure_state == "BULLISH"

compression = identify_market_state(
    analysis(
        trend_regime_score=25,
        range_regime_score=30,
        squeeze_regime_score=75,
        adx_14=14,
        ma_stack_state="mixed",
        local_trend="mixed",
        global_trend="mixed",
        htf_alignment="mixed",
        slope_pct_10=0,
        ema_20_50_spread_pct=0,
        vwap_distance_pct=0,
        rsi_14=50,
        market_structure="mixed",
    ),
    symbol="ETHUSDT",
    analysis_timeframe="4h",
    policy=policy,
)
assert compression.lifecycle_state == "compression_building"
assert compression.preferred_strategy_family == "compression_breakout"
assert compression.router_action == "PREPARE_BREAKOUT"
assert compression.risk_multiplier == 0
assert compression.volatility_state == "COMPRESSED"

breakout = identify_market_state(
    analysis(trend_regime_score=64, squeeze_regime_score=55, breakout_above_n_bar_high=True),
    symbol="SOLUSDT",
    analysis_timeframe="4h",
    policy=policy,
)
assert breakout.lifecycle_state == "breakout_attempt"
assert breakout.preferred_strategy_family == "compression_breakout"
assert breakout.router_action == "TRADE_CANDIDATE"
assert breakout.route_direction == "LONG"

range_edge = identify_market_state(
    analysis(
        trend_regime_score=20,
        range_regime_score=75,
        squeeze_regime_score=20,
        adx_14=12,
        range_position_20=0.10,
        ma_stack_state="mixed",
        local_trend="mixed",
        global_trend="mixed",
        htf_alignment="mixed",
        slope_pct_10=0,
        ema_20_50_spread_pct=0,
        vwap_distance_pct=0,
        rsi_14=50,
        market_structure="mixed",
    ),
    symbol="XRPUSDT",
    analysis_timeframe="4h",
    policy=policy,
)
assert range_edge.lifecycle_state == "range_chop"
assert range_edge.preferred_strategy_family == "range_reversion"
assert range_edge.router_action == "TRADE_CANDIDATE"
assert range_edge.route_direction == "LONG"

range_mid = identify_market_state(
    analysis(
        trend_regime_score=20,
        range_regime_score=75,
        adx_14=12,
        range_position_20=0.50,
        ma_stack_state="mixed",
        local_trend="mixed",
        global_trend="mixed",
        htf_alignment="mixed",
        slope_pct_10=0,
        ema_20_50_spread_pct=0,
        vwap_distance_pct=0,
        rsi_14=50,
        market_structure="mixed",
    ),
    symbol="UNIUSDT",
    analysis_timeframe="4h",
    policy=policy,
)
assert range_mid.router_action == "WAIT_RANGE_MID"
assert range_mid.risk_multiplier == 0

panic = identify_market_state(
    analysis(panic_regime_score=85, atr_pct=0.06, volume_spike=True),
    symbol="BNBUSDT",
    analysis_timeframe="4h",
    policy=policy,
)
assert panic.lifecycle_state == "panic_volatility"
assert panic.volatility_state == "EXTREME"
assert panic.router_action == "WAIT"
assert panic.risk_multiplier == 0

exhaustion = identify_market_state(
    analysis(trend_regime_score=80, adx_14=36, trend_distance_pct=0.05, bearish_divergence=True),
    symbol="LTCUSDT",
    analysis_timeframe="4h",
    policy=policy,
)
assert exhaustion.lifecycle_state == "trend_exhaustion"
assert exhaustion.router_action == "WAIT_FOR_REVERSAL_CONFIRMATION"
assert exhaustion.exit_family == "reversal_defensive"

aligned = pullback.confidence
conflict = identify_market_state(
    analysis(htf_alignment="bearish"), symbol="AAVEUSDT", analysis_timeframe="4h", policy=policy
)
assert conflict.htf_alignment == "CONFLICTING"
assert conflict.confidence < aligned

frame = identify_analysis_map(
    {"BTCUSDT": analysis(), "ETHUSDT": analysis(squeeze_regime_score=75, trend_regime_score=20, adx_14=12)},
    analysis_timeframe="4h",
    policy=policy,
)
assert len(frame) == 2
assert {"state_label", "preferred_strategy_family", "router_action", "risk_multiplier"}.issubset(frame.columns)

for path in [
    "app_src/market_state_v2816.py",
    "app_src/market_state_ui.py",
    "pages/00_Market_State.py",
    "config/market_state_router_policy.json",
]:
    assert Path(path).exists(), path

print("V28.16 smoke test passed: market state identifier and adaptive research router are available.")
