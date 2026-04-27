/// 発注冪等性マップ（flowsurface `agent_session_state.rs` の移植）。
///
/// `client_order_id` を一次キーとし、同じ `client_order_id` + `request_key`
/// の再送を `IdempotentReplay` として処理する。
/// `venue_order_id`（= 立花 `sOrderNumber`）は Python から応答を受け取った後に
/// `update_venue_order_id()` で埋める。
///
/// - **当日分のみ保持**: 日跨ぎセッション切れ後に `OrderSessionState::new()` で再作成。
/// - **プロセス再起動跨ぎ**: WAL (Phase O0 T0.7) から `load_from_wal()` で復元する。
use std::{collections::HashMap, path::Path};

// ── 命名対応（flowsurface → e-station）─────────────────────────────────────
// place_or_replay(...)      → try_insert(client_order_id, key)
// order_id                  → venue_order_id
// key (u64 hash)            → request_key (u64 hash)
// PlaceOrderOutcome::IdempotentReplay { order_id } → { venue_order_id }

/// `client_order_id` を強く型付けした newtype。
/// nautilus の `ClientOrderId` 制約（長さ 1〜36、ASCII printable）は
/// HTTP 層 (Phase O0 T0.5) で事前に検証済みを前提とし、ここでは保持のみ行う。
///
/// 内部フィールドは `pub(crate)` に制限する（A-2: C-3）。
/// 外部からは `try_new()` または `ClientOrderId::from_str_unchecked()` でのみ構築する。
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct ClientOrderId(pub(crate) String);

impl ClientOrderId {
    /// バリデーション付きコンストラクタ（A-2: C-3）。
    ///
    /// 長さ 1〜36、ASCII printable（0x20〜0x7E）の制約を満たす場合のみ `Some` を返す。
    /// `From<String>` / `From<&str>` は実装しない（意図しない構築を防ぐため）。
    pub fn try_new(s: &str) -> Option<Self> {
        if s.is_empty() || s.len() > 36 {
            return None;
        }
        if !s.bytes().all(|b| (0x20..=0x7E).contains(&b)) {
            return None;
        }
        Some(Self(s.to_string()))
    }

    /// 検証なしで構築する（WAL 復元・テスト内部専用）。
    /// 呼び出し元が入力の安全性を保証する場合のみ使用する。
    pub(crate) fn from_raw(s: String) -> Self {
        Self(s)
    }
}

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
    Conflict {
        existing_venue_order_id: Option<String>,
    },
    /// セッションが frozen 状態（p_errno=2 受領後）— 以降の発注はすべてここに。
    SessionFrozen,
}

/// 発注冪等性マップ。`Arc<Mutex<Self>>` で Axum State として渡す想定。
pub struct OrderSessionState {
    map: HashMap<ClientOrderId, AgentOrderRecord>,
    /// `p_errno=2` 受領後に `true` にセット。`try_insert` が `SessionFrozen` を返す。
    frozen: bool,
}

impl OrderSessionState {
    pub fn new() -> Self {
        Self {
            map: HashMap::new(),
            frozen: false,
        }
    }

    /// セッションを frozen 状態にする（A-8: H-12）。
    ///
    /// `p_errno=2` 受領後に呼び出す。以降の `try_insert` は `SessionFrozen` を返す。
    pub fn freeze(&mut self) {
        self.frozen = true;
    }

    /// セッションが frozen 状態かどうかを返す。
    pub fn is_frozen(&self) -> bool {
        self.frozen
    }

    /// WAL ファイル（JSONL 形式）から当日分のエントリを復元して返す。
    ///
    /// architecture.md §4.3 の起動時復元ロジックに対応する。
    ///
    /// - WAL ファイルが存在しない場合は空 map で初期化する（初回起動等）。
    /// - 末尾行に `\n` が無い（truncated）場合はその行をスキップし `log::warn!` を出す。
    /// - `phase == "submit"` 行: `client_order_id → AgentOrderRecord { venue_order_id: None, ... }` で登録。
    /// - `phase == "accepted"` 行: `venue_order_id` を後着更新し `status` を `"ACCEPTED"` に変更。
    /// - `phase == "rejected"` 行: エントリを map から除去（再送防止対象外）。
    /// - 当日分のみ: `ts` フィールド（Unix ms UTC）が今日（UTC date）でなければスキップ。
    pub fn load_from_wal(wal_path: &Path) -> Self {
        let mut state = Self::new();

        // 当日の UTC date（millisecond → day 判定用）
        let today_utc = today_utc_date();

        // truncation 検知のために生バイトを読み、自前で \n チェックを行う。
        let raw_content = match std::fs::read(wal_path) {
            Ok(b) => b,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                // 初回起動など — 空 map で OK
                return state;
            }
            Err(e) => {
                log::warn!("load_from_wal: failed to read WAL {wal_path:?}: {e}");
                return state;
            }
        };

        // 行分割: `\n` で区切り、各行の末尾 \n 有無を記録する。
        // 最後の要素が空文字列（= ファイルが \n で終わっている）の場合はスキップ。
        let text = String::from_utf8_lossy(&raw_content);
        let all_lines: Vec<&str> = text.split('\n').collect();

        // split('\n') の最後の要素はファイルが \n で終わる場合は空文字列になる。
        // 例: "A\nB\n".split('\n') => ["A", "B", ""]
        //     "A\nB".split('\n')   => ["A", "B"]      ← 末尾 \n なし = truncated
        let n = all_lines.len();
        for (i, raw_line) in all_lines.iter().enumerate() {
            // 最後の要素が空文字列 = ファイルが \n で終わっていた（正常終端）
            if i == n - 1 {
                if raw_line.is_empty() {
                    break; // 正常終端の空文字列 → スキップ
                }
                // 最後の要素が非空 → truncated（末尾 \n なし）
                log::warn!(
                    "load_from_wal: truncated WAL line skipped (no trailing newline): file={wal_path:?} line_index={i}"
                );
                break;
            }

            let line = raw_line.trim();
            if line.is_empty() {
                continue;
            }

            let record: serde_json::Value = match serde_json::from_str(line) {
                Ok(v) => v,
                Err(e) => {
                    log::warn!("load_from_wal: invalid JSON at line {i}: {e}");
                    continue;
                }
            };

            // 当日チェック: ts フィールドが今日（UTC date）でなければスキップ。
            let ts_ms = record.get("ts").and_then(|v| v.as_i64()).unwrap_or(0);
            if !is_today_utc(ts_ms, today_utc) {
                continue;
            }

            let phase = match record.get("phase").and_then(|v| v.as_str()) {
                Some(p) => p,
                None => {
                    log::warn!("load_from_wal: missing 'phase' field at line {i}");
                    continue;
                }
            };

            let cid_str = match record.get("client_order_id").and_then(|v| v.as_str()) {
                Some(s) => s.to_string(),
                None => {
                    log::warn!("load_from_wal: missing 'client_order_id' at line {i}");
                    continue;
                }
            };

            match phase {
                "submit" => {
                    let request_key = record
                        .get("request_key")
                        .and_then(|v| v.as_u64())
                        .unwrap_or(0);
                    // A-3 (H-14): request_key=0 の submit 行は map に登録しない。
                    // Python 側が 0 を仮置きのまま書いた行は「未登録」として扱い、
                    // 再起動後は新規発注として受け付ける（重複発注リスクより冪等性崩壊のほうが深刻）。
                    if request_key == 0 {
                        log::warn!(
                            "load_from_wal: WAL submit line has request_key=0, skipping: cid={cid_str:?}"
                        );
                        continue;
                    }
                    // A-2 (C-3): WAL 復元パスでも try_new を通す。失敗した行は skip + warn。
                    let cid = match ClientOrderId::try_new(&cid_str) {
                        Some(c) => c,
                        None => {
                            log::warn!(
                                "load_from_wal: invalid client_order_id skipped at line {i}: {cid_str:?}"
                            );
                            continue;
                        }
                    };
                    state.map.insert(
                        cid,
                        AgentOrderRecord {
                            venue_order_id: None,
                            request_key,
                            status: "SUBMITTED".to_string(),
                        },
                    );
                }
                "accepted" => {
                    let venue_order_id = record
                        .get("venue_order_id")
                        .and_then(|v| v.as_str())
                        .map(str::to_string);
                    let cid_key = ClientOrderId::from_raw(cid_str);
                    if let Some(record) = state.map.get_mut(&cid_key) {
                        if record.venue_order_id.is_none() {
                            record.venue_order_id = venue_order_id;
                        }
                        record.status = "ACCEPTED".to_string();
                    }
                }
                "rejected" => {
                    // 拒否済み → 再送防止対象外なので map から除去する。
                    state.map.remove(&ClientOrderId::from_raw(cid_str));
                }
                other => {
                    log::warn!("load_from_wal: unknown phase {other:?} at line {i}, skipping");
                }
            }
        }

        state
    }

    /// `client_order_id` と `request_key` の組み合わせで挿入を試みる。
    ///
    /// - frozen 状態 → `SessionFrozen`
    /// - 未登録 → `Created`
    /// - 登録済み + 同一 key → `IdempotentReplay { venue_order_id }`
    /// - 登録済み + 異なる key → `Conflict { existing_venue_order_id }`
    pub fn try_insert(
        &mut self,
        client_order_id: ClientOrderId,
        request_key: u64,
    ) -> PlaceOrderOutcome {
        // A-8 (H-12): frozen 状態では新規発注を拒否する。
        if self.frozen {
            return PlaceOrderOutcome::SessionFrozen;
        }
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
    ///
    /// `None → Some` の遷移のみ許容する。既に `Some` が入っている場合は上書きせず
    /// `false` を返す（M-8: IdempotentReplay 時の二重セットを防ぐ）。
    /// 登録済みでない `client_order_id` に対しても `false` を返す。
    #[must_use = "false は None→Some 遷移が起きなかったことを示す"]
    pub fn update_venue_order_id(
        &mut self,
        client_order_id: ClientOrderId,
        venue_order_id: String,
    ) -> bool {
        if let Some(record) = self.map.get_mut(&client_order_id)
            && record.venue_order_id.is_none()
        {
            record.venue_order_id = Some(venue_order_id);
            return true;
        }
        false
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

    /// `GetOrderList` 応答から `venue_order_id` が unknown な状態を補完する（T1.5）。
    ///
    /// WAL 起動時復元で `venue_order_id = None` のまま残ったエントリに対して、
    /// `GetOrderList` 応答で返ってきた `venue_order_id` で `client_order_id` を特定する。
    /// 同一 `venue_order_id` で登録済みの `client_order_id` が無い場合は何もしない。
    ///
    /// # Arguments
    /// - `venue_order_id`: 立花 `sOrderNumber`
    /// - `new_status`: `GetOrderList` 応答から得た nautilus OrderStatus 文字列
    ///
    /// # Returns
    /// 更新が行われた場合は `Some(client_order_id)`、対象が見つからない場合は `None`。
    pub fn update_venue_order_id_from_list(
        &mut self,
        venue_order_id: &str,
        new_status: &str,
    ) -> Option<ClientOrderId> {
        // ① 既に venue_order_id が確定しているエントリを優先してステータス更新する。
        //    GetOrderList で「知っている venue_order_id」が返ってきた場合はここで解決。
        if let Some((cid, record)) = self
            .map
            .iter_mut()
            .find(|(_, rec)| rec.venue_order_id.as_deref() == Some(venue_order_id))
        {
            record.status = new_status.to_string();
            return Some(cid.clone());
        }

        // ② 一致するエントリがなければ、unknown（None）エントリに venue_order_id を割り当てる。
        //    複数の unknown エントリが存在する場合は最初のものを選ぶ（WAL 復元は FIFO 順序が保証されないが
        //    in-flight 注文が複数の unknown 状態になるシナリオは GetOrderList で一度に全件補完される）。
        let matched = self
            .map
            .iter_mut()
            .find(|(_, rec)| rec.venue_order_id.is_none());

        if let Some((cid, record)) = matched {
            record.venue_order_id = Some(venue_order_id.to_string());
            record.status = new_status.to_string();
            Some(cid.clone())
        } else {
            None
        }
    }
}

impl Default for OrderSessionState {
    fn default() -> Self {
        Self::new()
    }
}

// ── WAL date helpers ─────────────────────────────────────────────────────────

/// 今日の UTC 日付を `(year, month, day)` で返す。
fn today_utc_date() -> (i32, u32, u32) {
    use chrono::{Datelike, Utc};
    let today = Utc::now().date_naive();
    (today.year(), today.month(), today.day())
}

/// `ts_ms`（Unix milliseconds UTC）が `today`（UTC date）と同じ日かどうか判定する。
fn is_today_utc(ts_ms: i64, today: (i32, u32, u32)) -> bool {
    use chrono::{Datelike, TimeZone, Utc};
    let Some(dt) = Utc.timestamp_millis_opt(ts_ms).single() else {
        return false;
    };
    let d = dt.date_naive();
    (d.year(), d.month(), d.day()) == today
}
