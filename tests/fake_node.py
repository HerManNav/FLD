"""Local persistent FCP test node; jobs survive connections and client processes."""

from pathlib import Path
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
import socketserver
import threading
import uuid

PAYLOAD = b"Freenet persistent test data\x00\xff" * 40000


class PersistentNode(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.jobs = {}
        self.handlers = set()
        self.lock = threading.RLock()
        self.submissions = self.restarts = self.removals = 0
        self.errors = []
        self.web = ThreadingHTTPServer(("127.0.0.1", 0), WebHandler)
        self.web.node = self
        threading.Thread(target=self.web.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()

    def server_close(self):
        self.web.shutdown()
        self.web.server_close()
        super().server_close()

    def handle_error(self, request, client_address):
        import sys
        self.errors.append(sys.exception())

    def broadcast(self, identifier, name, **fields):
        with self.lock:
            job = self.jobs[identifier]
            for handler in list(self.handlers):
                if handler.client == job["client"] or handler.watching:
                    handler.send(name, Identifier=identifier, **fields)

    def progress(self, identifier, succeeded):
        with self.lock:
            self.jobs[identifier]["succeeded"] = succeeded
            self.broadcast(identifier, "SimpleProgress", Required=10, Succeeded=succeeded, FinalizedTotal="true")

    def fail(self, identifier, code=18, fatal=False):
        with self.lock:
            self.jobs[identifier]["failure"] = dict(Code=code, CodeDescription="Transfer failed", Fatal=str(fatal).lower())
            self.broadcast(identifier, "GetFailed", **self.jobs[identifier]["failure"])

    def complete(self, identifier):
        with self.lock:
            job = self.jobs[identifier]
            Path(job["Filename"]).write_bytes(PAYLOAD)
            job["completed"] = True
            self.broadcast(identifier, "DataFound", DataLength=len(PAYLOAD))

    def drop_connections(self):
        with self.lock:
            for handler in list(self.handlers):
                try:
                    handler.request.shutdown(socket.SHUT_RDWR)
                    handler.request.close()
                except OSError:
                    pass


class Handler(socketserver.StreamRequestHandler):
    def setup(self):
        super().setup()
        self.client = None
        self.watching = False
        self.send_lock = threading.Lock()

    def send(self, name, **fields):
        try:
            with self.send_lock:
                self.wfile.write(("\n".join([name, *[f"{k}={v}" for k, v in fields.items()], "EndMessage", ""])).encode())
                self.wfile.flush()
        except OSError:
            pass

    def state(self, identifier):
        job = self.server.jobs[identifier]
        fields = {key: job[key] for key in ("URI", "Filename", "PriorityClass", "ReturnType")}
        self.send("PersistentGet", Identifier=identifier, **fields)
        self.send("ExpectedDataLength", Identifier=identifier, DataLength=len(PAYLOAD), Finalized="true")
        self.send("SimpleProgress", Identifier=identifier, Succeeded=job["succeeded"], Required=10)
        if job["failure"]:
            self.send("GetFailed", Identifier=identifier, **job["failure"])
        if job["completed"]:
            self.send("DataFound", Identifier=identifier, DataLength=len(PAYLOAD))

    def handle(self):
        try:
            while True:
                raw = self.rfile.readline()
                if not raw:
                    break
                name = raw.decode().strip()
                fields = {}
                while True:
                    raw = self.rfile.readline()
                    if not raw:
                        return
                    line = raw.decode().rstrip("\r\n")
                    if line == "EndMessage":
                        break
                    key, value = line.split("=", 1)
                    fields[key] = value
                with self.server.lock:
                    identifier = fields.get("Identifier")
                    if name == "ClientHello":
                        self.client = fields["Name"]
                        self.server.handlers.add(self)
                        self.send("NodeHello", FCPVersion="2.0")
                    elif name == "TestDDARequest":
                        self.directory = Path(fields["Directory"])
                        self.challenge = self.directory / ("DDACheck-" + uuid.uuid4().hex + ".tmp")
                        self.send("TestDDAReply", Directory=str(self.directory), WriteFilename=str(self.challenge), ContentToWrite="challenge")
                    elif name == "TestDDAResponse":
                        allowed = self.challenge.read_text() == "challenge"
                        self.challenge.unlink()
                        self.send("TestDDAComplete", Directory=str(self.directory), WriteDirectoryAllowed=str(allowed).lower())
                    elif name == "ListPersistentRequests":
                        for identifier, job in list(self.server.jobs.items()):
                            if job["client"] == self.client or self.watching:
                                self.state(identifier)
                        self.send("EndListPersistentRequests")
                    elif name == "WatchGlobal":
                        self.watching = fields.get("Enabled") == "true"
                    elif name == "ClientGet":
                        if identifier in self.server.jobs:
                            self.send("IdentifierCollision", Identifier=identifier)
                            continue
                        if fields.get("Persistence") != "forever" or fields.get("ReturnType") != "disk":
                            raise AssertionError("Downloads must be persistent disk requests")
                        self.server.submissions += 1
                        self.server.jobs[identifier] = dict(**fields, client=self.client, succeeded=0, completed=False, failure=None)
                        self.state(identifier)
                    elif name == "GetRequestStatus":
                        if identifier not in self.server.jobs:
                            self.send("ProtocolError", Identifier=identifier, Code=15, CodeDescription="No such identifier")
                        else:
                            self.state(identifier)
                    elif name == "ModifyPersistentRequest":
                        job = self.server.jobs[identifier]
                        job["PriorityClass"] = fields["PriorityClass"]
                        self.send("PersistentRequestModified", Identifier=identifier, PriorityClass=fields["PriorityClass"])
                    elif name == "RemovePersistentRequest":
                        job = self.server.jobs.pop(identifier, None)
                        if job:
                            Path(job["Filename"]).unlink(missing_ok=True)
                        self.server.removals += 1
                        self.send("PersistentRequestRemoved", Identifier=identifier)
                    else:
                        raise AssertionError("Unexpected FCP command: " + name)
        except (ConnectionError, OSError):
            pass
        finally:
            with self.server.lock:
                self.server.handlers.discard(self)


class WebHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'<form><input name="formPassword" value="test-token"></form>'
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        message = BytesParser(policy=default).parsebytes(("Content-Type: " + self.headers["Content-Type"] + "\r\n\r\n").encode() + body)
        fields = {part.get_param("name", header="content-disposition"): part.get_content().strip() for part in message.iter_parts()}
        if fields.get("formPassword") != "test-token" or fields.get("restart_request") != "Restart":
            self.send_error(403)
            return
        node = self.server.node
        with node.lock:
            identifier = fields["identifier-0"]
            if identifier not in node.jobs:
                self.send_error(404)
                return
            node.restarts += 1
            node.jobs[identifier]["failure"] = None
            for handler in list(node.handlers):
                if handler.watching:
                    handler.state(identifier)
        self.send_response(303)
        self.send_header("Location", "/downloads/")
        self.end_headers()

    def log_message(self, *args):
        pass
