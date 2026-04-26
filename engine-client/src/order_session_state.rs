/// 発注冪等性マップ（flowsurface `agent_session_state.rs` の移植）。
///
/// `client_order_id` を一次キーとし、同じ `client_order_id` + `request_key`
/// の再送を `IdempotentReplay` として処理する。
/// `venue_order_id`（= 立花 `sOrderNumber`）は Python から応答を受け取った後に
/// `update_venue_order_id()` で埋める。
///
/// - **当日分のみ保持**: 日跨ぎセッション切れ後に `OrderSessionState::new()` で再作成。
/// - **プロセス再起動跨ぎ**: WAL (Phase O0 T0.7) から復元する設計。本モジュールは
///   in-memory 状態のみ管理し、WAL 読み書きは `order_api.rs` (Phase O0) 側の責務。
use std::collections::HashMap;

// ── 命名対応（flowsurface → e-station）─────────────────────────────────────
// place_or_replay(...)      → try_insert(client_order_id, key)
// order_id                  → venue_order_id
// key (u64 hash)            → request_key (u64 hash)
// PlaceOrderOutcome::IdempotentReplay { order_id } → { venue_order_id }

/// `client_order_id` を強く型付けした newtype。
/// nautilus の `ClientOrderId` 制約（長さ 1〜36、ASCII printable）は
/// HTTP 層 (Phase O0 T0.5) で事前に検証済みを前提とし、ここでは保持のみ行う。
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct ClientOrderId(pub String);

/// 注文レコード（`OrderSessionState` マップの値型）。
#[derive(Debug, Clone)]
pub struct AgentOrderRecord {
    /// 立花 `sOrderNumber`。Python 応答受領前は `None`。
    pub venue_order_id: Option<String>,
    /// 入力 body の構造ハッシュ（冪等キー）。
    pub request_key: u64,
    /// nautilus OrderStatus 文字列（例 "SUBMITTED" / "ACCEPTED" / "REJECTED"）。
    pub status: String,
}

/// `try_insert()` の戻り値。
#[derive(Debug, Clone)]
pub enum PlaceOrderOutcome {
    /// 新規発注として処理する。
    Created { client_order_id: ClientOrderId },
    /// 同一 `client_order_id` + 同一 `request_key` の再送。冪等応答を返す。
    IdempotentReplay { venue_order_id: Option<String> },
    /// 同一 `client_order_id` で異なる `request_key`（本体が違う）— 409 Conflict。
    Conflict { existing_venue_order_id: Option<String> },
}

/// 発注冪等性マップ。`Arc<Mutex<Self>>` で Axum State として渡す想定。
pub struct OrderSessionState {
    map: HashMap<ClientOrderId, AgentOrderRecord>,
}

impl OrderSessionState {
    pub fn new() -> Self {
        Self {
            map: HashMap::new(),
        }
    }

    /// `client_order_id` と `request_key` の組み合わせで挿入を試みる。
    ///
    /// - 未登録 → `Created`
    /// - 登録済み + 同一 key → `IdempotentReplay { venue_order_id }`
    /// - 登録済み + 異なる key → `Conflict { existing_venue_order_id }`
    pub fn try_insert(
        &mut self,
        client_order_id: ClientOrderId,
        request_key: u64,
    ) -> PlaceOrderOutcome {
        if let Some(record) = self.map.get(&client_order_id) {
            if record.request_key == request_key {
                return PlaceOrderOutcome::IdempotentReplay {
                    venue_order_id: record.venue_order_id.clone(),
                };
            } else {
                return PlaceOrderOutcome::Conflict {
                    existing_venue_order_id: record.venue_order_id.clone(),
                };
            }
        }

        let cid = client_order_id.clone();
        self.map.insert(
            client_order_id,
            AgentOrderRecord {
                venue_order_id: None,
                request_key,
                status: "SUBMITTED".to_string(),
            },
        );
        PlaceOrderOutcome::Created {
            client_order_id: cid,
        }
    }

    /// Python から `sOrderNumber` を受け取ったら呼び出す。
    pub fn update_venue_order_id(&mut self, client_order_id: ClientOrderId, venue_order_id: String) {
        if let Some(record) = self.map.get_mut(&client_order_id) {
            record.venue_order_id = Some(venue_order_id);
        }
    }

    /// `client_order_id` から `venue_order_id` を取得する（取消フロー §2.3）。
    pub fn get_venue_order_id(&self, client_order_id: &ClientOrderId) -> Option<&str> {
        self.map
            .get(client_order_id)
            .and_then(|r| r.venue_order_id.as_deref())
    }

    /// ステータスを更新する（約定通知 / 取消確認受領時）。
    pub fn update_status(&mut self, client_order_id: &ClientOrderId, status: String) {
        if let Some(record) = self.map.get_mut(client_order_id) {
            record.status = status;
        }
    }
}

impl Default for OrderSessionState {
    fn default() -> Self {
        Self::new()
    }
}
