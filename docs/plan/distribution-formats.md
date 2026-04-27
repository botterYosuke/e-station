# Linux 配布形式の選定 (T4.d)

> 作成: 2026-04-25 / Phase 7 T4.d 判断記録
>
> 関連: [phase-7-ui-regression-remediation.md](./✅python-data-engine/phase-7-ui-regression-remediation.md) §T4.d

## 1. 結論

**現状の `flowsurface-x86_64-linux.tar.gz` (および `aarch64`) を維持し、AppImage / Flatpak は採用しない。**

将来的にユーザー要望が複数件発生した時点で再評価する。再評価の目安は本ドキュメント §4 を参照。

## 2. 検討対象

| 形式 | 提供物 | 配布先 | 必要なホスト依存 |
|---|---|---|---|
| **tar.gz (現行)** | `bin/flowsurface` + `bin/flowsurface-engine` + `assets/` | GitHub Releases | glibc, Vulkan loader, fontconfig |
| **AppImage** | 単一実行ファイル (FUSE マウント) | GitHub Releases | FUSE 2 (Ubuntu 22.04+ は FUSE 3 のみで要 `libfuse2`) |
| **Flatpak** | sandbox 化アプリ (Flathub or 自前 repo) | Flathub | flatpak runtime (`org.freedesktop.Platform`) |

## 3. 判断根拠

### 3.1 採用しない理由 — AppImage

1. **ビルド複雑度の増加**
   - `linuxdeploy` + `linuxdeploy-plugin-gtk` 等のツールチェーン導入が必要。
   - `wgpu` / Vulkan / fontconfig / icu の bundling パスを別途解決する必要があり、`scripts/package-linux.sh` の単純さが失われる。
2. **glibc 互換問題は解決しない**
   - AppImage は ABI を bundle するわけではないため、ビルド時の glibc が古いディストリ（CentOS 7 系）でしか動かない問題は依然残る。
   - 現行 tar.gz と「ビルドホスト依存」の点で本質的な改善にならない。
3. **FUSE 依存**
   - Ubuntu 22.04 / Fedora 36 以降は `libfuse2` が default で入っておらず、利用者側で別途 install が必要。tar.gz より UX が悪化するケースがある。
4. **ユーザー要望なし**
   - GitHub Issues / Discord 等で AppImage を求める明示的な要望は現時点で 0 件 (2026-04-25 現在)。

### 3.2 採用しない理由 — Flatpak

1. **sandbox とアプリ機能の衝突が大きい**
   - flowsurface は **secret-service (`keyring` crate)** で取引所トークンを保存する。Flatpak では `--talk-name=org.freedesktop.secrets` の portal 設定が必要で、ホスト keyring 連携の動作不整合が起きやすい。
   - **Vulkan / wgpu** バックエンドの GPU アクセスは `--device=dri` + 適切な runtime version 選択が必要で、ハード環境差異の debug 工数が増える。
   - **WebSocket 大量接続** (5 venue 同時接続 + Python サブプロセス) は portal 経由ではないため動作するが、IPC 用の loopback ポート (19876 等) が portal の network 設定により挙動を変える可能性がある。
2. **manifest 維持コスト**
   - `org.freedesktop.Platform` のメジャーバージョン (例: `23.08`) ごとに依存 runtime の追従が必要。
   - flowsurface は CPython runtime + PyInstaller bundle を使うため、Flatpak runtime に組み込まれている Python と整合させる必要があり、`flowsurface-engine` を別途 manifest 化するか、bundle 済みバイナリを `extra-data` で取り込む選択が必要。いずれも tar.gz より維持工数が大幅に増える。
3. **配布チャネル構築コスト**
   - Flathub への登録は審査プロセスがあり、リジェクト時の修正サイクルで開発リソースを消費する。自前 repo はユーザー側で `flatpak remote-add` が必要となり、tar.gz より導入手順が長い。
4. **ユーザー要望なし**（同上）。

### 3.3 tar.gz を維持する理由

1. **ビルド・配布ともに単純** — [scripts/package-linux.sh](../../scripts/package-linux.sh) は `cargo build` → `bin/` レイアウト → `tar czvf` の 3 ステップ。
2. **ホスト keyring・GPU・network が透過的** — sandbox なしで `keyring` / `wgpu` / loopback IPC が問題なく動く。
3. **現行 Phase 6 で baseline 計測・cold-start 計測も tar.gz 前提で済む** — フォーマットを増やすたびに計測対象も増える。
4. **`engine-client` が `flowsurface-engine` を相対パスで探す** ([engine-client/src/process.rs](../../engine-client/src/process.rs) の `EngineCommand::resolve`) ため、`bin/` 隣接配置で完結する。AppImage / Flatpak ではこの探索パスを別途調整する必要がある。

## 4. 再評価の目安

以下のいずれかが発生した時点で本判断を見直す:

- [ ] AppImage 化を求める Issue / Discussion が **3 件以上** 蓄積される
- [ ] Flatpak 化（または Flathub 公開）を求める要望が **3 件以上** 蓄積される
- [ ] tar.gz 配布が glibc / fontconfig 不整合で動かないユーザー報告が **複数ディストリ** で発生する
- [ ] flowsurface が GUI shop（GNOME Software / KDE Discover）への露出を戦略目標として持つようになる

再評価時は本ドキュメントを更新し、必要であれば `docs/plan/` 配下に独立 phase として計画を起こす。

## 5. 参考

- 現行 Linux パッケージスクリプト: [scripts/package-linux.sh](../../scripts/package-linux.sh)
- Engine binary 探索ロジック: `engine-client/src/process.rs::EngineCommand::resolve`
- Phase 6 配布まわり完了条件: [implementation-plan.md](./✅python-data-engine/implementation-plan.md) §フェーズ 6
