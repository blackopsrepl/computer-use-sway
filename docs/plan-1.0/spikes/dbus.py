"""Minimal stdlib D-Bus client (spike, reference implementation).

Enough for AT-SPI: SASL EXTERNAL auth, method calls with typed marshalling,
replies, errors, and signals. Little-endian only on the wire we send; both
endiannesses are accepted on receive.
"""
from __future__ import annotations

import os
import socket
import struct
import threading
import time
from typing import Any

ALIGN = {"y": 1, "b": 4, "n": 2, "q": 2, "i": 4, "u": 4, "x": 8, "t": 8, "d": 8,
         "s": 4, "o": 4, "g": 1, "a": 4, "(": 8, "{": 8, "v": 1, "h": 4}
FIXED = {"y": "B", "b": "I", "n": "h", "q": "H", "i": "i", "u": "I", "x": "q", "t": "Q", "d": "d", "h": "I"}

METHOD_CALL, METHOD_RETURN, ERROR, SIGNAL = 1, 2, 3, 4
NO_REPLY_EXPECTED = 0x1


class DBusError(Exception):
    def __init__(self, name: str, message: str = ""):
        super().__init__(f"{name}: {message}")
        self.name = name


class ObjectPath(str):
    pass


class Signature(str):
    pass


class Variant:
    def __init__(self, signature: str, value: Any):
        self.signature, self.value = signature, value

    def __repr__(self):
        return f"Variant({self.signature!r}, {self.value!r})"


def split_signature(sig: str) -> list[str]:
    """Split a signature into complete types."""
    out, i = [], 0
    while i < len(sig):
        j = _complete_end(sig, i)
        out.append(sig[i:j])
        i = j
    return out


def _complete_end(sig: str, i: int) -> int:
    c = sig[i]
    if c == "a":
        return _complete_end(sig, i + 1)
    if c in "({":
        close = ")" if c == "(" else "}"
        depth, j = 0, i
        while True:
            if sig[j] in "({":
                depth += 1
            elif sig[j] in ")}":
                depth -= 1
                if depth == 0:
                    return j + 1
            j += 1
    return i + 1


class Writer:
    def __init__(self, endian: str = "<"):
        self.e, self.buf = endian, bytearray()

    def pad(self, n: int) -> None:
        self.buf += b"\0" * (-len(self.buf) % n)

    def write(self, sig: str, value: Any) -> None:
        c = sig[0]
        self.pad(ALIGN[c])
        if c in FIXED:
            if c == "b":
                value = 1 if value else 0
            self.buf += struct.pack(self.e + FIXED[c], value)
        elif c in "so":
            raw = value.encode()
            self.buf += struct.pack(self.e + "I", len(raw)) + raw + b"\0"
        elif c == "g":
            raw = value.encode()
            self.buf += struct.pack("B", len(raw)) + raw + b"\0"
        elif c == "v":
            self.write("g", value.signature)
            self.write(value.signature, value.value)
        elif c == "a":
            inner = sig[1:]
            self.pad(4)
            at = len(self.buf)
            self.buf += b"\0\0\0\0"
            self.pad(ALIGN[inner[0]])
            start = len(self.buf)
            items = value.items() if inner[0] == "{" else value
            for item in items:
                self.write(inner, item)
            struct.pack_into(self.e + "I", self.buf, at, len(self.buf) - start)
        elif c in "({":
            for part, item in zip(split_signature(sig[1:-1]), value):
                self.write(part, item)
        else:
            raise ValueError(f"cannot marshal {sig}")


class Reader:
    def __init__(self, data: bytes, endian: str, pos: int = 0):
        self.d, self.e, self.p = data, endian, pos

    def align(self, n: int) -> None:
        self.p += -self.p % n

    def read(self, sig: str) -> Any:
        c = sig[0]
        self.align(ALIGN[c])
        if c in FIXED:
            fmt = self.e + FIXED[c]
            (v,) = struct.unpack_from(fmt, self.d, self.p)
            self.p += struct.calcsize(fmt)
            return bool(v) if c == "b" else v
        if c in "so":
            (n,) = struct.unpack_from(self.e + "I", self.d, self.p)
            v = self.d[self.p + 4:self.p + 4 + n].decode(errors="replace")
            self.p += 5 + n
            return ObjectPath(v) if c == "o" else v
        if c == "g":
            n = self.d[self.p]
            v = self.d[self.p + 1:self.p + 1 + n].decode()
            self.p += 2 + n
            return Signature(v)
        if c == "v":
            inner = self.read("g")
            return Variant(inner, self.read(inner))
        if c == "a":
            (n,) = struct.unpack_from(self.e + "I", self.d, self.p)
            self.p += 4
            inner = sig[1:]
            self.align(ALIGN[inner[0]])
            end = self.p + n
            items = []
            while self.p < end:
                items.append(self.read(inner))
            return dict(items) if inner[0] == "{" else items
        if c in "({":
            return tuple(self.read(part) for part in split_signature(sig[1:-1]))
        raise ValueError(f"cannot unmarshal {sig}")


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
            out.append(int(value[i + 1:i + 3], 16))
            i += 3
        else:
            out += value[i].encode()
            i += 1
    return out.decode()


class Bus:
    def __init__(self, address: str, timeout: float = 5.0):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(timeout)
        self.sock.connect(parse_address(address))
        self.sock.sendall(b"\0AUTH EXTERNAL " + str(os.getuid()).encode().hex().encode() + b"\r\n")
        line = self._readline()
        if not line.startswith(b"OK"):
            raise DBusError("org.freedesktop.DBus.Error.AuthFailed", line.decode(errors="replace"))
        self.sock.sendall(b"BEGIN\r\n")
        self.serial = 0
        self.buf = b""
        self.lock = threading.RLock()
        self.pending: dict[int, tuple] = {}
        self.signals: list[tuple[float, dict, tuple]] = []
        self.unique_name = self.call("org.freedesktop.DBus", "/org/freedesktop/DBus",
                                     "org.freedesktop.DBus", "Hello")[0]

    def _readline(self) -> bytes:
        line = b""
        while not line.endswith(b"\r\n"):
            line += self.sock.recv(1)
        return line

    def _send(self, kind: int, fields: dict[int, tuple[str, Any]], sig: str, args: tuple, flags: int = 0) -> int:
        self.serial += 1
        body = Writer()
        for part, arg in zip(split_signature(sig), args):
            body.write(part, arg)
        if sig:
            fields[8] = ("g", sig)
        head = Writer()
        head.buf += struct.pack("<cBBBII", b"l", kind, flags, 1, len(body.buf), self.serial)
        head.write("a(yv)", [(code, Variant(s, v)) for code, (s, v) in sorted(fields.items())])
        head.pad(8)
        self.sock.sendall(bytes(head.buf) + bytes(body.buf))
        return self.serial

    def _recv_message(self) -> tuple[dict, tuple]:
        while True:
            if len(self.buf) >= 16:
                endian = "<" if self.buf[0:1] == b"l" else ">"
                body_len, _serial, fields_len = struct.unpack_from(endian + "III", self.buf, 4)
                head_len = 16 + fields_len + (-(16 + fields_len) % 8)
                total = head_len + body_len
                if len(self.buf) >= total:
                    msg, self.buf = self.buf[:total], self.buf[total:]
                    r = Reader(msg, endian, 12)
                    raw_fields = r.read("a(yv)")
                    hdr = {code: v.value for code, v in raw_fields}
                    hdr["type"] = msg[1]
                    hdr["serial"] = _serial
                    body = Reader(msg, endian, head_len)
                    sig = hdr.get(8, "")
                    values = tuple(body.read(part) for part in split_signature(sig))
                    return hdr, values
            chunk = self.sock.recv(1 << 16)
            if not chunk:
                raise DBusError("org.freedesktop.DBus.Error.Disconnected", "bus closed")
            self.buf += chunk

    def call(self, dest: str, path: str, iface: str, member: str, sig: str = "", *args,
             timeout: float = 5.0) -> tuple:
        with self.lock:
            serial = self._send(METHOD_CALL, {1: ("o", path), 2: ("s", iface), 3: ("s", member),
                                              6: ("s", dest)}, sig, args)
            deadline = time.monotonic() + timeout
            while True:
                if time.monotonic() > deadline:
                    raise DBusError("org.freedesktop.DBus.Error.Timeout", f"{dest} {member}")
                self.sock.settimeout(max(0.01, deadline - time.monotonic()))
                try:
                    hdr, values = self._recv_message()
                except socket.timeout:
                    continue
                if hdr["type"] == SIGNAL:
                    self.signals.append((time.monotonic(), hdr, values))
                    continue
                if hdr.get(5) != serial:
                    continue
                if hdr["type"] == ERROR:
                    raise DBusError(hdr.get(4, "?"), values[0] if values else "")
                return values

    def get_property(self, dest: str, path: str, iface: str, name: str) -> Any:
        (v,) = self.call(dest, path, "org.freedesktop.DBus.Properties", "Get", "ss", iface, name)
        return v.value

    def set_property(self, dest: str, path: str, iface: str, name: str, sig: str, value: Any) -> None:
        self.call(dest, path, "org.freedesktop.DBus.Properties", "Set", "ssv", iface, name, Variant(sig, value))

    def add_match(self, rule: str) -> None:
        self.call("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "AddMatch", "s", rule)

    def pump(self, duration: float) -> None:
        """Read signals for `duration` seconds."""
        deadline = time.monotonic() + duration
        with self.lock:
            while time.monotonic() < deadline:
                self.sock.settimeout(max(0.01, deadline - time.monotonic()))
                try:
                    hdr, values = self._recv_message()
                except socket.timeout:
                    return
                if hdr["type"] == SIGNAL:
                    self.signals.append((time.monotonic(), hdr, values))
