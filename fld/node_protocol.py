"""Persistent FCP framing and the local-node restart request."""

from __future__ import annotations

from html.parser import HTMLParser
import socket
from urllib.request import Request, urlopen
import uuid


def restart_node_request(web_base, identifier):
    """Use the node's native restart action; FCP has no restart message."""
    class TokenParser(HTMLParser):
        token = None

        def handle_starttag(self, tag, attrs):
            values = dict(attrs)
            if tag == "input" and values.get("name") == "formPassword":
                self.token = values.get("value")

    parser = TokenParser()
    url = web_base.rstrip("/") + "/downloads/"
    with urlopen(url, timeout=3) as response:
        for _ in range(64):
            chunk = response.read(16384)
            if not chunk:
                break
            parser.feed(chunk.decode("utf-8", errors="replace"))
            if parser.token:
                break
    if not parser.token:
        raise OSError("Cannot find Freenet's download queue form token.")
    boundary = "FLD" + uuid.uuid4().hex
    values = {"formPassword": parser.token, "identifier-0": identifier, "restart_request": "Restart"}
    body = b""
    for name, value in values.items():
        body += (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n').encode()
    body += f"--{boundary}--\r\n".encode()
    request = Request(url, data=body, headers={"Content-Type": "multipart/form-data; boundary=" + boundary})
    with urlopen(request, timeout=3) as response:
        response.read(1024)


class FCPConnection:
    """Incremental framing leaves partial headers intact across socket timeouts."""

    def __init__(self, host, port):
        self.socket = socket.create_connection((host, port), timeout=5)
        self.socket.settimeout(0.2)
        self.buffer = bytearray()
        self.name = None
        self.fields = {}

    def send(self, name, **fields):
        values = [name, *[f"{key}={value}" for key, value in fields.items()], "EndMessage", ""]
        if any("\n" in str(value) or "\r" in str(value) for value in fields.values()):
            raise ValueError("Invalid line break in FCP field.")
        self.socket.sendall("\n".join(values).encode("utf-8"))

    def read(self):
        while True:
            while b"\n" not in self.buffer:
                if len(self.buffer) > 65536:
                    raise OSError("Invalid FCP header.")
                try:
                    data = self.socket.recv(65536)
                except socket.timeout:
                    return None
                if not data:
                    raise ConnectionError("Freenet disconnected")
                self.buffer.extend(data)
            end = self.buffer.index(b"\n")
            line = self.buffer[:end].decode("utf-8").rstrip("\r")
            del self.buffer[:end + 1]
            if self.name is None:
                self.name = line
            elif line == "EndMessage":
                result = self.name, self.fields
                self.name, self.fields = None, {}
                return result
            elif line == "Data":
                raise OSError("Unexpected file stream for a disk download.")
            else:
                key, separator, value = line.partition("=")
                if not separator or len(self.fields) >= 2000:
                    raise OSError("Invalid FCP message.")
                self.fields[key] = value

    def close(self):
        self.socket.close()
