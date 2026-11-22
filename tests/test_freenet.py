import io
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socketserver
import threading
import unittest
from urllib.parse import quote

from fld.freenet import Cancelled, FCPClient, scrape
from fld.key_parser import extract_keys, filename_for, source_key


KEY = "CHK@" + "a" * 43 + "," + "b" * 43 + ",AAMC--8"
PAYLOAD = bytes(range(256)) * 700


class FCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, mode="success"):
        super().__init__(("127.0.0.1", 0), FCPHandler)
        self.mode = mode
        self.requests = []
        self.errors = []
        self.ready = threading.Event()
        self.barrier = None

    def handle_error(self, request, client_address):
        import sys
        self.errors.append(sys.exception())


class FCPHandler(socketserver.StreamRequestHandler):
    def read_message(self):
        name = self.rfile.readline().decode().strip()
        fields = {}
        while True:
            line = self.rfile.readline().decode().strip()
            if line == "EndMessage":
                return name, fields
            if not line:
                raise ConnectionError("Client disconnected")
            field, value = line.split("=", 1)
            fields[field] = value

    def handle(self):
        try:
            name, fields = self.read_message()
            if name != "ClientHello" or fields["ExpectedVersion"] != "2.0":
                raise AssertionError("Bad handshake")
            # Deliberately fragment the greeting across socket writes.
            self.wfile.write(b"Node")
            self.wfile.flush()
            self.wfile.write(b"Hello\nVersion=2.0\nEndMessage\n")
            self.wfile.flush()
            name, fields = self.read_message()
            if name != "ClientGet":
                raise AssertionError("Expected ClientGet")
            self.server.requests.append(fields)
            self.server.ready.set()
            if self.server.barrier:
                self.server.barrier.wait(3)
            mode = self.server.mode
            if mode == "stall":
                self.request.settimeout(4)
                self.rfile.read(1)
                return
            if mode == "failure":
                self.wfile.write(b"GetFailed\nCode=13\nCodeDescription=Data not found\nEndMessage\n")
            elif mode == "redirect" and fields["URI"].startswith("USK@"):
                self.wfile.write(f"GetFailed\nCode=27\nRedirectURI={KEY}/file.bin\nEndMessage\n".encode())
            else:
                self.wfile.write(b"SimpleProgress\nSucceeded=3\nRequired=5\nEndMessage\n")
                length = len(PAYLOAD)
                if mode == "oversized":
                    length = 99999999
                self.wfile.write(f"AllData\nIdentifier=download\nMetadata.ContentType=application/octet-stream\nDataLength={length}\nData\n".encode())
                self.wfile.flush()
                data = PAYLOAD if mode != "truncated" else PAYLOAD[:10]
                for index in range(0, len(data), 7919):
                    self.wfile.write(data[index:index + 7919])
            self.wfile.flush()
        except (ConnectionError, OSError):
            pass


class NetworkTest(unittest.TestCase):
    def server(self, mode="success"):
        server = FCPServer(mode)
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.addCleanup(lambda: self.assertEqual(server.errors, []))
        return server, FCPClient(port=server.server_address[1], timeout=5)

    def test_fetch_stream_integrity_progress_and_node_failure(self):
        server, client = self.server()
        progress = []
        numeric = []
        output = io.BytesIO()
        client.fetch(KEY, output, threading.Event(), progress.append, on_progress=lambda *values: numeric.append(values))
        self.assertEqual(output.getvalue(), PAYLOAD)
        self.assertIn("Blocks: 3 / 5", progress)
        self.assertEqual(numeric[-1], (len(PAYLOAD), len(PAYLOAD), "save"))
        self.assertEqual(server.requests[0]["ReturnType"], "direct")

        failed_server, failed_client = self.server("failure")
        with self.assertRaisesRegex(OSError, "Data not found"):
            failed_client.fetch(KEY, io.BytesIO(), threading.Event())
        self.assertEqual(failed_server.requests[0]["URI"], KEY)

    def test_fetch_truncation_and_cancellation(self):
        server, client = self.server("truncated")
        with self.assertRaisesRegex(OSError, "before the file completed"):
            client.fetch(KEY, io.BytesIO(), threading.Event())

        stalled, stalled_client = self.server("stall")
        cancel = threading.Event()
        errors = []
        worker = threading.Thread(target=lambda: self._fetch_cancel(stalled_client, cancel, errors))
        worker.start()
        self.assertTrue(stalled.ready.wait(2))
        cancel.set()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], Cancelled)

    @staticmethod
    def _fetch_cancel(client, cancel, errors):
        try:
            client.fetch(KEY, io.BytesIO(), cancel)
        except Exception as exc:
            errors.append(exc)

    def test_redirect_fetches_target(self):
        server, client = self.server("redirect")
        output = io.BytesIO()
        client.fetch("USK@a,b,c/site/0/file.bin", output, threading.Event())
        self.assertEqual(output.getvalue(), PAYLOAD)
        self.assertEqual(len(server.requests), 2)

    def test_page_size_limit_applies_to_fcp_data(self):
        server, client = self.server("oversized")
        with self.assertRaisesRegex(ValueError, "16 MiB"):
            client.fetch(KEY, io.BytesIO(), threading.Event(), max_size=100)
        self.assertEqual(server.requests[0]["MaxSize"], "100")

    def test_fcp_header_injection_rejected(self):
        with self.assertRaises(ValueError):
            FCPClient().fetch(KEY + "\nEndMessage", io.BytesIO(), threading.Event())

    def test_http_scrape_finds_plain_text_keys(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                data = f'<html><p>{KEY}/one.zip</p><a href="/{KEY}/two.zip">file</a></html>'.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        try:
            self.assertEqual(scrape(f"http://127.0.0.1:{server.server_port}/", threading.Event()), [KEY + "/one.zip", KEY + "/two.zip"])
        finally:
            server.shutdown()
            server.server_close()

    def test_http_page_limit_cancellation_and_charset_fallback(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/large":
                    data = b"x" * (16 * 1024 * 1024 + 1)
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                elif self.path == "/cancel":
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write((KEY + "/first.zip\n").encode())
                    self.wfile.flush()
                    self.server.first_sent.set()
                    self.server.release.wait(3)
                    try:
                        self.wfile.write((KEY + "/later.zip").encode())
                    except OSError:
                        pass
                else:
                    data = (KEY + "/café.mp4").encode("latin-1")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=" + ("iso-8859-1" if self.path == "/latin" else "x-unknown"))
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.first_sent, server.release = threading.Event(), threading.Event()
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_port}"
        with self.assertRaisesRegex(ValueError, "16 MiB"):
            scrape(base + "/large", threading.Event())
        self.assertEqual(scrape(base + "/latin", threading.Event()), [KEY + "/café.mp4"])
        self.assertEqual(scrape(base + "/unknown", threading.Event()), [KEY + "/caf�.mp4"])

        cancel, errors = threading.Event(), []
        worker = threading.Thread(target=lambda: self._scrape_cancel(base + "/cancel", cancel, errors))
        worker.start()
        self.assertTrue(server.first_sent.wait(2))
        cancel.set()
        server.release.set()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], Cancelled)

    @staticmethod
    def _scrape_cancel(url, cancel, errors):
        try:
            scrape(url, cancel)
        except Exception as exc:
            errors.append(exc)


class ParsingTest(unittest.TestCase):
    def test_space_containing_files_one_per_line(self):
        names = ["A video with many spaces.mp4", "Photo 001 - summer trip.jpg",
                 "Archive of several files.part01.rar", "A  double  spaced archive.zip"]
        keys = [KEY + "/" + name for name in names]
        self.assertEqual(extract_keys("\r\n".join(keys)), keys)

    def test_space_containing_html_lines_and_inline_markup(self):
        document = (f"<p>{KEY}/A <b>long video</b> name.mp4</p>"
                    f"{KEY}/Photo [1080p] (1).jpg<br>"
                    f"<a href='{KEY}/A linked archive.zip'>Download archive</a>"
                    f"<p>{KEY}/A final file.rar (12 MB)</p>")
        self.assertCountEqual(extract_keys(document), [KEY + "/A long video name.mp4",
            KEY + "/Photo [1080p] (1).jpg", KEY + "/A linked archive.zip", KEY + "/A final file.rar"])

    def test_mixed_encoded_spaces_and_multiple_links(self):
        line = KEY + "/First%20file.mp4 " + KEY + "/Second file.zip"
        self.assertEqual(extract_keys(line), [KEY + "/First file.mp4", KEY + "/Second file.zip"])

    def test_plain_html_encoded_and_duplicate_keys(self):
        encoded = quote(KEY + "/file name.zip", safe="/")
        document = f'<p>({KEY}/archive.zip).</p><a href="http://localhost:8888/{KEY}/archive.zip">same</a><p>{encoded}</p>'
        self.assertEqual(extract_keys(document), [KEY + "/archive.zip", KEY + "/file name.zip"])
        self.assertEqual(extract_keys(quote(KEY + "/file name.zip", safe="")), [KEY + "/file name.zip"])

    def test_inline_tags_and_html_entities(self):
        document = f'<p>{KEY}<b>/file</b>.zip</p><p>{KEY}/a&amp;b.zip</p>'
        self.assertEqual(extract_keys(document), [KEY + "/file.zip", KEY + "/a&b.zip"])

    def test_other_key_types_and_query_removal(self):
        keys = ["SSK@a,b,c/name/file.txt", "USK@a,b,c/site/42/file.txt", "KSK@sample.txt"]
        self.assertEqual(extract_keys("\n".join(keys) + "?forcedownload=true"), keys)

    def test_encoded_control_characters_rejected(self):
        self.assertEqual(extract_keys(KEY + "/x%0AEndMessage"), [])

    def test_page_sources_route_to_local_fcp(self):
        self.assertEqual(source_key("localhost:8888/" + KEY + "/index.html?x=1"), KEY + "/index.html")
        self.assertEqual(source_key("freenet:" + KEY), KEY)
        self.assertEqual(source_key("http://[::1]:8888/" + KEY), KEY)
        self.assertIsNone(source_key("https://example.com/" + KEY))
        self.assertIsNone(source_key("http://localhost:8888/"))

    def test_safe_windows_filenames(self):
        self.assertEqual(filename_for(KEY + "/../../CON.txt"), "_CON.txt")
        self.assertEqual(filename_for(KEY + '/a:b*?.zip'), "a_b__.zip")
        self.assertEqual(filename_for(KEY + "/.."), "download.bin")
        self.assertLessEqual(len(filename_for(KEY + "/" + "a" * 300)), 160)
        long_name = filename_for(KEY + "/" + "A long name " * 30 + ".mp4")
        self.assertLessEqual(len(long_name), 160)
        self.assertTrue(long_name.endswith(".mp4"))

    def test_bad_source_and_precancel(self):
        with self.assertRaisesRegex(ValueError, "Paste"):
            scrape("", threading.Event())
        with self.assertRaisesRegex(ValueError, "http"):
            scrape("file:///C:/data", threading.Event())
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(Cancelled):
            scrape("http://localhost:8888/", cancel)


if __name__ == "__main__":
    unittest.main()
