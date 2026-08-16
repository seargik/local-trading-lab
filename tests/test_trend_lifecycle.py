from app_src.trend_lifecycle import classify_trend_lifecycle


def test_trend_pullback_entry_state():
    result = classify_trend_lifecycle(
        {
            "trend_regime_score": 62,
            "range_regime_score": 30,
            "squeeze_regime_score": 20,
            "panic_regime_score": 10,
            "adx_14": 25,
            "rsi_14": 58,
            "vwap_distance_pct": 0.004,
            "range_position_20": 0.5,
            "ma_stack_state": "bullish",
            "local_trend": "uptrend",
            "global_trend": "above_ma200",
        },
        symbol="ETHUSDT",
        analysis_tf="1h",
    )
    assert result.lifecycle_state == "trend_pullback_entry"
    assert result.trend_direction == "LONG"
    assert "trend_pullback" in result.allowed_strategy_families


def test_panic_state_precedes_trend():
    result = classify_trend_lifecycle(
        {
            "trend_regime_score": 80,
            "panic_regime_score": 75,
            "atr_pct": 0.05,
            "volume_spike": True,
        },
        symbol="BTCUSDT",
        analysis_tf="1h",
    )
    assert result.lifecycle_state == "panic_volatility"
