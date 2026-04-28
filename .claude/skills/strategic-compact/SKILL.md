---
name: strategic-compact
description: Suggests manual context compaction at logical intervals to preserve context through task phases rather than arbitrary auto-compaction.
origin: ECC
---

# Strategic Compact Skill

Suggests manual `/compact` at strategic points in your workflow rather than relying on arbitrary auto-compaction.

## When to Activate

- Running long sessions that approach context limits (200K+ tokens)
- Working on multi-phase tasks (research → plan → implement → test)
- Switching between unrelated tasks within the same session
- After completing a major milestone and starting new work
- When responses slow down or become less coherent (context pressure)

## Why Strategic Compaction?

Auto-compaction triggers at arbitrary points:
- Often mid-task, losing important context
- No awareness of logical task boundaries
- Can interrupt complex multi-step operations

Strategic compaction at logical boundaries:
- **After exploration, before execution** — Compact research context, keep implementation plan
- **After completing a milestone** — Fresh start for next phase
- **Before major context shifts** — Clear exploration context before different task

## このプロジェクトでのフック使用について

このプロジェクトでは Node.js が利用できないため、`suggest-compact.js` スクリプトは動作しません。
自動的なコンパクト提案フックは**使用していません**。

長いセッションではフェーズの区切りを意識して、手動で `/compact` を実行してください。

## Compaction Decision Guide

Use this table to decide when to compact:

| Phase Transition | Compact? | Why |
|-----------------|----------|-----|
| Research → Planning | Yes | Research context is bulky; plan is the distilled output |
| Planning → Implementation | Yes | `cargo check` でコンパイル確認後、コンテキストを整理してから実装開始 |
| Implementation → Testing | Maybe | `cargo test` 実行前後が自然な境界。テストが最近のコードを参照するなら保持 |
| Debugging → Next feature | Yes | デバッグトレースは次の機能開発のノイズになる |
| Mid-implementation | No | 変数名・ファイルパス・部分的な状態を失うコストが高い |
| After a failed approach | Yes | 行き止まりの推論を整理してから新しいアプローチを試みる |

## What Survives Compaction

Understanding what persists helps you compact with confidence:

| Persists | Lost |
|----------|------|
| CLAUDE.md instructions | Intermediate reasoning and analysis |
| TodoWrite task list | File contents you previously read |
| Memory files (`~/.claude/memory/`) | Multi-step conversation context |
| Git state (commits, branches) | Tool call history and counts |
| Files on disk | Nuanced user preferences stated verbally |

## Best Practices

1. **Compact after planning** — Once plan is finalized in TodoWrite or `docs/`, compact to start fresh
2. **Compact after debugging** — Clear error-resolution context before continuing
3. **Don't compact mid-implementation** — Preserve context for related changes
4. **Write before compacting** — Save important context to files or memory before compacting
5. **Use `/compact` with a summary** — Add a custom message: `/compact Focus on implementing X next`

## Related

- `verification-loop` — Run after implementation phases to verify quality gates
- `agent-introspection-debugging` — Use when agent is looping before compacting