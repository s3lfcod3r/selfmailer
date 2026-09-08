"""Read attachment metadata without downloading MIME bodies or attachments."""
import re

from imap_tools import MailMessage


def parse_imap(data: bytes) -> list:
    """Small bounded IMAP data parser (lists, quoted strings, literals, NIL)."""
    pos = 0

    def values(depth=0):
        nonlocal pos
        if depth > 40:
            raise ValueError("IMAP metadata nesting too deep")
        out = []
        while pos < len(data):
            ch = data[pos:pos + 1]
            if ch.isspace():
                pos += 1
            elif ch == b")":
                if not depth:
                    raise ValueError("Unexpected closing parenthesis")
                pos += 1
                return out
            elif ch == b"(":
                pos += 1
                out.append(values(depth + 1))
            elif ch == b'"':
                pos += 1
                value = bytearray()
                while pos < len(data) and data[pos:pos + 1] != b'"':
                    if data[pos:pos + 1] == b"\\":
                        pos += 1
                    if pos >= len(data):
                        raise ValueError("Truncated quoted string")
                    value.append(data[pos])
                    pos += 1
                if pos >= len(data):
                    raise ValueError("Unterminated quoted string")
                pos += 1
                out.append(bytes(value))
            elif ch == b"{":
                match = re.match(rb"\{(\d+)\}\r\n", data[pos:])
                if not match:
                    raise ValueError("Invalid literal")
                length = int(match[1])
                pos += match.end()
                if pos + length > len(data):
                    raise ValueError("Truncated literal")
                out.append(data[pos:pos + length])
                pos += length
            else:
                end = pos
                while end < len(data) and not data[end:end + 1].isspace() and data[end:end + 1] not in (b"(", b")"):
                    end += 1
                atom = data[pos:end]
                out.append(None if atom.upper() == b"NIL" else atom)
                pos = end
        if depth:
            raise ValueError("Unterminated list")
        return out

    return values()


def _has_attachment(node) -> bool:
    if not isinstance(node, list):
        return False
    if node and isinstance(node[0], bytes) and node[0].upper() == b"ATTACHMENT":
        return True
    # MIME parameter list: a named inline part is also an attachment in imap_tools.
    for key, value in zip(node[::2], node[1::2]):
        if isinstance(key, bytes) and key.upper() in (b"NAME", b"FILENAME") and isinstance(value, bytes) and value:
            return True
    return any(_has_attachment(value) for value in node if isinstance(value, list))


def _records(box, uids: list[str], parts: str) -> list[dict]:
    if not uids:
        return []
    if not all(re.fullmatch(r"[1-9][0-9]*", uid) for uid in uids):
        raise ValueError("Invalid UID")
    status, rows = box.client.uid("FETCH", ",".join(uids), parts)
    if status != "OK":
        raise ValueError("BODYSTRUCTURE rejected")
    # imaplib separates literals into tuples; restore literal framing before parsing.
    raw = b" ".join((item[0] + b"\r\n" + item[1]) if isinstance(item, tuple) else item for item in rows if item)
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError("Oversized metadata response")
    parsed = parse_imap(raw)
    found = []
    for record in parsed:
        if not isinstance(record, list):
            continue
        fields = {key.upper(): value for key, value in zip(record[::2], record[1::2]) if isinstance(key, bytes)}
        if fields.get(b"UID", b"").decode("ascii") in uids:
            found.append(fields)
    return found


def attachment_uids(box, uids: list[str]) -> set[str]:
    return {fields[b"UID"].decode("ascii") for fields in _records(box, uids, "(UID BODYSTRUCTURE)")
            if _has_attachment(fields.get(b"BODYSTRUCTURE"))}


def header_messages(box, uids: list[str]) -> list[MailMessage]:
    """One FETCH for headers, flags, size and attachment metadata; no body bytes."""
    out = []
    for fields in _records(box, uids, "(UID FLAGS RFC822.SIZE BODYSTRUCTURE BODY.PEEK[HEADER])"):
        header = fields.get(b"BODY[HEADER]")
        if not isinstance(header, bytes):
            raise ValueError("Missing header literal")
        flags = fields.get(b"FLAGS", [])
        if not isinstance(flags, list) or not all(isinstance(flag, bytes) for flag in flags):
            raise ValueError("Invalid flags")
        prefix = (b'(UID ' + fields[b"UID"] + b' FLAGS (' + b' '.join(flags) +
                  b') RFC822.SIZE ' + fields.get(b"RFC822.SIZE", b"0") + b')')
        message = MailMessage([(prefix, header)])
        message.has_attachments_hint = _has_attachment(fields.get(b"BODYSTRUCTURE"))
        out.append(message)
    return out
