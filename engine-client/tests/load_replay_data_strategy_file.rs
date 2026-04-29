//! N4.2: `Command::LoadReplayData` の `strategy_file` / `strategy_init_kwargs`
//! フィールドが wire JSON に正しくシリアライズされることを保証するリグレッションテスト。

#[test]
fn load_replay_data_serializes_strategy_file_field() {
    let mut kwargs = serde_json::Map::new();
    kwargs.insert("short".to_string(), serde_json::json!(5));
    let cmd = flowsurface_engine_client::dto::Command::LoadReplayData {
        request_id: "r1".to_string(),
        instrument_id: "1301.TSE".to_string(),
        start_date: "2024-01-01".to_string(),
        end_date: "2024-01-02".to_string(),
        granularity: flowsurface_engine_client::dto::ReplayGranularity::Trade,
        strategy_file: Some("/tmp/foo.py".to_string()),
        strategy_init_kwargs: Some(kwargs),
    };
    let json = serde_json::to_string(&cmd).unwrap();
    assert!(
        json.contains("\"strategy_file\":\"/tmp/foo.py\""),
        "expected strategy_file in wire JSON, got: {json}"
    );
    assert!(
        json.contains("\"strategy_init_kwargs\""),
        "expected strategy_init_kwargs in wire JSON, got: {json}"
    );
}
