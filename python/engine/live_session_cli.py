"""issue #42 Phase 1: ``python -m engine.live_session_cli run`` CLI エントリポイント。

リプレイで検証したユーザー戦略ファイルをそのまま **ライブモード**（demo / 本番 venue）
で実行する CLI。``replay_session.py::ReplaySession`` の ``run`` サブコマンドと対称な
インターフェースを提供する（受け入れ基準 #1）。

統一決定との対応:
  - #1 (CLI module 名): ``engine.live_session_cli``
  - #5 (is_market_open authoritative reject): engine 経由（``EngineError{code:"market_closed"}``）
  - #7 (第二暗証番号の OS 露出対策): ``--second-password-stdin`` / env を推奨、
    ``--second-password`` 平文は非推奨で stderr 警告。
  - #8 (``SecondPasswordRequired`` 固定文言): 「第二暗証番号を設定してください」
  - #13 (concurrent live ガード): ``EngineBusy{busy_kind}`` と ``Error{engine_already_running}``
    両方をハンドル。
  - #16 (warm_up failure cleanup): engine_runner 側で実装、CLI 側は emit を表示するだけ。

呼び出し方の例:

    python -m engine.live_session_cli run \
        --strategy examples/test_strategy_minute.py \
        --instrument 8306.T \
        --max-qty 100 \
        --max-notional-jpy 500000 \
        --venue tachibana \
        --demo \
        --mode attach
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
from datetime import datetime, timezone
from typing import Any, Sequence, TextIO

# is_market_open は CLI の事前 hint 専用（authoritative reject ではない）。
# authoritative reject は engine_runner.py::start_live() の冒頭で行われ、
# CLI は engine から ``EngineError{code:"market_closed"}`` を受けたら stderr 表示する。
from engine.exchanges.kabusapi_url import resolve_kabu_env
from engine.exchanges.tachibana_ws import is_market_open
from engine.replay_session import LiveSession


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


# ``SecondPasswordRequired`` の固定文言（GUI ステータスバー赤帯と同一、統一決定 #8）。
SECOND_PASSWORD_REQUIRED_MESSAGE = "第二暗証番号を設定してください"

# CLI exit codes（``replay_session.py`` と整合）。
EXIT_OK = 0
EXIT_GENERAL_ERROR = 1
EXIT_BUSY = 2
EXIT_AUTH_REQUIRED = 3


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    """``python -m engine.live_session_cli`` の argparse を構築する。"""
    parser = argparse.ArgumentParser(
        prog="python -m engine.live_session_cli",
        description=(
            "live トレード CLI: リプレイ検証済みの戦略ファイルを demo / 本番 venue で実行する。"
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run", help="live strategy を起動する")
    run_p.add_argument(
        "--strategy", required=True,
        help="戦略ファイルパス（.py）",
    )
    # NOTE: Phase 2 で LIVE_SCENARIO 不在時のフォールバックを実装する予定だが、
    # Phase 1 では --instrument / --max-qty / --max-notional-jpy をすべて argparse 必須にする。
    run_p.add_argument(
        "--instrument", required=True,
        help="銘柄 ID（例: 8306.T）",
    )

    def _validate_max_qty(s: str) -> int:
        try:
            v = int(s)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"--max-qty must be int, got {s!r}: {exc}")
        if not (1 <= v <= 10_000):
            raise argparse.ArgumentTypeError(
                f"--max-qty must be in [1, 10000], got {v}"
            )
        return v

    def _validate_max_notional(s: str) -> int:
        try:
            v = int(s)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(
                f"--max-notional-jpy must be int, got {s!r}: {exc}"
            )
        if not (1 <= v <= 100_000_000):
            raise argparse.ArgumentTypeError(
                f"--max-notional-jpy must be in [1, 100000000], got {v}"
            )
        return v

    run_p.add_argument(
        "--max-qty", required=True, type=_validate_max_qty,
        help="1 注文あたりの最大株数（1 ≤ n ≤ 10000）",
    )
    run_p.add_argument(
        "--max-notional-jpy", required=True, type=_validate_max_notional,
        help="1 注文あたりの最大金額（円）（1 ≤ n ≤ 100000000）",
    )
    run_p.add_argument(
        "--venue", default="tachibana", choices=["tachibana", "kabu_station"],
        help=(
            "venue 識別子（Phase 4 + R2-C で kabu_station 対応済 — "
            "Nautilus 親継承で `node.build()` 通過可能 / capability "
            "supports_live_strategy=True を満たす venue のみ）"
        ),
    )

    # demo / prod (mutex)
    grp = run_p.add_mutually_exclusive_group()
    grp.add_argument("--demo", dest="demo", action="store_true", default=True,
                     help="demo 環境に接続する（デフォルト）")
    grp.add_argument("--prod", dest="demo", action="store_false",
                     help=(
                         "本番環境に接続する（venue 別の prod env 必須: "
                         "tachibana=TACHIBANA_ALLOW_PROD=1 / "
                         "kabu_station=KABU_ALLOW_PROD=1 + KABU_ENV=prod）"
                     ))

    run_p.add_argument(
        "--mode", choices=["auto", "attach", "inprocess"], default="auto",
        help="LiveSession の force_mode（attach: engine attach, inprocess: 直接起動）",
    )
    run_p.add_argument("--user-id", default=None,
                       help="ユーザー ID（省略時は DEV_TACHIBANA_USER_ID env）")
    run_p.add_argument("--password", default=None,
                       help="パスワード（省略時は DEV_TACHIBANA_PASSWORD env）")

    # 第二暗証番号: 推奨 / 非推奨を help に明記する（統一決定 #7）。
    run_p.add_argument(
        "--second-password-stdin", action="store_true", default=False,
        help=(
            "[推奨] 第二暗証番号を stdin から読み込む。"
            "対話時は isatty を尊重、非対話で空 stdin は reject。"
        ),
    )
    run_p.add_argument(
        "--second-password", default=None,
        help=(
            "[非推奨: shell history / ps / Windows タスクマネージャに露出します] "
            "第二暗証番号を平文引数で渡す。代わりに --second-password-stdin を使ってください。"
        ),
    )

    def _validate_init_kwargs(s: str) -> dict:
        try:
            obj = json.loads(s)
        except json.JSONDecodeError as exc:
            raise argparse.ArgumentTypeError(
                f"--strategy-init-kwargs must be valid JSON object: {exc}"
            )
        if not isinstance(obj, dict):
            raise argparse.ArgumentTypeError(
                f"--strategy-init-kwargs must be a JSON object, got {type(obj).__name__}"
            )
        return obj

    run_p.add_argument(
        "--strategy-init-kwargs", default=None, type=_validate_init_kwargs,
        help="strategy の __init__ に渡す追加引数（JSON object 文字列）",
    )
    run_p.add_argument(
        "--strategy-id", default="live-strategy",
        help="strategy 識別子（既定: live-strategy）",
    )
    return parser


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_second_password(
    args: argparse.Namespace,
    *,
    stdin: TextIO,
    stderr: TextIO,
    mode: str,
) -> "str | None":
    """第二暗証番号を引数 / stdin / env から優先順位に従って解決する（統一決定 #7）.

    優先順位: ``--second-password-stdin`` > ``DEV_TACHIBANA_SECOND_PASSWORD`` env >
    ``--second-password`` (warning 付)

    attach mode では wire に流さないため None を返す（統一決定 #7 + spec §3.2-D）。
    in-process mode で 3 経路すべて空なら ``ValueError`` を raise。

    Returns:
        平文の第二暗証番号、または attach mode では常に ``None``。

    Raises:
        ValueError: in-process mode で第二暗証番号が解決できなかった場合。
    """
    # attach mode: wire 経由送信を禁止 → CLI は受けない（DEV_TACHIBANA_SECOND_PASSWORD env が
    # 設定されていても LiveSession には渡さない）。engine 側 SessionHolder で事前設定済みの前提。
    # ユーザーが明示的に passing したことに気付けるよう、--second-password / stdin /
    # env のいずれかが解決可能だった場合は stderr に hint を出す（silent ignore 防止）。
    if mode == "attach":
        provided = (
            args.second_password_stdin
            or os.environ.get("DEV_TACHIBANA_SECOND_PASSWORD")
            or args.second_password is not None
        )
        if provided:
            print(
                "[live_session_cli] attach mode: 第二暗証番号は wire に流されません "
                "(spec §3.2-D)。GUI 経由で engine 側に事前設定してください。",
                file=stderr,
            )
        return None

    # 1) --second-password-stdin
    if args.second_password_stdin:
        # 非対話で空 stdin は reject（CI 等での silent failure 防止）。
        is_interactive = bool(getattr(stdin, "isatty", lambda: False)())
        raw = stdin.read()
        # 統一決定 #7 / R3-CM3: rstrip("\r\n") のみ
        # （内部空白は意図的に保持。trailing newline / CRLF だけ除去）。
        value = raw.rstrip("\r\n")
        if not value:
            if not is_interactive:
                raise ValueError(
                    "--second-password-stdin received empty input in non-interactive context. "
                    "Pipe a value (e.g. `echo $PASS | python -m engine.live_session_cli ...`) "
                    "or set DEV_TACHIBANA_SECOND_PASSWORD env."
                )
            raise ValueError(
                "--second-password-stdin: empty input from interactive terminal."
            )
        return value

    # 2) env
    env_value = os.environ.get("DEV_TACHIBANA_SECOND_PASSWORD")
    if env_value:
        return env_value

    # 3) --second-password (deprecated)
    if args.second_password is not None:
        # stderr に警告を表示（OS 露出リスクを surface する）。
        print(
            "[live_session_cli] WARNING: --second-password 平文渡しは非推奨です "
            "(deprecated). shell history / ps / Windows タスクマネージャに露出します。"
            " --second-password-stdin か DEV_TACHIBANA_SECOND_PASSWORD env を使ってください。",
            file=stderr,
        )
        return args.second_password

    raise ValueError(
        "in-process mode requires a second password. Provide one of: "
        "--second-password-stdin / DEV_TACHIBANA_SECOND_PASSWORD env / --second-password (deprecated)."
    )


def _hint_market_status(stderr: TextIO) -> None:
    """CLI 起動直後の事前 hint。authoritative reject は engine 側（統一決定 #5）."""
    try:
        if not is_market_open(datetime.now(timezone.utc)):
            print(
                "[live_session_cli] hint: 東証は閉場時間帯のようです "
                "(authoritative reject は engine 側で行われます)。",
                file=stderr,
            )
    except Exception:  # noqa: BLE001
        # hint 判定失敗は致命的ではない。authoritative path は engine が握っているので silent OK。
        pass


def _format_event_for_stderr(evt: dict) -> str:
    """engine からの event を stderr 用の 1 行サマリに整形する。"""
    kind = evt.get("event", "?")
    parts = [f"event={kind}"]
    for key in ("code", "busy_kind", "current_state", "attempted_command",
                "venue", "reason", "message", "strategy_id", "request_id"):
        if key in evt and evt[key] is not None:
            parts.append(f"{key}={evt[key]!r}")
    return "; ".join(parts)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main(
    argv: "Sequence[str] | None" = None,
    *,
    stdin: "TextIO | None" = None,
    stdout: "TextIO | None" = None,
    stderr: "TextIO | None" = None,
) -> int:
    """``python -m engine.live_session_cli`` のエントリポイント。

    Args:
        argv: 引数（None なら ``sys.argv[1:]``）。
        stdin: 第二暗証番号読込用（None なら ``sys.stdin``）。
        stdout: 通常出力（None なら ``sys.stdout``）。
        stderr: 警告 / エラー出力（None なら ``sys.stderr``）。

    Returns:
        exit code（0 = 正常、非ゼロ = エラー）。
    """
    if stdin is None:
        stdin = sys.stdin
    if stdout is None:
        stdout = sys.stdout
    if stderr is None:
        stderr = sys.stderr

    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    if args.cmd != "run":
        parser.print_help(stderr)
        return EXIT_GENERAL_ERROR

    # --prod は venue 別に prod env と AND 条件（統一決定 #14 / 受け入れ基準 #7）。
    # R1 HIGH-2: venue-agnostic に TACHIBANA_ALLOW_PROD のみ要求していた旧実装を
    # venue 別に分岐する (`--venue kabu_station --prod` でも tachibana env を要求する
    # 誤運用を解消)。
    #   - tachibana   : `TACHIBANA_ALLOW_PROD == "1"` 必須
    #   - kabu_station: `resolve_kabu_env() == "prod"` 必須
    #                   (内部で `KABU_ALLOW_PROD=1` + `KABU_ENV=prod` の二重判定)
    #   - 他 venue    : 将来追加された venue は fail-safe で reject
    if not args.demo:
        if args.venue == "tachibana":
            allow_prod = os.environ.get("TACHIBANA_ALLOW_PROD", "")
            if allow_prod != "1":
                parser.error(
                    "--prod --venue tachibana requires TACHIBANA_ALLOW_PROD=1 "
                    "environment variable. Refusing to start live engine on "
                    "production venue."
                )
        elif args.venue == "kabu_station":
            if resolve_kabu_env() != "prod":
                # R3 M7: error 文言を help 表記 (`KABU_ALLOW_PROD=1 + KABU_ENV=prod`)
                # と統一する。両 env 変数の **両方** が必要であることを明示し、
                # 内部の `resolve_kabu_env()` も併記して trace を容易にする。
                parser.error(
                    "--prod --venue kabu_station requires BOTH "
                    "KABU_ALLOW_PROD=1 AND KABU_ENV=prod environment variables "
                    "(resolve_kabu_env() must return 'prod'). Refusing to start "
                    "live engine on production venue."
                )
        else:
            # 将来 venue 追加時の fail-safe (argparse choices で reject 済のはずだが
            # 二重防御として CLI 側でも明示 reject する)
            parser.error(
                f"--prod is not supported for venue={args.venue!r}; "
                "no production env guard is defined for this venue."
            )

    # 第二暗証番号を解決（in-process では必須、attach では None で固定）。
    # R8 MEDIUM-1: ``--mode auto`` で credential 未設定の場合に CLI 段階で
    # SystemExit していた旧実装を修正する。auto モードでは ``LiveSession``
    # 内部で attach probe → 必要に応じて inprocess fallback の順で解決するため、
    # CLI 段階で credential を強制すると attach 経由のワークフローが実質使えなくなる。
    #
    # 解決方針:
    #   - mode=attach   → 既存通り (attach は wire に流さない → None 返却)
    #   - mode=inprocess → 既存通り (必須、欠落で argparse error)
    #   - mode=auto      → attach 同様 wire 安全性を優先し、credential 解決を試みるが
    #                      欠落しても ``None`` で先に進む。inprocess fallback が
    #                      最終的に必要だった場合は ``LiveSession.run()`` 側で
    #                      ``SecondPasswordRequired`` event か ValueError として出る。
    if args.mode == "auto":
        try:
            second_password = _resolve_second_password(
                args, stdin=stdin, stderr=stderr, mode="inprocess",
            )
        except ValueError:
            # auto モードでは credential 未設定でも CLI で停めない。
            # attach probe 成功時は LiveSession.run() は second_password を read しない。
            second_password = None
    else:
        try:
            second_password = _resolve_second_password(
                args, stdin=stdin, stderr=stderr, mode=args.mode,
            )
        except ValueError as exc:
            # argparse error 経由で SystemExit にする（テストが SystemExit を想定するため）。
            parser.error(str(exc))

    # CLI 起動直後の事前 hint（authoritative reject は engine 経由）。
    _hint_market_status(stderr)

    # credential 解決（user_id / password）。in-process では必須、attach では env override 可。
    user_id = args.user_id if args.user_id else os.environ.get("DEV_TACHIBANA_USER_ID")
    password = args.password if args.password else os.environ.get("DEV_TACHIBANA_PASSWORD")

    # exit reason の追跡（複数の error 経路を区別するため）。
    exit_code: int = EXIT_OK

    def _on_event(evt: dict) -> None:
        """engine から流れてくる event を捌く。"""
        nonlocal exit_code
        # JSON 1 行で stdout に流す（replay CLI と対称）。
        try:
            print(json.dumps(evt, ensure_ascii=False), file=stdout, flush=True)
        except Exception:  # noqa: BLE001
            # JSON 化失敗は致命的ではない。
            pass

        kind = evt.get("event")
        if kind == "SecondPasswordRequired":
            # 統一決定 #8: 固定文言を stderr に表示。
            print(SECOND_PASSWORD_REQUIRED_MESSAGE, file=stderr)
            exit_code = EXIT_AUTH_REQUIRED
            return
        if kind == "EngineBusy":
            print(
                f"[live_session_cli] EngineBusy: {_format_event_for_stderr(evt)}",
                file=stderr,
            )
            exit_code = EXIT_BUSY
            return
        if kind == "Error":
            code = evt.get("code", "")
            if code == "engine_already_running":
                print(
                    f"[live_session_cli] engine_already_running: {_format_event_for_stderr(evt)}",
                    file=stderr,
                )
                exit_code = EXIT_BUSY
                return
            # 一般 Error
            print(
                f"[live_session_cli] Error: {_format_event_for_stderr(evt)}",
                file=stderr,
            )
            exit_code = EXIT_GENERAL_ERROR
            return
        if kind == "EngineError":
            code = evt.get("code", "")
            if code == "market_closed":
                print(
                    "[live_session_cli] market_closed: "
                    f"{_format_event_for_stderr(evt)}",
                    file=stderr,
                )
                exit_code = EXIT_GENERAL_ERROR
                return
            if code == "warm_up_failed":
                print(
                    f"[live_session_cli] warm_up_failed: {_format_event_for_stderr(evt)}",
                    file=stderr,
                )
                exit_code = EXIT_GENERAL_ERROR
                return
            if code == "node_build_failed":
                print(
                    f"[live_session_cli] node_build_failed: {_format_event_for_stderr(evt)}",
                    file=stderr,
                )
                exit_code = EXIT_GENERAL_ERROR
                return
            # 一般 EngineError
            print(
                f"[live_session_cli] EngineError: {_format_event_for_stderr(evt)}",
                file=stderr,
            )
            exit_code = EXIT_GENERAL_ERROR
            return

    # SIGINT で stop() を呼ぶ（冪等。LiveSession.stop が複数呼出し OK な実装）。
    session_holder: list[LiveSession] = []

    def _sigint_handler(_signum, _frame) -> None:
        if session_holder:
            try:
                session_holder[0].stop()
            except Exception:  # noqa: BLE001
                # 二重 stop / 既に切断などは silent OK。
                pass

    try:
        signal.signal(signal.SIGINT, _sigint_handler)
    except (ValueError, OSError):
        # signal は main thread でしか登録できない。テスト等で main thread 以外から
        # 呼ばれた場合は silent skip（CLI 経路では問題にならない）。
        pass

    # LiveSession 構築・実行。
    try:
        with LiveSession(
            venue=args.venue,
            demo=args.demo,
            force_mode=args.mode,
            second_password=second_password,
        ) as s:
            session_holder.append(s)
            # login: env / 明示引数 / attach 経路で挙動が違うが LiveSession 側が分岐する。
            try:
                s.login(user_id=user_id, password=password)
            except ValueError as exc:
                # credential 不在等
                print(f"[live_session_cli] login failed: {exc}", file=stderr)
                return EXIT_GENERAL_ERROR
            except RuntimeError as exc:
                print(f"[live_session_cli] login error: {exc}", file=stderr)
                return EXIT_GENERAL_ERROR

            try:
                s.run(
                    instrument_id=args.instrument,
                    strategy_file=args.strategy,
                    max_qty=args.max_qty,
                    max_notional_jpy=args.max_notional_jpy,
                    on_event=_on_event,
                    strategy_init_kwargs=args.strategy_init_kwargs,
                    strategy_id=args.strategy_id,
                )
            except RuntimeError as exc:
                # SecondPasswordRequired 等の attach 経路 RuntimeError も
                # 既に on_event で処理しているはずだが念のため stderr に出す。
                msg = str(exc)
                if "second password" in msg.lower() or "second_password" in msg.lower():
                    # 既に on_event 経由で固定文言を出している場合は重複しない
                    if exit_code == EXIT_OK:
                        print(SECOND_PASSWORD_REQUIRED_MESSAGE, file=stderr)
                        exit_code = EXIT_AUTH_REQUIRED
                else:
                    print(f"[live_session_cli] run failed: {exc}", file=stderr)
                    if exit_code == EXIT_OK:
                        exit_code = EXIT_GENERAL_ERROR
    except KeyboardInterrupt:
        # SIGINT 経路（_sigint_handler 経由で stop 呼出済）。正常終了として扱う。
        return EXIT_OK
    except Exception as exc:  # noqa: BLE001
        print(f"[live_session_cli] unexpected error: {exc}", file=stderr)
        return EXIT_GENERAL_ERROR

    return exit_code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
