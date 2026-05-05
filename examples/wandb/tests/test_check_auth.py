"""
RED phase tests for examples/wandb/check_auth.py.

Tests are written BEFORE the implementation (TDD).
wandb module is monkeypatched -- no actual wandb install required.
"""
import importlib
import json
import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure examples/wandb is importable from any cwd
EXAMPLES_WANDB = Path(__file__).resolve().parent.parent
if str(EXAMPLES_WANDB) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_WANDB))


def _run_check_auth(env: dict, netrc_data=None, wandb_viewer=None, wandb_raises=False):
    """
    Execute check_auth.main() in an isolated context and return the parsed JSON dict.
    Uses monkeypatching instead of subprocess so wandb is not required to be installed.

    netrc_data:
      None            -> FileNotFoundError (no netrc file)
      "parse_error"   -> NetrcParseError
      dict            -> mapping host -> (user, acct, passwd)  (or None for missing host)
    """
    import netrc as netrc_module

    # Build a fake wandb module
    fake_wandb = types.ModuleType("wandb")
    if wandb_raises:
        fake_wandb.Api = MagicMock(side_effect=TimeoutError("simulated timeout"))
    else:
        fake_viewer = MagicMock()
        fake_viewer.username = wandb_viewer
        fake_api_inst = MagicMock()
        fake_api_inst.viewer = fake_viewer
        fake_wandb.Api = MagicMock(return_value=fake_api_inst)

    captured_lines: list[str] = []

    def fake_print(*args, **kwargs):
        f = kwargs.get("file", None)
        if f is None or f is sys.stdout:
            captured_lines.append(" ".join(str(a) for a in args))

    def _reload_and_run():
        if "check_auth" in sys.modules:
            del sys.modules["check_auth"]
        import check_auth  # noqa: F401  (imported for side-effect / attribute access)
        importlib.reload(check_auth)
        check_auth.main()

    patched_env = patch.dict(os.environ, env, clear=True)
    patched_sys_modules = patch.dict(sys.modules, {"wandb": fake_wandb})
    patched_print = patch("builtins.print", fake_print)

    if netrc_data is None:
        def _no_netrc(*a, **kw):
            raise FileNotFoundError("no netrc")
        patched_netrc = patch.object(netrc_module, "netrc", _no_netrc)
    elif netrc_data == "parse_error":
        def _bad_netrc(*a, **kw):
            raise netrc_module.NetrcParseError("bad netrc file")
        patched_netrc = patch.object(netrc_module, "netrc", _bad_netrc)
    else:
        fake_netrc_obj = MagicMock()
        fake_netrc_obj.authenticators = lambda host: netrc_data.get(host)
        patched_netrc = patch.object(netrc_module, "netrc", return_value=fake_netrc_obj)

    with patched_env, patched_sys_modules, patched_netrc, patched_print:
        try:
            _reload_and_run()
        except SystemExit as exc:
            # main() calls sys.exit(0) at the end; that's expected.
            assert exc.code in (0, None), f"Expected exit 0, got {exc.code}"

    assert len(captured_lines) >= 1, (
        f"check_auth.main() produced no stdout output. captured={captured_lines}"
    )
    return json.loads(captured_lines[0])


# ---------------------------------------------------------------------------
# 1. WANDB_API_KEY env set -> authenticated=true, method="env"
# ---------------------------------------------------------------------------

def test_wandb_api_key_env_returns_authenticated_env():
    result = _run_check_auth(env={"WANDB_API_KEY": "fake-api-key-1234"})
    assert result["authenticated"] is True
    assert result["method"] == "env"
    assert result["error"] is None


# ---------------------------------------------------------------------------
# 2. No env, no netrc -> authenticated=false, method="none"
# ---------------------------------------------------------------------------

def test_no_env_no_netrc_returns_unauthenticated():
    result = _run_check_auth(env={})
    assert result["authenticated"] is False
    assert result["method"] == "none"
    assert result["username"] is None


# ---------------------------------------------------------------------------
# 3. No env, netrc has api.wandb.ai entry -> authenticated=true, method="netrc"
# ---------------------------------------------------------------------------

def test_netrc_with_entry_returns_authenticated_netrc():
    netrc_data = {"api.wandb.ai": ("alice", None, "fake-pass")}
    result = _run_check_auth(env={}, netrc_data=netrc_data, wandb_viewer="alice")
    assert result["authenticated"] is True
    assert result["method"] == "netrc"


# ---------------------------------------------------------------------------
# 4. netrc parse error -> authenticated=false, error field is set
# ---------------------------------------------------------------------------

def test_netrc_parse_error_returns_unauthenticated_with_error():
    result = _run_check_auth(env={}, netrc_data="parse_error")
    assert result["authenticated"] is False
    assert result["error"] is not None
    assert len(result["error"]) > 0


# ---------------------------------------------------------------------------
# 5. Always exits zero (main() must not raise SystemExit with non-zero code)
# ---------------------------------------------------------------------------

def test_always_exits_zero():
    import netrc as netrc_module

    fake_wandb = types.ModuleType("wandb")
    fake_wandb.Api = MagicMock(side_effect=RuntimeError("unexpected"))

    captured_lines: list[str] = []

    def fake_print(*args, **kwargs):
        f = kwargs.get("file", None)
        if f is None or f is sys.stdout:
            captured_lines.append(" ".join(str(a) for a in args))

    def _no_netrc(*a, **kw):
        raise FileNotFoundError("no netrc")

    with (
        patch.dict(os.environ, {}, clear=True),
        patch.dict(sys.modules, {"wandb": fake_wandb}),
        patch.object(netrc_module, "netrc", _no_netrc),
        patch("builtins.print", fake_print),
    ):
        if "check_auth" in sys.modules:
            del sys.modules["check_auth"]
        import check_auth
        importlib.reload(check_auth)
        try:
            check_auth.main()
        except SystemExit as e:
            assert e.code in (0, None), f"Expected exit 0, got {e.code}"


# ---------------------------------------------------------------------------
# 6. stdout is exactly one JSON line
# ---------------------------------------------------------------------------

def test_stdout_is_single_json_line():
    import netrc as netrc_module

    fake_wandb = types.ModuleType("wandb")
    fake_wandb.Api = MagicMock(side_effect=FileNotFoundError("no wandb"))

    captured_lines: list[str] = []

    def fake_print(*args, **kwargs):
        f = kwargs.get("file", None)
        if f is None or f is sys.stdout:
            captured_lines.append(" ".join(str(a) for a in args))

    def _no_netrc(*a, **kw):
        raise FileNotFoundError("no netrc")

    with (
        patch.dict(os.environ, {}, clear=True),
        patch.dict(sys.modules, {"wandb": fake_wandb}),
        patch.object(netrc_module, "netrc", _no_netrc),
        patch("builtins.print", fake_print),
    ):
        if "check_auth" in sys.modules:
            del sys.modules["check_auth"]
        import check_auth
        importlib.reload(check_auth)
        try:
            check_auth.main()
        except SystemExit as exc:
            assert exc.code in (0, None), f"Expected exit 0, got {exc.code}"

    assert len(captured_lines) == 1, (
        f"Expected exactly 1 stdout line, got {len(captured_lines)}: {captured_lines}"
    )
    parsed = json.loads(captured_lines[0])
    assert isinstance(parsed, dict)


# ---------------------------------------------------------------------------
# 7. API key value not in stdout
# ---------------------------------------------------------------------------

def test_api_key_value_not_in_stdout():
    secret_key = "s3cr3t-api-key-abcdef1234567890"
    import netrc as netrc_module

    fake_wandb = types.ModuleType("wandb")
    fake_wandb.Api = MagicMock(side_effect=FileNotFoundError())

    captured_lines: list[str] = []

    def fake_print(*args, **kwargs):
        f = kwargs.get("file", None)
        if f is None or f is sys.stdout:
            captured_lines.append(" ".join(str(a) for a in args))

    def _no_netrc(*a, **kw):
        raise FileNotFoundError("no netrc")

    with (
        patch.dict(os.environ, {"WANDB_API_KEY": secret_key}, clear=True),
        patch.dict(sys.modules, {"wandb": fake_wandb}),
        patch.object(netrc_module, "netrc", _no_netrc),
        patch("builtins.print", fake_print),
    ):
        if "check_auth" in sys.modules:
            del sys.modules["check_auth"]
        import check_auth
        importlib.reload(check_auth)
        check_auth.main()

    all_output = "\n".join(captured_lines)
    assert secret_key not in all_output, f"Secret key leaked in stdout: {all_output}"


# ---------------------------------------------------------------------------
# 8. wandb.Api timeout -> authenticated=true, username=null, error="viewer_lookup_timeout"
# ---------------------------------------------------------------------------

def test_viewer_lookup_timeout_falls_back_gracefully():
    netrc_data = {"api.wandb.ai": ("bob", None, "fake-pass")}
    result = _run_check_auth(
        env={},
        netrc_data=netrc_data,
        wandb_raises=True,
    )
    assert result["authenticated"] is True
    assert result["method"] == "netrc"
    assert result["username"] is None
    assert result["error"] == "viewer_lookup_timeout"


# ---------------------------------------------------------------------------
# 9. wandb.AuthenticationError -> authenticated=false (no fail-open)  (H4)
# ---------------------------------------------------------------------------

def test_invalid_key_returns_unauthenticated():
    """If wandb.Api(...).viewer raises AuthenticationError (invalid key in netrc),
    check_auth must NOT fail-open. It must return authenticated=false,
    method='none', error='invalid_key'."""
    import netrc as netrc_module

    # Build a fake wandb that defines AuthenticationError under wandb.errors
    fake_wandb = types.ModuleType("wandb")
    fake_errors = types.ModuleType("wandb.errors")

    class AuthenticationError(Exception):
        pass

    class CommError(Exception):
        pass

    fake_errors.AuthenticationError = AuthenticationError
    fake_errors.CommError = CommError
    fake_wandb.errors = fake_errors
    # Also expose top-level alias (older wandb versions)
    fake_wandb.AuthenticationError = AuthenticationError
    fake_wandb.CommError = CommError

    fake_api_inst = MagicMock()
    # Accessing .viewer raises AuthenticationError
    type(fake_api_inst).viewer = property(
        lambda self: (_ for _ in ()).throw(AuthenticationError("invalid api key"))
    )
    fake_wandb.Api = MagicMock(return_value=fake_api_inst)

    captured_lines: list[str] = []

    def fake_print(*args, **kwargs):
        f = kwargs.get("file", None)
        if f is None or f is sys.stdout:
            captured_lines.append(" ".join(str(a) for a in args))

    fake_netrc_obj = MagicMock()
    fake_netrc_obj.authenticators = lambda host: ("bob", None, "bad-pass") if host == "api.wandb.ai" else None

    with (
        patch.dict(os.environ, {}, clear=True),
        patch.dict(sys.modules, {"wandb": fake_wandb, "wandb.errors": fake_errors}),
        patch.object(netrc_module, "netrc", return_value=fake_netrc_obj),
        patch("builtins.print", fake_print),
    ):
        if "check_auth" in sys.modules:
            del sys.modules["check_auth"]
        import check_auth
        importlib.reload(check_auth)
        try:
            check_auth.main()
        except SystemExit as exc:
            assert exc.code in (0, None)

    assert len(captured_lines) >= 1
    result = json.loads(captured_lines[0])
    assert result["authenticated"] is False, f"fail-open detected: {result}"
    assert result["method"] == "none"
    assert result["error"] == "invalid_key"
