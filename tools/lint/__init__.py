"""issue #42 Phase 6: lint scripts for live-strategy invariants.

Scripts in this package are CI-enforced lints (see
``.github/workflows/python-tests.yml``) and pin invariants that are easy to
regress silently:

- ``check_examples_readme``: ``examples/README.md`` exposes a "ライブで動かす"
  section so users discover the live launch path (受け入れ基準 #3 / #4).
- ``check_live_login_call``: ``python/engine/live_session_cli.py`` always calls
  ``LiveSession.login()`` before ``LiveSession.run()`` (受け入れ基準 #10).
"""
