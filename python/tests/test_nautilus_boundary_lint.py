"""横断 D2-H1: nautilus 互換境界 lint テスト。

IPC 層 (dto.rs / schemas.py) と Rust UI 層 (src/) に立花固有禁止語が
漏洩していないことを確認する。

立花固有用語は python/engine/exchanges/tachibana_*.py にのみ存在すべきであり、
Rust/IPC の境界をまたいではならない（architecture.md §6 の隔離方針）。
"""

from pathlib import Path

REPO_ROOT = Path(__file__).parents[2]

# spec.md §6 の禁止語リスト（IPC/Rust UI 層に漏洩してはいけない立花固有用語）
FORBIDDEN_WORDS = [
    "sCLMID",
    "p_sd_date",
    "Zyoutoeki",
    "p_no",
    "p_eda_no",
    "sGenkinShinyouKubun",
    "sZyoutoekiKazeiC",
    "CLMKabuNewOrder",
    "CLMKabuCorrectOrder",
    "CLMKabuCancelOrder",
]


def _strip_comments_rs(content: str) -> str:
    """Rust の行コメント (//) を除外する。"""
    lines = [line for line in content.splitlines() if not line.strip().startswith("//")]
    return "\n".join(lines)


def _strip_comments_py(content: str) -> str:
    """Python の行コメント (#) を除外する。"""
    lines = [line for line in content.splitlines() if not line.strip().startswith("#")]
    return "\n".join(lines)


def test_dto_rs_has_no_tachibana_specific_terms():
    """engine-client/src/dto.rs に立花固有禁止語が含まれないこと。"""
    dto_path = REPO_ROOT / "engine-client" / "src" / "dto.rs"
    if not dto_path.exists():
        return  # ファイルが存在しない場合はスキップ

    content = _strip_comments_rs(dto_path.read_text(encoding="utf-8"))

    violations = []
    for word in FORBIDDEN_WORDS:
        if word in content:
            violations.append(f"  dto.rs contains forbidden term: '{word}'")

    assert not violations, (
        "IPC layer (dto.rs) must not contain Tachibana-specific terms:\n"
        + "\n".join(violations)
    )


def test_schemas_py_ipc_classes_have_no_tachibana_order_wire_terms():
    """python/engine/schemas.py の IPC クラスに立花注文ワイヤー固有語が含まれないこと。

    schemas.py は IPC メッセージ（Rust ↔ Python）と立花 REST レスポンス解析の
    両方を含む。REST レスポンス用クラス（MarketPriceResponse など）は
    sCLMID のような立花固有フィールドを合法的に持つ。

    ここでは「立花注文ワイヤーのみに存在すべき用語」が IPC メッセージクラスに
    漏洩していないことを確認する（より精密なチェック）。
    """
    # 注文ワイヤー専用語（IPC の Command/Event 層に漏洩してはいけない）
    order_wire_terms = [
        "sGenkinShinyouKubun",
        "sZyoutoekiKazeiC",
        "CLMKabuNewOrder",
        "CLMKabuCorrectOrder",
        "CLMKabuCancelOrder",
        "p_sd_date",
        "p_eda_no",
        "Zyoutoeki",
    ]

    schemas_path = REPO_ROOT / "python" / "engine" / "schemas.py"
    if not schemas_path.exists():
        return

    content = _strip_comments_py(schemas_path.read_text(encoding="utf-8"))

    violations = []
    for word in order_wire_terms:
        if word in content:
            violations.append(f"  schemas.py contains forbidden order-wire term: '{word}'")

    assert not violations, (
        "IPC layer (schemas.py) must not contain Tachibana order-wire-specific terms:\n"
        + "\n".join(violations)
    )


def test_rust_ui_src_has_no_tachibana_specific_terms():
    """src/ ディレクトリ（Rust UI 層）に立花固有禁止語が含まれないこと。"""
    src_dir = REPO_ROOT / "src"
    if not src_dir.exists():
        return

    violations = []
    for rs_file in sorted(src_dir.rglob("*.rs")):
        content = _strip_comments_rs(
            rs_file.read_text(encoding="utf-8", errors="replace")
        )
        for word in FORBIDDEN_WORDS:
            if word in content:
                violations.append(f"  {rs_file.relative_to(REPO_ROOT)}: '{word}'")

    assert not violations, (
        "Rust UI layer (src/) must not contain Tachibana-specific terms:\n"
        + "\n".join(violations)
    )


def test_engine_client_lib_rs_has_no_tachibana_specific_terms():
    """engine-client/src/lib.rs に立花固有禁止語が含まれないこと。"""
    lib_path = REPO_ROOT / "engine-client" / "src" / "lib.rs"
    if not lib_path.exists():
        return

    content = _strip_comments_rs(lib_path.read_text(encoding="utf-8"))

    violations = []
    for word in FORBIDDEN_WORDS:
        if word in content:
            violations.append(f"  lib.rs contains forbidden term: '{word}'")

    assert not violations, (
        "IPC layer (lib.rs) must not contain Tachibana-specific terms:\n"
        + "\n".join(violations)
    )
