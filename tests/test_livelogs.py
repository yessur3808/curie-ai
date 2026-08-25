from cli import livelogs


def test_follower_reads_initial_and_appended_lines(tmp_path):
    log = tmp_path / "curie.log"
    log.write_text("INFO ready\nWARNING warm\n")
    follower = livelogs.LogFollower({"default": log}, lines=10)
    assert list(follower.entries)[-1] == ("default", "WARNING warm")

    with log.open("a") as handle:
        handle.write("ERROR failed\n")
    assert follower.poll() == 1
    assert list(follower.entries)[-1] == ("default", "ERROR failed")


def test_level_filter_includes_error_aliases(tmp_path):
    log = tmp_path / "curie.log"
    log.write_text("INFO ready\nTraceback happened\n")
    follower = livelogs.LogFollower({"curie": log}, level="error")
    assert list(follower.entries) == [("curie", "Traceback happened")]


def test_livelogs_parser_defaults():
    from cli.main import _build_parser

    args = _build_parser().parse_args(["livelogs"])
    assert args.instance == "all"
    assert args.lines == 100
    assert args.level == "all"
    assert args.interval == 0.5
