//! N4.2 RED: `Command::LoadReplayData` に `strategy_file` / `strategy_init_kwargs`
//! 2 フィールドを追加することを駆動する失敗テスト。
//!
//! 現状の dto.rs `Command::LoadReplayData` には該当フィールドが存在しないため、
//! このテストは **コンパイルエラー** で fail する (RED 成立)。
//! GREEN 実装側は dto.rs に Option<String> / Option<serde_json::Value> を
//! 追加し、wire JSON が `strategy_file` / `strategy_init_kwargs` キーを
//! 含むようにする。

#[test]
fn load_replay_data_serializes_strategy_file_field() {
    let cmd = flowsurface_engine_client::dto::Command::LoadReplayData {
        request_id: "r1".to_string(),
        instrument_id: "1301.TSE".to_string(),
        start_date: "2024-01-01".to_string(),
        end_date: "2024-01-02".to_string(),
        granularity: flowsurface_engine_client::dto::ReplayGranularity::Trade,
        strategy_file: Some("/tmp/foo.py".to_string()),
        strategy_init_kwargs: Some(serde_json::json!({"short": 5})),
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
