#!/usr/bin/env python3
"""verify_nav_depth.py

Assert that the section nesting depth of `nav:` in mkdocs.yml is ≤ 4.

A 'section' is a dict node in the nav tree. Leaf entries (strings, file paths)
do not count toward the section depth.

Example:
  nav:
    - A:
        - B:
            - C:
                - D: file.md   # 4 sections deep — OK
                - E:
                    - F: x.md  # 5 sections — FAIL

Exit codes:
  0  depth ≤ max
  1  depth > max
  2  parse failure
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Iterable

import yaml


REPO_ROOT_DEFAULT = Path(__file__).resolve().parent.parent
MAX_DEPTH = 4


class _PermissiveLoader(yaml.SafeLoader):
    pass


def _ignore_unknown(loader, tag_suffix, node):  # noqa: ANN001
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    return None


_PermissiveLoader.add_multi_constructor("!", _ignore_unknown)
_PermissiveLoader.add_multi_constructor("tag:yaml.org,2002:python/", _ignore_unknown)


def _load_mkdocs(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    # Strip mkdocs-specific !!python/name and similar tags by replacing them
    # with a safe placeholder. Simpler than full constructors for our purpose.
    text = re.sub(r"!![a-zA-Z_/]+", "", text)
    text = re.sub(r"!\S+", "", text)
    try:
        data = yaml.load(text, Loader=_PermissiveLoader)
    except yaml.YAMLError as e:
        print(f"ERROR: failed to parse {path}: {e}", file=sys.stderr)
        sys.exit(2)
    if not isinstance(data, dict):
        print(f"ERROR: {path} did not parse to a mapping", file=sys.stderr)
        sys.exit(2)
    return data


def _max_section_depth(node, current: int = 0) -> int:
    """Return max nesting depth counting only dict (section) levels."""
    if isinstance(node, dict):
        # Each dict entry: key is a section name, value is its children.
        depths = [current]
        for v in node.values():
            depths.append(_max_section_depth(v, current + 1))
        return max(depths)
    if isinstance(node, list):
        depths = [current]
        for item in node:
            depths.append(_max_section_depth(item, current))
        return max(depths)
    # leaf (str, None, etc.) — does not add depth
    return current


def verify(repo_root: Path, max_depth: int = MAX_DEPTH) -> tuple[int, int]:
    mkdocs_yml = repo_root / "mkdocs.yml"
    if not mkdocs_yml.is_file():
        print(f"ERROR: {mkdocs_yml} not found", file=sys.stderr)
        sys.exit(2)
    cfg = _load_mkdocs(mkdocs_yml)
    nav = cfg.get("nav")
    if nav is None:
        print("WARN: no nav section in mkdocs.yml; nothing to check")
        return 0, max_depth
    depth = _max_section_depth(nav, current=0)
    return depth, max_depth


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT_DEFAULT)
    parser.add_argument("--max-depth", type=int, default=MAX_DEPTH)
    args = parser.parse_args(list(argv) if argv is not None else None)
    depth, limit = verify(args.repo_root.resolve(), args.max_depth)
    print(f"nav section depth = {depth}  (limit = {limit})")
    if depth > limit:
        print(f"FAILED: nav section depth {depth} exceeds limit {limit}", file=sys.stderr)
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
