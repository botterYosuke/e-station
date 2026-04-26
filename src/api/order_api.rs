//! HTTP handlers for the order API:
//!   - `POST /api/order/submit`
//!   - `POST /api/order/modify`
//!   - `POST /api/order/cancel`
//!   - `POST /api/order/cancel-all`
//!   - `GET  /api/order/list`
#![allow(dead_code)] // public API — consumed by replay_api.rs route dispatcher
//!
//! Architecture (submit):
//! ```text
//! HTTP client
//!     │ POST /api/order/submit (JSON body)
//!     ▼
//! order_api::handle_submit_request()
//!     │ ① validate input
//!     │ ② check REPLAY mode → 503
//!     │ ③ check OrderGuardConfig.enabled → 503 if not configured
//!     │ ④ OrderGuardConfig: qty/yen limits → 400; rate limit → 429
//!     │ ⑤ OrderSessionState.try_insert(client_order_id, request_key)
//!     │      Created         → continue
//!     │      IdempotentReplay → 200/202 immediate
//!     │      Conflict         → 409
//!     │ ⑥ conn.subscribe_events() BEFORE sending command
//!     │ ⑦ conn.send(Command::SubmitOrder { ... })
//!     │ ⑧ wait_for_order_result(30 s timeout)
//!     │      OrderAccepted    → update_venue_order_id → 201
//!     │      OrderRejected    → map reason_code → 4xx/5xx
//!     │      Timeout          → 504
//!     └──────────────────────────────────────────────────
//! ```
use std::{
    collections::HashMap,
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    time::Duration,
};

use engine_client::{
    EngineConnection,
    dto::{
        Command, EngineEvent, OrderListFilter as IpcOrderListFilter, OrderModifyChange, OrderSide,
        OrderType, SubmitOrderRequest, TimeInForce, TriggerType,
    },
    order_session_state::{ClientOrderId, OrderSessionState, PlaceOrderOutcome},
};
use tokio::{
    io::AsyncWriteExt,
    net::TcpStream,
    sync::{Mutex, watch},
};
use xxhash_rust::xxh3::xxh3_64_with_seed;

// ── Safety guard config ────────────────────────────────────────────────────────

/// Safety guard configuration for the order API.
///
/// When `enabled` is `false` (the default when no config is provided), every
/// call to `/api/order/submit` returns HTTP 503 with
/// `reason_code="ORDER_GUARD_NOT_CONFIGURED"`. This is an explicit opt-in
/// requirement: the operator must deliberately configure the guard before
/// live orders are accepted.
#[derive(Debug, Clone)]
pub struct OrderGuardConfig {
    /// Whether the order guard is active. `false` → 503 on every submit.
    pub enabled: bool,
    /// Maximum quantity per single order. `None` = no limit.
    pub max_qty_per_order: Option<u64>,
    /// Maximum notional (price × qty) per single LIMIT order in JPY. `None` = no limit.
    pub max_yen_per_order: Option<u64>,
    /// Width of the rate-limit sliding window in seconds. Default: 3.
    pub rate_limit_window_secs: u64,
    /// Maximum number of accepted orders within the sliding window per key.
    /// Exceeding this returns HTTP 429. Default: 2.
    pub rate_limit_max_hits: u32,
}

impl Default for OrderGuardConfig {
    fn default() -> Self {
        Self {
            enabled: false,
            max_qty_per_order: None,
            max_yen_per_order: None,
            rate_limit_window_secs: 3,
            rate_limit_max_hits: 2,
        }
    }
}

impl OrderGuardConfig {
    /// Convenience constructor: guard enabled with no quantity/yen limits.
    ///
    /// Useful for tests that want to exercise the happy path without
    /// accidentally triggering the "not configured" 503.
    #[cfg(test)]
    pub fn enabled_no_limits() -> Self {
        Self {
            enabled: true,
            max_qty_per_order: None,
            max_yen_per_order: None,
            rate_limit_window_secs: 3,
            rate_limit_max_hits: 2,
        }
    }
}

// ── Rate limiter ───────────────────────────────────────────────────────────────

/// Rate-limit key: `(instrument_id, order_side, quantity, price)`.
///
/// Two requests that differ on any of these dimensions are tracked
/// independently.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
struct RateLimitKey {
    instrument_id: String,
    order_side: String,
    quantity: String,
    price: Option<String>,
}

/// Per-key sliding-window counter.
///
/// Stores the timestamps (as `tokio::time::Instant`) of recent hits and
/// evicts those older than `window` on each check.
struct RateLimiter {
    /// Map from rate-limit key → sorted list of hit timestamps.
    hits: HashMap<RateLimitKey, Vec<tokio::time::Instant>>,
}

impl RateLimiter {
    fn new() -> Self {
        Self {
            hits: HashMap::new(),
        }
    }

    /// Record a new hit and return whether the limit has been exceeded.
    ///
    /// Returns `true` if the number of hits within `window` **after** adding
    /// the new hit exceeds `max_hits`.
    fn record_and_check(&mut self, key: RateLimitKey, window: Duration, max_hits: u32) -> bool {
        let now = tokio::time::Instant::now();
        let cutoff = now
            .checked_sub(window)
            .unwrap_or(tokio::time::Instant::now());

        let entry = self.hits.entry(key).or_default();

        // Evict stale entries.
        entry.retain(|&t| t > cutoff);

        // Record this hit.
        entry.push(now);

        // Check whether we've exceeded the limit.
        entry.len() > max_hits as usize
    }

    /// Inject a hit at a specific instant (test helper — compiled only in tests).
    #[cfg(test)]
    fn inject_hit_at(&mut self, key: RateLimitKey, at: tokio::time::Instant) {
        self.hits.entry(key).or_default().push(at);
    }
}

// ── Shared state ───────────────────────────────────────────────────────────────

/// Shared state for the order API handler.
pub struct OrderApiState {
    pub session: Arc<Mutex<OrderSessionState>>,
    pub engine_rx: watch::Receiver<Option<Arc<EngineConnection>>>,
    pub is_replay_mode: Arc<AtomicBool>,
    /// Timeout waiting for `OrderAccepted`/`OrderRejected`. Default 30 s.
    pub submit_timeout: Duration,
    /// Safety guard configuration. Default: `enabled: false` → 503.
    pub guard_config: OrderGuardConfig,
    /// Sliding-window rate limiter (one counter per key).
    rate_limiter: Mutex<RateLimiter>,
}

impl OrderApiState {
    pub fn new(
        session: Arc<Mutex<OrderSessionState>>,
        engine_rx: watch::Receiver<Option<Arc<EngineConnection>>>,
        is_replay_mode: Arc<AtomicBool>,
    ) -> Self {
        Self {
            session,
            engine_rx,
            is_replay_mode,
            submit_timeout: Duration::from_secs(30),
            guard_config: OrderGuardConfig::default(), // enabled: false
            rate_limiter: Mutex::new(RateLimiter::new()),
        }
    }

    #[cfg(test)]
    pub fn with_timeout(mut self, t: Duration) -> Self {
        self.submit_timeout = t;
        self
    }

    /// Override the guard configuration (builder pattern).
    pub fn with_guard_config(mut self, cfg: OrderGuardConfig) -> Self {
        self.guard_config = cfg;
        self
    }
}

// ── HTTP wire types ────────────────────────────────────────────────────────────

#[derive(serde::Deserialize)]
struct SubmitOrderBody {
    client_order_id: String,
    instrument_id: String,
    order_side: String,
    order_type: String,
    quantity: String,
    #[serde(default)]
    price: Option<String>,
    #[serde(default)]
    trigger_price: Option<String>,
    #[serde(default)]
    trigger_type: Option<String>,
    time_in_force: String,
    #[serde(default)]
    expire_time: Option<String>,
    post_only: bool,
    reduce_only: bool,
    #[serde(default)]
    tags: Vec<String>,
}

// ── HTTP wire types (Phase O1) ─────────────────────────────────────────────────

#[derive(serde::Deserialize)]
struct ModifyOrderBody {
    client_order_id: String,
    change: ModifyChangeBody,
}

#[derive(serde::Deserialize)]
struct ModifyChangeBody {
    #[serde(default)]
    new_quantity: Option<String>,
    #[serde(default)]
    new_price: Option<String>,
    #[serde(default)]
    new_trigger_price: Option<String>,
    #[serde(default)]
    new_expire_time_ns: Option<i64>,
}

#[derive(serde::Deserialize)]
struct CancelOrderBody {
    client_order_id: String,
}

/// `POST /api/order/cancel-all` body — `confirm: true` is **required**.
#[derive(serde::Deserialize)]
struct CancelAllBody {
    confirm: Option<serde_json::Value>,
    #[serde(default)]
    instrument_id: Option<String>,
    #[serde(default)]
    order_side: Option<String>,
}

#[derive(serde::Deserialize)]
struct OrderListQueryBody {
    #[serde(default)]
    status: Option<String>,
    #[serde(default)]
    instrument_id: Option<String>,
    #[serde(default)]
    date: Option<String>,
}

// ── Public handlers ────────────────────────────────────────────────────────────

/// Entry point called by the TCP dispatcher for `POST /api/order/submit`.
pub async fn handle_submit_request(stream: &mut TcpStream, body: &str, state: &Arc<OrderApiState>) {
    let response = submit_order(body, state).await;
    let _ = stream
        .write_all(format_http_response(response.status, &response.body).as_bytes())
        .await;
}

/// Entry point for `POST /api/order/modify`.
pub async fn handle_modify_request(stream: &mut TcpStream, body: &str, state: &Arc<OrderApiState>) {
    let response = modify_order(body, state).await;
    let _ = stream
        .write_all(format_http_response(response.status, &response.body).as_bytes())
        .await;
}

/// Entry point for `POST /api/order/cancel`.
pub async fn handle_cancel_request(stream: &mut TcpStream, body: &str, state: &Arc<OrderApiState>) {
    let response = cancel_order(body, state).await;
    let _ = stream
        .write_all(format_http_response(response.status, &response.body).as_bytes())
        .await;
}

/// Entry point for `POST /api/order/cancel-all`.
pub async fn handle_cancel_all_request(
    stream: &mut TcpStream,
    body: &str,
    state: &Arc<OrderApiState>,
) {
    let response = cancel_all_orders(body, state).await;
    let _ = stream
        .write_all(format_http_response(response.status, &response.body).as_bytes())
        .await;
}

/// Entry point for `GET /api/order/list`.
pub async fn handle_list_request(stream: &mut TcpStream, body: &str, state: &Arc<OrderApiState>) {
    let response = list_orders(body, state).await;
    let _ = stream
        .write_all(format_http_response(response.status, &response.body).as_bytes())
        .await;
}

// ── Core handler ───────────────────────────────────────────────────────────────

struct HttpResponse {
    status: u16,
    body: String,
}

async fn submit_order(raw_body: &str, state: &Arc<OrderApiState>) -> HttpResponse {
    // ── ① REPLAY mode guard ───────────────────────────────────────────────────
    if state.is_replay_mode.load(Ordering::Acquire) {
        return error_response(503, "REPLAY_MODE_ACTIVE", "replay mode is active");
    }

    // ── ② Parse body ─────────────────────────────────────────────────────────
    let body: SubmitOrderBody = match serde_json::from_str(raw_body) {
        Ok(b) => b,
        Err(e) => return error_response(400, "VALIDATION_ERROR", &format!("invalid JSON: {e}")),
    };

    // ── ③ Validate ───────────────────────────────────────────────────────────
    if let Err(r) = validate(&body) {
        return r;
    }

    // Convert expire_time (ISO8601) → expire_time_ns (UTC nanoseconds)
    let expire_time_ns: Option<i64> = match body.expire_time.as_deref() {
        None => None,
        Some(s) => match parse_expire_time(s) {
            Ok(ns) => Some(ns),
            Err(msg) => return error_response(400, "VALIDATION_ERROR", &msg),
        },
    };

    // ── ④ Order guard: enabled check ─────────────────────────────────────────
    if !state.guard_config.enabled {
        return error_response(
            503,
            "ORDER_GUARD_NOT_CONFIGURED",
            "order guard is not configured; set tachibana.order config to enable order submission",
        );
    }

    // ── ⑤ Order guard: quantity limit ────────────────────────────────────────
    if let Some(max_qty) = state.guard_config.max_qty_per_order {
        // quantity was validated as positive integer in validate()
        let qty: u64 = body.quantity.parse().unwrap_or(0);
        if qty > max_qty {
            return error_response(
                400,
                "QTY_LIMIT_EXCEEDED",
                &format!("quantity {qty} exceeds max_qty_per_order {max_qty}"),
            );
        }
    }

    // ── ⑥ Order guard: yen notional limit (LIMIT orders only) ────────────────
    if let (Some(max_yen), Some(price_str)) =
        (state.guard_config.max_yen_per_order, body.price.as_deref())
        && matches!(body.order_type.as_str(), "LIMIT" | "STOP_LIMIT")
    {
        // Parse price as u64 (yen; fractional prices not supported here).
        // If price cannot be parsed as integer, skip the check conservatively
        // (the validate() step ensures price is present for LIMIT).
        if let (Ok(price_u64), Ok(qty_u64)) =
            (price_str.parse::<u64>(), body.quantity.parse::<u64>())
        {
            let notional = price_u64.saturating_mul(qty_u64);
            if notional > max_yen {
                return error_response(
                    400,
                    "YEN_LIMIT_EXCEEDED",
                    &format!(
                        "notional {notional} yen (price={price_u64} × qty={qty_u64}) exceeds \
                         max_yen_per_order {max_yen}"
                    ),
                );
            }
        }
    }

    // ── ⑦ Order guard: rate limit ────────────────────────────────────────────
    {
        let key = RateLimitKey {
            instrument_id: body.instrument_id.clone(),
            order_side: body.order_side.clone(),
            quantity: body.quantity.clone(),
            price: body.price.clone(),
        };
        let window = Duration::from_secs(state.guard_config.rate_limit_window_secs);
        let max_hits = state.guard_config.rate_limit_max_hits;

        let exceeded = {
            let mut limiter = state.rate_limiter.lock().await;
            limiter.record_and_check(key, window, max_hits)
        };

        if exceeded {
            return error_response(429, "RATE_LIMITED", "rate limit exceeded; slow down");
        }
    }

    // ── ⑧ Get EngineConnection ────────────────────────────────────────────────
    let conn: Arc<EngineConnection> = match state.engine_rx.borrow().clone() {
        Some(c) => c,
        None => return error_response(502, "INTERNAL_ERROR", "engine not connected"),
    };

    // ── ⑨ Compute request_key ────────────────────────────────────────────────
    let request_key = compute_request_key(&body, expire_time_ns);

    // ── ⑩ Idempotency check ──────────────────────────────────────────────────
    let client_order_id = ClientOrderId(body.client_order_id.clone());
    let outcome = {
        let mut session = state.session.lock().await;
        session.try_insert(client_order_id.clone(), request_key)
    };

    match outcome {
        PlaceOrderOutcome::IdempotentReplay {
            venue_order_id: Some(vid),
        } => {
            let body = serde_json::json!({
                "client_order_id": body.client_order_id,
                "venue_order_id": vid,
                "status": "ACCEPTED"
            });
            return HttpResponse {
                status: 200,
                body: body.to_string(),
            };
        }
        PlaceOrderOutcome::IdempotentReplay {
            venue_order_id: None,
        } => {
            let body = serde_json::json!({
                "client_order_id": body.client_order_id,
                "status": "SUBMITTED",
                "venue_order_id": null,
                "warning": "order_status_unknown"
            });
            return HttpResponse {
                status: 202,
                body: body.to_string(),
            };
        }
        PlaceOrderOutcome::Conflict {
            existing_venue_order_id,
        } => {
            let body = serde_json::json!({
                "reason_code": "CONFLICT",
                "existing_venue_order_id": existing_venue_order_id,
            });
            return HttpResponse {
                status: 409,
                body: body.to_string(),
            };
        }
        PlaceOrderOutcome::Created { .. } => {} // continue to submission
    }

    // ── ⑪ Subscribe to events BEFORE sending command ─────────────────────────
    let events_rx = conn.subscribe_events();

    // ── ⑫ Build and send SubmitOrder command ─────────────────────────────────
    let request_id = uuid::Uuid::new_v4().to_string();
    let ipc_order = build_ipc_order(&body, expire_time_ns, &request_id);
    let cmd = Command::SubmitOrder {
        request_id: request_id.clone(),
        venue: "tachibana".to_string(),
        order: ipc_order,
    };

    if let Err(e) = conn.send(cmd).await {
        return error_response(
            502,
            "INTERNAL_ERROR",
            &format!("failed to forward order to engine: {e}"),
        );
    }

    // ── ⑬ Wait for result (with timeout) ─────────────────────────────────────
    let cid = body.client_order_id.clone();
    match tokio::time::timeout(
        state.submit_timeout,
        wait_for_order_result(&cid, &request_id, events_rx),
    )
    .await
    {
        Ok(OrderWaitResult::Accepted { venue_order_id, .. }) => {
            if let Some(ref vid) = venue_order_id {
                let mut session = state.session.lock().await;
                let _ = session.update_venue_order_id(client_order_id, vid.clone());
            }
            let resp = serde_json::json!({
                "client_order_id": cid,
                "venue_order_id": venue_order_id,
                "status": "ACCEPTED"
            });
            HttpResponse {
                status: 201,
                body: resp.to_string(),
            }
        }
        Ok(OrderWaitResult::Rejected {
            reason_code,
            reason_text,
        }) => {
            let status = reason_code_to_status(&reason_code);
            let resp = serde_json::json!({
                "reason_code": reason_code,
                "reason_text": reason_text,
            });
            HttpResponse {
                status,
                body: resp.to_string(),
            }
        }
        Ok(OrderWaitResult::SecondPasswordRequired) => {
            error_response(401, "SECOND_PASSWORD_REQUIRED", "second password required")
        }
        Ok(OrderWaitResult::Disconnected) => error_response(
            502,
            "INTERNAL_ERROR",
            "engine connection lost while waiting",
        ),
        Err(_timeout) => error_response(504, "INTERNAL_ERROR", "order submission timed out"),
    }
}

// ── Phase O1 core handlers ─────────────────────────────────────────────────────

/// `POST /api/order/modify` core logic.
///
/// Flow:
/// 1. Parse body → ModifyOrderBody
/// 2. Lookup `venue_order_id` via OrderSessionState → 404 if None
/// 3. Require `guard_config.enabled` → 503
/// 4. Get EngineConnection → 502 if None
/// 5. Subscribe events BEFORE send
/// 6. Send `Command::ModifyOrder`
/// 7. Wait for `OrderPendingUpdate` → 200
async fn modify_order(raw_body: &str, state: &Arc<OrderApiState>) -> HttpResponse {
    // ① Parse
    let body: ModifyOrderBody = match serde_json::from_str(raw_body) {
        Ok(b) => b,
        Err(e) => return error_response(400, "VALIDATION_ERROR", &format!("invalid JSON: {e}")),
    };

    // ② Guard
    if !state.guard_config.enabled {
        return error_response(
            503,
            "ORDER_GUARD_NOT_CONFIGURED",
            "order guard is not configured",
        );
    }

    // ③ Lookup venue_order_id
    let cid = ClientOrderId(body.client_order_id.clone());
    let venue_order_id = {
        let session = state.session.lock().await;
        session.get_venue_order_id(&cid).map(str::to_string)
    };
    let venue_order_id = match venue_order_id {
        Some(v) => v,
        None => {
            return error_response(
                404,
                "ORDER_NOT_FOUND",
                &format!(
                    "client_order_id {:?} not found or venue_order_id unknown",
                    body.client_order_id
                ),
            );
        }
    };

    // ④ Engine connection
    let conn: Arc<EngineConnection> = match state.engine_rx.borrow().clone() {
        Some(c) => c,
        None => return error_response(502, "INTERNAL_ERROR", "engine not connected"),
    };

    // ⑤ Subscribe events BEFORE sending
    let events_rx = conn.subscribe_events();

    // ⑥ Send ModifyOrder
    let request_id = uuid::Uuid::new_v4().to_string();
    let change = OrderModifyChange {
        new_quantity: body.change.new_quantity,
        new_price: body.change.new_price,
        new_trigger_price: body.change.new_trigger_price,
        new_expire_time_ns: body.change.new_expire_time_ns,
    };
    let cmd = Command::ModifyOrder {
        request_id: request_id.clone(),
        venue: "tachibana".to_string(),
        client_order_id: body.client_order_id.clone(),
        change,
    };
    if let Err(e) = conn.send(cmd).await {
        return error_response(
            502,
            "INTERNAL_ERROR",
            &format!("failed to send to engine: {e}"),
        );
    }

    // ⑦ Wait for OrderPendingUpdate
    let cid_str = body.client_order_id.clone();
    match tokio::time::timeout(
        state.submit_timeout,
        wait_for_pending_update(&cid_str, events_rx),
    )
    .await
    {
        Ok(Ok(ts_event_ms)) => {
            let resp = serde_json::json!({
                "client_order_id": cid_str,
                "venue_order_id": venue_order_id,
                "status": "PENDING_UPDATE",
                "ts_event_ms": ts_event_ms
            });
            HttpResponse {
                status: 200,
                body: resp.to_string(),
            }
        }
        Ok(Err(msg)) => error_response(502, "INTERNAL_ERROR", &msg),
        Err(_) => error_response(504, "INTERNAL_ERROR", "modify order timed out"),
    }
}

/// `POST /api/order/cancel` core logic.
///
/// Flow:
/// 1. Parse body → CancelOrderBody
/// 2. Guard enabled → 503
/// 3. Lookup `venue_order_id` → 404 if None
/// 4. Engine connection → 502
/// 5. Subscribe events BEFORE send
/// 6. `Command::CancelOrder`
/// 7. Wait for `OrderPendingCancel` → 200
async fn cancel_order(raw_body: &str, state: &Arc<OrderApiState>) -> HttpResponse {
    // ① Parse
    let body: CancelOrderBody = match serde_json::from_str(raw_body) {
        Ok(b) => b,
        Err(e) => return error_response(400, "VALIDATION_ERROR", &format!("invalid JSON: {e}")),
    };

    // ② Guard
    if !state.guard_config.enabled {
        return error_response(
            503,
            "ORDER_GUARD_NOT_CONFIGURED",
            "order guard is not configured",
        );
    }

    // ③ Lookup venue_order_id
    let cid = ClientOrderId(body.client_order_id.clone());
    let venue_order_id = {
        let session = state.session.lock().await;
        session.get_venue_order_id(&cid).map(str::to_string)
    };
    let venue_order_id = match venue_order_id {
        Some(v) => v,
        None => {
            return error_response(
                404,
                "ORDER_NOT_FOUND",
                &format!(
                    "client_order_id {:?} not found or venue_order_id unknown",
                    body.client_order_id
                ),
            );
        }
    };

    // ④ Engine connection
    let conn: Arc<EngineConnection> = match state.engine_rx.borrow().clone() {
        Some(c) => c,
        None => return error_response(502, "INTERNAL_ERROR", "engine not connected"),
    };

    // ⑤ Subscribe events BEFORE sending
    let events_rx = conn.subscribe_events();

    // ⑥ Send CancelOrder
    let request_id = uuid::Uuid::new_v4().to_string();
    let cmd = Command::CancelOrder {
        request_id: request_id.clone(),
        venue: "tachibana".to_string(),
        client_order_id: body.client_order_id.clone(),
        venue_order_id: venue_order_id.clone(),
    };
    if let Err(e) = conn.send(cmd).await {
        return error_response(
            502,
            "INTERNAL_ERROR",
            &format!("failed to send to engine: {e}"),
        );
    }

    // ⑦ Wait for OrderPendingCancel
    let cid_str = body.client_order_id.clone();
    match tokio::time::timeout(
        state.submit_timeout,
        wait_for_pending_cancel(&cid_str, events_rx),
    )
    .await
    {
        Ok(Ok(ts_event_ms)) => {
            let resp = serde_json::json!({
                "client_order_id": cid_str,
                "venue_order_id": venue_order_id,
                "status": "PENDING_CANCEL",
                "ts_event_ms": ts_event_ms
            });
            HttpResponse {
                status: 200,
                body: resp.to_string(),
            }
        }
        Ok(Err(msg)) => error_response(502, "INTERNAL_ERROR", &msg),
        Err(_) => error_response(504, "INTERNAL_ERROR", "cancel order timed out"),
    }
}

/// `POST /api/order/cancel-all` core logic.
///
/// Requires `confirm: true` (boolean) in the JSON body.
/// Returns 202 Accepted immediately after forwarding to the engine
/// (cancel-all is fire-and-forget at the HTTP layer).
async fn cancel_all_orders(raw_body: &str, state: &Arc<OrderApiState>) -> HttpResponse {
    // ① Parse — empty body is allowed (treated as `{}`, which will fail confirm check)
    let body: CancelAllBody = if raw_body.trim().is_empty() {
        CancelAllBody {
            confirm: None,
            instrument_id: None,
            order_side: None,
        }
    } else {
        match serde_json::from_str(raw_body) {
            Ok(b) => b,
            Err(e) => {
                return error_response(400, "VALIDATION_ERROR", &format!("invalid JSON: {e}"));
            }
        }
    };

    // ② confirm guard — must be boolean `true`, not string "true" or absent
    match body.confirm.as_ref() {
        Some(serde_json::Value::Bool(true)) => {} // OK
        Some(serde_json::Value::Bool(false)) => {
            return error_response(
                400,
                "CONFIRM_REQUIRED",
                "confirm must be true to cancel all orders",
            );
        }
        Some(_) => {
            return error_response(
                400,
                "CONFIRM_REQUIRED",
                "confirm must be a boolean true, not a string",
            );
        }
        None => {
            return error_response(
                400,
                "CONFIRM_REQUIRED",
                "confirm: true is required in the request body",
            );
        }
    }

    // ③ Guard
    if !state.guard_config.enabled {
        return error_response(
            503,
            "ORDER_GUARD_NOT_CONFIGURED",
            "order guard is not configured",
        );
    }

    // ④ Engine connection
    let conn: Arc<EngineConnection> = match state.engine_rx.borrow().clone() {
        Some(c) => c,
        None => return error_response(502, "INTERNAL_ERROR", "engine not connected"),
    };

    // ⑤ Parse optional order_side
    let order_side = match body.order_side.as_deref() {
        None => None,
        Some("BUY") => Some(engine_client::dto::OrderSide::Buy),
        Some("SELL") => Some(engine_client::dto::OrderSide::Sell),
        Some(s) => {
            return error_response(
                400,
                "VALIDATION_ERROR",
                &format!("order_side must be BUY or SELL, got {s:?}"),
            );
        }
    };

    // ⑥ Send CancelAllOrders (fire-and-forget)
    let request_id = uuid::Uuid::new_v4().to_string();
    let cmd = Command::CancelAllOrders {
        request_id,
        venue: "tachibana".to_string(),
        instrument_id: body.instrument_id,
        order_side,
    };
    if let Err(e) = conn.send(cmd).await {
        return error_response(
            502,
            "INTERNAL_ERROR",
            &format!("failed to send to engine: {e}"),
        );
    }

    let resp = serde_json::json!({ "status": "accepted" });
    HttpResponse {
        status: 202,
        body: resp.to_string(),
    }
}

/// `GET /api/order/list` core logic.
///
/// Body is optional JSON with filter fields.
/// Sends `Command::GetOrderList` and waits for `OrderListUpdated`.
async fn list_orders(raw_body: &str, state: &Arc<OrderApiState>) -> HttpResponse {
    // ① Parse optional filter body (GET may have empty body)
    let filter_body: OrderListQueryBody = if raw_body.trim().is_empty() {
        OrderListQueryBody {
            status: None,
            instrument_id: None,
            date: None,
        }
    } else {
        match serde_json::from_str(raw_body) {
            Ok(b) => b,
            Err(e) => {
                return error_response(400, "VALIDATION_ERROR", &format!("invalid JSON: {e}"));
            }
        }
    };

    // ② Engine connection
    let conn: Arc<EngineConnection> = match state.engine_rx.borrow().clone() {
        Some(c) => c,
        None => return error_response(502, "INTERNAL_ERROR", "engine not connected"),
    };

    // ③ Subscribe events BEFORE sending
    let events_rx = conn.subscribe_events();

    // ④ Send GetOrderList
    let request_id = uuid::Uuid::new_v4().to_string();
    let filter = IpcOrderListFilter {
        status: filter_body.status,
        instrument_id: filter_body.instrument_id,
        date: filter_body.date,
    };
    let cmd = Command::GetOrderList {
        request_id: request_id.clone(),
        venue: "tachibana".to_string(),
        filter,
    };
    if let Err(e) = conn.send(cmd).await {
        return error_response(
            502,
            "INTERNAL_ERROR",
            &format!("failed to send to engine: {e}"),
        );
    }

    // ⑤ Wait for OrderListUpdated matching request_id
    match tokio::time::timeout(
        state.submit_timeout,
        wait_for_order_list(&request_id, events_rx),
    )
    .await
    {
        Ok(Ok(orders)) => {
            let resp = serde_json::json!({ "orders": orders });
            HttpResponse {
                status: 200,
                body: serde_json::to_string(&resp).unwrap_or_default(),
            }
        }
        Ok(Err(msg)) => error_response(502, "INTERNAL_ERROR", &msg),
        Err(_) => error_response(504, "INTERNAL_ERROR", "order list timed out"),
    }
}

// ── Validation ─────────────────────────────────────────────────────────────────

fn validate(body: &SubmitOrderBody) -> Result<(), HttpResponse> {
    // client_order_id: 1–36 chars, ASCII printable (0x20–0x7E)
    let cid = &body.client_order_id;
    if cid.is_empty() || cid.len() > 36 {
        return Err(error_response(
            400,
            "VALIDATION_ERROR",
            "client_order_id: must be 1–36 characters",
        ));
    }
    if !cid.bytes().all(|b| (0x20..=0x7E).contains(&b)) {
        return Err(error_response(
            400,
            "VALIDATION_ERROR",
            "client_order_id: must contain ASCII printable characters only",
        ));
    }

    // instrument_id: <symbol>.TSE format (Phase O0–O2: TSE only)
    if !body.instrument_id.ends_with(".TSE") {
        return Err(error_response(
            400,
            "VALIDATION_ERROR",
            "instrument_id: must be in <symbol>.TSE format (Phase O0–O2: TSE only)",
        ));
    }
    let symbol = body.instrument_id.trim_end_matches(".TSE");
    if symbol.is_empty() {
        return Err(error_response(
            400,
            "VALIDATION_ERROR",
            "instrument_id: symbol part must not be empty",
        ));
    }

    // order_side
    match body.order_side.as_str() {
        "BUY" | "SELL" => {}
        _ => {
            return Err(error_response(
                400,
                "VALIDATION_ERROR",
                "order_side: must be BUY or SELL",
            ));
        }
    }

    // order_type — MARKET_IF_TOUCHED / LIMIT_IF_TOUCHED → VENUE_UNSUPPORTED
    match body.order_type.as_str() {
        "MARKET" | "LIMIT" | "STOP_MARKET" | "STOP_LIMIT" => {}
        "MARKET_IF_TOUCHED" | "LIMIT_IF_TOUCHED" => {
            return Err(error_response(
                400,
                "VENUE_UNSUPPORTED",
                "order_type: MARKET_IF_TOUCHED and LIMIT_IF_TOUCHED are not supported by the venue",
            ));
        }
        _ => {
            return Err(error_response(
                400,
                "VALIDATION_ERROR",
                "order_type: must be MARKET, LIMIT, STOP_MARKET, or STOP_LIMIT",
            ));
        }
    }

    // quantity: positive integer string
    if body.quantity.is_empty() {
        return Err(error_response(
            400,
            "VALIDATION_ERROR",
            "quantity: must not be empty",
        ));
    }
    if !body.quantity.bytes().all(|b| b.is_ascii_digit()) {
        return Err(error_response(
            400,
            "VALIDATION_ERROR",
            "quantity: must be a positive integer string",
        ));
    }
    if body.quantity.parse::<u64>().unwrap_or(0) == 0 {
        return Err(error_response(
            400,
            "VALIDATION_ERROR",
            "quantity: must be greater than zero",
        ));
    }

    // price: required for LIMIT / STOP_LIMIT
    if matches!(body.order_type.as_str(), "LIMIT" | "STOP_LIMIT") && body.price.is_none() {
        return Err(error_response(
            400,
            "VALIDATION_ERROR",
            "price: required for LIMIT and STOP_LIMIT order types",
        ));
    }

    // trigger_price: required for STOP_MARKET / STOP_LIMIT
    if matches!(body.order_type.as_str(), "STOP_MARKET" | "STOP_LIMIT")
        && body.trigger_price.is_none()
    {
        return Err(error_response(
            400,
            "VALIDATION_ERROR",
            "trigger_price: required for STOP_MARKET and STOP_LIMIT order types",
        ));
    }

    // time_in_force — GTC / IOC / FOK → VENUE_UNSUPPORTED
    match body.time_in_force.as_str() {
        "DAY" | "GTD" | "AT_THE_OPEN" | "AT_THE_CLOSE" => {}
        "GTC" | "IOC" | "FOK" => {
            return Err(error_response(
                400,
                "VENUE_UNSUPPORTED",
                "time_in_force: GTC, IOC, and FOK are not supported by the venue",
            ));
        }
        _ => {
            return Err(error_response(
                400,
                "VALIDATION_ERROR",
                "time_in_force: must be DAY, GTD, AT_THE_OPEN, or AT_THE_CLOSE",
            ));
        }
    }

    // expire_time: required for GTD
    if body.time_in_force == "GTD" && body.expire_time.is_none() {
        return Err(error_response(
            400,
            "VALIDATION_ERROR",
            "expire_time: required when time_in_force is GTD",
        ));
    }

    // tags: each element must be `key=value` with exactly one `=`; ASCII printable
    for tag in &body.tags {
        let eq_count = tag.bytes().filter(|&b| b == b'=').count();
        if eq_count != 1 {
            return Err(error_response(
                400,
                "VALIDATION_ERROR",
                &format!("tags: each tag must be key=value (exactly one '='): {tag:?}"),
            ));
        }
        if !tag.bytes().all(|b| (0x20..=0x7E).contains(&b)) {
            return Err(error_response(
                400,
                "VALIDATION_ERROR",
                &format!("tags: tags must contain ASCII printable characters only: {tag:?}"),
            ));
        }
    }

    Ok(())
}

// ── Request key ────────────────────────────────────────────────────────────────

/// Compute a canonical u64 hash for the order request body.
///
/// Excludes `client_order_id`, `request_id`, and `venue` — same order with
/// a different `client_order_id` is a *Conflict*, not an IdempotentReplay.
///
/// Seed: xxh3_64(b"order_request_key_v1") — computed once.
fn compute_request_key(body: &SubmitOrderBody, expire_time_ns: Option<i64>) -> u64 {
    static SEED: std::sync::OnceLock<u64> = std::sync::OnceLock::new();
    let seed = *SEED.get_or_init(|| xxhash_rust::xxh3::xxh3_64(b"order_request_key_v1"));

    let canonical = canonical_bytes(body, expire_time_ns);
    xxh3_64_with_seed(&canonical, seed)
}

/// Produce canonical byte representation for hashing.
///
/// Rules (architecture.md §4.1):
/// - `tags`: sorted ascending + deduped before hashing
/// - `null` values are encoded as `\x00` (distinct from empty string `\x01""`)
/// - numeric `expire_time_ns` is used rather than the raw ISO8601 string
fn canonical_bytes(body: &SubmitOrderBody, expire_time_ns: Option<i64>) -> Vec<u8> {
    let mut sorted_tags = body.tags.clone();
    sorted_tags.sort_unstable();
    sorted_tags.dedup();

    let mut buf = Vec::with_capacity(256);

    write_str_field(&mut buf, &body.instrument_id);
    write_str_field(&mut buf, &body.order_side);
    write_str_field(&mut buf, &body.order_type);
    write_str_field(&mut buf, &body.quantity);
    write_opt_field(&mut buf, body.price.as_deref());
    write_opt_field(&mut buf, body.trigger_price.as_deref());
    write_opt_field(&mut buf, body.trigger_type.as_deref());
    write_str_field(&mut buf, &body.time_in_force);
    write_opt_i64_field(&mut buf, expire_time_ns);
    write_bool_field(&mut buf, body.post_only);
    write_bool_field(&mut buf, body.reduce_only);
    write_str_field(&mut buf, &sorted_tags.join("\x1F")); // unit separator

    buf
}

fn write_str_field(buf: &mut Vec<u8>, s: &str) {
    buf.push(0x01); // present marker
    buf.extend_from_slice(s.as_bytes());
    buf.push(0x00); // field separator
}

fn write_opt_field(buf: &mut Vec<u8>, v: Option<&str>) {
    match v {
        None => buf.push(0x00), // null marker
        Some(s) => {
            buf.push(0x01);
            buf.extend_from_slice(s.as_bytes());
            buf.push(0x00);
        }
    }
}

fn write_opt_i64_field(buf: &mut Vec<u8>, v: Option<i64>) {
    match v {
        None => buf.push(0x00),
        Some(n) => {
            buf.push(0x01);
            buf.extend_from_slice(n.to_string().as_bytes());
            buf.push(0x00);
        }
    }
}

fn write_bool_field(buf: &mut Vec<u8>, v: bool) {
    buf.push(if v { 0x01 } else { 0x00 });
}

// ── IPC order builder ─────────────────────────────────────────────────────────

fn build_ipc_order(
    body: &SubmitOrderBody,
    expire_time_ns: Option<i64>,
    _request_id: &str,
) -> SubmitOrderRequest {
    SubmitOrderRequest {
        client_order_id: body.client_order_id.clone(),
        instrument_id: body.instrument_id.clone(),
        order_side: parse_order_side(&body.order_side),
        order_type: parse_order_type(&body.order_type),
        quantity: body.quantity.clone(),
        price: body.price.clone(),
        trigger_price: body.trigger_price.clone(),
        trigger_type: body.trigger_type.as_deref().and_then(parse_trigger_type),
        time_in_force: parse_time_in_force(&body.time_in_force),
        expire_time_ns,
        post_only: body.post_only,
        reduce_only: body.reduce_only,
        tags: body.tags.clone(),
    }
}

fn parse_order_side(s: &str) -> OrderSide {
    match s {
        "BUY" => OrderSide::Buy,
        _ => OrderSide::Sell,
    }
}

fn parse_order_type(s: &str) -> OrderType {
    match s {
        "MARKET" => OrderType::Market,
        "LIMIT" => OrderType::Limit,
        "STOP_MARKET" => OrderType::StopMarket,
        _ => OrderType::StopLimit,
    }
}

fn parse_time_in_force(s: &str) -> TimeInForce {
    match s {
        "DAY" => TimeInForce::Day,
        "GTD" => TimeInForce::Gtd,
        "AT_THE_OPEN" => TimeInForce::AtTheOpen,
        _ => TimeInForce::AtTheClose,
    }
}

fn parse_trigger_type(s: &str) -> Option<TriggerType> {
    match s {
        "LAST" => Some(TriggerType::Last),
        "BID_ASK" => Some(TriggerType::BidAsk),
        "INDEX" => Some(TriggerType::Index),
        _ => None,
    }
}

// ── Event waiter ──────────────────────────────────────────────────────────────

enum OrderWaitResult {
    Accepted {
        venue_order_id: Option<String>,
        ts_event_ms: i64,
    },
    Rejected {
        reason_code: String,
        reason_text: String,
    },
    SecondPasswordRequired,
    Disconnected,
}

async fn wait_for_order_result(
    client_order_id: &str,
    request_id: &str,
    mut rx: tokio::sync::broadcast::Receiver<EngineEvent>,
) -> OrderWaitResult {
    loop {
        match rx.recv().await {
            Ok(EngineEvent::OrderAccepted {
                client_order_id: cid,
                venue_order_id,
                ts_event_ms,
            }) if cid == client_order_id => {
                return OrderWaitResult::Accepted {
                    venue_order_id,
                    ts_event_ms,
                };
            }
            Ok(EngineEvent::OrderRejected {
                client_order_id: cid,
                reason_code,
                reason_text,
                ..
            }) if cid == client_order_id => {
                return OrderWaitResult::Rejected {
                    reason_code,
                    reason_text,
                };
            }
            Ok(EngineEvent::SecondPasswordRequired {
                request_id: rid, ..
            }) if rid == request_id => return OrderWaitResult::SecondPasswordRequired,
            Ok(EngineEvent::ConnectionDropped) => return OrderWaitResult::Disconnected,
            Ok(_) => continue,
            Err(tokio::sync::broadcast::error::RecvError::Lagged(_)) => continue,
            Err(tokio::sync::broadcast::error::RecvError::Closed) => {
                return OrderWaitResult::Disconnected;
            }
        }
    }
}

/// Wait for `OrderPendingUpdate` for the given `client_order_id`.
async fn wait_for_pending_update(
    client_order_id: &str,
    mut rx: tokio::sync::broadcast::Receiver<EngineEvent>,
) -> Result<i64, String> {
    loop {
        match rx.recv().await {
            Ok(EngineEvent::OrderPendingUpdate {
                client_order_id: cid,
                ts_event_ms,
            }) if cid == client_order_id => return Ok(ts_event_ms),
            Ok(EngineEvent::OrderRejected {
                client_order_id: cid,
                reason_code,
                reason_text,
                ..
            }) if cid == client_order_id => {
                return Err(format!("{reason_code}: {reason_text}"));
            }
            Ok(EngineEvent::ConnectionDropped) => return Err("engine connection lost".to_string()),
            Ok(_) => continue,
            Err(tokio::sync::broadcast::error::RecvError::Lagged(_)) => continue,
            Err(tokio::sync::broadcast::error::RecvError::Closed) => {
                return Err("engine connection closed".to_string());
            }
        }
    }
}

/// Wait for `OrderPendingCancel` for the given `client_order_id`.
async fn wait_for_pending_cancel(
    client_order_id: &str,
    mut rx: tokio::sync::broadcast::Receiver<EngineEvent>,
) -> Result<i64, String> {
    loop {
        match rx.recv().await {
            Ok(EngineEvent::OrderPendingCancel {
                client_order_id: cid,
                ts_event_ms,
            }) if cid == client_order_id => return Ok(ts_event_ms),
            Ok(EngineEvent::OrderRejected {
                client_order_id: cid,
                reason_code,
                reason_text,
                ..
            }) if cid == client_order_id => {
                return Err(format!("{reason_code}: {reason_text}"));
            }
            Ok(EngineEvent::ConnectionDropped) => return Err("engine connection lost".to_string()),
            Ok(_) => continue,
            Err(tokio::sync::broadcast::error::RecvError::Lagged(_)) => continue,
            Err(tokio::sync::broadcast::error::RecvError::Closed) => {
                return Err("engine connection closed".to_string());
            }
        }
    }
}

/// Wait for `OrderListUpdated` matching the given `request_id`.
async fn wait_for_order_list(
    request_id: &str,
    mut rx: tokio::sync::broadcast::Receiver<EngineEvent>,
) -> Result<Vec<engine_client::dto::OrderRecordWire>, String> {
    loop {
        match rx.recv().await {
            Ok(EngineEvent::OrderListUpdated {
                request_id: rid,
                orders,
            }) if rid == request_id => return Ok(orders),
            Ok(EngineEvent::ConnectionDropped) => return Err("engine connection lost".to_string()),
            Ok(_) => continue,
            Err(tokio::sync::broadcast::error::RecvError::Lagged(_)) => continue,
            Err(tokio::sync::broadcast::error::RecvError::Closed) => {
                return Err("engine connection closed".to_string());
            }
        }
    }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

fn reason_code_to_status(code: &str) -> u16 {
    match code {
        "REPLAY_MODE_ACTIVE" | "SESSION_EXPIRED" => 503,
        "SECOND_PASSWORD_REQUIRED" | "SECOND_PASSWORD_INVALID" => 401,
        "SECOND_PASSWORD_LOCKED" => 423,
        "RATE_LIMITED" => 429,
        "MARKET_CLOSED" | "ORDER_STATUS_UNKNOWN" => 409,
        "INSUFFICIENT_FUNDS" => 403,
        "INTERNAL_ERROR" => 500,
        _ => 400,
    }
}

fn error_response(status: u16, reason_code: &str, reason_text: &str) -> HttpResponse {
    let body = serde_json::json!({
        "reason_code": reason_code,
        "reason_text": reason_text,
    });
    HttpResponse {
        status,
        body: body.to_string(),
    }
}

fn format_http_response(status: u16, body: &str) -> String {
    let status_text = match status {
        200 => "OK",
        201 => "Created",
        202 => "Accepted",
        400 => "Bad Request",
        401 => "Unauthorized",
        402 => "Payment Required",
        403 => "Forbidden",
        404 => "Not Found",
        409 => "Conflict",
        423 => "Locked",
        429 => "Too Many Requests",
        500 => "Internal Server Error",
        501 => "Not Implemented",
        502 => "Bad Gateway",
        503 => "Service Unavailable",
        504 => "Gateway Timeout",
        _ => "Unknown",
    };
    format!(
        "HTTP/1.1 {status} {status_text}\r\n\
         Content-Type: application/json\r\n\
         Content-Length: {}\r\n\
         Connection: close\r\n\
         \r\n\
         {body}",
        body.len()
    )
}

fn parse_expire_time(s: &str) -> Result<i64, String> {
    use chrono::{DateTime, Utc};
    let dt: DateTime<Utc> = s
        .parse()
        .map_err(|e| format!("expire_time: invalid ISO8601 datetime: {e}"))?;
    dt.timestamp_nanos_opt()
        .ok_or_else(|| "expire_time: timestamp out of representable range".to_string())
}

// ── Tests ──────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use futures_util::{SinkExt, StreamExt};
    use std::net::SocketAddr;
    use tokio::io::{AsyncBufReadExt, BufReader};
    use tokio::net::{TcpListener, TcpStream as StdTcpStream};
    use tokio_tungstenite::{accept_async, tungstenite::Message};

    // ── Test helpers ──────────────────────────────────────────────────────────

    /// Bind a random loopback port for a mock WebSocket server.
    async fn bind_ws_loopback() -> (TcpListener, SocketAddr) {
        let l = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = l.local_addr().unwrap();
        (l, addr)
    }

    /// Send Ready frame in response to Hello.
    async fn ws_send_ready<S>(ws: &mut tokio_tungstenite::WebSocketStream<S>)
    where
        S: tokio::io::AsyncRead + tokio::io::AsyncWrite + Unpin,
    {
        let ready = serde_json::json!({
            "event": "Ready",
            "schema_major": engine_client::SCHEMA_MAJOR,
            "schema_minor": engine_client::SCHEMA_MINOR,
            "engine_version": "1.0.0-mock",
            "engine_session_id": "00000000-0000-0000-0000-000000000001",
            "capabilities": {}
        });
        ws.send(Message::Text(ready.to_string().into()))
            .await
            .unwrap();
    }

    /// Spawn a mock WS engine that:
    /// 1. Performs Hello/Ready handshake
    /// 2. Reads SubmitOrder command
    /// 3. Sends OrderSubmitted then OrderAccepted (or OrderRejected)
    fn spawn_mock_engine_accepts(
        listener: TcpListener,
        client_order_id: &str,
        venue_order_id: &str,
    ) {
        let cid = client_order_id.to_owned();
        let vid = venue_order_id.to_owned();
        tokio::spawn(async move {
            let (tcp, _) = listener.accept().await.unwrap();
            let mut ws = accept_async(tcp).await.unwrap();

            // Handshake: skip Hello, send Ready
            let _ = ws.next().await; // consume Hello
            ws_send_ready(&mut ws).await;

            // Read SubmitOrder command
            if let Some(Ok(msg)) = ws.next().await {
                let text = msg.into_text().unwrap();
                let v: serde_json::Value = serde_json::from_str(&text).unwrap();
                assert_eq!(v["op"], "SubmitOrder", "expected SubmitOrder command");
                let _ = v["order"]["client_order_id"].as_str(); // just read it
            }

            // Send OrderSubmitted
            let submitted = serde_json::json!({
                "event": "OrderSubmitted",
                "client_order_id": cid,
                "ts_event_ms": 1000000
            });
            ws.send(Message::Text(submitted.to_string().into()))
                .await
                .unwrap();

            // Send OrderAccepted
            let accepted = serde_json::json!({
                "event": "OrderAccepted",
                "client_order_id": cid,
                "venue_order_id": vid,
                "ts_event_ms": 1000100
            });
            ws.send(Message::Text(accepted.to_string().into()))
                .await
                .unwrap();

            // Keep connection alive briefly
            tokio::time::sleep(Duration::from_millis(200)).await;
        });
    }

    /// Spawn a mock engine that sends OrderRejected.
    fn spawn_mock_engine_rejects(listener: TcpListener, client_order_id: &str, reason_code: &str) {
        let cid = client_order_id.to_owned();
        let rc = reason_code.to_owned();
        tokio::spawn(async move {
            let (tcp, _) = listener.accept().await.unwrap();
            let mut ws = accept_async(tcp).await.unwrap();
            let _ = ws.next().await;
            ws_send_ready(&mut ws).await;

            // Read SubmitOrder
            let _ = ws.next().await;

            // Send OrderRejected
            let rejected = serde_json::json!({
                "event": "OrderRejected",
                "client_order_id": cid,
                "reason_code": rc,
                "reason_text": "mock rejection",
                "ts_event_ms": 1000000
            });
            ws.send(Message::Text(rejected.to_string().into()))
                .await
                .unwrap();

            tokio::time::sleep(Duration::from_millis(200)).await;
        });
    }

    /// Spawn a mock engine that never responds to SubmitOrder (for timeout test).
    fn spawn_mock_engine_silent(listener: TcpListener) {
        tokio::spawn(async move {
            let (tcp, _) = listener.accept().await.unwrap();
            let mut ws = accept_async(tcp).await.unwrap();
            let _ = ws.next().await;
            ws_send_ready(&mut ws).await;
            let _ = ws.next().await; // consume SubmitOrder but don't reply
            // Hold connection alive so the test sees Timeout (not Disconnected)
            tokio::time::sleep(Duration::from_secs(10)).await;
        });
    }

    /// Connect an EngineConnection to the mock server.
    async fn connect_engine(addr: SocketAddr) -> Arc<EngineConnection> {
        // Give the server task a tick to accept.
        tokio::time::sleep(Duration::from_millis(5)).await;
        let url = format!("ws://{addr}");
        Arc::new(
            EngineConnection::connect(&url, "test-token")
                .await
                .expect("engine connect failed"),
        )
    }

    /// Build a default valid HTTP POST body for /api/order/submit.
    fn default_submit_body(client_order_id: &str) -> String {
        serde_json::json!({
            "client_order_id": client_order_id,
            "instrument_id": "7203.TSE",
            "order_side": "BUY",
            "order_type": "MARKET",
            "quantity": "100",
            "price": null,
            "trigger_price": null,
            "trigger_type": null,
            "time_in_force": "DAY",
            "expire_time": null,
            "post_only": false,
            "reduce_only": false,
            "tags": ["cash_margin=cash"]
        })
        .to_string()
    }

    /// Build a LIMIT order body.
    fn limit_submit_body(client_order_id: &str, quantity: &str, price: &str) -> String {
        serde_json::json!({
            "client_order_id": client_order_id,
            "instrument_id": "7203.TSE",
            "order_side": "BUY",
            "order_type": "LIMIT",
            "quantity": quantity,
            "price": price,
            "trigger_price": null,
            "trigger_type": null,
            "time_in_force": "DAY",
            "expire_time": null,
            "post_only": false,
            "reduce_only": false,
            "tags": ["cash_margin=cash"]
        })
        .to_string()
    }

    /// Spawn a minimal HTTP server for the order API (test-only).
    async fn spawn_test_http_server(state: Arc<OrderApiState>) -> u16 {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = listener.local_addr().unwrap().port();
        tokio::spawn(async move {
            while let Ok((mut stream, _)) = listener.accept().await {
                let state = Arc::clone(&state);
                tokio::spawn(async move {
                    // Parse raw HTTP/1.1 request
                    let mut reader = BufReader::new(&mut stream);
                    let mut request_line = String::new();
                    let _ = reader.read_line(&mut request_line).await;

                    let mut content_length: usize = 0;
                    loop {
                        let mut line = String::new();
                        let _ = reader.read_line(&mut line).await;
                        if line.trim().is_empty() {
                            break;
                        }
                        if line.to_lowercase().starts_with("content-length:") {
                            if let Some(v) = line.splitn(2, ':').nth(1) {
                                content_length = v.trim().parse().unwrap_or(0);
                            }
                        }
                    }

                    let body = if content_length > 0 {
                        let mut buf = vec![0u8; content_length.min(65_536)];
                        use tokio::io::AsyncReadExt;
                        let _ = reader.read_exact(&mut buf).await;
                        String::from_utf8_lossy(&buf).into_owned()
                    } else {
                        String::new()
                    };

                    drop(reader);
                    handle_submit_request(&mut stream, &body, &state).await;
                });
            }
        });
        port
    }

    /// Make a simple raw HTTP POST request and return status + body.
    async fn http_post(port: u16, path: &str, body: &str) -> (u16, String) {
        let mut stream = StdTcpStream::connect(format!("127.0.0.1:{port}"))
            .await
            .unwrap();

        let req = format!(
            "POST {path} HTTP/1.1\r\n\
             Host: 127.0.0.1\r\n\
             Content-Type: application/json\r\n\
             Content-Length: {}\r\n\
             Connection: close\r\n\
             \r\n\
             {body}",
            body.len()
        );

        stream.write_all(req.as_bytes()).await.unwrap();

        // Read the response
        let mut response = String::new();
        use tokio::io::AsyncReadExt;
        stream.read_to_string(&mut response).await.unwrap();

        // Parse status line
        let status = response
            .lines()
            .next()
            .and_then(|l| l.split_whitespace().nth(1))
            .and_then(|s| s.parse::<u16>().ok())
            .unwrap_or(0);

        // Extract body (after \r\n\r\n)
        let resp_body = response.split("\r\n\r\n").nth(1).unwrap_or("").to_string();

        (status, resp_body)
    }

    // ── Acceptance tests ──────────────────────────────────────────────────────

    #[tokio::test]
    async fn test_submit_order_returns_201_with_venue_order_id() {
        let (ws_listener, ws_addr) = bind_ws_loopback().await;
        let cid = "order-201-test-uuid-00001";
        let vid = "VENUE-ORDER-001";
        spawn_mock_engine_accepts(ws_listener, cid, vid);

        let conn = connect_engine(ws_addr).await;
        let (engine_tx, engine_rx) = watch::channel(Some(conn));
        let session = Arc::new(Mutex::new(OrderSessionState::new()));
        let is_replay = Arc::new(AtomicBool::new(false));
        let state = Arc::new(
            OrderApiState::new(session, engine_rx, is_replay)
                .with_guard_config(OrderGuardConfig::enabled_no_limits())
                .with_timeout(Duration::from_secs(5)),
        );

        let port = spawn_test_http_server(Arc::clone(&state)).await;
        tokio::time::sleep(Duration::from_millis(10)).await;

        let body = default_submit_body(cid);
        let (status, resp_body) = http_post(port, "/api/order/submit", &body).await;

        assert_eq!(status, 201, "expected 201 Created; body={resp_body}");
        let json: serde_json::Value = serde_json::from_str(&resp_body).unwrap();
        assert_eq!(json["venue_order_id"].as_str(), Some(vid));
        assert_eq!(json["status"].as_str(), Some("ACCEPTED"));

        drop(engine_tx);
    }

    #[tokio::test]
    async fn test_submit_order_idempotent_replay_returns_200() {
        let (ws_listener, ws_addr) = bind_ws_loopback().await;
        let cid = "order-200-idempotent-uuid-00002";
        let vid = "VENUE-ORDER-002";
        spawn_mock_engine_accepts(ws_listener, cid, vid);

        let conn = connect_engine(ws_addr).await;
        let (engine_tx, engine_rx) = watch::channel(Some(conn));
        let session = Arc::new(Mutex::new(OrderSessionState::new()));
        let is_replay = Arc::new(AtomicBool::new(false));
        let state = Arc::new(
            OrderApiState::new(session, engine_rx, is_replay)
                .with_guard_config(OrderGuardConfig::enabled_no_limits())
                .with_timeout(Duration::from_secs(5)),
        );

        let port = spawn_test_http_server(Arc::clone(&state)).await;
        tokio::time::sleep(Duration::from_millis(10)).await;

        let body = default_submit_body(cid);

        // First request: should return 201
        let (status1, _) = http_post(port, "/api/order/submit", &body).await;
        assert_eq!(status1, 201, "first request should return 201");

        // Small delay to ensure the session state is updated
        tokio::time::sleep(Duration::from_millis(50)).await;

        // Second request with same client_order_id and same body: should return 200
        let (status2, resp_body2) = http_post(port, "/api/order/submit", &body).await;
        assert_eq!(
            status2, 200,
            "idempotent replay should return 200; body={resp_body2}"
        );
        let json: serde_json::Value = serde_json::from_str(&resp_body2).unwrap();
        assert_eq!(json["venue_order_id"].as_str(), Some(vid));

        drop(engine_tx);
    }

    #[tokio::test]
    async fn test_submit_order_conflict_returns_409() {
        let (ws_listener, ws_addr) = bind_ws_loopback().await;
        let cid = "order-409-conflict-uuid-00003";
        let vid = "VENUE-ORDER-003";
        spawn_mock_engine_accepts(ws_listener, cid, vid);

        let conn = connect_engine(ws_addr).await;
        let (engine_tx, engine_rx) = watch::channel(Some(conn));
        let session = Arc::new(Mutex::new(OrderSessionState::new()));
        let is_replay = Arc::new(AtomicBool::new(false));
        let state = Arc::new(
            OrderApiState::new(session, engine_rx, is_replay)
                .with_guard_config(OrderGuardConfig::enabled_no_limits())
                .with_timeout(Duration::from_secs(5)),
        );

        let port = spawn_test_http_server(Arc::clone(&state)).await;
        tokio::time::sleep(Duration::from_millis(10)).await;

        let body1 = default_submit_body(cid);

        // First request: 201
        let (status1, _) = http_post(port, "/api/order/submit", &body1).await;
        assert_eq!(status1, 201);
        tokio::time::sleep(Duration::from_millis(50)).await;

        // Second request: same client_order_id but different quantity → Conflict
        let body2 = serde_json::json!({
            "client_order_id": cid,
            "instrument_id": "7203.TSE",
            "order_side": "BUY",
            "order_type": "MARKET",
            "quantity": "999",           // different from body1
            "price": null,
            "trigger_price": null,
            "trigger_type": null,
            "time_in_force": "DAY",
            "expire_time": null,
            "post_only": false,
            "reduce_only": false,
            "tags": ["cash_margin=cash"]
        })
        .to_string();

        let (status2, resp_body2) = http_post(port, "/api/order/submit", &body2).await;
        assert_eq!(
            status2, 409,
            "conflict should return 409; body={resp_body2}"
        );

        drop(engine_tx);
    }

    #[tokio::test]
    async fn test_submit_order_replay_mode_returns_503() {
        // No engine needed — replay mode check fires first
        let (_engine_tx, engine_rx) = watch::channel(None::<Arc<EngineConnection>>);
        let session = Arc::new(Mutex::new(OrderSessionState::new()));
        let is_replay = Arc::new(AtomicBool::new(true)); // REPLAY MODE ON
        let state = Arc::new(OrderApiState::new(session, engine_rx, is_replay));

        let port = spawn_test_http_server(Arc::clone(&state)).await;
        tokio::time::sleep(Duration::from_millis(10)).await;

        let body = default_submit_body("order-503-replay-uuid-00004");
        let (status, resp_body) = http_post(port, "/api/order/submit", &body).await;

        assert_eq!(
            status, 503,
            "replay mode should return 503; body={resp_body}"
        );
        let json: serde_json::Value = serde_json::from_str(&resp_body).unwrap();
        assert_eq!(json["reason_code"].as_str(), Some("REPLAY_MODE_ACTIVE"));
    }

    #[tokio::test]
    async fn test_submit_order_timeout_returns_504() {
        let (ws_listener, ws_addr) = bind_ws_loopback().await;
        spawn_mock_engine_silent(ws_listener);

        let conn = connect_engine(ws_addr).await;
        let (engine_tx, engine_rx) = watch::channel(Some(conn));
        let session = Arc::new(Mutex::new(OrderSessionState::new()));
        let is_replay = Arc::new(AtomicBool::new(false));
        let state = Arc::new(
            OrderApiState::new(session, engine_rx, is_replay)
                .with_guard_config(OrderGuardConfig::enabled_no_limits())
                .with_timeout(Duration::from_millis(200)), // short timeout for test
        );

        let port = spawn_test_http_server(Arc::clone(&state)).await;
        tokio::time::sleep(Duration::from_millis(10)).await;

        let body = default_submit_body("order-504-timeout-uuid-00005");
        let (status, resp_body) = http_post(port, "/api/order/submit", &body).await;

        assert_eq!(status, 504, "timeout should return 504; body={resp_body}");
        let json: serde_json::Value = serde_json::from_str(&resp_body).unwrap();
        assert_eq!(json["reason_code"].as_str(), Some("INTERNAL_ERROR"));

        drop(engine_tx);
    }

    // ── Unit tests for validation / request key ───────────────────────────────

    // ── T0.8 Schema validation tests ─────────────────────────────────────────

    /// client_order_id 長さ 0 → 400 VALIDATION_ERROR
    #[test]
    fn test_invalid_client_order_id_empty_rejected() {
        let body = serde_json::json!({
            "client_order_id": "",
            "instrument_id": "7203.TSE",
            "order_side": "BUY",
            "order_type": "MARKET",
            "quantity": "100",
            "time_in_force": "DAY",
            "post_only": false,
            "reduce_only": false,
            "tags": []
        })
        .to_string();
        let parsed: SubmitOrderBody = serde_json::from_str(&body).unwrap();
        let result = validate(&parsed);
        assert!(result.is_err());
        let err = result.unwrap_err();
        assert_eq!(err.status, 400);
        assert!(err.body.contains("VALIDATION_ERROR"), "body: {}", err.body);
    }

    /// client_order_id 長さ 37 → 400 VALIDATION_ERROR
    #[test]
    fn test_invalid_client_order_id_too_long_rejected() {
        let long_id = "A".repeat(37);
        let body = serde_json::json!({
            "client_order_id": long_id,
            "instrument_id": "7203.TSE",
            "order_side": "BUY",
            "order_type": "MARKET",
            "quantity": "100",
            "time_in_force": "DAY",
            "post_only": false,
            "reduce_only": false,
            "tags": []
        })
        .to_string();
        let parsed: SubmitOrderBody = serde_json::from_str(&body).unwrap();
        let result = validate(&parsed);
        assert!(result.is_err());
        let err = result.unwrap_err();
        assert_eq!(err.status, 400);
        assert!(err.body.contains("VALIDATION_ERROR"), "body: {}", err.body);
    }

    /// client_order_id に非 ASCII 文字 → 400 VALIDATION_ERROR
    #[test]
    fn test_invalid_client_order_id_non_ascii_rejected() {
        let body = serde_json::json!({
            "client_order_id": "注文ID-001",
            "instrument_id": "7203.TSE",
            "order_side": "BUY",
            "order_type": "MARKET",
            "quantity": "100",
            "time_in_force": "DAY",
            "post_only": false,
            "reduce_only": false,
            "tags": []
        })
        .to_string();
        let parsed: SubmitOrderBody = serde_json::from_str(&body).unwrap();
        let result = validate(&parsed);
        assert!(result.is_err());
        let err = result.unwrap_err();
        assert_eq!(err.status, 400);
        assert!(err.body.contains("VALIDATION_ERROR"), "body: {}", err.body);
    }

    /// quantity="0" → 400 VALIDATION_ERROR
    #[test]
    fn test_zero_quantity_rejected() {
        let body = serde_json::json!({
            "client_order_id": "valid-cid-001",
            "instrument_id": "7203.TSE",
            "order_side": "BUY",
            "order_type": "MARKET",
            "quantity": "0",
            "time_in_force": "DAY",
            "post_only": false,
            "reduce_only": false,
            "tags": []
        })
        .to_string();
        let parsed: SubmitOrderBody = serde_json::from_str(&body).unwrap();
        let result = validate(&parsed);
        assert!(result.is_err());
        let err = result.unwrap_err();
        assert_eq!(err.status, 400);
        assert!(err.body.contains("VALIDATION_ERROR"), "body: {}", err.body);
    }

    /// instrument_id が `.TSE` 形式でない → 400 VALIDATION_ERROR
    #[test]
    fn test_invalid_instrument_id_rejected() {
        for bad_id in &["INVALID", "7203", ".TSE", "7203.INVALID", "7203TSE"] {
            let body = serde_json::json!({
                "client_order_id": "valid-cid-002",
                "instrument_id": bad_id,
                "order_side": "BUY",
                "order_type": "MARKET",
                "quantity": "100",
                "time_in_force": "DAY",
                "post_only": false,
                "reduce_only": false,
                "tags": []
            })
            .to_string();
            let parsed: SubmitOrderBody = serde_json::from_str(&body).unwrap();
            let result = validate(&parsed);
            assert!(
                result.is_err(),
                "instrument_id={bad_id:?} should be rejected"
            );
            let err = result.unwrap_err();
            assert_eq!(
                err.status, 400,
                "instrument_id={bad_id:?} should return 400"
            );
            assert!(
                err.body.contains("VALIDATION_ERROR"),
                "instrument_id={bad_id:?} body: {}",
                err.body
            );
        }
    }

    #[test]
    fn test_client_order_id_too_long_returns_validation_error() {
        let long_id = "A".repeat(37);
        let body = serde_json::json!({
            "client_order_id": long_id,
            "instrument_id": "7203.TSE",
            "order_side": "BUY",
            "order_type": "MARKET",
            "quantity": "100",
            "time_in_force": "DAY",
            "post_only": false,
            "reduce_only": false,
            "tags": []
        })
        .to_string();
        let parsed: SubmitOrderBody = serde_json::from_str(&body).unwrap();
        let result = validate(&parsed);
        assert!(result.is_err());
        let err = result.unwrap_err();
        assert_eq!(err.status, 400);
        assert!(err.body.contains("VALIDATION_ERROR"));
    }

    #[test]
    fn test_market_if_touched_returns_venue_unsupported() {
        let body = serde_json::json!({
            "client_order_id": "test-cid-001",
            "instrument_id": "7203.TSE",
            "order_side": "BUY",
            "order_type": "MARKET_IF_TOUCHED",
            "quantity": "100",
            "time_in_force": "DAY",
            "post_only": false,
            "reduce_only": false,
            "tags": []
        })
        .to_string();
        let parsed: SubmitOrderBody = serde_json::from_str(&body).unwrap();
        let result = validate(&parsed);
        assert!(result.is_err());
        let err = result.unwrap_err();
        assert_eq!(err.status, 400);
        assert!(err.body.contains("VENUE_UNSUPPORTED"));
    }

    #[test]
    fn test_gtc_time_in_force_returns_venue_unsupported() {
        let body = serde_json::json!({
            "client_order_id": "test-cid-002",
            "instrument_id": "7203.TSE",
            "order_side": "BUY",
            "order_type": "MARKET",
            "quantity": "100",
            "time_in_force": "GTC",
            "post_only": false,
            "reduce_only": false,
            "tags": []
        })
        .to_string();
        let parsed: SubmitOrderBody = serde_json::from_str(&body).unwrap();
        let result = validate(&parsed);
        assert!(result.is_err());
        let err = result.unwrap_err();
        assert_eq!(err.status, 400);
        assert!(err.body.contains("VENUE_UNSUPPORTED"));
    }

    #[test]
    fn test_request_key_tags_order_invariant() {
        let body1 = serde_json::json!({
            "client_order_id": "cid-key-test-1",
            "instrument_id": "7203.TSE",
            "order_side": "BUY",
            "order_type": "MARKET",
            "quantity": "100",
            "time_in_force": "DAY",
            "post_only": false,
            "reduce_only": false,
            "tags": ["account_type=specific_with_withholding", "cash_margin=cash"]
        })
        .to_string();

        let body2 = serde_json::json!({
            "client_order_id": "cid-key-test-2",
            "instrument_id": "7203.TSE",
            "order_side": "BUY",
            "order_type": "MARKET",
            "quantity": "100",
            "time_in_force": "DAY",
            "post_only": false,
            "reduce_only": false,
            "tags": ["cash_margin=cash", "account_type=specific_with_withholding"]
        })
        .to_string();

        let parsed1: SubmitOrderBody = serde_json::from_str(&body1).unwrap();
        let parsed2: SubmitOrderBody = serde_json::from_str(&body2).unwrap();

        let key1 = compute_request_key(&parsed1, None);
        let key2 = compute_request_key(&parsed2, None);

        assert_eq!(key1, key2, "tags order must not affect request_key");
    }

    #[test]
    fn test_request_key_different_quantity_differs() {
        let make_body = |qty: &str| {
            let v = serde_json::json!({
                "client_order_id": "cid-qty-test",
                "instrument_id": "7203.TSE",
                "order_side": "BUY",
                "order_type": "MARKET",
                "quantity": qty,
                "time_in_force": "DAY",
                "post_only": false,
                "reduce_only": false,
                "tags": []
            })
            .to_string();
            serde_json::from_str::<SubmitOrderBody>(&v).unwrap()
        };
        let key1 = compute_request_key(&make_body("100"), None);
        let key2 = compute_request_key(&make_body("200"), None);
        assert_ne!(
            key1, key2,
            "different quantity must yield different request_key"
        );
    }

    // ── T0.6 Order Guard tests ────────────────────────────────────────────────

    /// Helper: build an OrderApiState with no engine (engine_rx = None).
    fn no_engine_state(guard_config: OrderGuardConfig) -> Arc<OrderApiState> {
        let (_engine_tx, engine_rx) = watch::channel(None::<Arc<EngineConnection>>);
        let session = Arc::new(Mutex::new(OrderSessionState::new()));
        let is_replay = Arc::new(AtomicBool::new(false));
        Arc::new(OrderApiState::new(session, engine_rx, is_replay).with_guard_config(guard_config))
    }

    /// `enabled: false` (default) → 503 ORDER_GUARD_NOT_CONFIGURED
    #[tokio::test]
    async fn test_order_guard_config_not_set_returns_503() {
        // Default state has guard_config.enabled = false.
        let (_engine_tx, engine_rx) = watch::channel(None::<Arc<EngineConnection>>);
        let session = Arc::new(Mutex::new(OrderSessionState::new()));
        let is_replay = Arc::new(AtomicBool::new(false));
        let state = Arc::new(OrderApiState::new(session, engine_rx, is_replay));
        // Note: no .with_guard_config() call → enabled = false

        let port = spawn_test_http_server(Arc::clone(&state)).await;
        tokio::time::sleep(Duration::from_millis(10)).await;

        let body = default_submit_body("guard-503-cid-00001");
        let (status, resp_body) = http_post(port, "/api/order/submit", &body).await;

        assert_eq!(
            status, 503,
            "guard not configured should return 503; body={resp_body}"
        );
        let json: serde_json::Value = serde_json::from_str(&resp_body).unwrap();
        assert_eq!(
            json["reason_code"].as_str(),
            Some("ORDER_GUARD_NOT_CONFIGURED")
        );
    }

    /// qty > max_qty_per_order → 400 QTY_LIMIT_EXCEEDED
    #[tokio::test]
    async fn test_order_guard_max_qty_exceeded_returns_400() {
        let guard = OrderGuardConfig {
            enabled: true,
            max_qty_per_order: Some(500),
            max_yen_per_order: None,
            rate_limit_window_secs: 3,
            rate_limit_max_hits: 100, // high limit so rate limit doesn't fire
        };
        let state = no_engine_state(guard);

        let port = spawn_test_http_server(Arc::clone(&state)).await;
        tokio::time::sleep(Duration::from_millis(10)).await;

        // quantity 501 > max 500 → 400
        let body = serde_json::json!({
            "client_order_id": "guard-qty-cid-00001",
            "instrument_id": "7203.TSE",
            "order_side": "BUY",
            "order_type": "MARKET",
            "quantity": "501",
            "price": null,
            "trigger_price": null,
            "trigger_type": null,
            "time_in_force": "DAY",
            "expire_time": null,
            "post_only": false,
            "reduce_only": false,
            "tags": []
        })
        .to_string();

        let (status, resp_body) = http_post(port, "/api/order/submit", &body).await;
        assert_eq!(
            status, 400,
            "qty exceeded should return 400; body={resp_body}"
        );
        let json: serde_json::Value = serde_json::from_str(&resp_body).unwrap();
        assert_eq!(json["reason_code"].as_str(), Some("QTY_LIMIT_EXCEEDED"));
    }

    /// qty == max_qty_per_order → NOT rejected (boundary: equal is allowed)
    #[tokio::test]
    async fn test_order_guard_max_qty_at_limit_is_allowed() {
        let guard = OrderGuardConfig {
            enabled: true,
            max_qty_per_order: Some(500),
            max_yen_per_order: None,
            rate_limit_window_secs: 3,
            rate_limit_max_hits: 100,
        };
        let state = no_engine_state(guard);

        let raw = serde_json::json!({
            "client_order_id": "guard-qty-boundary-cid-00001",
            "instrument_id": "7203.TSE",
            "order_side": "BUY",
            "order_type": "MARKET",
            "quantity": "500",
            "time_in_force": "DAY",
            "post_only": false,
            "reduce_only": false,
            "tags": []
        })
        .to_string();
        // Validate passes, and guard qty check passes (500 == 500, not exceeded)
        // We can't go to engine submission without a real engine, so just test
        // the guard logic directly via submit_order() → will stop at "engine not connected" (502)
        let resp = submit_order(&raw, &state).await;
        // 502 means the guard passed and we hit the engine-not-connected check
        assert_eq!(
            resp.status, 502,
            "qty at limit should reach engine check (502), not qty guard (400)"
        );
    }

    /// price * qty > max_yen_per_order for LIMIT order → 400 YEN_LIMIT_EXCEEDED
    #[tokio::test]
    async fn test_order_guard_max_yen_exceeded_returns_400() {
        let guard = OrderGuardConfig {
            enabled: true,
            max_qty_per_order: None,
            max_yen_per_order: Some(1_000_000), // 1M yen
            rate_limit_window_secs: 3,
            rate_limit_max_hits: 100,
        };
        let state = no_engine_state(guard);

        let port = spawn_test_http_server(Arc::clone(&state)).await;
        tokio::time::sleep(Duration::from_millis(10)).await;

        // price=2000 * qty=600 = 1_200_000 > 1_000_000
        let body = limit_submit_body("guard-yen-cid-00001", "600", "2000");
        let (status, resp_body) = http_post(port, "/api/order/submit", &body).await;
        assert_eq!(
            status, 400,
            "yen exceeded should return 400; body={resp_body}"
        );
        let json: serde_json::Value = serde_json::from_str(&resp_body).unwrap();
        assert_eq!(json["reason_code"].as_str(), Some("YEN_LIMIT_EXCEEDED"));
    }

    /// Yen limit does NOT apply to MARKET orders (no price).
    #[tokio::test]
    async fn test_order_guard_yen_limit_not_applied_to_market_orders() {
        let guard = OrderGuardConfig {
            enabled: true,
            max_qty_per_order: None,
            max_yen_per_order: Some(100), // tiny limit
            rate_limit_window_secs: 3,
            rate_limit_max_hits: 100,
        };
        let state = no_engine_state(guard);

        let raw = serde_json::json!({
            "client_order_id": "guard-yen-market-cid-00001",
            "instrument_id": "7203.TSE",
            "order_side": "BUY",
            "order_type": "MARKET",
            "quantity": "999999",
            "time_in_force": "DAY",
            "post_only": false,
            "reduce_only": false,
            "tags": []
        })
        .to_string();

        let resp = submit_order(&raw, &state).await;
        // Should hit engine-not-connected (502), not yen limit (400)
        assert_eq!(
            resp.status, 502,
            "yen limit should not apply to MARKET orders; got {}",
            resp.status
        );
    }

    /// Rate limit: up to max_hits are allowed within the window.
    #[tokio::test]
    async fn test_rate_limit_allows_up_to_max_hits() {
        // max_hits=3, window=60s → first 3 requests should pass the rate limit guard
        let guard = OrderGuardConfig {
            enabled: true,
            max_qty_per_order: None,
            max_yen_per_order: None,
            rate_limit_window_secs: 60,
            rate_limit_max_hits: 3,
        };
        let state = no_engine_state(guard);

        // All 3 requests should NOT be rate-limited (they stop at engine-not-connected = 502)
        for i in 0..3u32 {
            let raw = serde_json::json!({
                "client_order_id": format!("rl-allow-cid-{i:05}"),
                "instrument_id": "7203.TSE",
                "order_side": "BUY",
                "order_type": "MARKET",
                "quantity": "100",
                "time_in_force": "DAY",
                "post_only": false,
                "reduce_only": false,
                "tags": []
            })
            .to_string();
            let resp = submit_order(&raw, &state).await;
            assert_eq!(
                resp.status, 502,
                "request {i} should pass rate limit (502 = engine not connected), got {}",
                resp.status
            );
        }
    }

    /// Rate limit: the (N+1)-th request within the window is rejected with 429.
    #[tokio::test]
    async fn test_rate_limit_rejects_on_n_plus_1() {
        let guard = OrderGuardConfig {
            enabled: true,
            max_qty_per_order: None,
            max_yen_per_order: None,
            rate_limit_window_secs: 60,
            rate_limit_max_hits: 2,
        };
        let state = no_engine_state(guard);

        // Note: all requests share the SAME key (instrument_id + order_side + quantity + price)
        let make_raw = |i: u32| {
            serde_json::json!({
                "client_order_id": format!("rl-reject-cid-{i:05}"),
                "instrument_id": "7203.TSE",
                "order_side": "BUY",
                "order_type": "MARKET",
                "quantity": "100",
                "time_in_force": "DAY",
                "post_only": false,
                "reduce_only": false,
                "tags": []
            })
            .to_string()
        };

        // First 2 pass
        for i in 0..2u32 {
            let resp = submit_order(&make_raw(i), &state).await;
            assert_eq!(
                resp.status, 502,
                "request {i} should pass rate limit, got {}",
                resp.status
            );
        }

        // 3rd (N+1) is rate limited
        let resp = submit_order(&make_raw(2), &state).await;
        assert_eq!(
            resp.status, 429,
            "3rd request should be rate limited (429), got {}",
            resp.status
        );
        assert!(resp.body.contains("RATE_LIMITED"));
    }

    /// Rate limit resets after the window expires.
    #[tokio::test]
    async fn test_rate_limit_resets_after_window() {
        tokio::time::pause();

        let guard = OrderGuardConfig {
            enabled: true,
            max_qty_per_order: None,
            max_yen_per_order: None,
            rate_limit_window_secs: 3,
            rate_limit_max_hits: 2,
        };
        let state = no_engine_state(guard);

        let make_raw = |i: u32| {
            serde_json::json!({
                "client_order_id": format!("rl-reset-cid-{i:05}"),
                "instrument_id": "7203.TSE",
                "order_side": "BUY",
                "order_type": "MARKET",
                "quantity": "100",
                "time_in_force": "DAY",
                "post_only": false,
                "reduce_only": false,
                "tags": []
            })
            .to_string()
        };

        // Fill the window to max (2 hits)
        for i in 0..2u32 {
            let resp = submit_order(&make_raw(i), &state).await;
            assert_eq!(
                resp.status, 502,
                "request {i} should pass, got {}",
                resp.status
            );
        }

        // 3rd → rate limited
        let resp = submit_order(&make_raw(2), &state).await;
        assert_eq!(resp.status, 429, "should be rate limited");

        // Advance time past the window (3 s + 1 ms)
        tokio::time::advance(Duration::from_millis(3001)).await;

        // Now the window is cleared → next request passes
        let resp = submit_order(&make_raw(3), &state).await;
        assert_eq!(
            resp.status, 502,
            "after window reset, request should pass (502), got {}",
            resp.status
        );
    }

    // ── T1.3 / T1.6 Phase O1 tests ───────────────────────────────────────────

    /// cancel-all without body → 400 CONFIRM_REQUIRED
    #[tokio::test]
    async fn test_cancel_all_confirm_required() {
        let (_engine_tx, engine_rx) = watch::channel(None::<Arc<EngineConnection>>);
        let session = Arc::new(Mutex::new(OrderSessionState::new()));
        let is_replay = Arc::new(AtomicBool::new(false));
        let state = Arc::new(
            OrderApiState::new(session, engine_rx, is_replay)
                .with_guard_config(OrderGuardConfig::enabled_no_limits()),
        );

        let resp = cancel_all_orders("", &state).await;
        assert_eq!(resp.status, 400, "missing body should return 400");
        assert!(
            resp.body.contains("CONFIRM_REQUIRED"),
            "body: {}",
            resp.body
        );
    }

    /// cancel-all with `confirm: false` → 400 CONFIRM_REQUIRED
    #[tokio::test]
    async fn test_cancel_all_confirm_false_rejected() {
        let (_engine_tx, engine_rx) = watch::channel(None::<Arc<EngineConnection>>);
        let session = Arc::new(Mutex::new(OrderSessionState::new()));
        let is_replay = Arc::new(AtomicBool::new(false));
        let state = Arc::new(
            OrderApiState::new(session, engine_rx, is_replay)
                .with_guard_config(OrderGuardConfig::enabled_no_limits()),
        );

        let body = r#"{"confirm": false}"#;
        let resp = cancel_all_orders(body, &state).await;
        assert_eq!(resp.status, 400, "confirm:false should return 400");
        assert!(
            resp.body.contains("CONFIRM_REQUIRED"),
            "body: {}",
            resp.body
        );
    }

    /// cancel-all with `confirm: "true"` (string, not bool) → 400 CONFIRM_REQUIRED
    #[tokio::test]
    async fn test_cancel_all_confirm_string_rejected() {
        let (_engine_tx, engine_rx) = watch::channel(None::<Arc<EngineConnection>>);
        let session = Arc::new(Mutex::new(OrderSessionState::new()));
        let is_replay = Arc::new(AtomicBool::new(false));
        let state = Arc::new(
            OrderApiState::new(session, engine_rx, is_replay)
                .with_guard_config(OrderGuardConfig::enabled_no_limits()),
        );

        let body = r#"{"confirm": "true"}"#;
        let resp = cancel_all_orders(body, &state).await;
        assert_eq!(
            resp.status, 400,
            "confirm:\"true\" (string) should return 400"
        );
        assert!(
            resp.body.contains("CONFIRM_REQUIRED"),
            "body: {}",
            resp.body
        );
    }

    /// cancel-all with `confirm: true` (bool) and engine connected → 202 Accepted
    #[tokio::test]
    async fn test_cancel_all_confirm_true_accepted() {
        let (ws_listener, ws_addr) = bind_ws_loopback().await;
        // Spawn an engine that accepts any command and stays alive
        tokio::spawn(async move {
            let (tcp, _) = ws_listener.accept().await.unwrap();
            let mut ws = accept_async(tcp).await.unwrap();
            let _ = ws.next().await; // consume Hello
            ws_send_ready(&mut ws).await;
            // Hold open — consume any commands but don't reply
            while let Some(Ok(_)) = ws.next().await {}
        });

        let conn = connect_engine(ws_addr).await;
        let (engine_tx, engine_rx) = watch::channel(Some(conn));
        let session = Arc::new(Mutex::new(OrderSessionState::new()));
        let is_replay = Arc::new(AtomicBool::new(false));
        let state = Arc::new(
            OrderApiState::new(session, engine_rx, is_replay)
                .with_guard_config(OrderGuardConfig::enabled_no_limits()),
        );

        let body = r#"{"confirm": true}"#;
        let resp = cancel_all_orders(body, &state).await;
        assert_eq!(
            resp.status, 202,
            "confirm:true should return 202; body={}",
            resp.body
        );

        drop(engine_tx);
    }

    /// cancel with unknown client_order_id → 404 ORDER_NOT_FOUND
    #[tokio::test]
    async fn test_cancel_with_unknown_venue_order_id_returns_404() {
        let (_engine_tx, engine_rx) = watch::channel(None::<Arc<EngineConnection>>);
        let session = Arc::new(Mutex::new(OrderSessionState::new()));
        let is_replay = Arc::new(AtomicBool::new(false));
        let state = Arc::new(
            OrderApiState::new(session, engine_rx, is_replay)
                .with_guard_config(OrderGuardConfig::enabled_no_limits()),
        );

        let body = r#"{"client_order_id": "nonexistent-cid-00001"}"#;
        let resp = cancel_order(body, &state).await;
        assert_eq!(
            resp.status, 404,
            "unknown cid should return 404; body={}",
            resp.body
        );
        assert!(resp.body.contains("ORDER_NOT_FOUND"), "body: {}", resp.body);
    }

    /// Different (instrument_id, order_side, quantity, price) keys are independent.
    #[tokio::test]
    async fn test_rate_limit_different_key_independent_counter() {
        let guard = OrderGuardConfig {
            enabled: true,
            max_qty_per_order: None,
            max_yen_per_order: None,
            rate_limit_window_secs: 60,
            rate_limit_max_hits: 1, // very low limit: 2nd hit on same key → 429
        };
        let state = no_engine_state(guard);

        // First request: key = (7203.TSE, BUY, 100, None)
        let raw_a = serde_json::json!({
            "client_order_id": "rl-key-cid-a-001",
            "instrument_id": "7203.TSE",
            "order_side": "BUY",
            "order_type": "MARKET",
            "quantity": "100",
            "time_in_force": "DAY",
            "post_only": false,
            "reduce_only": false,
            "tags": []
        })
        .to_string();

        let resp_a1 = submit_order(&raw_a, &state).await;
        assert_eq!(resp_a1.status, 502, "first request on key A should pass");

        // Second request on same key A → 429
        let resp_a2 = submit_order(&raw_a, &state).await;
        assert_eq!(
            resp_a2.status, 429,
            "second request on key A should be rate limited"
        );

        // First request on a different key B (different quantity) → should pass (independent counter)
        let raw_b = serde_json::json!({
            "client_order_id": "rl-key-cid-b-001",
            "instrument_id": "7203.TSE",
            "order_side": "BUY",
            "order_type": "MARKET",
            "quantity": "200",           // different quantity = different key
            "time_in_force": "DAY",
            "post_only": false,
            "reduce_only": false,
            "tags": []
        })
        .to_string();

        let resp_b1 = submit_order(&raw_b, &state).await;
        assert_eq!(
            resp_b1.status, 502,
            "first request on key B should pass independently (got {})",
            resp_b1.status
        );
    }

    /// T3.5 Phase O3 — 余力不足時に HTTP 403 が返ること。
    ///
    /// Python が `OrderRejected{reason_code="INSUFFICIENT_FUNDS"}` を返した場合、
    /// Rust 側で `reason_code_to_status("INSUFFICIENT_FUNDS")` → 403 に写像される。
    #[tokio::test]
    async fn test_insufficient_funds_returns_403() {
        let (ws_listener, ws_addr) = bind_ws_loopback().await;
        let cid = "cid-insuf-001";
        spawn_mock_engine_rejects(ws_listener, cid, "INSUFFICIENT_FUNDS");

        let conn = connect_engine(ws_addr).await;
        let (engine_tx, engine_rx) = watch::channel(Some(conn));
        let session = Arc::new(Mutex::new(OrderSessionState::new()));
        let is_replay = Arc::new(AtomicBool::new(false));
        let state = Arc::new(
            OrderApiState::new(session, engine_rx, is_replay)
                .with_guard_config(OrderGuardConfig::enabled_no_limits())
                .with_timeout(Duration::from_secs(5)),
        );

        let port = spawn_test_http_server(Arc::clone(&state)).await;
        tokio::time::sleep(Duration::from_millis(10)).await;

        let body = default_submit_body(cid);
        let (status, resp_body) = http_post(port, "/api/order/submit", &body).await;

        assert_eq!(
            status, 403,
            "INSUFFICIENT_FUNDS should map to HTTP 403, got {}; body={}",
            status, resp_body
        );
        let json: serde_json::Value = serde_json::from_str(&resp_body).unwrap();
        assert_eq!(
            json["reason_code"].as_str(),
            Some("INSUFFICIENT_FUNDS"),
            "reason_code must be INSUFFICIENT_FUNDS"
        );

        drop(engine_tx);
    }
}
