# e-station ドキュメント再編計画

## Context

現状 `docs/` は「✅<モジュール名>/」というモジュール単位ディレクトリに `spec / architecture / implementation-plan / open-questions / invariant-tests / data-mapping` を一律で並べる構成になっており、開発フェーズ（実装区間）の進行管理用ディレクトリと、本来恒常的に参照されるべきエンジニア向けドキュメント（仕様・アーキテクチャ・規約）が混在している。さらに `docs/wiki/` はエンドユーザー向け操作手順なのに `docs/` 直下にあり、site_description にある「e-station エンジニア向けドキュメント」という位置づけと整合していない（現在は `exclude_docs` で MkDocs サイトから外しているだけ）。

このため:
- 読者（エンジニア / ユーザー）と目的（仕様 / 操作手順 / 経緯）でナビゲーションが切れていない
- ADR / 技術採用経緯が `docs/✅<module>/archive/review-fixes-*.md` などに埋もれて発見不能
- 開発環境セットアップ・ビルド/リリース手順・テスト戦略・コーディング規約といった「モジュール横断」ドキュメントの居場所がない
- ✅ プレフィックスは `exclude_docs` の運用パターンに依存しており、命名としても異質

ゴールは「**読者と目的でトップレベルを切った、モジュール横断のトピック構成**」へ移行し、wiki はリポジトリから分離して GitHub Wiki に移送すること。

## 決定事項（ユーザー確認済）

1. `wiki/` は **GitHub Wiki / 別サイトに分離**（`docs/wiki/` から物理移動）
2. ✅ プレフィックスは外し、**現状のモジュール別構成は破棄**してトピックベースに再編
3. 新設優先カテゴリ: アーキテクチャ全体図 / 境界、開発環境セットアップ・ビルド/リリース、テスト戦略、コーディング規約・Contribution Guide

## 表記正規化ルール（計画書全体に適用）

- 旧パスは常に `docs/✅<module>/...` 形式で書く（`docs/` を省略しない）
- archive 参照は常に `docs/✅<module>/archive/...` 形式（リポジトリ直下に `archive/` は存在しない）
- 旧モジュール名は `nautilus_trader`（アンダースコア・実在ディレクトリ名）、新モジュール名は `nautilus-trader`（ハイフン）に統一
- `❌` プレフィックスのファイル / ディレクトリ（例: `docs/✅python-data-engine/❌archive/`）は **ADR 抽出候補から除外**し、`archive/` 据え置きとする

## 新ディレクトリ構成（提案）

```
docs/
├── index.md                          # ランディング: 読者別ナビ（エンジニア / コントリビュータ）。エンドユーザーは GitHub Wiki への外部導線のみ。運用 runbook は `development/troubleshooting.md` に集約
│
├── architecture/                     # アーキテクチャ（恒常）
│   ├── overview.md                   # 全体図 / プロセス構成 / Rust↔Python↔UI 境界の俯瞰
│   ├── boundaries.md                 # IPC 境界・所有権・責務分担
│   ├── data-flow.md                  # live / replay / backtest のデータフロー
│   ├── ipc-schema.md                 # IPC スキーマと SCHEMA_MAJOR/MINOR
│   └── modules/                      # コンポーネント別の構造
│       ├── data-engine.md            # 旧 docs/✅python-data-engine/current-architecture.md
│       ├── tachibana-adapter.md      # 旧 docs/✅tachibana/architecture.md（外部仕様部分は reference/ へ）
│       ├── kabusapi-adapter.md       # 旧 docs/✅kabusapi/architecture.md（同上）
│       ├── nautilus-trader.md       # 旧 ✅nautilus_trader/architecture.md
│       └── ui-shell.md               # 旧 ✅menu-and-footer 群（UI 構造の側面のみ）
│
├── specs/                            # 機能仕様（実装契約）
│   ├── order.md                      # 旧 docs/✅order/spec.md
│   ├── replay.md                     # 旧 docs/✅python-data-engine/spec.md の replay 章。バックテスト用 replay は backtest.md 側、本ファイルとは cross-link で分離
│   ├── live-strategy.md              # venue 横断の **抽象契約のみ**（注文ライフサイクル / 状態遷移 / Strategy SDK 接点）
│   ├── venues/                       # venue 固有仕様
│   │   ├── tachibana.md              # 旧 docs/✅tachibana/spec.md（CLMAuthLogin / Shift-JIS / ^A^B^C / 流量制限など）
│   │   └── kabusapi.md               # 旧 docs/✅kabusapi/spec.md（X-API-KEY / 50 銘柄上限 / WebSocket PUSH など）
│   ├── backtest.md                   # 旧 docs/✅nautilus_trader/spec.md（バックテスト用 replay は本ファイル側に置き、replay.md から cross-link）
│   ├── data-engine.md                # 旧 docs/✅python-data-engine/spec.md（replay は replay.md と分担）
│   └── strategy-sdk.md               # ユーザー戦略の責任境界（採用根拠 ADR が確定するまで status: proposed）
│
├── reference/                        # 外部 API / データモデル / 用語集
│   ├── external-apis/
│   │   ├── tachibana.md              # 旧 docs/✅tachibana/{architecture.md, data-mapping.md} の外部仕様部分
│   │   ├── kabusapi.md
│   │   └── nautilus-trader.md
│   ├── data-models.md                # 共通データ型・Newtype（Price/Qty 等）
│   ├── ipc-protocol.md               # IPC 契約: `python/engine/schemas.py` ↔ `engine-client/src/dto.rs` ↔ `proto/engine.proto`（SCHEMA_MAJOR/MINOR の **SoT は `engine-client/src/lib.rs` の履歴コメント**、parity は `scripts/check_schema_parity.py`）
│   ├── schemas/                      # 機械可読 artifact: 旧 docs/✅python-data-engine/schemas/{commands,events}.json はここに据え置き（exclude_docs で MkDocs 公開対象外）
│   └── glossary.md
│
├── development/                      # 開発者向け運用（新設）
│   ├── setup.md                      # toolchain / 依存 / IDE / ローカル起動
│   ├── build-and-release.md          # ビルド・配布・バージョニング
│   ├── ci-cd.md
│   └── troubleshooting.md            # 開発時のハマりどころ（ログ位置・再現手順）
│
├── testing/                          # テスト戦略（新設）
│   ├── strategy.md                   # unit / integration / E2E / invariant / TDD 全体方針
│   ├── invariants.md                 # 旧 ✅*/invariant-tests を統合
│   ├── e2e.md                        # ReplaySession / LiveSession（メモリ方針反映）
│   └── benchmarks.md                 # 旧 ✅python-data-engine/benchmarks のインデックス
│
├── contributing/                     # 規約（新設）
│   ├── coding-standards.md           # 既存 coding-standards スキルの言語化
│   ├── contribution-guide.md         # PR フロー / レビュー基準 / ブランチ戦略
│   └── commit-conventions.md
│
├── decisions/                        # ADR（新設、ナンバリング付）
│   ├── README.md                     # 一覧 + ステータス凡例（proposed / accepted / deferred / superseded）+ 遷移ルール（後述）
│   ├── manifest.yaml                 # 移送台帳の最終形（機械可読 YAML、ADR ナンバリング体系外）
│   ├── MANIFEST.md                   # 上の YAML を人間向けに索引するだけの索引ページ（YAML パースは行わない）
│   ├── 0001-rust-python-boundary.md  # docs/✅python-data-engine/archive/refactor-rust-python-boundary-2026-05-01.md から抽出
│   ├── 0002-ipc-schema-versioning.md # SCHEMA_MAJOR/MINOR 運用方針（SoT は engine-client/src/lib.rs 履歴コメント）
│   ├── 0003-no-bundled-ai.md         # 典拠が repo 内に確定するまで status: deferred
│   ├── 0004-user-strategy-responsibility.md  # 同上
│   ├── 0005-python-only-mode.md      # 同上
│   ├── 0006-language-stack-rust-plus-python.md  # Rust + Python 二言語構成の採用根拠（status: proposed）
│   ├── 0007-rust-adoption.md         # Rust 言語選択の根拠（status: proposed）
│   └── 0008-python-adoption.md       # Python 採用根拠（data engine 側）（status: proposed）
│
└── roadmap/                          # 実装計画 / 進行中の論点
    ├── README.md                     # モジュール別 roadmap への索引（編集ホットスポット化を避けるための入り口のみ）
    ├── data-engine/
    │   ├── implementation-plan.md    # 旧 docs/✅python-data-engine/implementation-plan.md
    │   └── open-questions.md
    ├── tachibana/
    │   ├── implementation-plan.md
    │   └── open-questions.md
    ├── kabusapi/
    │   ├── implementation-plan.md
    │   └── open-questions.md
    ├── order/
    │   ├── implementation-plan.md
    │   └── open-questions.md
    ├── nautilus-trader/
    │   ├── implementation-plan.md
    │   └── open-questions.md
    ├── ui-shell/                     # 旧 docs/✅menu-and-footer/ の implementation 系 + docs/plan/floating-windows/ の生存分
    │   ├── implementation-plan.md
    │   └── open-questions.md
    └── changelog.md                  # スキーマ・破壊的変更の年表（横断、これは集約してよい）

> **集約しない理由**: 旧 ✅*/implementation-plan・open-questions を単一ファイルに集約すると、後述「1 PR = 1 旧モジュール」の移送方針と衝突し、毎 PR が同じファイルを触る編集ホットスポットになる。移行が完了して個別計画が落ち着いた後に、必要なら「現在進行中の横断ロードマップ」だけを別途 `roadmap/now.md` として抜き出すかを再検討する。
```

### wiki（移送先）

`docs/wiki/` の以下は **GitHub Wiki に移送**してリポジトリから削除（exclude_docs から `wiki/**` を除去）:

- Home / Getting Started / Live Strategy / Replay / Backtest
- Orders / Charts / Modes & Venues / File Menu / Settings
- Troubleshooting（ユーザー向け）
- 旧 ✅menu-and-footer の操作手順部分（UI 構造の側面は `architecture/modules/ui-shell.md` へ）

## 移送マッピング（要約）

| 旧パス                                                          | 新パス                                          |
| -------------------------------------------------------------- | ----------------------------------------------- |
| `docs/✅python-data-engine/spec.md`                             | `docs/specs/data-engine.md`                     |
| `docs/✅python-data-engine/current-architecture.md`             | `docs/architecture/modules/data-engine.md`      |
| `docs/✅python-data-engine/implementation-plan.md`              | `docs/roadmap/data-engine/implementation-plan.md` |
| `docs/✅python-data-engine/open-questions.md`                   | `docs/roadmap/data-engine/open-questions.md`    |
| `docs/✅python-data-engine/benchmarks/{baseline,phase-2,phase-6}.md` | `docs/testing/benchmarks.md`（**索引のみ**）+ 元 md は `docs/testing/benchmarks/` に据え置き |
| `docs/✅python-data-engine/schemas/CHANGELOG.md`                | `docs/roadmap/changelog.md`（年表のみ。SoT は `engine-client/src/lib.rs` 履歴コメント） |
| `docs/✅python-data-engine/schemas/{commands,events}.json`      | `docs/reference/schemas/{commands,events}.json`（exclude_docs 維持） |
| `docs/✅tachibana/spec.md`                                      | `docs/specs/venues/tachibana.md`（venue 固有）+ `docs/specs/live-strategy.md`（venue 横断の抽象契約のみ） |
| `docs/✅tachibana/architecture.md` / `data-mapping.md` / `invariant-tests.md` | `docs/architecture/modules/tachibana-adapter.md` / `docs/reference/external-apis/tachibana.md`（外部仕様 + data-mapping）/ `docs/testing/invariants.md` |
| `docs/✅tachibana/implementation-plan.md` / `open-questions.md` | `docs/roadmap/tachibana/...`                    |
| `docs/✅kabusapi/spec.md` / `architecture.md`                   | `docs/specs/venues/kabusapi.md` + 共通 hook は `docs/specs/live-strategy.md` / `docs/architecture/modules/kabusapi-adapter.md` / `docs/reference/external-apis/kabusapi.md` |
| `docs/✅kabusapi/implementation-plan.md` / `open-questions.md`  | `docs/roadmap/kabusapi/...`                     |
| `docs/✅order/spec.md` / `architecture.md` / `invariant-tests.md` | `docs/specs/order.md` + `docs/testing/invariants.md` |
| `docs/✅order/implementation-plan.md` / `open-questions.md`     | `docs/roadmap/order/...`                        |
| `docs/✅nautilus_trader/spec.md` / `architecture.md` / `data-mapping.md` | `docs/specs/backtest.md` + `docs/architecture/modules/nautilus-trader.md` + `docs/reference/external-apis/nautilus-trader.md` |
| `docs/✅nautilus_trader/implementation-plan.md` / `open-questions.md` | `docs/roadmap/nautilus-trader/...`              |
| `docs/✅menu-and-footer/{menu-bar,replay-control,footer,mode-switch,file-menu,venue-login-footer}.md`（UI 構造側） | `docs/architecture/modules/ui-shell.md`         |
| `docs/✅menu-and-footer/*`（操作手順）                          | GitHub Wiki                                     |
| `docs/✅menu-and-footer/assets/**`                              | UI 構造図に必要な分は `docs/assets/` へ移送、残りは GitHub Wiki の `assets/` へ |
| `docs/✅<module>/archive/review-fixes-*.md`・`refactor-*.md`    | `docs/decisions/NNNN-*.md` に決定要旨を抽出（原本は archive 据え置きで参照リンク） |
| `docs/plan/distribution-formats/{linux-formats,pip-install-estation}.md` | `docs/development/build-and-release.md` に統合（採用しない案は `docs/plan/❌archive/` に据え置き） |
| `docs/plan/floating-windows/{spec,architecture,implementation-plan,open-questions,README}.md` | 進行中なら `docs/roadmap/ui-shell/`、停止なら `docs/plan/❌archive/` 据え置き判断（実行ステップ 1 で決定） |
| `docs/plan/floating-windows/archive/**`                        | **ADR 抽出候補に含める**（特に `2026-04-29-pre-bevy-rewrite/` は iced/Bevy 採用根拠 ADR の典拠候補）。本体ファイルは `exclude_docs` で MkDocs から隠す。`❌` プレフィックスは付かないので除外ルールの対象外 |
| `docs/plan/❌archive/**`                                       | 据え置き、ADR 抽出候補から除外                  |
| `docs/plan/README.md`                                          | 内容を `docs/index.md` または `docs/roadmap/README.md` に統合 |

## 主要ファイル

実際に編集対象となるファイル:

- `mkdocs.yml` — `nav` を全面書き換え、`exclude_docs` を下記方針表どおり更新、`plugins:` に `redirects:` を追加（中間 PR 期間の旧 URL 救済用）
- `pyproject.toml` または `requirements-docs.txt` — `mkdocs-redirects` を依存に追加（CI の `mkdocs build --strict` import error を防ぐ）
- `mkdocs.yml` の `plugins.redirects.redirect_maps:` — **redirect map の単一累積先**。各 PR は本セクションに `old_path: new_path` 形式（例: `✅python-data-engine/spec.md: specs/data-engine.md`）で 1 行ずつ追記する。別ファイル（`docs/_redirects.yml` 等）は新設しない
- `docs/index.md` — 読者別ナビへ書き換え（区分は「エンジニア / コントリビュータ」の 2 系統。エンドユーザーは GitHub Wiki への導線のみ）
- `docs/` 直下の **全 ✅* ディレクトリ** — リネーム + ファイル分割
- `docs/wiki/**` — GitHub Wiki にコピー後、`docs/wiki/` を削除
- `docs/plan/**` — 上記マッピング表に従って `roadmap/` / `development/` / `❌archive/` に振り分け
- `docs/decisions/0001-*.md` 以降 — 新規 ADR 化（archive・PR・spec から）。`docs/decisions/MANIFEST.md` は番号外 artifact として併設
- `README.md` / `AGENTS.md` / `CLAUDE.md` — 旧 `docs/✅*` への参照を新パスに rewrite（壊れたリンク `docs/✅menu-and-footer/native-menu-bar-impl.md` も同時修正）

### `mkdocs.yml` `exclude_docs` 方針表（再編後）

| パターン        | 方針        | 理由                                               |
| --------------- | ----------- | -------------------------------------------------- |
| `*.json`        | 維持（除外）| 機械可読 artifact は MkDocs に出さない             |
| `wiki/**`       | 削除（再包含） | GitHub Wiki に物理移送し、`docs/wiki/` 自体を削除 |
| `plan/**`       | 削除（再包含 or 廃止）| `docs/plan/` を新構成に振り分け、残骸 `❌archive/` のみ exclude |
| `*/archive/**`  | 維持（除外）| ADR 抽出後の原本据え置き                           |
| `roadmap/**`    | **再包含（nav 公開）** | 進行中の論点は読者が見える方が価値がある          |
| `_migration-ledger.yaml` | 一時的に除外 → 完了後 `decisions/manifest.yaml` に移動。`decisions/MANIFEST.md` は人間向け索引のみ（YAML パースは行わない） | 移送中の中間 artifact |

## 実行ステップ

1. **棚卸し（read-only）**: 全 `docs/✅*/` および `docs/plan/` 配下の各ファイルを開き「どの章が specs / architecture / reference / roadmap / testing / development に属すか」を台帳化。台帳は `docs/_migration-ledger.yaml`（**機械可読 YAML**、一時ファイル、最後に `docs/decisions/MANIFEST.md` へ rename）に書き出す。各エントリは次のスキーマ:
   ```yaml
   - old_path: docs/✅<module>/<file>.md
     old_anchor: "#<section>"        # 任意
     new_path: docs/<area>/<file>.md
     new_anchor: "#<section>"
     source_commit: <sha>
     status: pending|migrated|deferred
   ```
   棚卸し中、`docs/plan/distribution-formats/`・`docs/plan/floating-windows/` の各ファイルが「進行中 / 採用しない / 完了」のどれに当たるかも判定する。判定基準:
   - **進行中**: 直近 90 日以内の commit があり、かつ `open-questions.md` に未解決項目が残っている（→ `roadmap/<topic>/` に移送）
   - **完了**: 実装が main にマージ済みで `open-questions.md` が空（→ `decisions/` に決定要旨を抽出 + 本体は archive 据え置き）
   - **停止**: 直近 90 日 commit 無し or `archive/` 配下のみで本体が空（→ `docs/plan/❌archive/` 据え置き）

   `docs/plan/❌archive/`・`docs/✅<module>/❌archive/` 配下（`❌` プレフィックスあり）は ADR 抽出候補から除外。`docs/plan/floating-windows/archive/`（`❌` 無し）は ADR 抽出候補に含める。
2. **新ディレクトリ骨格作成**: 上の構成で空ディレクトリと各 `README.md` / `index.md` のスケルトンを作る。
3. **コンテンツ移送**: 台帳の各行を「旧 → 新」で実際に移動。1 PR = 1 旧モジュール: `data-engine → tachibana → kabusapi → order → nautilus-trader → menu-and-footer → docs/plan/distribution-formats → docs/plan/floating-windows`（`docs/plan/` 配下は 2 PR に分割）。各 PR の冒頭コミットは **「リネームのみ（内容変更ゼロ）」** に限定し、続くコミットで分割・編集する（git の rename 検知を最大化するため）。`roadmap/<module>/` は集約せず分割を維持するので、PR 同士のホットスポット衝突は起きない。
   各 PR の merge gate:
   - (a) 当該モジュールの台帳行が全て `status: migrated` に到達
   - (b) `mkdocs build --strict` 緑（移送中の中間状態でも成立すること）
   - (c) 旧 `docs/✅<module>/` への外部参照ゼロ（後述 grep 検証）
   - (d) `mkdocs-redirects` plugin で旧 URL → 新 URL の 301 マップを記述
4. **ADR 抽出**: 出典は **repo 内に commit 済みの artifact のみ**を一次ソースとする:
   - `docs/✅<module>/archive/review-fixes-*.md` および `docs/✅<module>/archive/refactor-*.md`
   - 関連する commit message（`git log --grep`）と PR 説明
   - `docs/✅<module>/spec.md` 内の決定記述
   ローカルメモリや `~/.claude/skills/*` 配下の SKILL.md は **補助参照のみ**にとどめ、ADR 本文の根拠としては使わない。出典を repo 内に確定できないものは ADR 化を保留する（`status: deferred`）。`❌` プレフィックス artifact は抽出対象外。
5. **wiki 移送**: 下記「Wiki 移送手順」のチェックリストに沿って `docs/wiki/**` と `docs/wiki/assets/**` を GitHub Wiki に push、ローカルは削除、`mkdocs.yml` の `exclude_docs` を方針表どおりに更新。
6. **`mkdocs.yml` の nav 全面書き換え** + リンク切れチェック（`mkdocs build --strict`）。`exclude_docs` 方針表を反映する。
6.5. **検証スクリプトと CI ゲート整備**:
   - `scripts/verify_migration_manifest.py` 新設: `docs/_migration-ledger.yaml`（実行ステップ 8 後は `docs/decisions/manifest.yaml`）を読み、全 `new_path` の存在 + `new_anchor` 実在を assert。`status: migrated` の旧パスは削除済みであることも検証。`MANIFEST.md` はパース対象外。
   - `scripts/verify_migrated_from.py` 新設: `git ls-files 'docs/✅*' 'docs/plan/*'` の出力を baseline に、新 docs の YAML フロントマター `migrated_from:` 値集合との `comm -23` で差分 0 を検証。
   - `scripts/verify_no_legacy_paths.py` 新設: 検査対象を `README.md AGENTS.md CLAUDE.md docs/ examples/ python/ scripts/ tests/`（`.git/` `site/` `target/` 除外）に限定し、`docs/✅` および互換文字 `[✅✓✔]` + 旧モジュール path string (`python-data-engine` 等の旧プレフィックス付き) を grep して 0 件を assert。
   - `scripts/verify_nav_depth.py` 新設: `mkdocs.yml` の nav 構造を YAML パースし、**nav section（カテゴリ）の最大深さ ≤ 4** を assert。葉ファイルパスのディレクトリ深さは検査対象外（ファイル深さは `roadmap/ui-shell/implementation-plan.md` のように 3 段になり得る）。
   - `.github/workflows/docs.yml` 新設または既存に追加: 上 4 スクリプト + `mkdocs build --strict` を required check 化し、main ブランチ保護に追加。
7. **横断ドキュメント新設**: `architecture/overview.md`・`development/setup.md`・`development/build-and-release.md`（`docs/plan/distribution-formats/` を統合）・`testing/strategy.md`・`contributing/coding-standards.md` を執筆。一次ソースは **repo 内の既存 docs / コード / commit 履歴**（例: `architecture/ipc-schema.md` は `docs/✅python-data-engine/spec.md`・`engine-client/src/dto.rs`・`engine-client/src/lib.rs`・`python/engine/schemas.py`・`proto/engine.proto`・`scripts/check_schema_parity.py` を典拠にする）。SKILL.md は補助参照に限定し、出典セクションには SKILL.md ではなく commit 済みの一次資料へのリンクを書く。
8. **`✅*` ディレクトリ削除と `docs/_migration-ledger.yaml` を `docs/decisions/manifest.yaml` に移動**（`scripts/verify_migration_manifest.py` 緑 + 全行 `status: migrated` を確認してから）。同一 PR で `decisions/MANIFEST.md`（人間向け索引）を新設し、CI スクリプトの参照パスも更新する。

## Wiki 移送手順

GitHub Wiki と MkDocs はリンク・アセット解決が異なるため、機械的にコピーすると参照が壊れる。以下を順守する:

1. 既存アセットの棚卸し: `docs/wiki/assets/**` を全列挙し、参照元（`docs/wiki/**.md` 内の `![...](...)` および `<img src=...>`）を全件 grep で抽出。
2. アセット移送先の決定: GitHub Wiki に画像を置く場合は **専用の `wiki` リポジトリ（`<repo>.wiki.git`）に `assets/` ディレクトリを切って push**。Wiki 内の相対参照は `assets/<file>` に統一する。`docs/wiki/assets/foo.png` を参照していた場合は `assets/foo.png` に rewrite する。
3. リンク書き換え:
   - Markdown 内のページ間リンク `[X](other-page.md)` → GitHub Wiki 形式 `[X](other-page)`（拡張子なし）または `[[other-page]]`
   - `docs/✅*/...` への相対リンクは **MkDocs サイト URL に絶対化** する（Wiki から MkDocs サイトへの cross-link）
   - アンカー（`#section`）は GitHub Wiki の slug 規則に合わせる（日本語見出しは要確認）
4. OG 画像 / `mkdocs.yml` 内の社会向けメタが `docs/wiki/assets/*` を指していないことを確認（指していれば `docs/assets/` に複製を残す）。
5. push 後に内部リンク・画像を `lychee --offline` または `markdown-link-check` で機械検査（`gh api` には link checker が存在しないため使わない。`gh api` はページ列挙のみに使う）。さらに Wiki UI 上で目視確認する。
6. ローカル `docs/wiki/` を `git rm -r` し、`mkdocs.yml` の `exclude_docs` から `wiki/**` を除去。
7. `docs/index.md` から GitHub Wiki トップへの導線リンクを追加。

## 履歴保持方針

旧 ✅* は「リネーム + ファイル分割（多対一・一対多）」が大半で、`git log --follow` の rename 検知はそもそも成立しないケースが多い。よって **rename 追跡を acceptance に置かない**。代わりに以下の **manifest 追跡**で履歴保持を担保する:

- `docs/_migration-ledger.yaml`（最終的に `docs/decisions/manifest.yaml` として残す。ADR ナンバリング体系外の番号無し artifact 扱い。`decisions/MANIFEST.md` は同 YAML を人間向けに索引するだけの md ファイル）に、移送した全ペアを上記 YAML スキーマで記録する。これが one-to-one でない章にも履歴起点を提供する第一級の artifact となる。
- 各 PR の冒頭コミットは **内容変更ゼロのリネームのみ**に限定し、後続コミットで分割編集する。one-to-one で済むファイル（spec.md→specs/<module>.md など）はこれで `--follow` も通る。
- 多対一・一対多になる章には、新ファイルの先頭に YAML フロントマター `migrated_from:` で旧パス（複数可）を記録する。これは grep 可能な機械可読の典拠となる。`scripts/verify_migrated_from.py`（実行ステップ 6.5）で旧ファイル一覧との差分 0 を CI で検証する。

## 検証

すべて CI で機械実行可能（実行ステップ 6.5 で整備）。手動目視は補助に留める。

- `mkdocs build --strict` がリンク切れ 0 で通ること（**Wiki 移送 PR では `exclude_docs` 変更前 / 変更後の両 commit で別個に緑**）
- `mkdocs serve` でローカルプレビューし、新 nav の 4 階層以内に主要ドキュメントが揃うこと（`scripts/verify_nav_depth.py` で機械化）
- `docs/decisions/README.md` から各 ADR（`status: accepted` のみ）へ全リンクが通ること。`status: deferred` の ADR は本文を持たず、解除時に出典 URL を必須化（`scripts/check_adr_status.py`）
- GitHub Actions の MkDocs デプロイ（`gh-pages` 等）が緑であること
- **manifest（`docs/decisions/MANIFEST.md`）の全行が新パスに到達できること**: `scripts/verify_migration_manifest.py` で `new_path` 実在 + `new_anchor` 存在 + `status` 整合を assert
- 新ファイル冒頭の `migrated_from:` フロントマターが、旧 ✅* および `docs/plan/` の現存ファイルすべてを少なくとも 1 箇所以上カバーしていること: `scripts/verify_migrated_from.py` が `git ls-files 'docs/✅*' 'docs/plan/*'` と `migrated_from:` 値集合の `comm -23` で差分 0 を assert
- one-to-one リネーム分のみ `git log --follow` で履歴が辿れること（多対一・一対多はこの検証から除外）
- 旧 `docs/✅*` への参照が残っていないこと: `scripts/verify_no_legacy_paths.py` が `README.md AGENTS.md CLAUDE.md docs/ examples/ python/ scripts/ tests/` を対象に `[✅✓✔]` および旧モジュール path string + GitHub Pages の URL エンコード形（`%E2%9C%85` 等）を grep し 0 件を assert
- 各 PR の冒頭で旧 URL → 新 URL の `mkdocs-redirects` マッピングが追加されていること（中間状態の外部リンク救済）
- invariant-tests の統合（`docs/✅*/invariant-tests.md` → `docs/testing/invariants.md`）で項目欠損がないこと: 各 invariant 項目に ID（`INV-<MODULE>-NNN`）を付与し manifest で 1:1 対応を取る
- **Wiki 側**: 移送後の GitHub Wiki で全画像が表示され、ページ間リンクが 404 にならないこと（`lychee --offline` で wiki clone を検査 + 手動目視）

## 既存資産の再利用（出典ポリシー: repo 内 artifact 限定）

新ドキュメントの **本文の典拠は repo 内に commit 済みの artifact に限定**する。ローカルメモリ・SKILL.md は補助的な「執筆の足場」としてのみ使い、出典セクションには載せない。

| 新ドキュメント                      | 一次ソース（repo 内）                                                                  | 補助参照（本文典拠としては不可）           |
| ----------------------------------- | -------------------------------------------------------------------------------------- | ------------------------------------------ |
| `architecture/ipc-schema.md`        | `python/engine/schemas.py`・`engine-client/src/dto.rs`・`engine-client/src/lib.rs`（SCHEMA_MAJOR/MINOR 履歴コメントが SoT）・`proto/engine.proto`・`scripts/check_schema_parity.py`・`docs/✅python-data-engine/spec.md` | `ipc-schema-check` スキル                  |
| `reference/ipc-protocol.md`         | 同上。schemas.py + dto.rs + proto/engine.proto が現行 IPC 主系統（gRPC transport は `engine-client/src/grpc_transport.rs`・`python/engine/server_grpc.py` で実利用） | —                                          |
| `contributing/coding-standards.md`  | `AGENTS.md`・`CLAUDE.md`・`Cargo.toml` の lint 設定・既存 commit                       | `coding-standards` スキル                  |
| `testing/strategy.md`               | 既存テストファイル群・`docs/✅*/invariant-tests.md`・CI 設定                            | `bug-postmortem`・`tdd-workflow` スキル    |
| `testing/e2e.md`                    | 既存 E2E テスト実装（`ReplaySession` / `LiveSession`）                                 | `e2e-testing` スキル                       |
| `development/troubleshooting.md`    | `docs/✅<module>/archive/review-fixes-*.md`・既存 commit のバグ修正履歴                | `verification-loop` スキル                 |
| ADR 0001 (Rust↔Python 境界)         | `docs/✅python-data-engine/archive/refactor-rust-python-boundary-2026-05-01.md`・関連 PR | —                                          |
| ADR 0002 (IPC schema versioning)    | `engine-client/src/lib.rs` の SCHEMA_MAJOR/MINOR 履歴コメント（**SoT**）・`python/engine/schemas.py`・`proto/engine.proto`・`scripts/check_schema_parity.py`・`docs/✅python-data-engine/schemas/CHANGELOG.md` | —                                          |
| ADR 0003 (AI/ML 非同梱)             | **ADR 化保留**: repo 内に出典が見つからなければ ADR 化しない（要確認）                | ローカルメモリ                             |
| ADR 0004 (ユーザー戦略は自己責任)   | **ADR 化保留**: 同上                                                                   | ローカルメモリ                             |
| ADR 0005 (将来の Python 単独モード) | **ADR 化保留**: 同上                                                                   | ローカルメモリ                             |

ADR 0003〜0005 は、対応するコード／spec／PR／issue が repo 内に commit 済みでない限り **ADR 化を保留** (`status: deferred`)する。先に該当方針を spec か README に書き起こす PR を別途立て、それを典拠にしてから ADR 化する。

### 採用根拠 ADR の棚卸し（実行ステップ 1 のサブタスク）

「なぜその技術を採用したか」が再編後に発見不能になるリスクを避けるため、棚卸しフェーズで次の採用根拠の典拠を repo 内から探す。見つかれば ADR 化、見つからなければ `status: deferred` で起票だけ行う。

- **Rust + Python 二言語構成の採用根拠** → ADR 0006 候補。典拠: `Cargo.toml`・`pyproject.toml`・初期 commit・`docs/✅python-data-engine/archive/refactor-rust-python-boundary-2026-05-01.md`
- **Rust 言語選択の根拠** → ADR 0007 候補。典拠: 上記 + `rust-toolchain.toml`
- **Python 採用根拠（data engine 側）** → ADR 0008 候補。典拠: `pyproject.toml`・`python/engine/` 初期 commit
- iced / Bevy 採用根拠 → `docs/plan/floating-windows/archive/2026-04-29-pre-bevy-rewrite/` に決定経緯がある可能性
- nautilus_trader 採用根拠 → `docs/✅nautilus_trader/spec.md`・関連 PR
- tachibana / kabusapi の二重実装根拠 → `docs/✅tachibana/spec.md`・`docs/✅kabusapi/spec.md`・関連 PR
- AI/ML 非同梱方針 → `docs/plan/❌archive/wandb-vision.md` 等の typed-source ではない参照のみ。typed-source が無ければ `deferred`

### ADR ステータス遷移ルール（`decisions/README.md` に明記）

| 遷移                  | トリガ                                                                                     |
| --------------------- | ------------------------------------------------------------------------------------------ |
| proposed → accepted   | PR で 1 名以上の reviewer 承認 + 出典 commit が main に到達後                              |
| proposed → deferred   | 出典が repo 内に確定できないと判明したとき（本文は空、解除待ち）                            |
| deferred → accepted   | 解除時は YAML フロントマター `source_commit:` に出典 commit SHA が必須                     |
| accepted → superseded | 新 ADR の YAML フロントマター `supersedes: NNNN` で参照されたとき自動                      |

`scripts/check_adr_status.py`（実行ステップ 6.5 で新設）の責務:
- `status: deferred` の ADR は本文を持たない（ヘッダのみ）ことを assert
- `status: accepted` の ADR は `source_commit:` フロントマターが必須
- `supersedes:` で参照される ADR が `superseded` になっているかの整合性検査

## 実装メモ

実行ステップ 1（棚卸し）+ ステップ 2（新ディレクトリ骨格作成）の判断記録（2026-05-08）:

- **対象ファイル件数**: `git -c core.quotepath=false ls-files 'docs/✅*' 'docs/plan/distribution-formats/*' 'docs/plan/floating-windows/*'` で 180 件を baseline 集合に確定。
- **`docs/_migration-ledger.yaml` 総エントリ数**: 191（180 件以上の要件を満たす）。spec.md の章分割移送（例: `docs/✅python-data-engine/spec.md` → `data-engine.md` + `replay.md`）と `menu-and-footer/{ui-files}` の二重移送（ui-shell.md + GitHub Wiki）で重複行を持つため 180 を超える。
- **判定: `docs/plan/floating-windows/`** → **進行中** と判定。理由: 直近 commit が 2026-05-06（90 日以内）、`open-questions.md` が 181 行と未解決項目あり。本体（`spec.md` / `architecture.md` / `implementation-plan.md` / `open-questions.md` / `README.md`）は `docs/roadmap/ui-shell/` に移送、`archive/2026-04-29-pre-bevy-rewrite/` 配下と `archive/review-fixes-2026-04-29.md` は ADR 抽出候補（status: deferred）として起票。
- **判定: `docs/plan/distribution-formats/`** → 両ファイルとも `docs/development/build-and-release.md` への統合先として `status: pending` で起票（採用判定は移送 PR 内で行う）。
- **`❌` プレフィックス除外**: 3 件（`docs/✅menu-and-footer/archive/❌wandb-login-dialog-impl.md`、`docs/✅python-data-engine/archive/❌replay-file-switch-stale-panes-approach-{a,c}.md`）を `status: excluded` で台帳に残置。`new_path` は旧パスと同値（移送なし）。
- **ADR 番号の自動割当範囲**: 0009〜0133（125 件、`status: deferred`）。0001〜0008 は計画書で予約済みのため割当除外。番号確定は ADR 抽出 PR（実行ステップ 4）で再割当する余地を残す。
- **新規スケルトン作成数**: 21 ファイル（19 ディレクトリ README + `decisions/README.md` + `decisions/MANIFEST.md`）。`decisions/README.md` には ADR ステータス遷移ルール表を転記済み。
- **既存ファイル無改変原則**: `docs/✅*` 配下および `docs/index.md` / `mkdocs.yml` は一切変更していない（このステップのスコープ外）。
