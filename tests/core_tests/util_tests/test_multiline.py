from types import SimpleNamespace

from cloudbot.util import multiline


class _Conn:
    def __init__(self, caps=None, available=None):
        self.lines = []
        self.memory = {}
        if caps is not None:
            self.memory["server_caps"] = caps
        if available is not None:
            self.memory["available_caps"] = available

    def send(self, line, log=True):
        self.lines.append(line)


def _cap(name, value=None):
    return SimpleNamespace(name=name, value=value)


def test_supports_multiline_reads_server_caps():
    assert multiline.supports_multiline(_Conn(caps={"draft/multiline": True}))
    assert not multiline.supports_multiline(_Conn(caps={}))
    assert not multiline.supports_multiline(_Conn())


def test_multiline_limits_parses_advertised_params():
    conn = _Conn(
        available=[_cap("draft/multiline", "max-bytes=4096,max-lines=24")]
    )
    assert multiline.multiline_limits(conn) == (4096, 24)


def test_multiline_limits_missing_or_partial():
    assert multiline.multiline_limits(_Conn()) == (None, None)
    assert multiline.multiline_limits(_Conn(available=[_cap("batch")])) == (
        None,
        None,
    )
    assert multiline.multiline_limits(
        _Conn(available=[_cap("draft/multiline", "max-bytes=512")])
    ) == (512, None)


def test_send_batch_multiline_single_batch():
    conn = _Conn(
        available=[_cap("draft/multiline", "max-bytes=4096,max-lines=24")]
    )
    multiline.send_batch_multiline(conn, "#chan", ["one", "two", "three"])

    assert conn.lines[0].endswith("draft/multiline #chan")
    assert conn.lines[0].startswith("BATCH +")
    assert sum(line.startswith("BATCH +") for line in conn.lines) == 1
    assert conn.lines[-1].startswith("BATCH -")
    privmsgs = [line for line in conn.lines if "PRIVMSG" in line]
    assert len(privmsgs) == 3


def test_send_batch_multiline_splits_on_max_lines():
    conn = _Conn(available=[_cap("draft/multiline", "max-lines=2")])
    multiline.send_batch_multiline(conn, "#chan", ["a", "b", "c", "d", "e"])

    assert sum(line.startswith("BATCH +") for line in conn.lines) == 3
    assert sum(line.startswith("BATCH -") for line in conn.lines) == 3


def test_send_batch_multiline_concat_flag_on_split_long_line():
    long_line = "x" * (multiline.MAX_BATCH_LINE_BYTES + 50)
    conn = _Conn()
    multiline.send_batch_multiline(conn, "#chan", [long_line])

    privmsgs = [line for line in conn.lines if "PRIVMSG" in line]
    assert len(privmsgs) == 2
    assert "draft/multiline-concat" not in privmsgs[0]
    assert "draft/multiline-concat" in privmsgs[1]


def test_send_batch_multiline_empty_is_noop():
    conn = _Conn()
    multiline.send_batch_multiline(conn, "#chan", [])
    assert conn.lines == []
