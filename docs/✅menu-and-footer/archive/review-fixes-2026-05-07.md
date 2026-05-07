# review-fixes-2026-05-07

対象: `docs/✅menu-and-footer/🔵venue-login-footer.md`  
フェーズ: venue-login-footer（フッター取引所ログイン状態バッジ）

---

## ラウンド 1（2026-05-07）

### 統一決定

- GetBuyingPower: kabu はこのフェーズで buying power IPC 対象外。ハンドラ複製から除外。
- bump_generation: kabu の Ready 遷移も market data 再購読トリガーとして有効。Tachibana と同条件で含める。
- EngineConnected 追加: §10 新規セクションとして計画に追記。
- venue_state.rs コメント更新: §変更しないもの に追記。

### Findings

| ID | 優先 | 観点 | 対象:行 | 問題 | 修正概要 |
|----|------|------|---------|------|---------|
| F1 | HIGH | B | 🔵venue-login-footer.md | `EngineConnected` ハンドラが kabu 未対応。§5/§8 はあるがこのパスが欠落 | §10 として EngineConnected 対応を追記 |
| F2 | HIGH | C | 🔵venue-login-footer.md §7 | `GetBuyingPower` の kabu 扱い未定義。コピー時に誤送信リスク | §7 に「GetBuyingPower は複製しない」を明記 |
| F3 | MEDIUM | C | 🔵venue-login-footer.md §7 | `bump_generation()` の kabu 適用が未記載 | §7 に bump_generation の適用条件を追記 |
| F4 | MEDIUM | D | 🔵venue-login-footer.md §テスト | IPC 送信失敗時（`KabuLoginIpcResult(Err)`）のテストが欠落 | テストテーブルに行を追加 |
| F5 | LOW | A | 🔵venue-login-footer.md §変更しないもの | `venue_state.rs` モジュールコメント更新が未記載 | §変更しないもの に更新指示を追記 |

---

## ラウンド 2（2026-05-07）

### 統一決定

- §10 の位置: §9 直後（## Python 側との契約 の前）に移動
- §10 のコード: if 条件 + Task::batch パターンのスニペットを追記
- テスト行追加: `engine_connected_restores_kabu_state_when_cached`
- // コメント行: 削除して散文に統合
- GetBuyingPower 除外: R1 の // コメント行削除時に消失 → 散文として §7 に補完

### Findings

| ID | 優先 | 対象:行 | 問題 | 修正概要 |
|----|------|---------|------|---------|
| R2-1 | MEDIUM | §10:229–237 | if 条件・Task::batch 組み立てが未記載 | スニペット追記 |
| R2-2 | MEDIUM | テスト方針テーブル | EngineConnected パスのテストが欠落 | `engine_connected_restores_kabu_state_when_cached` 行を追加 |
| R2-3 | LOW | §7:125–126 | // コメント行がコードブロック外に剥き出し | 削除、散文に統合 |
| R2-4 | LOW | §10 配置 | ## 変更しないもの の後に浮いていた | §9 直後に移動 |

---

## ラウンド 3（2026-05-07）— 収束確認

HIGH/MEDIUM: 0件。収束。

残存 LOW（対応不要）:
- §8 の .chain() 4 要素連結 — 実装時確認で十分
- §10 の match ブロック説明 — 実装者が元コード参照で解決できる範囲
