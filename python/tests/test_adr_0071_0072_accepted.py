"""issue #42 Phase 6: ADR 0071 / 0072 が accepted で本文起票されていることを pin.

統一決定 #6 / 受け入れ基準 #5 対応の正の pin。``scripts/check_adr_status.py`` は
"deferred は本文 < 200 chars" / "accepted には source_commit が必要" という
**一般的な status invariant** を検証するだけで、特定 ADR の status を assert しない。
本テストは「0071 / 0072 が accepted のままであること」を回帰防止として直接 pin する。

issue #42 で deferred → accepted 化したあと、誤って `status: deferred` に戻すと
ここで赤くなる（CI の python-tests job が止める）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
ADR_DIR = REPO_ROOT / "docs" / "decisions"


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """``---`` で囲まれた YAML frontmatter を抽出する."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        pytest.fail("ADR file missing YAML frontmatter")
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        pytest.fail("ADR frontmatter has no closing ---")
    fm = yaml.safe_load("\n".join(lines[1:end_idx]))
    body = "\n".join(lines[end_idx + 1 :])
    return fm, body


def _read_adr(adr_id: str) -> tuple[dict, str]:
    matches = list(ADR_DIR.glob(f"{adr_id}-*.md"))
    assert len(matches) == 1, (
        f"expected exactly one ADR for id {adr_id!r}, found {[p.name for p in matches]}"
    )
    text = matches[0].read_text(encoding="utf-8")
    return _split_frontmatter(text)


@pytest.mark.parametrize("adr_id", ["0071", "0072"])
def test_adr_status_accepted(adr_id: str) -> None:
    """0071 / 0072 は status: accepted（issue #42 統一決定 #6）."""
    fm, _body = _read_adr(adr_id)
    assert str(fm.get("status", "")).strip() == "accepted", (
        f"ADR {adr_id} の status が accepted ではありません "
        f"(現在: {fm.get('status')!r})。受け入れ基準 #5 違反。"
    )


@pytest.mark.parametrize("adr_id", ["0071", "0072"])
def test_adr_has_source_commit(adr_id: str) -> None:
    """accepted ADR には source_commit: が必須."""
    fm, _body = _read_adr(adr_id)
    sc = fm.get("source_commit")
    assert sc, (
        f"ADR {adr_id} は accepted だが source_commit が空です。"
        " check_adr_status.py の invariant 違反。"
    )


@pytest.mark.parametrize("adr_id", ["0071", "0072"])
def test_adr_body_is_not_commented_out(adr_id: str) -> None:
    """deferred 時代の HTML コメント雛形がそのまま残っていないこと.

    body 全体が `<!-- ... -->` で囲まれている＝コメントアウト＝中身が空 と判定する。
    accepted のはずなのに本文が見えていないと、ユーザーが内容を読めず昇格の意味がない。
    """
    _fm, body = _read_adr(adr_id)
    # HTML コメントを strip した残りに、最低限「## Status」「## Decision」相当の
    # ATX 見出しが見える必要がある。
    no_comments = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL).strip()
    assert "## Status" in no_comments or "## status" in no_comments.lower(), (
        f"ADR {adr_id} の本文に '## Status' 見出しがありません。"
        " deferred 時代のコメントアウト雛形に戻っていないか確認してください。"
    )
    assert "## Decision" in no_comments or "## decision" in no_comments.lower(), (
        f"ADR {adr_id} の本文に '## Decision' 見出しがありません。"
        " 統一決定 #6 / 受け入れ基準 #5 違反。"
    )


def test_adr_0071_title_includes_live_strategy_gui() -> None:
    fm, _ = _read_adr("0071")
    title = str(fm.get("title", ""))
    assert "Live Strategy" in title or "live strategy" in title.lower(), (
        f"ADR 0071 の title 文字列が想定外です: {title!r}"
    )


def test_adr_0072_title_includes_execute_live_strategy() -> None:
    fm, _ = _read_adr("0072")
    title = str(fm.get("title", ""))
    assert "Execute Live Strategy" in title or "execute live strategy" in title.lower(), (
        f"ADR 0072 の title 文字列が想定外です: {title!r}"
    )
