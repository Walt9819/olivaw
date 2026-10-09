r"""Files in the browser chat: what reaches the agent, and what is allowed back out.

Two things here are worth a test rather than a careful read.

The first is that **the browser never names a path**. It uploads a file and gets an id;
it sends an id and gets a file. Every other shape of this feature - "post the path you
want saved", "ask for the path you want back" - is an arbitrary file write and an
arbitrary file read wearing a chat window, on a server that is already authenticated as
the owner. So the tests below hand `send()` and `file_bytes()` paths, ids that were never
minted, and ids whose file has moved, and check that nothing comes of any of them.

The second is the **denylist on the way out**. A path only becomes downloadable because
the agent itself wrote it into a reply - and an agent can be talked into writing anything
by a web page it read. `_is_secret` is what stands between "deliver what the agent sent"
and exfiltrating the profile's .env to whoever planted the instruction.

Run: python tools/test_talk_files.py
"""

import base64
import io
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from wizard import talk as T  # noqa: E402

PASSED, FAILED = [], []


def check(name, cond, extra=""):
    (PASSED if cond else FAILED).append(name)
    print(("  ok   " if cond else "  FAIL ") + name +
          (("\n       " + str(extra)) if (extra and not cond) else ""))


def section(t):
    print("\n=== %s ===" % t)


def b64(data):
    return base64.b64encode(data).decode("ascii")


def write(path, data=b"x"):
    with io.open(path, "wb") as fh:
        fh.write(data)
    return path


class Captured(object):
    """Stands in for the agent's HTTP server, and keeps what was sent to it."""

    def __init__(self, reply=""):
        self.reply = reply
        self.payload = None

    def __call__(self, url, key, data=None, method=None, timeout=25):
        self.payload = data
        return True, {"message": {"role": "assistant", "content": self.reply},
                      "session_id": "s1"}, 200


def main():
    tmp = tempfile.mkdtemp(prefix="talkfiles-")
    ws = os.path.join(tmp, "workspace")
    os.makedirs(ws)
    os.environ["CLAUDE_BRIDGE_WORKSPACE"] = ws

    # ── names ────────────────────────────────────────────────────────────────
    section("a name out of the browser can only ever be a name")
    check("a path walks back to its last component",
          T.safe_name("../../../etc/passwd") == "passwd", T.safe_name("../../../etc/passwd"))
    check("so does a Windows one",
          T.safe_name("C:\\Users\\x\\..\\secreto.pdf") == "secreto.pdf",
          T.safe_name("C:\\Users\\x\\..\\secreto.pdf"))
    check("a name that is only dots does not survive as one",
          T.safe_name("..") == "archivo", T.safe_name(".."))
    check("the characters Windows forbids are replaced, not kept",
          T.safe_name('in<for>me?.pdf') == "in_for_me_.pdf", T.safe_name('in<for>me?.pdf'))
    check("a reserved device name is defused",
          T.safe_name("CON.txt") == "_CON.txt", T.safe_name("CON.txt"))
    check("an empty name still gets one", T.safe_name("") == "archivo")
    long_name = T.safe_name("a" * 300 + ".pdf")
    check("an absurd name is cut but keeps its extension",
          len(long_name) <= 73 and long_name.endswith(".pdf"), long_name)
    check("accents are left alone - they are ordinary characters here",
          T.safe_name("informe año.pdf") == "informe año.pdf", T.safe_name("informe año.pdf"))

    # ── uploads ──────────────────────────────────────────────────────────────
    section("an attachment lands in the agent's folder and nowhere else")
    r = T.save_upload(name="../../escape.txt", data_b64=b64(b"hola"))
    check("the upload is accepted", r.get("ok"), r)
    inside = os.path.realpath(r["file"]["path"]).startswith(
        os.path.realpath(os.path.join(ws, T.ATTACH_DIR)) + os.sep)
    check("and it is written inside the attachments folder, whatever it was called",
          inside, r["file"]["path"])
    check("it is given an id, which is what the browser gets back",
          bool(r["file"].get("id")), r["file"])

    first = r["file"]["path"]
    r2 = T.save_upload(name="escape.txt", data_b64=b64(b"otro"))
    check("a second file with the same name does not overwrite the first",
          r2["file"]["path"] != first and io.open(first, "rb").read() == b"hola",
          r2["file"]["path"])

    check("an empty file is refused",
          not T.save_upload(name="x.txt", data_b64="")["ok"])
    check("something that is not base64 is refused, not written",
          not T.save_upload(name="x.txt", data_b64="no-es-base64!!")["ok"])
    big = T.save_upload(name="x.bin", data_b64=b64(b"0" * (T.MAX_UPLOAD_BYTES + 1)))
    check("a file over the limit is refused, and the message says the limit",
          not big["ok"] and "25.0 MB" in big["detail"], big)
    check("a data: prefix from the browser is tolerated",
          T.save_upload(name="y.txt",
                        data_b64="data:text/plain;base64," + b64(b"hola"))["ok"])

    section("each agent's attachments go to that agent's own folder")
    other = T.workspace_for("daneel", install_dir=os.path.join(tmp, "install"))
    check("an agent with no registry row still gets a folder of its own",
          os.path.realpath(other) != os.path.realpath(ws) and "daneel" in other.lower(),
          other)
    check("and the main agent keeps the workspace it already had",
          os.path.realpath(T.workspace_for("default")) == os.path.realpath(ws),
          T.workspace_for("default"))

    # ── what send() does with them ───────────────────────────────────────────
    section("the browser sends ids; only this process can turn one into a path")
    T._require = lambda profile, hermes: ("clave", 9999, None)   # noqa: SLF001
    cap = Captured("Listo.")
    T._http = cap                                                # noqa: SLF001

    pdf = T.save_upload(name="informe.pdf", data_b64=b64(b"%PDF-1.4"))["file"]
    out = T.send(session_id="s1", text="revisa esto", files=[pdf["id"]])
    sent = cap.payload["message"]
    check("the turn names where the file landed, so the agent can open it",
          pdf["path"] in sent, sent)
    check("and it is a plain string, because there is no image in it",
          isinstance(sent, str), type(sent).__name__)
    check("the owner's own words are still the start of it",
          sent.startswith("revisa esto"), sent[:60])
    check("the answer comes back", out.get("ok"), out)

    cap.payload = None
    T.send(session_id="s1", text="hola", files=["id-que-nadie-acuño"])
    check("an id this process never minted buys nothing",
          cap.payload["message"] == "hola", cap.payload["message"])

    cap.payload = None
    T.send(session_id="s1", text="hola", files=[os.path.join(ws, "informe.pdf")])
    check("and neither does sending a path where an id goes",
          cap.payload["message"] == "hola", cap.payload["message"])

    gone = T.save_upload(name="borrado.txt", data_b64=b64(b"x"))["file"]
    os.remove(gone["path"])
    cap.payload = None
    T.send(session_id="s1", text="hola", files=[gone["id"]])
    check("an id whose file is gone is dropped rather than named",
          cap.payload["message"] == "hola", cap.payload["message"])

    cap.payload = None
    out = T.send(session_id="s1", text="", files=[])
    check("nothing to say and nothing to send is still refused",
          not out["ok"] and cap.payload is None, out)
    cap.payload = None
    out = T.send(session_id="s1", text="", files=[pdf["id"]])
    check("but a file on its own is a message",
          out.get("ok") and "informe.pdf" in cap.payload["message"], out)

    section("an image rides inside the turn; a big one goes by path alone")
    png = T.save_upload(name="captura.png", data_b64=b64(b"\x89PNG" + b"x" * 40))["file"]
    cap.payload = None
    T.send(session_id="s1", text="¿qué ves?", files=[png["id"]])
    msg = cap.payload["message"]
    parts = [p.get("type") for p in msg] if isinstance(msg, list) else []
    check("the message becomes parts, with the image among them",
          parts == ["text", "image_url"], parts)
    url = msg[1]["image_url"]["url"] if parts else ""
    check("the image travels as an image data URL",
          url.startswith("data:image/png;base64,"), url[:40])
    check("and its path is named too, so the agent can also work on the file",
          png["path"] in msg[0]["text"], msg[0]["text"][-120:])

    huge = os.path.join(ws, T.ATTACH_DIR, "enorme.png")
    write(huge, b"x" * (T.MAX_INLINE_IMAGE_BYTES + 10))
    rec = {"name": "enorme.png", "path": huge, "size": os.path.getsize(huge),
           "kind": "image", "mime": "image/png"}
    rec["id"] = T._remember(T._UPLOADS, rec)                     # noqa: SLF001
    cap.payload = None
    T.send(session_id="s1", text="mira", files=[rec["id"]])
    check("an image too big for Hermes' 10MB body is not inlined",
          isinstance(cap.payload["message"], str), type(cap.payload["message"]).__name__)
    check("but it is still delivered, as a path",
          huge in cap.payload["message"], cap.payload["message"][-120:])

    cap.payload = None
    T.send(session_id="s1", text="sólo texto")
    check("a turn with no attachment is still a plain string, as it always was",
          cap.payload["message"] == "sólo texto", cap.payload["message"])

    section("the agent is told that this screen shows files")
    check("every turn carries the brief", "MEDIA:" in cap.payload.get("system_message", ""),
          cap.payload.get("system_message", "")[:80])
    check("and it is sent as system_message, which Hermes appends to the persona "
          "instead of replacing it", "system_message" in cap.payload)

    # ── what comes back ──────────────────────────────────────────────────────
    section("the reply is read for files instead of printed raw")
    doc = write(os.path.join(ws, "informe final.pdf"), b"%PDF-1.4 x")
    body, files = T.read_files_out(
        "Aquí tienes el informe.\n\nMEDIA:%s\n\nY la gráfica: "
        "![image](data:image/png;base64,AAAA) listo." % doc)
    check("the sentence is what is left", body == "Aquí tienes el informe.\n\nY la gráfica: listo.",
          repr(body))
    check("both files came out", len(files) == 2, files)
    img = [f for f in files if f["kind"] == "image"][0]
    check("the image keeps its data URL, so the page can show it",
          img["data_url"].startswith("data:image/png;base64,"), img)
    pdfout = [f for f in files if f["kind"] == "file"][0]
    check("the file is named as the agent named it",
          pdfout["name"] == "informe final.pdf", pdfout)
    check("and it is given an id to download it with", bool(pdfout.get("id")), pdfout)
    check("with its size in words, because the owner is about to click it",
          pdfout["human"].endswith("B"), pdfout)

    quoted = write(os.path.join(ws, "con espacios.csv"), b"a,b")
    body2, f2 = T.read_files_out('listo MEDIA:"%s"' % quoted)
    check("a quoted path works, which is how a path with spaces arrives",
          len(f2) == 1 and f2[0]["name"] == "con espacios.csv", (body2, f2))
    body3, f3 = T.read_files_out("listo MEDIA:`%s`" % quoted)
    check("and a backticked one", len(f3) == 1, (body3, f3))

    section("what the agent is NOT allowed to hand over")
    env = write(os.path.join(ws, ".env"), b"API_KEY=sk-123")
    body4, f4 = T.read_files_out("Toma: MEDIA:%s" % env)
    check("a .env never becomes a download", not f4, f4)
    check("and the tag stays on screen rather than vanishing silently",
          "MEDIA:" in body4, body4)
    for name, blob in (("server.pem", b"k"), ("tokens.json", b"{}"), ("id_rsa", b"k")):
        p = write(os.path.join(ws, name), blob)
        check("%s is refused too" % name, not T.read_files_out("MEDIA:%s" % p)[1])
    ok_doc = write(os.path.join(ws, "informe de tokens.pdf"), b"%PDF")
    check("a document whose NAME mentions tokens is not a credential",
          len(T.read_files_out("MEDIA:%s" % ok_doc)[1]) == 1)
    missing = os.path.join(ws, "no-existe.pdf")
    body5, f5 = T.read_files_out("MEDIA:%s" % missing)
    check("a path that is not there is left visible, not invented",
          not f5 and missing in body5, (body5, f5))
    check("a relative path is not resolved against whatever this process' cwd is",
          not T.read_files_out("MEDIA:../../informe.pdf")[1])

    section("downloading, by the id and only by the id")
    got = T.file_bytes(pdfout["id"])
    check("the file comes back", got.get("ok"), got)
    check("with its own bytes", base64.b64decode(got["data_b64"]) == b"%PDF-1.4 x", got)
    check("and its own name and type",
          got["name"] == "informe final.pdf" and got["mime"] == "application/pdf", got)
    check("an id nobody minted gets nothing", not T.file_bytes("inventado")["ok"])
    check("no id at all gets nothing", not T.file_bytes("")["ok"])
    # The one that matters: a real, readable, perfectly innocent path, handed in where an
    # id goes. If this ever passes, the download route is a file-read route.
    readable = write(os.path.join(ws, "cualquiera.txt"), b"contenido")
    check("a real path handed in as an id reads nothing",
          not T.file_bytes(readable)["ok"], T.file_bytes(readable))
    os.remove(doc)
    check("an id whose file has moved since is refused, not guessed at",
          not T.file_bytes(pdfout["id"])["ok"], T.file_bytes(pdfout["id"]))

    swap = write(os.path.join(ws, "informe2.pdf"), b"%PDF")
    rec2 = T.read_files_out("MEDIA:%s" % swap)[1][0]
    os.remove(swap)
    write(os.path.join(ws, "informe2.pdf"), b"%PDF again")
    check("a file replaced between the reply and the click is re-checked, not cached",
          T.file_bytes(rec2["id"])["ok"] and
          base64.b64decode(T.file_bytes(rec2["id"])["data_b64"]) == b"%PDF again")

    section("the tables cannot grow without end")
    before = len(T._GRANTS)                                      # noqa: SLF001
    for i in range(T.GRANT_LIMIT + 50):
        T._remember(T._GRANTS, {"name": "x", "path": "x", "mime": "x"})  # noqa: SLF001
    check("old ids fall off the end rather than piling up for ever",
          len(T._GRANTS) == T.GRANT_LIMIT, (before, len(T._GRANTS)))     # noqa: SLF001

    section("the whole round trip")
    report = write(os.path.join(ws, "resumen.pdf"), b"%PDF resumen")
    cap.reply = "Te dejo el resumen.\nMEDIA:%s" % report
    out = T.send(session_id="s1", text="hazme un resumen")
    check("the reply the owner reads has no path in it",
          out["reply"] == "Te dejo el resumen.", repr(out["reply"]))
    check("and the file is next to it, ready to download",
          len(out["files"]) == 1 and out["files"][0]["name"] == "resumen.pdf", out["files"])
    check("which really does download",
          base64.b64decode(T.file_bytes(out["files"][0]["id"])["data_b64"]) == b"%PDF resumen")

    check("a refused session id never reaches the agent",
          not T.send(session_id="no válida!", text="hola")["ok"])

    section("the seam between the page and this module")
    # The router without a socket, the way tools/test_team_routes.py drives it. This is
    # where a feature is wired up wrong rather than written wrong: a field the page sends
    # and the server drops, or a route that quietly never existed.
    from wizard import wizard_server as WS  # noqa: PLC0415
    saved = (WS.INSTALL_DIR, WS.which)
    WS.INSTALL_DIR = tmp
    WS.which = lambda name: "hermes"
    with io.open(os.path.join(tmp, "agents.json"), "w", encoding="utf-8") as fh:
        json.dump({"agents": [{"slug": "daneel", "name": "Daneel", "profile": "daneel"}]}, fh)
    h = WS.Handler.__new__(WS.Handler)

    res = h.dispatch("/api/talk/attach", {"name": "desde la pagina.txt",
                                          "data": b64(b"hola")})
    check("talk/attach reaches save_upload and comes back with an id",
          res.get("ok") and res["file"].get("id"), res)
    got = h.dispatch("/api/talk/file", {"id": res["file"]["id"]})
    check("talk/file refuses an upload id - only the agent's own files are downloadable",
          not got.get("ok"), got)

    cap.payload = None
    h.dispatch("/api/talk/send", {"session_id": "s1", "text": "con adjunto",
                                  "files": [res["file"]["id"]]})
    check("talk/send passes the ids through instead of dropping them",
          cap.payload and "desde la pagina.txt" in cap.payload["message"],
          cap.payload and cap.payload["message"])

    # The body cap. Until the chat could carry files nothing here was bigger than a form,
    # so do_POST read whatever Content-Length claimed; an upload route makes that a
    # promise to allocate whatever a caller says.
    class Rfile(object):
        read_calls = []

        def read(self, n):
            Rfile.read_calls.append(n)
            return b"{}"

    answers = []
    h.path = "/api/talk/attach"
    h.headers = {"Content-Length": str(WS.MAX_BODY_BYTES + 1), "Host": "127.0.0.1"}
    h.rfile = Rfile()
    h._json = lambda obj, code=200: answers.append((code, obj))
    h._local_host = lambda: True
    h._authed = lambda: True
    h.do_POST()
    check("a body over the cap is refused with 413",
          answers and answers[0][0] == 413, answers)
    check("and its bytes are never read into memory", not Rfile.read_calls, Rfile.read_calls)

    WS.INSTALL_DIR, WS.which = saved

    os.environ.pop("CLAUDE_BRIDGE_WORKSPACE", None)
    shutil.rmtree(tmp, ignore_errors=True)

    print("\n%d passed, %d failed" % (len(PASSED), len(FAILED)))
    for f in FAILED:
        print("  - " + f)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
