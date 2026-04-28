# e-station エンジニア向けドキュメント

Rust（Iced GUI）+ Python データエンジンで構成されるマーケットデータ可視化アプリ
**e-station** の機能別実装仕様書。

## 実装仕様書

- [Python データエンジン](/✅python-data-engine/spec.md)
  Rust ビュアーとローカル WebSocket IPC で連携するデータエンジン。取引所 REST/WebSocket
  接続・レート制限・データ正規化・配信を担当。IPC スキーマ・バックプレッシャ・
  起動ハンドシェイクを定義。

- [立花証券 API 統合](/✅tachibana/spec.md)
  立花証券 e支店 API（v4r8）を使った日本株チャート閲覧。認証フロー・銘柄マスタ・
  FD ストリーム・日足 kline・セッション管理・セキュリティ要件を定義。

- [立花注文機能](/✅order/spec.md)
  現物・信用の新規注文/訂正/取消/約定通知。nautilus_trader 互換 API 設計・
  第二暗証番号管理・誤発注防止安全装置・reason_code 体系を定義。

- [NautilusTrader 統合](/✅nautilus_trader/spec.md)
  NautilusTrader を中核エンジンに据えた live / replay 両対応の実装仕様。
  J-Quants バックテスト・立花 LiveExecutionClient・TradeTick 一本化・
  live/replay 互換不変条件・IPC スキーマを定義。

## ソース

[github.com/botterYosuke/e-station](https://github.com/botterYosuke/e-station)
