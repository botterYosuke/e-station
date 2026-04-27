---
layout: home

hero:
  name: flowsurface
  text: エンジニア向けドキュメント
  tagline: Rust（Iced GUI）+ Python データエンジン — アーキテクチャと実装仕様
  actions:
    - theme: brand
      text: Python データエンジン仕様
      link: /spec/data-engine
    - theme: alt
      text: 立花証券 API 統合
      link: /spec/tachibana
    - theme: alt
      text: GitHub
      link: https://github.com/flowsurface-rs/flowsurface

features:
  - icon: 🐍
    title: Python データエンジン
    details: Rust ビュアーとローカル WebSocket IPC で連携するデータエンジン。取引所 REST/WebSocket 接続・レート制限・データ正規化・配信を担当。IPC スキーマ・バックプレッシャ・起動ハンドシェイクの仕様を定義。
  - icon: 🏦
    title: 立花証券 API 統合
    details: 立花証券 e支店 API（v4r8）を使った日本株チャート閲覧。認証フロー・銘柄マスタ・FD ストリーム・日足 kline・セッション管理・セキュリティ要件を定義。
  - icon: 📋
    title: 立花注文機能
    details: 現物・信用の新規注文/訂正/取消/約定通知。nautilus_trader 互換 API 設計・第二暗証番号管理・誤発注防止安全装置・reason_code 体系を定義。
---
