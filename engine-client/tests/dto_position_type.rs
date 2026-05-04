//! Phase 8 review-fix-loop R1 / Phase 1 — H-Type1 RED→GREEN tests.
//!
//! `PositionRecordWire.position_type` を `String` から `PositionType` enum に
//! 格上げしたことで、既知値 (`cash`/`margin_credit`/`margin_general`) が成功し
//! 未知値はデシリアライズ失敗することを保証する。

use flowsurface_engine_client::dto::{PositionRecordWire, PositionType};

fn make_json(position_type: &str) -> String {
    format!(
        r#"{{
            "instrument_id": "7203.TSE",
            "qty": "100",
            "market_value": "345600",
            "position_type": "{position_type}",
            "tategyoku_id": null,
            "venue": "tachibana"
        }}"#
    )
}

#[test]
fn cash_position_type_deserializes() {
    let wire: PositionRecordWire = serde_json::from_str(&make_json("cash")).unwrap();
    assert_eq!(wire.position_type, PositionType::Cash);
}

#[test]
fn margin_credit_position_type_deserializes() {
    let wire: PositionRecordWire = serde_json::from_str(&make_json("margin_credit")).unwrap();
    assert_eq!(wire.position_type, PositionType::MarginCredit);
}

#[test]
fn margin_general_position_type_deserializes() {
    let wire: PositionRecordWire = serde_json::from_str(&make_json("margin_general")).unwrap();
    assert_eq!(wire.position_type, PositionType::MarginGeneral);
}

#[test]
fn unknown_position_type_fails_to_deserialize() {
    let result: Result<PositionRecordWire, _> = serde_json::from_str(&make_json("unknown"));
    assert!(
        result.is_err(),
        "unknown position_type should fail to deserialize, got {result:?}"
    );
}

#[test]
fn position_type_serializes_to_snake_case() {
    let wire = PositionRecordWire {
        instrument_id: "7203.TSE".to_string(),
        qty: "100".to_string(),
        market_value: "345600".to_string(),
        position_type: PositionType::MarginCredit,
        tategyoku_id: None,
        venue: "tachibana".to_string(),
    };
    let json = serde_json::to_string(&wire).unwrap();
    assert!(
        json.contains(r#""position_type":"margin_credit""#),
        "expected snake_case serialization, got {json}"
    );
}
