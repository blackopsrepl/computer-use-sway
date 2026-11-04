"""A minimal D-Bus client, standard library only.

Enough for AT-SPI: SASL EXTERNAL authentication, method calls with full
type marshalling (basic types, strings, object paths, signatures, variants,
arrays, dicts, structs), error replies, and signal capture. Messages we send
are little-endian; both byte orders are accepted on receive.
"""

from __future__ import annotations

import collections
import os
import socket
import struct
import time
from typing import Any

from . import core

ALIGN = {"y": 1, "b": 4, "n": 2, "q": 2, "i": 4, "u": 4, "x": 8, "t": 8, "d": 8,
         "s": 4, "o": 4, "g": 1, "a": 4, "(": 8, "{": 8, "v": 1}
FIXED = {"y": "B", "b": "I", "n": "h", "q": "H", "i": "i", "u": "I", "x": "q", "t": "Q", "d": "d"}
METHOD_CALL, METHOD_RETURN, ERROR, SIGNAL = 1, 2, 3, 4
MAX_MESSAGE = 128 * 1024 * 1024


class DBusError(core.ToolError):
    def __init__(self, name: str, message: str = "") -> None:
        super().__init__(f"{name}: {message}".strip())
        self.name = name


class ObjectPath(str):
    pass


class Signature(str):
    pass


class Variant:
    def __init__(self, signature: str, value: Any) -> None:
        self.signature = signature
        self.value = value

    def __repr__(self) -> str:
        return f"Variant({self.signature!r}, {self.value!r})"


def _complete_end(sig: str, i: int) -> int:
    c = sig[i]
    if c == "a":
        return _complete_end(sig, i + 1)
    if c in "({":
        depth, j = 0, i
        while j < len(sig):
            if sig[j] in "({":
                depth += 1
            elif sig[j] in ")}":
                depth -= 1
                if depth == 0:
                    return j + 1
            j += 1
        raise DBusError("org.freedesktop.DBus.Error.InvalidSignature", sig)
    if c not in ALIGN:
        raise DBusError("org.freedesktop.DBus.Error.InvalidSignature", f"unsupported type {c!r} in {sig}")
    return i + 1


def split_signature(sig: str) -> list[str]:
    parts, i = [], 0
    while i < len(sig):
        j = _complete_end(sig, i)
        parts.append(sig[i:j])
        i = j
    return parts


class Writer:
    def __init__(self) -> None:
        self.buf = bytearray()

    def pad(self, n: int) -> None:
        self.buf += b"\0" * (-len(self.buf) % n)

    def write(self, sig: str, value: Any) -> None:
        c = sig[0]
        self.pad(ALIGN[c])
        if c in FIXED:
            if c == "b":
                value = 1 if value else 0
            self.buf += struct.pack("<" + FIXED[c], value)
        elif c in "so":
            raw = str(value).encode("utf-8")
            self.buf += struct.pack("<I", len(raw)) + raw + b"\0"
        elif c == "g":
            raw = str(value).encode("ascii")
            self.buf += struct.pack("B", len(raw)) + raw + b"\0"
        elif c == "v":
            self.write("g", value.signature)
            self.write(value.signature, value.value)
        elif c == "a":
            inner = sig[1:]
            at = len(self.buf)
            self.buf += b"\0\0\0\0"
            self.pad(ALIGN[inner[0]])
            start = len(self.buf)
            for item in (value.items() if inner[0] == "{" else value):
                self.write(inner, item)
            struct.pack_into("<I", self.buf, at, len(self.buf) - start)
        elif c in "({":
            for part, item in zip(split_signature(sig[1:-1]), value):
                self.write(part, item)


class Reader:
    def __init__(self, data: bytes, endian: str, pos: int = 0) -> None:
        self.d, self.e, self.p = data, endian, pos

    def _need(self, n: int) -> None:
        if self.p + n > len(self.d):
            raise DBusError("org.freedesktop.DBus.Error.InvalidArgs", "truncated message")

    def align(self, n: int) -> None:
        self.p += -self.p % n

    def read(self, sig: str) -> Any:
        c = sig[0]
        self.align(ALIGN[c])
        if c in FIXED:
            fmt = self.e + FIXED[c]
            self._need(struct.calcsize(fmt))
            (value,) = struct.unpack_from(fmt, self.d, self.p)
            self.p += struct.calcsize(fmt)
            return bool(value) if c == "b" else value
        if c in "so":
            self._need(4)
            (n,) = struct.unpack_from(self.e + "I", self.d, self.p)
            self._need(5 + n)
            value = self.d[self.p + 4 : self.p + 4 + n].decode("utf-8", errors="replace")
            self.p += 5 + n
            return ObjectPath(value) if c == "o" else value
        if c == "g":
            self._need(1)
            n = self.d[self.p]
            self._need(2 + n)
            value = self.d[self.p + 1 : self.p + 1 + n].decode("ascii", errors="replace")
            self.p += 2 + n
            return Signature(value)
        if c == "v":
            inner = self.read("g")
            return Variant(inner, self.read(inner))
        if c == "a":
            self._need(4)
            (n,) = struct.unpack_from(self.e + "I", self.d, self.p)
            self.p += 4
            inner = sig[1:]
            self.align(ALIGN[inner[0]])
            end = self.p + n
            self._need(n)
            items = []
            while self.p < end:
                items.append(self.read(inner))
            return dict(items) if inner[0] == "{" else items
        if c in "({":
            return tuple(self.read(part) for part in split_signature(sig[1:-1]))
        raise DBusError("org.freedesktop.DBus.Error.InvalidSignature", sig)


def parse_address(address: str) -> str | bytes:
    for entry in address.split(";"):
        kind, _, params = entry.partition(":")
        kv = dict(p.split("=", 1) for p in params.split(",") if "=" in p)
        if kind == "unix" and "path" in kv:
            return _unescape(kv["path"])
        if kind == "unix" and "abstract" in kv:
            return b"\0" + _unescape(kv["abstract"]).encode()
    raise DBusError("org.freedesktop.DBus.Error.BadAddress", address)


def _unescape(value: str) -> str:
    out, i = bytearray(), 0
    while i < len(value):
        if value[i] == "%":
            out.append(int(value[i + 1 : i + 3], 16))
            i += 3
        else:
            out += value[i].encode()
            i += 1
    return out.decode()


def build_message(kind: int, serial: int, fields: dict[int, tuple[str, Any]], sig: str, args: tuple,
                  flags: int = 0) -> bytes:
    body = Writer()
    for part, arg in zip(split_signature(sig), args):
        body.write(part, arg)
    if sig:
        fields = dict(fields)
        fields[8] = ("g", sig)
    head = Writer()
    head.buf += struct.pack("<cBBBII", b"l", kind, flags, 1, len(body.buf), serial)
    head.write("a(yv)", [(code, Variant(s, v)) for code, (s, v) in sorted(fields.items())])
    head.pad(8)
    return bytes(head.buf) + bytes(body.buf)


def parse_message(data: bytes) -> tuple[dict, tuple, int]:
    """Parse one message from the front of data: (header, values, total_length)."""
    endian = "<" if data[0:1] == b"l" else ">"
    body_len, serial, fields_len = struct.unpack_from(endian + "III", data, 4)
    head_len = 16 + fields_len + (-(16 + fields_len) % 8)
    total = head_len + body_len
    if total > MAX_MESSAGE:
        raise DBusError("org.freedesktop.DBus.Error.LimitsExceeded", "message too large")
    if len(data) < total:
        return {}, (), -1
    reader = Reader(data[:total], endian, 12)
    header = {code: v.value for code, v in reader.read("a(yv)")}
    header["type"] = data[1]
    header["serial"] = serial
    sig = header.get(8, "")
    body = Reader(data[:total], endian, head_len)
    values = tuple(body.read(part) for part in split_signature(sig))
    return header, values, total


class Bus:
    def __init__(self, address: str, timeout: float = 5.0) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(timeout)
        try:
            self.sock.connect(parse_address(address))
            self.sock.sendall(b"\0AUTH EXTERNAL " + str(os.getuid()).encode().hex().encode() + b"\r\n")
            line = self._readline()
            if not line.startswith(b"OK"):
                raise DBusError("org.freedesktop.DBus.Error.AuthFailed", line.decode(errors="replace"))
            self.sock.sendall(b"BEGIN\r\n")
        except OSError as exc:
            self.sock.close()
            raise core.ToolError(f"cannot connect to D-Bus at {address}: {exc}") from exc
        self.serial = 0
        self.buf = b""
        self.signals: collections.deque[tuple[float, dict, tuple]] = collections.deque(maxlen=2048)
        self.unique_name = self.call("org.freedesktop.DBus", "/org/freedesktop/DBus",
                                     "org.freedesktop.DBus", "Hello")[0]

    def _readline(self) -> bytes:
        line = b""
        while not line.endswith(b"\r\n"):
            chunk = self.sock.recv(1)
            if not chunk:
                raise OSError("bus closed during authentication")
            line += chunk
        return line

    def _next_serial(self) -> int:
        self.serial = 1 if self.serial >= 0xFFFFFFFF else self.serial + 1
        return self.serial

    def _recv(self, timeout: float) -> tuple[dict, tuple] | None:
        while True:
            if len(self.buf) >= 16:
                header, values, total = parse_message(self.buf)
                if total > 0:
                    self.buf = self.buf[total:]
                    return header, values
            self.sock.settimeout(max(timeout, 0.001))
            try:
                chunk = self.sock.recv(1 << 16)
            except socket.timeout:
                return None
            except OSError as exc:
                raise DBusError("org.freedesktop.DBus.Error.Disconnected", str(exc)) from exc
            if not chunk:
                raise DBusError("org.freedesktop.DBus.Error.Disconnected", "bus closed")
            self.buf += chunk

    def call(self, dest: str, path: str, iface: str, member: str, sig: str = "", *args: Any,
             timeout: float = 5.0) -> tuple:
        serial = self._next_serial()
        message = build_message(METHOD_CALL, serial, {1: ("o", path), 2: ("s", iface), 3: ("s", member),
                                                      6: ("s", dest)}, sig, args)
        try:
            self.sock.sendall(message)
        except OSError as exc:
            raise DBusError("org.freedesktop.DBus.Error.Disconnected", str(exc)) from exc
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise DBusError("org.freedesktop.DBus.Error.Timeout", f"{dest} {iface}.{member}")
            got = self._recv(left)
            if got is None:
                continue
            header, values = got
            if header["type"] == SIGNAL:
                self.signals.append((time.monotonic(), header, values))
                continue
            if header.get(5) != serial:
                continue
            if header["type"] == ERROR:
                raise DBusError(str(header.get(4, "org.freedesktop.DBus.Error.Failed")),
                                str(values[0]) if values else "")
            return values

    def get_property(self, dest: str, path: str, iface: str, name: str) -> Any:
        (variant,) = self.call(dest, path, "org.freedesktop.DBus.Properties", "Get", "ss", iface, name)
        return variant.value

    def set_property(self, dest: str, path: str, iface: str, name: str, sig: str, value: Any) -> None:
        self.call(dest, path, "org.freedesktop.DBus.Properties", "Set", "ssv", iface, name, Variant(sig, value))

    def add_match(self, rule: str) -> None:
        self.call("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "AddMatch", "s", rule)

    def pump(self, duration: float) -> None:
        deadline = time.monotonic() + duration
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                return
            got = self._recv(left)
            if got is None:
                return
            header, values = got
            if header["type"] == SIGNAL:
                self.signals.append((time.monotonic(), header, values))

    def signals_since(self, t: float, sender: str | None = None) -> list[tuple[float, dict, tuple]]:
        return [s for s in self.signals if s[0] >= t and (sender is None or s[1].get(7) == sender)]

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass
