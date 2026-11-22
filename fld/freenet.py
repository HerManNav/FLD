"""Freenet key extraction, page fetching, and streaming FCP downloads."""

from __future__ import annotations

import io
import re
import socket
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import uuid

from .key_parser import extract_keys, source_key

PAGE_LIMIT = 16 * 1024 * 1024


class Cancelled(Exception):
    """The user cancelled the current operation."""


def check_cancel(cancel):
    if cancel.is_set():
        raise Cancelled()


class FCPClient:
    """One connection per request; closing it cancels the node request."""

    def __init__(self, host="127.0.0.1", port=9481, timeout=1800):
        self.host, self.port, self.timeout = host, port, timeout

    def fetch(self, key, output, cancel, progress=lambda status: None, max_size=None, _redirects=0, on_progress=None):
        if "\n" in key or "\r" in key:
            raise ValueError("Invalid Freenet key.")
        check_cancel(cancel)
        try:
            connection = socket.create_connection((self.host, self.port), timeout=5)
        except OSError as exc:
            raise OSError(f"Cannot connect to Freenet FCP at {self.host}:{self.port}. Check that Freenet and FCP are enabled.") from exc
        with connection:
            connection.settimeout(0.5)
            deadline = time.monotonic() + self.timeout
            buffer = bytearray()

            def receive():
                while True:
                    check_cancel(cancel)
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Freenet request timed out after 30 minutes. Retry the file.")
                    try:
                        data = connection.recv(65536)
                    except socket.timeout:
                        continue
                    if not data:
                        raise OSError("Freenet closed the connection before the file completed.")
                    return data

            def line():
                while b"\n" not in buffer:
                    if len(buffer) > 65536:
                        raise OSError("Invalid FCP header.")
                    buffer.extend(receive())
                end = buffer.index(b"\n")
                value = buffer[:end].decode("utf-8").rstrip("\r")
                del buffer[:end + 1]
                return value

            def message():
                name = line()
                fields = {}
                for _ in range(1000):
                    value = line()
                    if value in {"EndMessage", "Data"}:
                        return name, fields, value
                    field, separator, content = value.partition("=")
                    if not separator:
                        raise OSError("Invalid FCP message.")
                    fields[field] = content
                raise OSError("FCP message has too many fields.")

            def send(name, **fields):
                payload = "\n".join([name, *[f"{k}={v}" for k, v in fields.items()], "EndMessage", ""])
                connection.sendall(payload.encode("utf-8"))

            send("ClientHello", Name="FLD-" + uuid.uuid4().hex, ExpectedVersion="2.0")
            name, fields, terminator = message()
            if name != "NodeHello":
                raise OSError("Freenet rejected the FCP handshake: " + name)
            request = dict(URI=key, Identifier="download", Verbosity="1", ReturnType="direct",
                           Persistence="connection", Global="false", FilterData="false",
                           MaxRetries="3", PriorityClass="3", RealTimeFlag="false")
            if max_size is not None:
                request["MaxSize"] = str(max_size)
            send("ClientGet", **request)
            progress("Fetching from Freenet...")
            while True:
                check_cancel(cancel)
                name, fields, terminator = message()
                if name == "SimpleProgress":
                    progress(f"Blocks: {fields.get('Succeeded', '0')} / {fields.get('Required', '?')}")
                    if on_progress:
                        on_progress(int(fields.get("Succeeded", 0)), int(fields.get("Required", 0)), "fetch")
                elif name in {"GetFailed", "ProtocolError", "CloseConnectionDuplicateClientName"}:
                    redirect = fields.get("RedirectURI")
                    if name == "GetFailed" and redirect and _redirects < 5:
                        connection.close()
                        return self.fetch(redirect, output, cancel, progress, max_size, _redirects + 1, on_progress)
                    raise OSError(fields.get("CodeDescription") or fields.get("ShortCodeDescription") or fields.get("ExtraDescription") or name)
                elif name == "AllData":
                    length = int(fields["DataLength"])
                    if terminator != "Data" or length < 0:
                        raise OSError("Invalid FCP file length.")
                    if max_size is not None and length > max_size:
                        raise ValueError("Page exceeds the 16 MiB scraping limit.")
                    written = 0
                    while written < length:
                        check_cancel(cancel)
                        if not buffer:
                            buffer.extend(receive())
                        count = min(len(buffer), length - written)
                        output.write(buffer[:count])
                        del buffer[:count]
                        written += count
                        progress(f"Saving: {written:,} / {length:,} bytes")
                        if on_progress:
                            on_progress(written, length, "save")
                    return fields
                elif terminator == "Data":
                    raise OSError("Unexpected FCP data message.")

def scrape(source, cancel, progress=lambda status: None, client=None):
    source = source.strip()
    if not source:
        raise ValueError("Paste a page URL first.")
    key = source_key(source)
    charset = "utf-8"
    if key:
        output = io.BytesIO()
        fields = (client or FCPClient()).fetch(key, output, cancel, progress, PAGE_LIMIT)
        content = output.getvalue()
        mime = fields.get("Metadata.ContentType", "")
        encoding = re.search(r"charset=([^;\s]+)", mime, re.I)
        if encoding:
            charset = encoding[1].strip("\"'")
    else:
        if source.lower().startswith(("localhost:", "127.0.0.1:")):
            source = "http://" + source
        if urlsplit(source).scheme not in {"http", "https"}:
            raise ValueError("Use an http:// or https:// page URL, or a Freenet key.")
        check_cancel(cancel)
        request = Request(source, headers={"User-Agent": "FLD/1.0", "Accept-Encoding": "identity"})
        with urlopen(request, timeout=10) as response:
            charset = response.headers.get_content_charset() or charset
            chunks, size = [], 0
            while True:
                check_cancel(cancel)
                chunk = response.read(65536)
                if not chunk:
                    break
                size += len(chunk)
                if size > PAGE_LIMIT:
                    raise ValueError("Page exceeds the 16 MiB scraping limit.")
                chunks.append(chunk)
            content = b"".join(chunks)
    check_cancel(cancel)
    try:
        document = content.decode(charset, errors="replace")
    except LookupError:
        document = content.decode("utf-8", errors="replace")
    return extract_keys(document)
