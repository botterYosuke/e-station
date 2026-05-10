/// gRPC transport layer for EngineConnection (G2).
///
/// Provides `start_grpc_session()` which performs the `HelloRequest`/`ReadyResponse`
/// handshake and starts the background converter + reader tasks.  The returned
/// raw channel parts are assembled into an `EngineConnection` by the caller.
use crate::{SCHEMA_MINOR, dto, error::EngineClientError};
use serde_json::Value;
use std::{sync::Arc, time::Duration};
use tokio::sync::{Notify, broadcast, mpsc};
use tokio_stream::wrappers::ReceiverStream;
use tonic::Request;

// ── Proto stubs ───────────────────────────────────────────────────────────────

pub(crate) mod engine {
    tonic::include_proto!("engine");
}

// ── Constants ─────────────────────────────────────────────────────────────────

const BROADCAST_CAPACITY: usize = 512;
const COMMAND_BUFFER: usize = 256;
const HANDSHAKE_TIMEOUT: Duration = Duration::from_secs(10);
const CLIENT_VERSION: &str = env!("CARGO_PKG_VERSION");

// ── Panic-safe RAII guard ─────────────────────────────────────────────────────

/// Calls `notify_waiters()` on drop so that `wait_closed()` resolves even if
/// the reader task panics.
struct NotifyOnDrop(Arc<Notify>);

impl Drop for NotifyOnDrop {
    fn drop(&mut self) {
        self.0.notify_waiters();
    }
}

// ── Public entry point ────────────────────────────────────────────────────────

/// Perform the gRPC handshake and start background IO tasks.
///
/// `target` must use the `http://` scheme (e.g., `http://127.0.0.1:50051`).
/// `grpc://` URLs must be converted by the caller before passing here.
///
/// Returns the raw channel parts used to construct an `EngineConnection`:
/// `(cmd_tx, events_tx, closed, capabilities)`.
pub(crate) async fn start_grpc_session(
    target: &str,
    token: &str,
    mode: dto::AppMode,
) -> Result<
    (
        mpsc::Sender<dto::Command>,
        broadcast::Sender<dto::EngineEvent>,
        Arc<Notify>,
        Arc<Value>,
    ),
    EngineClientError,
> {
    start_grpc_session_with_schema(target, token, mode, crate::SCHEMA_MAJOR).await
}

/// Perform the gRPC handshake with an explicit `schema_major` override.
///
/// `target` must use the `http://` scheme (e.g., `http://127.0.0.1:50051`).
/// `grpc://` URLs must be converted by the caller before passing here.
///
/// # Note
/// This function is primarily intended for testing schema rejection.
/// Production code should use [`start_grpc_session`] instead.
/// The `connection.rs` public re-export is gated on `#[cfg(feature = "testing")]`.
pub(crate) async fn start_grpc_session_with_schema(
    target: &str,
    token: &str,
    mode: dto::AppMode,
    schema_major: u32,
) -> Result<
    (
        mpsc::Sender<dto::Command>,
        broadcast::Sender<dto::EngineEvent>,
        Arc<Notify>,
        Arc<Value>,
    ),
    EngineClientError,
> {
    use engine::data_engine_client::DataEngineClient;

    // 1. Connect (with timeout).
    let endpoint = tonic::transport::Endpoint::from_shared(target.to_owned())
        .map_err(|e| EngineClientError::GrpcTransport(format!("invalid gRPC endpoint: {e}")))?;

    let channel = tokio::time::timeout(HANDSHAKE_TIMEOUT, endpoint.connect())
        .await
        .map_err(|_| EngineClientError::HandshakeTimeout)?
        .map_err(|e| {
            let msg = e.to_string();
            if msg.contains("Connection refused") || msg.contains("connect error") {
                EngineClientError::ConnectionRefused
            } else {
                EngineClientError::GrpcTransport(msg)
            }
        })?;

    let mut client = DataEngineClient::new(channel);

    // 2. Build channels.
    let (proto_tx, proto_rx) = mpsc::channel::<engine::Command>(COMMAND_BUFFER);
    let (cmd_tx, cmd_rx) = mpsc::channel::<dto::Command>(COMMAND_BUFFER);
    let (events_tx, _) = broadcast::channel::<dto::EngineEvent>(BROADCAST_CAPACITY);

    // 3. Pre-send HelloRequest as the very first proto command.
    proto_tx
        .send(engine::Command {
            payload: Some(engine::command::Payload::Hello(engine::HelloRequest {
                schema_major,
                schema_minor: SCHEMA_MINOR,
                client_version: CLIENT_VERSION.to_string(),
                token: token.to_string(),
                mode: dto_mode_to_proto(mode),
            })),
        })
        .await
        .map_err(|_| {
            EngineClientError::GrpcTransport("proto cmd channel closed immediately".to_string())
        })?;

    // 4. Open the bidirectional stream.
    let request_stream = ReceiverStream::new(proto_rx);
    let response = tokio::time::timeout(
        HANDSHAKE_TIMEOUT,
        client.session(Request::new(request_stream)),
    )
    .await
    .map_err(|_| EngineClientError::HandshakeTimeout)?
    .map_err(|s| grpc_status_to_error(&s))?;

    let mut event_stream = response.into_inner();

    // 5. Wait for the first event (must be ReadyResponse).
    let first = tokio::time::timeout(HANDSHAKE_TIMEOUT, event_stream.message())
        .await
        .map_err(|_| EngineClientError::HandshakeTimeout)?
        .map_err(|s| grpc_status_to_error(&s))?
        .ok_or_else(|| {
            EngineClientError::GrpcTransport("server closed stream before Ready".to_string())
        })?;

    let capabilities = match &first.payload {
        Some(engine::event::Payload::Ready(r)) => {
            // H11: client-side schema_major cross-check. The server already
            // validates the Hello.schema_major we sent, but we also verify the
            // schema_major echoed back in ReadyResponse to detect proxy/mismatch.
            if r.schema_major != schema_major {
                return Err(EngineClientError::SchemaMismatch {
                    local_major: schema_major,
                    local_minor: SCHEMA_MINOR,
                    remote_major: r.schema_major,
                    remote_minor: r.schema_minor,
                });
            }
            // C2-Rust: warn on schema_minor mismatch — connection continues.
            if r.schema_minor != SCHEMA_MINOR {
                log::warn!(
                    target: "engine_client::grpc_transport",
                    "schema_minor mismatch: local={} remote={} — continuing",
                    SCHEMA_MINOR, r.schema_minor
                );
            }
            capabilities_to_json(r.capabilities.clone())
        }
        _ => {
            return Err(EngineClientError::GrpcTransport(
                "first gRPC event was not ReadyResponse".to_string(),
            ));
        }
    };

    // Broadcast the Ready event to any early subscribers.
    if let Some(ev) = proto_event_to_dto(first)
        && events_tx.send(ev).is_err()
    {
        log::debug!(
            target: "engine_client::grpc_transport",
            "Ready event dropped: no active subscribers at handshake time"
        );
    }

    let closed = Arc::new(Notify::new());

    // 6. Spawn: dto::Command → proto::Command converter.
    {
        let proto_tx = proto_tx;
        let mut cmd_rx = cmd_rx;
        let closed_arc_conv = Arc::clone(&closed);
        tokio::spawn(async move {
            // H10: NotifyOnDrop ensures closed.notify_waiters() is called even
            // if the converter task panics, preventing wait_closed() from hanging.
            let _guard = NotifyOnDrop(Arc::clone(&closed_arc_conv));
            while let Some(cmd) = cmd_rx.recv().await {
                if let Some(proto_cmd) = dto_cmd_to_proto(cmd)
                    && proto_tx.send(proto_cmd).await.is_err()
                {
                    let pending = cmd_rx.len();
                    log::warn!(
                        target: "engine_client::grpc_transport",
                        "gRPC proto_tx closed — converter task exiting ({pending} commands discarded)"
                    );
                    break;
                }
            }
            // Dropping proto_tx closes the request stream → server sees EOF.
        });
    }

    // 7. Spawn: proto::Event reader → dto::EngineEvent broadcast.
    {
        let events_tx = events_tx.clone();
        let closed_arc = Arc::clone(&closed);
        tokio::spawn(async move {
            // NotifyOnDrop ensures closed.notify_waiters() is called even if
            // this task panics, preventing wait_closed() from hanging forever.
            let _guard = NotifyOnDrop(Arc::clone(&closed_arc));
            loop {
                match event_stream.message().await {
                    Ok(Some(proto_ev)) => match proto_event_to_dto(proto_ev) {
                        Some(dto_ev) => {
                            if events_tx.send(dto_ev).is_err() {
                                // No subscribers yet (or all dropped) — continue as the WS
                                // transport does; do not exit, subscribers may join later.
                                log::debug!(
                                    target: "engine_client::grpc_transport",
                                    "gRPC event dropped: no active subscribers"
                                );
                            }
                        }
                        None => {
                            log::debug!(
                                target: "engine_client::grpc_transport",
                                "proto_event_to_dto returned None — event skipped"
                            );
                        }
                    },
                    Ok(None) => {
                        log::info!(
                            target: "engine_client::grpc_transport",
                            "gRPC server closed stream (EOF) — reader task exiting"
                        );
                        let _ = events_tx.send(dto::EngineEvent::ConnectionDropped);
                        break;
                    }
                    Err(status) => {
                        log::warn!(
                            target: "engine_client::grpc_transport",
                            "gRPC event stream error: {status}"
                        );
                        let _ = events_tx.send(dto::EngineEvent::ConnectionDropped);
                        break;
                    }
                }
            }
            // _guard drops here → notify_waiters() called.
        });
    }

    Ok((cmd_tx, events_tx, closed, Arc::new(capabilities)))
}

// ── gRPC status → EngineClientError ──────────────────────────────────────────

fn grpc_status_to_error(status: &tonic::Status) -> EngineClientError {
    use tonic::Code;
    match status.code() {
        Code::DeadlineExceeded => EngineClientError::HandshakeTimeout,
        Code::Unavailable | Code::Internal => EngineClientError::ConnectionRefused,
        Code::FailedPrecondition => {
            EngineClientError::GrpcTransport(format!("schema/mode mismatch: {}", status.message()))
        }
        Code::Unauthenticated => {
            EngineClientError::GrpcTransport(format!("auth failed: {}", status.message()))
        }
        Code::ResourceExhausted => {
            log::warn!(
                target: "engine_client::grpc_transport",
                "engine rejected connection: resource exhausted (max connections)"
            );
            EngineClientError::ConnectionRefused
        }
        _ => EngineClientError::GrpcTransport(format!(
            "gRPC {}: {}",
            status.code(),
            status.message()
        )),
    }
}

// ── Capabilities JSON ─────────────────────────────────────────────────────────

fn capabilities_to_json(caps: Option<engine::EngineCapabilities>) -> Value {
    let Some(c) = caps else {
        return Value::Object(serde_json::Map::new());
    };
    serde_json::json!({
        "supported_venues": c.supported_venues,
        "supports_bulk_trades": c.supports_bulk_trades,
        "supports_depth_binary": c.supports_depth_binary,
    })
}

// ── dto::Command → proto::Command ────────────────────────────────────────────

fn dto_cmd_to_proto(cmd: dto::Command) -> Option<engine::Command> {
    use engine::command::Payload;

    let payload = match cmd {
        dto::Command::Hello { .. } => return None, // never enqueued post-handshake
        dto::Command::SetProxy { url } => Payload::SetProxy(engine::SetProxyRequest { url }),
        dto::Command::Subscribe {
            venue,
            ticker,
            stream,
            timeframe,
            market,
        } => Payload::Subscribe(engine::SubscribeRequest {
            venue,
            ticker,
            stream,
            timeframe,
            market,
        }),
        dto::Command::Unsubscribe {
            venue,
            ticker,
            stream,
            timeframe,
            market,
        } => Payload::Unsubscribe(engine::UnsubscribeRequest {
            venue,
            ticker,
            stream,
            timeframe,
            market,
        }),
        dto::Command::FetchKlines {
            request_id,
            venue,
            ticker,
            timeframe,
            limit,
            start_ms,
            end_ms,
            market,
        } => Payload::FetchKlines(engine::FetchKlinesRequest {
            request_id,
            venue,
            ticker,
            timeframe,
            limit,
            start_ms,
            end_ms,
            market,
        }),
        dto::Command::FetchTrades {
            request_id,
            venue,
            ticker,
            market,
            start_ms,
            end_ms,
            data_path,
        } => Payload::FetchTrades(engine::FetchTradesRequest {
            request_id,
            venue,
            ticker,
            market,
            start_ms,
            end_ms,
            data_path,
        }),
        dto::Command::FetchOpenInterest {
            request_id,
            venue,
            ticker,
            timeframe,
            limit,
            start_ms,
            end_ms,
            market,
        } => Payload::FetchOpenInterest(engine::FetchOpenInterestRequest {
            request_id,
            venue,
            ticker,
            timeframe,
            limit,
            start_ms,
            end_ms,
            market,
        }),
        dto::Command::FetchTickerStats {
            request_id,
            venue,
            ticker,
            market,
        } => Payload::FetchTickerStats(engine::FetchTickerStatsRequest {
            request_id,
            venue,
            ticker,
            market,
        }),
        dto::Command::ListTickers {
            request_id,
            venue,
            market,
        } => Payload::ListTickers(engine::ListTickersRequest {
            request_id,
            venue,
            market,
        }),
        dto::Command::GetTickerMetadata {
            request_id,
            venue,
            ticker,
        } => Payload::GetTickerMetadata(engine::GetTickerMetadataRequest {
            request_id,
            venue,
            ticker,
        }),
        dto::Command::RequestDepthSnapshot {
            request_id,
            venue,
            ticker,
            market,
        } => Payload::RequestDepthSnapshot(engine::RequestDepthSnapshotRequest {
            request_id,
            venue,
            ticker,
            market,
        }),
        dto::Command::Ping { request_id } => Payload::Ping(engine::PingRequest { request_id }),
        dto::Command::Shutdown => Payload::Shutdown(engine::ShutdownRequest {}),
        dto::Command::RequestVenueLogin { request_id, venue } => {
            Payload::RequestVenueLogin(engine::RequestVenueLoginRequest { request_id, venue })
        }
        dto::Command::RequestVenueLogout { venue } => {
            Payload::RequestVenueLogout(engine::RequestVenueLogoutRequest { venue })
        }
        dto::Command::SetSecondPassword { request_id, value } => {
            Payload::SetSecondPassword(engine::SetSecondPasswordRequest { request_id, value })
        }
        dto::Command::ForgetSecondPassword => {
            Payload::ForgetSecondPassword(engine::ForgetSecondPasswordRequest {})
        }
        dto::Command::SubmitOrder {
            request_id,
            venue,
            order,
        } => Payload::SubmitOrder(engine::SubmitOrderRequest {
            request_id,
            venue,
            order: Some(engine::SubmitOrderPayload {
                client_order_id: order.client_order_id,
                instrument_id: order.instrument_id,
                order_side: dto_order_side_to_proto(order.order_side),
                order_type: dto_order_type_to_proto(order.order_type),
                quantity: order.quantity,
                price: order.price,
                trigger_price: order.trigger_price,
                trigger_type: order.trigger_type.map(dto_trigger_type_to_proto),
                time_in_force: dto_tif_to_proto(order.time_in_force),
                expire_time_ns: order.expire_time_ns,
                post_only: order.post_only,
                reduce_only: order.reduce_only,
                tags: order.tags,
                request_key: order.request_key,
            }),
        }),
        dto::Command::ModifyOrder {
            request_id,
            venue,
            client_order_id,
            venue_order_id,
            change,
        } => Payload::ModifyOrder(engine::ModifyOrderRequest {
            request_id,
            venue,
            client_order_id,
            venue_order_id,
            change: Some(engine::OrderModifyChange {
                new_quantity: change.new_quantity,
                new_price: change.new_price,
                new_trigger_price: change.new_trigger_price,
                new_expire_time_ns: change.new_expire_time_ns,
            }),
        }),
        dto::Command::CancelOrder {
            request_id,
            venue,
            client_order_id,
            venue_order_id,
        } => Payload::CancelOrder(engine::CancelOrderRequest {
            request_id,
            venue,
            client_order_id,
            venue_order_id,
        }),
        dto::Command::CancelAllOrders {
            request_id,
            venue,
            instrument_id,
            order_side,
        } => Payload::CancelAllOrders(engine::CancelAllOrdersRequest {
            request_id,
            venue,
            instrument_id,
            order_side: order_side.map(dto_order_side_to_proto),
        }),
        dto::Command::GetOrderList {
            request_id,
            venue,
            filter,
        } => Payload::GetOrderList(engine::GetOrderListRequest {
            request_id,
            venue,
            filter: Some(engine::OrderListFilter {
                status: filter.status,
                instrument_id: filter.instrument_id,
                date: filter.date,
            }),
        }),
        dto::Command::GetBuyingPower { request_id, venue } => {
            Payload::GetBuyingPower(engine::GetBuyingPowerRequest { request_id, venue })
        }
        dto::Command::GetPositions { request_id, venue } => {
            Payload::GetPositions(engine::GetPositionsRequest { request_id, venue })
        }
        dto::Command::StartEngine {
            request_id,
            engine: engine_kind,
            strategy_id,
            config,
        } => {
            let strategy_init_kwargs = match config.strategy_init_kwargs {
                Some(m) => match serde_json::to_string(&m) {
                    Ok(s) => Some(s),
                    Err(e) => {
                        log::error!(
                            target: "engine_client::grpc_transport",
                            "StartEngine: failed to serialize strategy_init_kwargs — command dropped: {e}"
                        );
                        return None;
                    }
                },
                None => None,
            };
            Payload::StartEngine(engine::StartEngineRequest {
                request_id,
                engine: dto_engine_kind_to_proto(engine_kind),
                strategy_id,
                config: Some(engine::EngineStartConfig {
                    instrument_id: config.instrument_id,
                    instrument_ids: config.instrument_ids.unwrap_or_default(),
                    start_date: config.start_date,
                    end_date: config.end_date,
                    initial_cash: config.initial_cash,
                    granularity: config.granularity.map(dto_granularity_to_proto),
                    strategy_file: config.strategy_file,
                    strategy_init_kwargs,
                    max_qty: config.max_qty,
                    max_notional_jpy: config.max_notional_jpy,
                }),
            })
        }
        dto::Command::StopEngine {
            request_id,
            strategy_id,
        } => Payload::StopEngine(engine::StopEngineRequest {
            request_id,
            strategy_id,
        }),
        dto::Command::LoadReplayData {
            request_id,
            instrument_id,
            start_date,
            end_date,
            granularity,
            instrument_ids,
        } => Payload::LoadReplayData(engine::LoadReplayDataRequest {
            request_id,
            instrument_id,
            start_date,
            end_date,
            granularity: dto_granularity_to_proto(granularity),
            instrument_ids: instrument_ids.unwrap_or_default(),
        }),
        dto::Command::SetReplaySpeed {
            request_id,
            multiplier,
        } => Payload::SetReplaySpeed(engine::SetReplaySpeedRequest {
            request_id,
            multiplier,
        }),
        dto::Command::StopReplay { request_id } => {
            Payload::StopReplay(engine::StopReplayRequest { request_id })
        }
        dto::Command::ForceStopReplay { request_id } => {
            Payload::ForceStopReplay(engine::ForceStopReplayRequest { request_id })
        }
        dto::Command::PauseReplay { request_id } => {
            Payload::PauseReplay(engine::PauseReplayRequest { request_id })
        }
        dto::Command::ResumeReplay { request_id } => {
            Payload::ResumeReplay(engine::ResumeReplayRequest { request_id })
        }
        dto::Command::StepReplay { request_id } => {
            Payload::StepReplay(engine::StepReplayRequest { request_id })
        }
        dto::Command::StepBackward { request_id } => {
            Payload::StepBackward(engine::StepBackwardRequest { request_id })
        }
        dto::Command::LoadStrategyScenario { request_id, path } => {
            Payload::LoadStrategyScenario(engine::LoadStrategyScenarioRequest { request_id, path })
        }
        dto::Command::SaveStrategyScenario {
            request_id,
            path,
            scenario,
            save_as,
            loaded_path,
        } => {
            let scenario_json = match serde_json::to_string(&scenario) {
                Ok(s) => s,
                Err(e) => {
                    log::error!(
                        target: "engine_client::grpc_transport",
                        "SaveStrategyScenario: failed to serialize scenario — command dropped: {e}"
                    );
                    return None;
                }
            };
            Payload::SaveStrategyScenario(engine::SaveStrategyScenarioRequest {
                request_id,
                path,
                scenario: scenario_json,
                save_as,
                loaded_path,
            })
        }
        dto::Command::LoadLiveStrategyScenario {
            request_id,
            strategy_path,
        } => Payload::LoadLiveStrategyScenario(engine::LoadLiveStrategyScenarioRequest {
            request_id,
            strategy_path,
        }),
    };

    Some(engine::Command {
        payload: Some(payload),
    })
}

// ── proto::Event → dto::EngineEvent ──────────────────────────────────────────

fn proto_event_to_dto(event: engine::Event) -> Option<dto::EngineEvent> {
    use engine::event::Payload;

    match event.payload? {
        Payload::Ready(r) => Some(dto::EngineEvent::Ready {
            schema_major: r.schema_major,
            schema_minor: r.schema_minor,
            engine_version: r.engine_version,
            engine_session_id: r.engine_session_id,
            capabilities: capabilities_to_json(r.capabilities),
        }),
        Payload::EngineError(e) => Some(dto::EngineEvent::EngineError {
            code: e.code,
            message: e.message,
            strategy_id: e.strategy_id,
        }),
        Payload::Connected(c) => Some(dto::EngineEvent::Connected {
            venue: c.venue,
            ticker: c.ticker,
            stream: c.stream,
            market: c.market,
        }),
        Payload::Disconnected(d) => Some(dto::EngineEvent::Disconnected {
            venue: d.venue,
            ticker: d.ticker,
            stream: d.stream,
            market: d.market,
            reason: d.reason,
        }),
        Payload::Trades(t) => Some(dto::EngineEvent::Trades {
            venue: t.venue,
            ticker: t.ticker,
            market: t.market,
            stream_session_id: t.stream_session_id,
            trades: t.trades.into_iter().map(proto_trade_to_dto).collect(),
        }),
        Payload::TradesFetched(tf) => Some(dto::EngineEvent::TradesFetched {
            request_id: tf.request_id,
            venue: tf.venue,
            ticker: tf.ticker,
            trades: tf.trades.into_iter().map(proto_trade_to_dto).collect(),
            is_last: tf.is_last,
        }),
        Payload::KlineUpdate(ku) => {
            let Some(kline_proto) = ku.kline else {
                log::warn!(
                    target: "engine_client::grpc_transport",
                    "KlineUpdate missing kline field — dropped (venue={} ticker={})",
                    ku.venue, ku.ticker
                );
                return None;
            };
            let kline = proto_kline_to_dto(kline_proto);
            Some(dto::EngineEvent::KlineUpdate {
                venue: ku.venue,
                ticker: ku.ticker,
                market: ku.market,
                timeframe: ku.timeframe,
                kline,
            })
        }
        Payload::Klines(kl) => Some(dto::EngineEvent::Klines {
            request_id: kl.request_id,
            venue: kl.venue,
            ticker: kl.ticker,
            timeframe: kl.timeframe,
            klines: kl.klines.into_iter().map(proto_kline_to_dto).collect(),
        }),
        Payload::DepthSnapshot(ds) => Some(dto::EngineEvent::DepthSnapshot {
            request_id: ds.request_id,
            venue: ds.venue,
            ticker: ds.ticker,
            market: ds.market,
            stream_session_id: ds.stream_session_id,
            sequence_id: ds.sequence_id,
            bids: ds.bids.into_iter().map(proto_depth_to_dto).collect(),
            asks: ds.asks.into_iter().map(proto_depth_to_dto).collect(),
            checksum: ds.checksum,
        }),
        Payload::DepthDiff(dd) => Some(dto::EngineEvent::DepthDiff {
            venue: dd.venue,
            ticker: dd.ticker,
            market: dd.market,
            stream_session_id: dd.stream_session_id,
            sequence_id: dd.sequence_id,
            prev_sequence_id: dd.prev_sequence_id,
            bids: dd.bids.into_iter().map(proto_depth_to_dto).collect(),
            asks: dd.asks.into_iter().map(proto_depth_to_dto).collect(),
        }),
        Payload::DepthGap(dg) => Some(dto::EngineEvent::DepthGap {
            venue: dg.venue,
            ticker: dg.ticker,
            market: dg.market,
            stream_session_id: dg.stream_session_id,
        }),
        Payload::OpenInterest(oi) => Some(dto::EngineEvent::OpenInterest {
            request_id: oi.request_id,
            venue: oi.venue,
            ticker: oi.ticker,
            data: oi
                .data
                .into_iter()
                .map(|p| dto::OiPoint {
                    ts_ms: p.ts_ms,
                    open_interest: p.open_interest,
                })
                .collect(),
        }),
        Payload::TickerInfo(ti) => Some(dto::EngineEvent::TickerInfo {
            request_id: ti.request_id,
            venue: ti.venue,
            tickers: ti
                .tickers
                .into_iter()
                .filter_map(proto_ticker_entry_to_dto)
                .collect(),
        }),
        Payload::TickerStats(ts) => {
            let ticker = ts.ticker;
            Some(dto::EngineEvent::TickerStats {
                request_id: ts.request_id,
                venue: ts.venue,
                ticker: ticker.clone(),
                stats: serde_json::from_str(&ts.stats).unwrap_or_else(|e| {
                    log::warn!(
                        target: "engine_client::grpc_transport",
                        "TickerStats.stats JSON parse failed for ticker={}: {e}",
                        ticker
                    );
                    Value::Object(serde_json::Map::new())
                }),
            })
        }
        Payload::Pong(p) => Some(dto::EngineEvent::Pong {
            request_id: p.request_id,
        }),
        Payload::Error(e) => Some(dto::EngineEvent::Error {
            request_id: e.request_id,
            code: e.code,
            message: e.message,
        }),
        Payload::VenueReady(vr) => Some(dto::EngineEvent::VenueReady {
            venue: vr.venue,
            request_id: vr.request_id,
        }),
        Payload::VenueError(ve) => Some(dto::EngineEvent::VenueError {
            venue: ve.venue,
            request_id: ve.request_id,
            code: ve.code,
            message: ve.message,
        }),
        Payload::VenueLoginStarted(vls) => Some(dto::EngineEvent::VenueLoginStarted {
            venue: vls.venue,
            request_id: vls.request_id,
        }),
        Payload::VenueLoginCancelled(vlc) => Some(dto::EngineEvent::VenueLoginCancelled {
            venue: vlc.venue,
            request_id: vlc.request_id,
        }),
        Payload::SecondPasswordRequired(spr) => Some(dto::EngineEvent::SecondPasswordRequired {
            request_id: spr.request_id,
        }),
        Payload::OrderSubmitted(os) => Some(dto::EngineEvent::OrderSubmitted {
            client_order_id: os.client_order_id,
            ts_event_ms: os.ts_event_ms,
        }),
        Payload::OrderAccepted(oa) => Some(dto::EngineEvent::OrderAccepted {
            client_order_id: oa.client_order_id,
            venue_order_id: oa.venue_order_id,
            ts_event_ms: oa.ts_event_ms,
        }),
        Payload::OrderRejected(or_) => Some(dto::EngineEvent::OrderRejected {
            client_order_id: or_.client_order_id,
            reason_code: or_.reason_code,
            reason_text: or_.reason_text,
            ts_event_ms: or_.ts_event_ms,
        }),
        Payload::OrderPendingUpdate(opu) => Some(dto::EngineEvent::OrderPendingUpdate {
            client_order_id: opu.client_order_id,
            ts_event_ms: opu.ts_event_ms,
        }),
        Payload::OrderPendingCancel(opc) => Some(dto::EngineEvent::OrderPendingCancel {
            client_order_id: opc.client_order_id,
            ts_event_ms: opc.ts_event_ms,
        }),
        Payload::OrderFilled(of_) => Some(dto::EngineEvent::OrderFilled {
            client_order_id: of_.client_order_id,
            venue_order_id: of_.venue_order_id,
            trade_id: of_.trade_id,
            last_qty: of_.last_qty,
            last_price: of_.last_price,
            cumulative_qty: of_.cumulative_qty,
            leaves_qty: of_.leaves_qty,
            ts_event_ms: of_.ts_event_ms,
        }),
        Payload::OrderCanceled(oc) => Some(dto::EngineEvent::OrderCanceled {
            client_order_id: oc.client_order_id,
            venue_order_id: oc.venue_order_id,
            ts_event_ms: oc.ts_event_ms,
        }),
        Payload::OrderExpired(oe) => Some(dto::EngineEvent::OrderExpired {
            client_order_id: oe.client_order_id,
            venue_order_id: oe.venue_order_id,
            ts_event_ms: oe.ts_event_ms,
        }),
        Payload::OrderListUpdated(olu) => Some(dto::EngineEvent::OrderListUpdated {
            request_id: olu.request_id,
            orders: olu
                .orders
                .into_iter()
                .filter_map(proto_order_record_to_dto)
                .collect(),
        }),
        Payload::EngineStarted(es) => Some(dto::EngineEvent::EngineStarted {
            strategy_id: es.strategy_id,
            account_id: es.account_id,
            ts_event_ms: es.ts_event_ms,
        }),
        Payload::EngineStopped(est) => Some(dto::EngineEvent::EngineStopped {
            strategy_id: est.strategy_id,
            final_equity: est.final_equity,
            ts_event_ms: est.ts_event_ms,
        }),
        Payload::ReplayDataLoaded(rdl) => Some(dto::EngineEvent::ReplayDataLoaded {
            strategy_id: rdl.strategy_id,
            bars_loaded: rdl.bars_loaded,
            trades_loaded: rdl.trades_loaded,
            instrument_id: rdl.instrument_id,
            instrument_ids: if rdl.instrument_ids.is_empty() {
                None
            } else {
                Some(rdl.instrument_ids)
            },
            granularity: rdl.granularity.and_then(proto_optional_granularity_to_dto),
            session_epoch: rdl.session_epoch,
            ts_event_ms: rdl.ts_event_ms,
        }),
        Payload::PositionOpened(po) => Some(dto::EngineEvent::PositionOpened {
            strategy_id: po.strategy_id,
            venue: po.venue,
            instrument_id: po.instrument_id,
            position_id: po.position_id,
            side: po.side,
            opened_qty: po.opened_qty,
            avg_open_price: po.avg_open_price,
            ts_event_ms: po.ts_event_ms,
        }),
        Payload::PositionClosed(pc) => Some(dto::EngineEvent::PositionClosed {
            strategy_id: pc.strategy_id,
            venue: pc.venue,
            instrument_id: pc.instrument_id,
            position_id: pc.position_id,
            realized_pnl: pc.realized_pnl,
            ts_event_ms: pc.ts_event_ms,
        }),
        Payload::ExecutionMarker(em) => Some(dto::EngineEvent::ExecutionMarker {
            strategy_id: em.strategy_id,
            instrument_id: em.instrument_id,
            side: em.side,
            price: em.price,
            qty: em.qty,
            commission: em.commission,
            ts_event_ms: em.ts_event_ms,
        }),
        Payload::StrategySignal(ss) => {
            let signal_kind = proto_signal_kind_to_dto(ss.signal_kind)?;
            Some(dto::EngineEvent::StrategySignal {
                strategy_id: ss.strategy_id,
                instrument_id: ss.instrument_id,
                signal_kind,
                side: ss.side,
                price: ss.price,
                tag: ss.tag,
                note: ss.note,
                ts_event_ms: ss.ts_event_ms,
            })
        }
        Payload::BuyingPowerUpdated(bpu) => Some(dto::EngineEvent::BuyingPowerUpdated {
            request_id: bpu.request_id,
            venue: bpu.venue,
            cash_available: bpu.cash_available,
            cash_shortfall: bpu.cash_shortfall,
            credit_available: bpu.credit_available,
            ts_ms: bpu.ts_ms,
        }),
        Payload::PositionsUpdated(pu) => Some(dto::EngineEvent::PositionsUpdated {
            request_id: pu.request_id,
            venue: pu.venue,
            positions: pu
                .positions
                .into_iter()
                .filter_map(proto_position_record_to_dto)
                .collect(),
            ts_ms: pu.ts_ms,
        }),
        Payload::ReplayBuyingPower(rbp) => Some(dto::EngineEvent::ReplayBuyingPower {
            strategy_id: rbp.strategy_id,
            cash: rbp.cash,
            buying_power: rbp.buying_power,
            equity: rbp.equity,
            ts_event_ms: rbp.ts_event_ms,
        }),
        Payload::DateChangeMarker(dcm) => {
            Some(dto::EngineEvent::DateChangeMarker { date: dcm.date })
        }
        Payload::RestoreSnapshot(rs) => Some(dto::EngineEvent::RestoreSnapshot {
            step_index: rs.step_index,
            ts_event_ms: rs.ts_event_ms,
        }),
        Payload::ReplayHistoryChanged(rhc) => Some(dto::EngineEvent::ReplayHistoryChanged {
            has_history: rhc.has_history,
        }),
        Payload::ReplayStopped(rst) => Some(dto::EngineEvent::ReplayStopped {
            request_id: rst.request_id,
            final_equity: rst.final_equity,
        }),
        Payload::EngineBusy(eb) => Some(dto::EngineEvent::EngineBusy {
            current_state: proto_engine_state_to_dto(eb.current_state),
            attempted_command: proto_attempted_command_to_dto(eb.attempted_command),
            reason: eb.reason,
            request_id: eb.request_id,
        }),
        Payload::ClientConnected(cc) => Some(dto::EngineEvent::ClientConnected { count: cc.count }),
        Payload::ClientDisconnected(cd) => {
            Some(dto::EngineEvent::ClientDisconnected { count: cd.count })
        }
        Payload::StrategyScenarioLoaded(ssl) => Some(dto::EngineEvent::StrategyScenarioLoaded {
            request_id: ssl.request_id,
            path: ssl.path,
            scenario: ssl.scenario.and_then(|s| {
                serde_json::from_str(&s)
                    .map_err(|e| {
                        log::warn!(
                            target: "engine_client::grpc_transport",
                            "StrategyScenarioLoaded: scenario JSON parse failed: {e}"
                        );
                        e
                    })
                    .ok()
            }),
            // proto repeated string: 空 Vec は None（v1/v2）、非空は Some（v3 + ref）
            resolved_instruments: if ssl.resolved_instruments.is_empty() {
                None
            } else {
                Some(ssl.resolved_instruments)
            },
        }),
        Payload::StrategyScenarioLoadFailed(sslf) => {
            Some(dto::EngineEvent::StrategyScenarioLoadFailed {
                request_id: sslf.request_id,
                path: sslf.path,
                reason: sslf.reason,
            })
        }
        Payload::StrategyScenarioSaved(sss) => Some(dto::EngineEvent::StrategyScenarioSaved {
            request_id: sss.request_id,
            path: sss.path,
            ok: sss.ok,
            error: sss.error,
        }),
        Payload::LiveBuyingPower(lbp) => Some(dto::EngineEvent::LiveBuyingPower {
            strategy_id: lbp.strategy_id,
            cash: lbp.cash,
            buying_power: lbp.buying_power,
            equity: lbp.equity,
            ts_event_ms: lbp.ts_event_ms,
        }),
        // schema 3.22: per-tick replay time signal
        Payload::ReplayTimeUpdated(rtu) => Some(dto::EngineEvent::ReplayTimeUpdated {
            timestamp_ms: rtu.timestamp_ms,
        }),
        // issue #42 Phase 2 (schema 3.25): LIVE_SCENARIO 応答
        Payload::LiveStrategyScenarioLoaded(lssl) => {
            // strategy_init_kwargs は wire 上 JSON 文字列。dict として decode する。
            let strategy_init_kwargs = lssl.strategy_init_kwargs.and_then(|s| {
                match serde_json::from_str::<serde_json::Value>(&s) {
                    Ok(serde_json::Value::Object(m)) => Some(m),
                    Ok(other) => {
                        log::warn!(
                            target: "engine_client::grpc_transport",
                            "LiveStrategyScenarioLoaded: strategy_init_kwargs is not an object: {other:?}"
                        );
                        None
                    }
                    Err(e) => {
                        log::warn!(
                            target: "engine_client::grpc_transport",
                            "LiveStrategyScenarioLoaded: strategy_init_kwargs JSON parse failed: {e}"
                        );
                        None
                    }
                }
            });
            Some(dto::EngineEvent::LiveStrategyScenarioLoaded {
                request_id: lssl.request_id,
                instrument_id: lssl.instrument_id,
                max_qty: lssl.max_qty,
                max_notional_jpy: lssl.max_notional_jpy,
                venue: lssl.venue,
                strategy_init_kwargs,
            })
        }
    }
}

// ── Sub-message converters ────────────────────────────────────────────────────

fn proto_trade_to_dto(t: engine::TradeRecord) -> dto::TradeMsg {
    dto::TradeMsg {
        price: t.price,
        qty: t.qty,
        side: t.side,
        ts_ms: t.ts_ms,
        is_liquidation: t.is_liquidation,
    }
}

fn proto_kline_to_dto(k: engine::KlineRecord) -> dto::KlineMsg {
    dto::KlineMsg {
        open_time_ms: k.open_time_ms,
        open: k.open,
        high: k.high,
        low: k.low,
        close: k.close,
        volume: k.volume,
        is_closed: k.is_closed,
        taker_buy_volume: k.taker_buy_volume,
    }
}

fn proto_depth_to_dto(d: engine::DepthLevel) -> dto::DepthLevel {
    dto::DepthLevel {
        price: d.price,
        qty: d.qty,
    }
}

fn proto_ticker_entry_to_dto(te: engine::TickerEntry) -> Option<dto::TickerEntry> {
    use engine::ticker_entry::Kind;
    match te.kind? {
        Kind::Stock(s) => Some(dto::TickerEntry::Stock(dto::StockTickerEntry {
            symbol: s.symbol,
            display_symbol: s.display_symbol,
            display_name_ja: s.display_name_ja,
            min_ticksize: s.min_ticksize,
            lot_size: s.lot_size,
            min_qty: s.min_qty,
            quote_currency: s.quote_currency,
            yobine_code: s.yobine_code,
            sizyou_c: s.sizyou_c,
            venue_caps: proto_venue_caps_to_dto(s.venue_caps)?,
        })),
        Kind::Crypto(c) => Some(dto::TickerEntry::Crypto(dto::CryptoTickerEntry {
            symbol: c.symbol,
            display_symbol: c.display_symbol,
            min_ticksize: c.min_ticksize,
            min_qty: c.min_qty,
            contract_size: c.contract_size,
            venue_caps: proto_venue_caps_to_dto(c.venue_caps)?,
        })),
    }
}

fn proto_venue_caps_to_dto(caps: Option<engine::VenueCaps>) -> Option<dto::VenueCaps> {
    let c = caps?;
    Some(dto::VenueCaps {
        client_aggr_depth: c.client_aggr_depth,
        supports_spread_display: c.supports_spread_display,
        qty_norm_kind: c.qty_norm_kind.map(|v| {
            use engine::QtyNormKind;
            match QtyNormKind::try_from(v).unwrap_or(QtyNormKind::Unspecified) {
                QtyNormKind::Unspecified | QtyNormKind::None => dto::QtyNormKind::None,
                QtyNormKind::Contract => dto::QtyNormKind::Contract,
                QtyNormKind::Lot => dto::QtyNormKind::Lot,
            }
        }),
    })
}

fn proto_order_record_to_dto(or_: engine::OrderRecord) -> Option<dto::OrderRecordWire> {
    use engine::{OrderSide, OrderStatus, OrderType, TimeInForce};

    let order_side = match OrderSide::try_from(or_.order_side).unwrap_or(OrderSide::Unspecified) {
        OrderSide::Buy => dto::OrderSide::Buy,
        OrderSide::Sell => dto::OrderSide::Sell,
        OrderSide::Unspecified => {
            log::warn!(
                target: "engine_client::grpc_transport",
                "OrderRecord missing order_side — record dropped"
            );
            return None;
        }
    };
    let order_type = match OrderType::try_from(or_.order_type).unwrap_or(OrderType::Unspecified) {
        OrderType::Unspecified => {
            log::warn!(
                target: "engine_client::grpc_transport",
                "OrderRecord order_type=Unspecified — record dropped"
            );
            return None;
        }
        OrderType::Market => dto::OrderType::Market,
        OrderType::Limit => dto::OrderType::Limit,
        OrderType::StopMarket => dto::OrderType::StopMarket,
        OrderType::StopLimit => dto::OrderType::StopLimit,
        OrderType::MarketIfTouched => dto::OrderType::MarketIfTouched,
        OrderType::LimitIfTouched => dto::OrderType::LimitIfTouched,
    };
    let tif = match TimeInForce::try_from(or_.time_in_force).unwrap_or(TimeInForce::Unspecified) {
        TimeInForce::Unspecified => {
            log::warn!(
                target: "engine_client::grpc_transport",
                "OrderRecord time_in_force=Unspecified — record dropped"
            );
            return None;
        }
        TimeInForce::Day => dto::TimeInForce::Day,
        TimeInForce::Gtc => dto::TimeInForce::Gtc,
        TimeInForce::Gtd => dto::TimeInForce::Gtd,
        TimeInForce::Ioc => dto::TimeInForce::Ioc,
        TimeInForce::Fok => dto::TimeInForce::Fok,
        TimeInForce::AtTheOpen => dto::TimeInForce::AtTheOpen,
        TimeInForce::AtTheClose => dto::TimeInForce::AtTheClose,
    };
    let status = match OrderStatus::try_from(or_.status).unwrap_or(OrderStatus::Unspecified) {
        OrderStatus::Unspecified => {
            log::warn!(
                target: "engine_client::grpc_transport",
                "OrderRecord status=Unspecified — record dropped"
            );
            return None;
        }
        OrderStatus::Submitted => dto::OrderStatus::Submitted,
        OrderStatus::Accepted => dto::OrderStatus::Accepted,
        OrderStatus::Filled => dto::OrderStatus::Filled,
        OrderStatus::PendingCancel => dto::OrderStatus::PendingCancel,
        OrderStatus::Canceled => dto::OrderStatus::Canceled,
        OrderStatus::Expired => dto::OrderStatus::Expired,
        OrderStatus::Rejected => dto::OrderStatus::Rejected,
    };

    Some(dto::OrderRecordWire {
        client_order_id: or_.client_order_id,
        venue_order_id: or_.venue_order_id,
        instrument_id: or_.instrument_id,
        order_side,
        order_type,
        quantity: or_.quantity,
        filled_qty: or_.filled_qty,
        leaves_qty: or_.leaves_qty,
        price: or_.price,
        trigger_price: or_.trigger_price,
        time_in_force: tif,
        expire_time_ns: or_.expire_time_ns,
        status,
        ts_event_ms: or_.ts_event_ms,
        venue: or_.venue,
    })
}

fn proto_position_record_to_dto(pr: engine::PositionRecord) -> Option<dto::PositionRecordWire> {
    use engine::PositionType;
    let pt = match PositionType::try_from(pr.position_type).unwrap_or(PositionType::Unspecified) {
        PositionType::Unspecified => {
            log::warn!(
                target: "engine_client::grpc_transport",
                "received PositionType::Unspecified — dropping PositionRecord"
            );
            return None;
        }
        PositionType::Cash => dto::PositionType::Cash,
        PositionType::MarginCredit => dto::PositionType::MarginCredit,
        PositionType::MarginGeneral => dto::PositionType::MarginGeneral,
    };
    Some(dto::PositionRecordWire {
        instrument_id: pr.instrument_id,
        qty: pr.qty,
        market_value: pr.market_value,
        position_type: pt,
        tategyoku_id: pr.tategyoku_id,
        venue: pr.venue,
    })
}

fn proto_engine_state_to_dto(state: i32) -> dto::CurrentEngineState {
    use engine::EngineState;
    match EngineState::try_from(state).unwrap_or(EngineState::Unspecified) {
        EngineState::Unspecified => {
            log::warn!(
                target: "engine_client::grpc_transport",
                "received EngineState::Unspecified, treating as Idle"
            );
            dto::CurrentEngineState::Idle
        }
        EngineState::Idle => dto::CurrentEngineState::Idle,
        EngineState::Loaded => dto::CurrentEngineState::Loaded,
        EngineState::Running => dto::CurrentEngineState::Running,
        EngineState::Stopping => dto::CurrentEngineState::Stopping,
        EngineState::Disconnected => dto::CurrentEngineState::Disconnected,
        EngineState::Connecting => dto::CurrentEngineState::Connecting,
        EngineState::Connected => dto::CurrentEngineState::Connected,
        EngineState::Trading => dto::CurrentEngineState::Trading,
    }
}

fn proto_attempted_command_to_dto(cmd: i32) -> dto::AttemptedCommand {
    use engine::AttemptedCommand;
    match AttemptedCommand::try_from(cmd).unwrap_or(AttemptedCommand::Unspecified) {
        AttemptedCommand::Unspecified => {
            log::warn!(
                target: "engine_client::grpc_transport",
                "AttemptedCommand=Unspecified received — mapping to Unknown"
            );
            dto::AttemptedCommand::Unknown
        }
        AttemptedCommand::LoadReplayData => dto::AttemptedCommand::LoadReplayData,
        AttemptedCommand::StartEngine => dto::AttemptedCommand::StartEngine,
        AttemptedCommand::StopEngine => dto::AttemptedCommand::StopEngine,
        AttemptedCommand::SetReplaySpeed => dto::AttemptedCommand::SetReplaySpeed,
        AttemptedCommand::StopReplay => dto::AttemptedCommand::StopReplay,
        AttemptedCommand::ForceStopReplay => dto::AttemptedCommand::ForceStopReplay,
        AttemptedCommand::PauseReplay => dto::AttemptedCommand::PauseReplay,
        AttemptedCommand::ResumeReplay => dto::AttemptedCommand::ResumeReplay,
        AttemptedCommand::StepReplay => dto::AttemptedCommand::StepReplay,
        AttemptedCommand::StepBackward => dto::AttemptedCommand::StepBackward,
        AttemptedCommand::SubmitOrder => dto::AttemptedCommand::SubmitOrder,
        AttemptedCommand::ModifyOrder => dto::AttemptedCommand::ModifyOrder,
        AttemptedCommand::CancelOrder => dto::AttemptedCommand::CancelOrder,
        AttemptedCommand::CancelAllOrders => dto::AttemptedCommand::CancelAllOrders,
        AttemptedCommand::RequestVenueLogin => dto::AttemptedCommand::RequestVenueLogin,
        AttemptedCommand::RequestVenueLogout => dto::AttemptedCommand::RequestVenueLogout,
        AttemptedCommand::GetBuyingPower => dto::AttemptedCommand::GetBuyingPower,
        AttemptedCommand::GetPositions => dto::AttemptedCommand::GetPositions,
        AttemptedCommand::GetOrderList => dto::AttemptedCommand::GetOrderList,
    }
}

fn proto_signal_kind_to_dto(sk: i32) -> Option<dto::SignalKind> {
    use engine::SignalKind;
    match SignalKind::try_from(sk).unwrap_or(SignalKind::Unspecified) {
        SignalKind::Unspecified => {
            log::warn!(
                target: "engine_client::grpc_transport",
                "received SignalKind::Unspecified — dropping StrategySignal event"
            );
            None
        }
        SignalKind::EntryLong => Some(dto::SignalKind::EntryLong),
        SignalKind::EntryShort => Some(dto::SignalKind::EntryShort),
        SignalKind::Exit => Some(dto::SignalKind::Exit),
        SignalKind::Annotate => Some(dto::SignalKind::Annotate),
    }
}

fn proto_optional_granularity_to_dto(v: i32) -> Option<dto::ReplayGranularity> {
    use engine::ReplayGranularity;
    match ReplayGranularity::try_from(v).unwrap_or(ReplayGranularity::Unspecified) {
        ReplayGranularity::Unspecified => None,
        ReplayGranularity::Trade => Some(dto::ReplayGranularity::Trade),
        ReplayGranularity::Minute => Some(dto::ReplayGranularity::Minute),
        ReplayGranularity::Daily => Some(dto::ReplayGranularity::Daily),
    }
}

// ── enum → i32 helpers (dto → proto) ─────────────────────────────────────────

fn dto_mode_to_proto(mode: dto::AppMode) -> i32 {
    match mode {
        dto::AppMode::Live => engine::AppMode::Live as i32,
        dto::AppMode::Replay => engine::AppMode::Replay as i32,
    }
}

fn dto_order_side_to_proto(side: dto::OrderSide) -> i32 {
    match side {
        dto::OrderSide::Buy => engine::OrderSide::Buy as i32,
        dto::OrderSide::Sell => engine::OrderSide::Sell as i32,
    }
}

fn dto_order_type_to_proto(ot: dto::OrderType) -> i32 {
    match ot {
        dto::OrderType::Market => engine::OrderType::Market as i32,
        dto::OrderType::Limit => engine::OrderType::Limit as i32,
        dto::OrderType::StopMarket => engine::OrderType::StopMarket as i32,
        dto::OrderType::StopLimit => engine::OrderType::StopLimit as i32,
        dto::OrderType::MarketIfTouched => engine::OrderType::MarketIfTouched as i32,
        dto::OrderType::LimitIfTouched => engine::OrderType::LimitIfTouched as i32,
    }
}

fn dto_trigger_type_to_proto(tt: dto::TriggerType) -> i32 {
    match tt {
        dto::TriggerType::Last => engine::TriggerType::Last as i32,
        dto::TriggerType::BidAsk => engine::TriggerType::BidAsk as i32,
        dto::TriggerType::Index => engine::TriggerType::Index as i32,
    }
}

fn dto_tif_to_proto(tif: dto::TimeInForce) -> i32 {
    match tif {
        dto::TimeInForce::Day => engine::TimeInForce::Day as i32,
        dto::TimeInForce::Gtc => engine::TimeInForce::Gtc as i32,
        dto::TimeInForce::Gtd => engine::TimeInForce::Gtd as i32,
        dto::TimeInForce::Ioc => engine::TimeInForce::Ioc as i32,
        dto::TimeInForce::Fok => engine::TimeInForce::Fok as i32,
        dto::TimeInForce::AtTheOpen => engine::TimeInForce::AtTheOpen as i32,
        dto::TimeInForce::AtTheClose => engine::TimeInForce::AtTheClose as i32,
    }
}

fn dto_engine_kind_to_proto(ek: dto::EngineKind) -> i32 {
    match ek {
        dto::EngineKind::Backtest => engine::EngineKind::Backtest as i32,
        dto::EngineKind::Live => engine::EngineKind::Live as i32,
    }
}

fn dto_granularity_to_proto(g: dto::ReplayGranularity) -> i32 {
    match g {
        dto::ReplayGranularity::Trade => engine::ReplayGranularity::Trade as i32,
        dto::ReplayGranularity::Minute => engine::ReplayGranularity::Minute as i32,
        dto::ReplayGranularity::Daily => engine::ReplayGranularity::Daily as i32,
    }
}

#[cfg(test)]
mod tests {
    use super::engine;

    /// Issue #43 リグレッション: gRPC 経路で scenario が JSON string のとき
    /// serde_json::Value::Object に正しく変換されること。
    /// Python 修正後の outbox payload を模擬。
    #[test]
    fn grpc_scenario_json_string_parses_to_object() {
        let scenario_json = r#"{"schema_version":1,"instrument":"1301.TSE","start":"2025-01-06","end":"2025-01-06","granularity":"Trade","initial_cash":1000000}"#;
        let proto_ssl = engine::StrategyScenarioLoadedEvent {
            request_id: "test-id".to_string(),
            path: "/some/path.py".to_string(),
            scenario: Some(scenario_json.to_string()),
            resolved_instruments: vec![],
        };

        let scenario = proto_ssl
            .scenario
            .and_then(|s| serde_json::from_str::<serde_json::Value>(&s).ok());
        let s = scenario.expect("scenario should parse successfully");
        assert!(s.is_object(), "scenario must be a JSON object");
        assert_eq!(s["instrument"], "1301.TSE");
        assert_eq!(s["schema_version"], 1);
    }

    /// scenario=None (SCENARIO 定数なし) は None のまま変換されること。
    #[test]
    fn grpc_scenario_none_stays_none() {
        let proto_ssl = engine::StrategyScenarioLoadedEvent {
            request_id: "test-id".to_string(),
            path: "/some/path.py".to_string(),
            scenario: None,
            resolved_instruments: vec![],
        };
        let scenario = proto_ssl
            .scenario
            .and_then(|s| serde_json::from_str::<serde_json::Value>(&s).ok());
        assert!(scenario.is_none());
    }

    /// v3 + instruments_ref: resolved_instruments が非空のとき Some(Vec) になること。
    #[test]
    fn grpc_resolved_instruments_non_empty_is_some() {
        let proto_ssl = engine::StrategyScenarioLoadedEvent {
            request_id: "req-v3".to_string(),
            path: "/some/path.py".to_string(),
            scenario: Some(r#"{"schema_version":3,"instruments_ref":"data/u.json"}"#.to_string()),
            resolved_instruments: vec!["1301.TSE".to_string(), "7203.TSE".to_string()],
        };
        let resolved = if proto_ssl.resolved_instruments.is_empty() {
            None
        } else {
            Some(proto_ssl.resolved_instruments.clone())
        };
        let ids = resolved.expect("resolved_instruments should be Some");
        assert_eq!(ids, vec!["1301.TSE", "7203.TSE"]);
    }
}
