"""Post-login capabilities through real imaplib/imap-tools, without sockets.

The in-memory transcript exercises the protocol parser, not a prepared
capabilities tuple. All mail operations target synthetic messages only.
"""
from collections import deque
from contextlib import nullcontext
import imaplib
from types import SimpleNamespace as NS

from fastapi import HTTPException
from imap_tools.mailbox import BaseMailBox
import pytest

from app.mail import imap
from app.mail import timing


CAPS = b"IMAP4rev1 ENABLE MOVE CONDSTORE UIDPLUS"


class TranscriptIMAP(imaplib.IMAP4):
    def __init__(self, *, login_mode="tagged", caps=CAPS, query_status="OK",
                 query_error=None, login_status="OK", move_status="OK",
                 pre_caps=b"IMAP4rev1 AUTH=PLAIN", select_status="OK"):
        self.login_mode = login_mode
        self.auth_caps = caps
        self.query_status = query_status
        self.query_error = query_error
        self.login_status = login_status
        self.move_status = move_status
        self.pre_caps = pre_caps
        self.select_status = select_status
        super().__init__()

    def open(self, host="", port=143, timeout=None):
        self.responses = deque([b"* OK Synthetic IMAP ready\r\n"])
        self.commands = []
        self.authenticated = False
        self.condstore = False
        self.closed = False
        self.sock = NS(settimeout=lambda value: None)

    def send(self, data):
        tag, command = data.rstrip(b"\r\n").split(b" ", 1)
        verb = command.split(b" ", 1)[0]
        self.commands.append("LOGIN" if verb == b"LOGIN" else command.decode())
        status = "OK"
        if verb == b"CAPABILITY":
            caps = self.auth_caps if self.authenticated else self.pre_caps
            if self.authenticated:
                if self.query_error:
                    raise self.query_error
                status = self.query_status
            if status == "OK":
                self.responses.append(b"* CAPABILITY " + caps + b"\r\n")
        elif verb == b"LOGIN":
            status = self.login_status
            self.authenticated = status == "OK"
            if self.authenticated and self.login_mode == "tagged":
                self.responses.append(tag + b" OK [CAPABILITY " + self.auth_caps + b"] authenticated\r\n")
                return
            if self.authenticated and self.login_mode == "untagged":
                self.responses.append(b"* CAPABILITY " + self.auth_caps + b"\r\n")
        elif verb == b"ENABLE":
            self.condstore = True
            self.responses.append(b"* ENABLED CONDSTORE\r\n")
        elif verb == b"SELECT":
            status = self.select_status
            self.responses.extend([
                b"* FLAGS (\\Seen \\Deleted)\r\n",
                b"* 1 EXISTS\r\n",
                b"* 0 RECENT\r\n",
                b"* OK [UIDVALIDITY 123] valid\r\n",
            ])
            if self.condstore:
                self.responses.append(b"* OK [HIGHESTMODSEQ 7] modseq\r\n")
        elif verb == b"STATUS":
            self.responses.append(b'* STATUS "INBOX" (UIDVALIDITY 123)\r\n')
        elif verb == b"LIST":
            self.responses.append(b'* LIST (\\Trash) "/" "Trash"\r\n')
        elif verb == b"UID":
            if command.startswith(b"UID MOVE "):
                status = self.move_status
        elif verb == b"LOGOUT":
            self.responses.append(b"* BYE done\r\n")
        elif verb != b"EXPUNGE":
            raise AssertionError(f"Unexpected command: {verb!r}")
        self.responses.append(tag + b" " + status.encode() + b" synthetic response\r\n")

    def readline(self):
        return self.responses.popleft()

    def shutdown(self):
        self.closed = True


class TranscriptBox(BaseMailBox):
    def __init__(self, **options):
        self.options = options
        super().__init__()

    def _get_mailbox_client(self):
        return TranscriptIMAP(**self.options)


@pytest.fixture
def connect(monkeypatch):
    boxes = []

    def run(**options):
        def factory(*args, **kwargs):
            box = TranscriptBox(**options)
            # Deliberately stale response: it must be discarded before LOGIN.
            box.client.untagged_responses["CAPABILITY"] = [b"IMAP4rev1 X-STALE MOVE"]
            boxes.append(box)
            return box

        monkeypatch.setattr(imap, "MailBox", factory)
        return imap._connect(NS(imap_host="offline.invalid", imap_port=993), "test-user", "test-password", "INBOX")

    run.boxes = boxes
    return run


@pytest.mark.parametrize("login_mode", ["tagged", "untagged", "none"])
def test_authenticated_capabilities_activate_real_library_move_and_condstore(connect, login_mode):
    box = connect(login_mode=login_mode)
    assert {"MOVE", "CONDSTORE", "ENABLE"} <= set(box.client.capabilities)
    assert "X-STALE" not in box.client.capabilities
    assert imap.read_modseq(box) == 7
    box.move("1", "Trash")
    commands = box.client.commands
    assert commands.count("CAPABILITY") == (2 if login_mode == "none" else 1)
    assert commands.index("LOGIN") < commands.index("ENABLE CONDSTORE") < commands.index('SELECT "INBOX"')
    assert commands[-1] == 'UID MOVE 1 "Trash"'
    assert not any(c.startswith(("UID COPY ", "UID STORE ", "EXPUNGE")) for c in commands)


@pytest.mark.parametrize("login_mode", ["tagged", "untagged", "none"])
def test_server_without_extensions_retains_copy_delete_fallback(connect, login_mode):
    box = connect(login_mode=login_mode, caps=b"IMAP4rev1", pre_caps=CAPS)
    assert box.client.capabilities == ("IMAP4REV1",)
    assert imap.read_modseq(box) is None
    box.move("1", "Trash")
    assert not any(c.startswith("ENABLE ") for c in box.client.commands)
    assert box.client.commands[-3:] == ['UID COPY 1 "Trash"', r"UID STORE 1 +FLAGS (\Deleted)", "EXPUNGE"]


@pytest.mark.parametrize("status,caps", [("NO", CAPS), ("BAD", CAPS), ("OK", b"MOVE CONDSTORE"), ("OK", b"")])
def test_unavailable_capabilities_disable_stale_extensions_without_repeating_query(connect, status, caps):
    box = connect(login_mode="none", query_status=status, caps=caps, pre_caps=CAPS)
    assert box.client.capabilities == ("IMAP4REV1",)
    assert box.client.commands.count("CAPABILITY") == 2
    assert not any(c.startswith("ENABLE ") for c in box.client.commands)
    assert not box.client.closed


@pytest.mark.parametrize("error", [TimeoutError("private timeout"), OSError("private socket error"),
                                  imaplib.IMAP4.abort("private protocol error")])
def test_transport_failure_closes_setup_and_never_selects_or_writes(connect, error):
    # imaplib wraps send-side OSError/TimeoutError in its abort exception.
    with pytest.raises((type(error), imaplib.IMAP4.abort)):
        connect(login_mode="none", query_error=error)
    box, = connect.boxes
    assert box.client.closed
    assert box.client.commands == ["CAPABILITY", "LOGIN", "CAPABILITY"]


@pytest.mark.parametrize("options", [{"login_status": "NO"}, {"select_status": "NO"}])
def test_failed_login_or_select_releases_unowned_socket(connect, options):
    with pytest.raises(Exception):
        connect(**options)
    box, = connect.boxes
    assert box.client.closed
    assert "LOGOUT" not in box.client.commands
    assert not any(c.startswith("UID ") for c in box.client.commands)


@pytest.mark.parametrize("raw", [None, [], [None], [b""], [b"MOVE"], [b"IMAP4rev1 MOVE\r\nX"],
                                 [b"IMAP4rev1 \xff"], ["IMAP4rev1 ü"], [b'IMAP4rev1 "MOVE"'],
                                 [b"IMAP4rev1 (MOVE)"], "IMAP4rev1 MOVE"])
def test_malformed_capabilities_never_enable_extensions(raw):
    assert imap._parse_capabilities(raw) is None


@pytest.mark.parametrize("raw", [b"IMAP4rev1 move condstore", "IMAP4rev1 move condstore"])
def test_normalizes_supported_capabilities(raw):
    assert imap._parse_capabilities([raw]) == ("IMAP4REV1", "MOVE", "CONDSTORE")


def test_invalid_login_capabilities_are_queried_once():
    client = NS(capabilities=("IMAP4REV1",), untagged_responses={"CAPABILITY": [b"MOVE"]}, calls=0)

    def capability():
        client.calls += 1
        return "OK", [CAPS]

    client.capability = capability
    imap._refresh_capabilities(NS(client=client))
    assert client.calls == 1
    assert "MOVE" in client.capabilities


@pytest.mark.parametrize("expected", [123, 999])
def test_native_delete_still_checks_uidvalidity_first(connect, monkeypatch, expected):
    box = connect()
    monkeypatch.setattr(imap, "_mailbox", lambda *a, **kw: nullcontext(box))
    if expected == 123:
        assert imap.delete_message(NS(id=1), "test", "1", uidvalidity=expected) == "moved"
        commands = box.client.commands
        assert commands[-1] == 'UID MOVE 1 "Trash"'
        assert commands.index('STATUS "INBOX" (UIDVALIDITY)') < len(commands) - 1
    else:
        with pytest.raises(HTTPException) as caught:
            imap.delete_message(NS(id=1), "test", "1", uidvalidity=expected)
        assert caught.value.status_code == 409
        assert not any(c.startswith("UID ") for c in box.client.commands)


def test_native_move_failure_does_not_retry_or_switch_to_copy_delete(connect, monkeypatch):
    box = connect(move_status="NO")
    monkeypatch.setattr(imap, "_mailbox", lambda *a, **kw: nullcontext(box))
    with pytest.raises(Exception):
        imap.delete_message(NS(id=1), "test", "1", uidvalidity=123)
    assert [c for c in box.client.commands if c.startswith(("UID ", "EXPUNGE"))] == ['UID MOVE 1 "Trash"']


@pytest.mark.parametrize("login_mode", ["tagged", "none"])
def test_query_is_timed_only_when_needed_and_does_not_log_protocol_data(connect, caplog, monkeypatch, login_mode):
    monkeypatch.setenv("SELFMAILER_MAIL_TIMING_SLOW_MS", "0")
    with timing.request_scope(1, "read") as trace:
        timing.authorize(1)
        connect(login_mode=login_mode)
        trace.status = 200
    assert ("capability" in trace.phases) == (login_mode == "none")
    assert "condstore" in trace.phases
    assert not any(value in caplog.text for value in ["test-password", "test-user", "offline.invalid", "UIDPLUS"])
