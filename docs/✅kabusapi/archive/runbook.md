# kabuステーション venue 運用 Runbook（Phase 4）

> **目的**: 本番接続 (`localhost:18080`) で実弾発注を行う際の事故対応・取消手順・本体ダウン時オペレーションを定義する。
> **対象**: e-station を本番口座で運用するユーザー / オンコール担当。
> **前提環境**: Windows + kabuステーション本体起動 + 三菱UFJ eスマート証券（旧 auカブコム）口座。
>
> **TL;DR — 緊急時 3 ステップ**:
> 1. kabuステーション本体（GUI）で「全注文取消」（§2.1）
> 2. e-station を停止（フッター kabu ログアウト → アプリ終了）
> 3. ブローカー側 Web 取引画面でポジション・約定を最終確認（§1）
>
> 骨子フェーズ。Phase 4 実装の進捗に合わせて随時肉付けする。

---

## §1. 緊急時の連絡先・口座情報

- **ブローカー**: 三菱UFJ eスマート証券（旧 auカブコム証券）
- **緊急時取引画面**: kabuステーション本体（GUI）または Web 取引（[https://s20.si0.kabu.co.jp/](https://s20.si0.kabu.co.jp/)）
- **問い合わせ窓口**: ブローカー公式サイトの最新情報を参照（個別契約に依存）
- **e-station ログ収集場所**: `%APPDATA%\flowsurface\logs\` および Python engine stderr
- **インシデント記録テンプレ**: §8 参照

> **TODO**: Phase 4 実装完了時にユーザー個別の口座番号メモ場所を記入する場所を別途用意（このファイルにはコミットしない）。

---

## §2. 全注文一括取消手順

### §2.1 第一手段（推奨）: kabuステーション本体 GUI

1. kabuステーション本体を最前面に
2. 「注文照会」画面 → 全件選択 → 「取消」ボタン
3. 約 10 秒待ち、すべて「取消済」になることを確認

### §2.2 第二手段: REST `/orders` → `/cancelorder` ループ

e-station が応答不能な場合のみ使用。`scripts/kabu_panic_cancel.py`（後日整備）または手動 REST を使う。

```
GET /kabusapi/orders          → State != 5 のみ抽出
PUT /kabusapi/cancelorder     → 各 OrderID に対し順次（OrderBucket 5 req/sec）
```

> **TODO**: Phase 4-A 実装後に panic_cancel スクリプトを `scripts/` に追加し、ここからリンク。

### §2.3 第三手段: ブローカー Web 取引画面

kabuステーション本体・REST 両方が落ちている場合の最終手段。Web 取引で取消。

---

## §3. kabuステーション本体ダウン時のオペレーション

- e-station 側挙動: TCP refused → 5s × 3 回 retry → `VenueError{code:"local_app_down"}` バナー表示（spec.md §3.2）
- WebSocket: 5s × 5 回連続失敗で打ち切り → `local_app_down` 再発火
- **手順**:
  1. kabuステーション本体を起動
  2. ログイン完了を確認
  3. e-station フッターの kabu バッジから再ログイン
  4. ポジション・未約定注文がブローカー側と一致するか目視確認

---

## §4. 早朝強制ログアウト時の挙動・再ログイン手順

- kabuステーション本体は早朝（ブローカー仕様）に強制ログアウト
- e-station はバナー誘導のみ（自動再ログインしない、spec.md §3.2 / Q-K3）
- 手順:
  1. 早朝以降に kabuステーション本体を再起動
  2. e-station フッターの kabu バッジから再ログイン
  3. RegisterSet は再ログインで全件 re-register（spec.md §3.3 / U6）

---

## §5. 実弾スモークテスト手順（最小 1 単元）

> **重要**: AI ではなくユーザー手動で実施する。実施前に §1〜§4 を一読すること。

### §5.1 事前準備

- [ ] ブローカー口座に十分な資金があること
- [ ] テスト対象銘柄を 1 つ選定（最小単元かつ流動性のあるもの）
- [ ] kabuステーション本体ログイン済
- [ ] env 設定: `KABU_ALLOW_PROD=1` **かつ** `KABU_ENV=prod`（P4-2）
- [ ] e-station フッターの kabu バッジが **🔴 本番** 表示（P4-4）であること

### §5.2 スモーク手順

- [ ] e-station から最小 1 単元の **指値** buy を発注
- [ ] kabuステーション本体および Web 取引画面で発注を確認
- [ ] 5 秒以内に **取消** 操作（`PUT /cancelorder`）
- [ ] 取消完了をブローカー側で確認
- [ ] e-station ログで `OrderSubmitted` → `OrderCancelled` の順を確認
- [ ] 取引パスワードがログに含まれていないことを確認（`grep` で検査）

### §5.3 異常系チェック

- [ ] `KABU_ALLOW_PROD` 未設定で発注試行 → ValueError（P4-1）
- [ ] env mismatch（`KABU_ALLOW_PROD=1` のみ）→ verify にフォールバック + WARN（P4-2）

---

## §6. 取引パスワード忘却・lockout 復旧手順

- 3 回連続誤入力で 30 分 lockout（Phase 2 設計、Q-P2-2）
- 復旧:
  1. 30 分待機 or e-station 再起動でクリア
  2. 取引パスワード自体を忘れた場合はブローカー Web で再設定
- 専用コードは kabu API v1.5 公式 spec に存在しないことを確認済み（P4-7 / Q-P2-5 部分解決）。メッセージ文字列ベース検出を使用。実機で確定コード観測時は `kabusapi_auth.py` の `check_response()` をコードベース判定に切替えること

---

## §7. 本番 ↔ 検証切替の env 設定

| 用途 | `KABU_ALLOW_PROD` | `KABU_ENV` | port |
| :--- | :--- | :--- | :--- |
| 検証（デフォルト） | 未設定 or `0` | 任意 | 18081 |
| 本番 | `1` | `prod` | 18080 |
| **誤設定**（片方欠如） | `1` のみ | 未設定 | **verify にフォールバック + WARN** |

- PowerShell: `$env:KABU_ALLOW_PROD="1"; $env:KABU_ENV="prod"; cargo run --release`
- 本番運用時は **release ビルド**を推奨（debug 時の `DEV_KABU_API_PASSWORD` 自動ログインが prod では禁止される、P4-2）

---

## §8. ログ収集 / インシデントレポート雛形

```
発生日時:           YYYY-MM-DD HH:MM JST
e-station バージョン: <git rev>
env:                KABU_ALLOW_PROD=?  KABU_ENV=?
事象概要:
影響範囲:           （ポジション増減、未約定残、損益）
取った対応:         （§2 のどれか / §3 / §4 ...）
ブローカー側確認:   （Web 取引画面のスクリーンショット添付）
ログ抜粋:           （token / API パスワード / 取引パスワードを必ずマスク）
再発防止案:
```

ログ収集対象:
- `%APPDATA%\flowsurface\logs\flowsurface.log`
- Python engine stderr（`python -m engine` の出力）
- kabuステーション本体ログ（kabuステーション > 設定 > ログ）

> **重要**: ログを共有する前に、token / API パスワード / 取引パスワード / 口座番号がマスクされていることを必ず確認する（spec.md §3.1）。
