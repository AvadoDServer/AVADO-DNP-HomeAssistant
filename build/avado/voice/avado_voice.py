#!/usr/bin/env python3
"""AVADO voice helper for Home Assistant: "Use my AVADO's assistant for voice".

One switch on Home Assistant's AVADO page (https://homeassistant.my.ava.do/avado/, also the Admin's
Setup tab). Turning it on sets Home Assistant up to use only this box, no cloud:

* speech to text:  Wyoming, assistant.my.ava.do:10300 (faster-whisper in the Private Assistant)
* the answer:      Ollama, http://assistant.my.ava.do:11434 (the Private Assistant's model), as the
                   conversation agent "AVADO assistant" (Home Assistant's own sentences first, so
                   "turn on the kitchen light" is handled at once; anything else goes to the model,
                   which may also control the devices exposed to Assist)
* text to speech:  Wyoming, assistant.my.ava.do:10200 (Piper in the Private Assistant)
* an Assist pipeline "AVADO voice" with these three, made the preferred one.

Turning it off removes all of that again and puts back the pipeline that was preferred before.

The Private Assistant lets only this app through (its doors check the caller's address on the AVADO
network) and hands out the model key once, to this app (POST /avado/connect). Home Assistant's API is
used with the AVADO system user (avado_ha_auth.py) from 127.0.0.1 only. Standard library only (Home
Assistant's own Python). Listens on 127.0.0.1:8124; nginx forwards /avado/ here.
"""

import base64
import hashlib
import hmac
import json
import os
import socket
import struct
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
PUBLIC = os.path.join(HERE, "public")
STATE_PATH = os.environ.get("AVADO_VOICE_STATE", "/config/.avado/voice.json")
TOKEN_PATH = os.environ.get("AVADO_HA_TOKEN", "/config/.avado/ha-api.json")
HA = os.environ.get("AVADO_HA_URL", "http://127.0.0.1:8123")
ASSISTANT_HOST = os.environ.get("AVADO_ASSISTANT_HOST", "assistant.my.ava.do")
DOOR = "http://%s:11434" % ASSISTANT_HOST
STT_PORT, TTS_PORT = 10300, 10200
LISTEN = ("127.0.0.1", int(os.environ.get("AVADO_VOICE_PORT", "8124")))
PIPELINE_NAME = "AVADO voice"
AGENT_NAME = "AVADO assistant"
ASSISTANT_STORE = "http://my.ava.do/#/installer/assistant.avado.dnp.dappnode.eth"
PROMPT = ("You are the voice assistant of this home, running on the family's own AVADO box. "
          "Answer questions truthfully, in one or two short sentences of plain text: your answer is read out loud. "
          "Answer in the language you are spoken to.")
ALLOWED_ORIGINS = ("http://homeassistant.my.ava.do", "https://homeassistant.my.ava.do",
                   "http://my.ava.do", "https://my.ava.do")
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
       "base-uri 'none'; form-action 'self'; frame-ancestors 'self' http://my.ava.do https://my.ava.do")


def log(msg):
    sys.stderr.write("[avado] voice: %s\n" % msg)
    sys.stderr.flush()


# --------------------------------------------------------------------------
# Home Assistant API (REST + WebSocket), as the AVADO system user
# --------------------------------------------------------------------------


def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def access_token(token_path=TOKEN_PATH, now=None):
    """A 30-minute access token, signed like Home Assistant's own (HS256 with the refresh token's key)."""
    with open(token_path) as f:
        t = json.load(f)
    now = int(now if now is not None else time.time())
    head = b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = b64url(json.dumps({"iss": t["refresh_token_id"], "iat": now, "exp": now + 1800},
                             separators=(",", ":")).encode())
    sig = hmac.new(t["jwt_key"].encode(), ("%s.%s" % (head, body)).encode(), hashlib.sha256).digest()
    return "%s.%s.%s" % (head, body, b64url(sig))


class HAError(Exception):
    pass


def http_json(url, data=None, method=None, headers=None, timeout=20):
    body = None if data is None else json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, method=method or ("POST" if body is not None else "GET"))
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        return json.loads(raw) if raw else {}


class WebSocket:
    """Just enough of RFC 6455 for a short session with Home Assistant (text frames, ping, close)."""

    def __init__(self, host, port, path, timeout=20):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(("GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                           "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % (
                               path, host, port, key)).encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise HAError("Home Assistant closed the connection")
            head += chunk
        head, self.buf = head.split(b"\r\n\r\n", 1)
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise HAError("no websocket upgrade")

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise HAError("connection closed")
            self.buf += chunk
        data, self.buf = self.buf[:n], self.buf[n:]
        return data

    def _frame(self, opcode, payload):
        mask = os.urandom(4)
        n = len(payload)
        if n < 126:
            head = struct.pack("!BB", 0x80 | opcode, 0x80 | n)
        elif n < 65536:
            head = struct.pack("!BBH", 0x80 | opcode, 0x80 | 126, n)
        else:
            head = struct.pack("!BBQ", 0x80 | opcode, 0x80 | 127, n)
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def send(self, obj):
        self._frame(0x1, json.dumps(obj).encode())

    def recv(self):
        message = b""
        while True:
            b1, b2 = self._read(2)
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack("!H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", self._read(8))[0]
            payload = self._read(n)
            op = b1 & 0x0F
            if op == 0x8:
                raise HAError("Home Assistant closed the session")
            if op == 0x9:
                self._frame(0xA, payload)
                continue
            if op == 0xA:
                continue
            message += payload
            if b1 & 0x80:
                return json.loads(message.decode("utf-8", "replace"))

    def close(self):
        try:
            self._frame(0x8, struct.pack("!H", 1000))
            self.sock.close()
        except OSError:
            pass


class HomeAssistant:
    def __init__(self, base=HA, token=access_token):
        self.base = base
        self.token = token

    def _h(self):
        return {"Authorization": "Bearer " + self.token()}

    def get(self, path):
        return http_json(self.base + path, headers=self._h())

    def post(self, path, data):
        return http_json(self.base + path, data=data, headers=self._h(), timeout=120)

    def delete(self, path):
        return http_json(self.base + path, method="DELETE", headers=self._h())

    def up(self):
        try:
            self.get("/api/")
            return True
        except Exception:
            return False

    def onboarded(self):
        """True once the owner finished Home Assistant's first-run set-up."""
        try:
            steps = http_json(self.base + "/api/onboarding", timeout=5)
        except urllib.error.HTTPError as e:
            return e.code == 404  # the onboarding API is gone once everything is done
        except Exception:
            return None
        return all(s.get("done") for s in steps if isinstance(s, dict))

    def ws(self, *commands):
        """Runs commands (dicts without id) on one WebSocket session; returns their results."""
        u = urllib.parse.urlsplit(self.base)
        w = WebSocket(u.hostname, u.port or 80, "/api/websocket")
        try:
            if w.recv().get("type") != "auth_required":
                raise HAError("unexpected greeting")
            w.send({"type": "auth", "access_token": self.token()})
            if w.recv().get("type") != "auth_ok":
                raise HAError("Home Assistant refused the AVADO system user")
            out = []
            for i, cmd in enumerate(commands, start=1):
                w.send(dict(cmd, id=i))
                while True:
                    msg = w.recv()
                    if msg.get("id") == i and msg.get("type") == "result":
                        break
                if not msg.get("success"):
                    raise HAError("%s: %s" % (cmd.get("type"), (msg.get("error") or {}).get("message")))
                out.append(msg.get("result"))
            return out
        finally:
            w.close()

    def flow(self, handler, steps, subentry=False):
        """Starts a config flow (or a subentry flow) and answers its forms with `steps` in order."""
        base = "/api/config/config_entries/subentries/flow" if subentry else "/api/config/config_entries/flow"
        r = self.post(base, {"handler": handler, "show_advanced_options": False})
        for data in steps:
            if r.get("type") != "form":
                break
            r = self.post("%s/%s" % (base, r["flow_id"]), data)
        if r.get("type") == "abort":
            raise HAError("%s: %s" % (handler, r.get("reason")))
        if r.get("type") != "create_entry":
            raise HAError("%s: %s" % (handler, r.get("errors") or r.get("type")))
        return r

    def entries(self, domain):
        return self.get("/api/config/config_entries/entry?" + urllib.parse.urlencode({"domain": domain}))


# --------------------------------------------------------------------------
# The Private Assistant's door
# --------------------------------------------------------------------------


class AssistantError(Exception):
    def __init__(self, text, kind):
        super().__init__(text)
        self.kind = kind


def door(path, data=None, key=None, timeout=15):
    headers = {"Authorization": "Bearer " + key} if key else {}
    try:
        return http_json(DOOR + path, data=data, headers=headers, timeout=timeout)
    except urllib.error.HTTPError as e:
        if e.code == 403:
            raise AssistantError("The Private Assistant does not let this app in.", "refused")
        if e.code == 401:
            raise AssistantError("The Private Assistant no longer knows this app.", "key")
        raise AssistantError("The Private Assistant answered %d." % e.code, "error")
    except (OSError, ValueError):
        raise AssistantError("The Private Assistant is not installed or not running.", "missing")


def language_of(ha_language):
    lang = str(ha_language or "en").replace("_", "-")
    return lang.split("-")[0].lower() or "en"


# --------------------------------------------------------------------------
# The switch
# --------------------------------------------------------------------------


class Voice:
    def __init__(self, ha=None, state_path=STATE_PATH, door_fn=door, clock=time.monotonic, sleep=time.sleep):
        self.ha = ha or HomeAssistant()
        self.path = state_path
        self.door = door_fn
        self.clock = clock
        self.sleep = sleep
        self.lock = threading.Lock()
        self.job = None
        self.state = self._load()
        self.assistant = {"found": None, "model_name": None, "voice_ready": False, "checked": None}

    # -- state
    def _load(self):
        try:
            with open(self.path) as f:
                d = json.load(f)
            if isinstance(d, dict):
                d.setdefault("on", False)
                return d
        except (OSError, ValueError):
            pass
        return {"on": False}

    def _save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(self.state, f, indent=1)
        os.replace(tmp, self.path)

    def _set(self, **kw):
        with self.lock:
            self.state.update(kw)
            self._save()

    # -- what the page shows
    def check_assistant(self, force=False):
        now = self.clock()
        if not force and self.assistant["checked"] is not None and now - self.assistant["checked"] < 10:
            return self.assistant
        info = {"checked": now}
        try:
            st = self.door("/avado/status", key=self.state.get("key")) if self.state.get("key") else \
                self.door("/avado/connect", {})
            if "key" in st and not self.state.get("key"):
                self._set(key=st["key"])
            info.update(found=True, model=st.get("model"), model_name=st.get("model_name"),
                        paused=bool(st.get("paused")), voice_ready=bool((st.get("voice") or {}).get("ready")),
                        version=st.get("version"))
        except AssistantError as e:
            if e.kind == "key":
                self._set(key=None)
            info.update(found=False if e.kind == "missing" else None, problem=str(e), kind=e.kind,
                        voice_ready=False, model_name=None)
        self.assistant = info
        return info

    def status(self):
        a = self.check_assistant()
        st = dict(self.state)
        working = self.job is not None and self.job.is_alive()
        return {
            "on": bool(st.get("on")),
            "working": working,
            "step": st.get("step") if working else None,
            "problem": st.get("problem"),
            "assistant": {"found": a.get("found"), "model_name": a.get("model_name"), "has_model": bool(a.get("model")),
                          "paused": a.get("paused", False), "voice_ready": a.get("voice_ready", False),
                          "problem": a.get("problem"), "store": ASSISTANT_STORE},
            "ha": {"onboarded": self.ha.onboarded()},
            "language": st.get("language"),
            "pipeline": PIPELINE_NAME,
        }

    # -- turning on and off (in the background: the first time takes minutes)
    def request(self, on):
        with self.lock:
            if self.job is not None and self.job.is_alive():
                return "Please wait: the last change is still being made."
            self.state["problem"] = None
            self.job = threading.Thread(target=self._run, args=(bool(on),), daemon=True)
            self.job.start()
        return None

    def _run(self, on):
        try:
            self.turn_on() if on else self.turn_off()
        except (AssistantError, HAError, OSError, ValueError, KeyError) as e:
            log("could not turn voice %s: %r" % ("on" if on else "off", e))
            text = str(e) if isinstance(e, AssistantError) else (
                "Home Assistant did not accept the change (%s). Please try again." % e)
            self._set(problem=text, step=None)
        else:
            self._set(problem=None, step=None)
        self.assistant["checked"] = None

    def _step(self, text):
        self._set(step=text)
        log(text)

    def _key(self):
        """The model key, from the Private Assistant (once), kept in our state."""
        if self.state.get("key"):
            try:
                self.door("/avado/status", key=self.state["key"])
                return self.state["key"]
            except AssistantError as e:
                if e.kind != "key":
                    raise
        info = self.door("/avado/connect", {})
        self._set(key=info["key"])
        return info["key"]

    def turn_on(self):
        if self.ha.onboarded() is False:
            raise AssistantError("Finish Home Assistant's first set-up first (create your account), then switch "
                                 "this on.", "setup")
        self._step("Connecting to your Private Assistant")
        key = self._key()
        info = self.door("/avado/status", key=key)
        if not info.get("model"):
            raise AssistantError("Your Private Assistant has no AI model yet. Open it and get the model first.",
                                 "model")
        cfg = self.ha.get("/api/config")
        lang = language_of(cfg.get("language"))
        self._set(language=lang)
        self._step("Starting speech on your AVADO (the first time it fetches about 150 MB)")
        self.door("/avado/voice", {"on": True, "language": lang}, key=key)
        deadline = self.clock() + 900
        while True:
            st = self.door("/avado/status", key=key)
            if (st.get("voice") or {}).get("ready"):
                break
            if self.clock() > deadline:
                raise AssistantError("The speech part of your Private Assistant did not start. Please try again "
                                     "later.", "voice")
            self.sleep(5)
        self._step("Setting up Home Assistant")
        made = self.setup_ha(key, info["model"], lang)
        self._set(on=True, **made)
        log("voice is on (pipeline %s)" % made.get("pipeline_id"))

    def setup_ha(self, key, model, lang):
        ha, st = self.ha, self.state
        url = DOOR
        # 1. the model: an Ollama entry for the Private Assistant, with its key
        ollama = next((e for e in ha.entries("ollama") if e.get("entry_id") == st.get("ollama_entry")), None)
        if ollama is None:
            for e in ha.entries("ollama"):  # one we made before but lost track of
                if e.get("title") == url or e.get("title") == url + "/":
                    ha.delete("/api/config/config_entries/entry/%s" % e["entry_id"])
            r = ha.flow("ollama", [{"url": url, "api_key": key}])
            ollama_id = r["result"]["entry_id"]
        else:
            ollama_id = ollama["entry_id"]
        # 2. the conversation agent: a subentry with the assistant's model
        subs = ha.ws({"type": "config_entries/subentries/list", "entry_id": ollama_id})[0]
        sub = next((x for x in subs if x.get("subentry_id") == st.get("agent_subentry")), None)
        if sub is not None and st.get("model") not in (None, model):
            ha.ws({"type": "config_entries/subentries/delete", "entry_id": ollama_id, "subentry_id": sub["subentry_id"]})
            sub = None
        if sub is None:
            ha.flow([ollama_id, "conversation"], [{
                "name": AGENT_NAME, "model": model, "prompt": PROMPT, "llm_hass_api": ["assist"],
                "num_ctx": 8192, "max_history": 10, "keep_alive": 600, "think": False,
            }], subentry=True)
            subs = ha.ws({"type": "config_entries/subentries/list", "entry_id": ollama_id})[0]
            sub = next((x for x in subs if x.get("title") == AGENT_NAME), None) or (subs[-1] if subs else None)
        # 3. speech to text and text to speech
        wyoming = {e["entry_id"]: e for e in ha.entries("wyoming")}
        ids = {}
        for what, port in (("stt", STT_PORT), ("tts", TTS_PORT)):
            eid = st.get(what + "_entry")
            if eid not in wyoming:
                eid = ha.flow("wyoming", [{"host": ASSISTANT_HOST, "port": port}])["result"]["entry_id"]
            ids[what] = eid
        # 4. their entities
        reg = ha.ws({"type": "config/entity_registry/list"})[0]

        def entity(entry_id, domain, sub_id=None):
            for e in reg:
                if e.get("config_entry_id") == entry_id and e.get("entity_id", "").startswith(domain + ".") and \
                        (sub_id is None or e.get("config_subentry_id") == sub_id):
                    return e["entity_id"]
            return None

        agent = entity(ollama_id, "conversation", sub and sub.get("subentry_id"))
        stt = entity(ids["stt"], "stt")
        tts = entity(ids["tts"], "tts")
        if not (agent and stt and tts):
            raise HAError("the new parts did not appear in Home Assistant (agent %s, stt %s, tts %s)" % (agent, stt, tts))
        # 5. the pipeline, preferred
        listed = ha.ws({"type": "assist_pipeline/pipeline/list"})[0]
        pipes = {p["id"]: p for p in listed.get("pipelines") or []}
        preferred = listed.get("preferred_pipeline")
        fields = {
            "name": PIPELINE_NAME, "language": lang, "conversation_engine": agent, "conversation_language": lang,
            "stt_engine": stt, "stt_language": lang, "tts_engine": tts, "tts_language": tts_language(lang),
            "tts_voice": voice_name(lang), "wake_word_entity": None, "wake_word_id": None,
            "prefer_local_intents": True,
        }
        pid = st.get("pipeline_id")
        if pid in pipes:
            ha.ws(dict(fields, type="assist_pipeline/pipeline/update", pipeline_id=pid))
        else:
            pid = ha.ws(dict(fields, type="assist_pipeline/pipeline/create"))[0]["id"]
        before = st.get("previous_pipeline")
        if preferred and preferred != pid:
            before = preferred
        ha.ws({"type": "assist_pipeline/pipeline/set_preferred", "pipeline_id": pid})
        return {"ollama_entry": ollama_id, "agent_subentry": sub and sub.get("subentry_id"), "agent": agent,
                "stt_entry": ids["stt"], "tts_entry": ids["tts"], "pipeline_id": pid, "previous_pipeline": before,
                "model": model}

    def turn_off(self):
        st, ha = self.state, self.ha
        self._step("Putting Home Assistant's voice back as it was")
        if ha.up():
            listed = ha.ws({"type": "assist_pipeline/pipeline/list"})[0]
            pipes = {p["id"]: p for p in listed.get("pipelines") or []}
            pid = st.get("pipeline_id")
            if pid in pipes:
                back = st.get("previous_pipeline")
                if back not in pipes or back == pid:
                    back = next((p for p in pipes if p != pid), None)
                if back and listed.get("preferred_pipeline") == pid:
                    ha.ws({"type": "assist_pipeline/pipeline/set_preferred", "pipeline_id": back})
                ha.ws({"type": "assist_pipeline/pipeline/delete", "pipeline_id": pid})
            have = {e["entry_id"] for d in ("ollama", "wyoming") for e in ha.entries(d)}
            for k in ("ollama_entry", "stt_entry", "tts_entry"):
                if st.get(k) in have:
                    ha.delete("/api/config/config_entries/entry/%s" % st[k])
        if st.get("key"):
            try:
                self.door("/avado/voice", {"on": False}, key=st["key"])
            except AssistantError as e:
                log("could not tell the Private Assistant: %s" % e)
        self._set(on=False, pipeline_id=None, ollama_entry=None, agent_subentry=None, agent=None, stt_entry=None,
                  tts_entry=None)
        log("voice is off")

    def reconcile(self):
        """While on: follow the Private Assistant's model and put back anything removed. Quiet when all is well."""
        if not self.state.get("on") or (self.job is not None and self.job.is_alive()):
            return
        try:
            a = self.check_assistant(force=True)
            if not a.get("found") or not a.get("model") or not self.ha.up():
                return
            pipes = {p["id"] for p in self.ha.ws({"type": "assist_pipeline/pipeline/list"})[0].get("pipelines") or []}
            if a["model"] != self.state.get("model") or self.state.get("pipeline_id") not in pipes:
                log("bringing the voice set-up up to date (model %s)" % a["model"])
                self.request(True)
        except Exception as e:
            log("check failed: %r" % e)


def tts_language(lang):
    v = voice_name(lang)
    return v.split("-")[0]


VOICES = {"en": "en_US-lessac-medium", "de": "de_DE-thorsten-medium", "sl": "sl_SI-artur-medium",
          "it": "it_IT-paola-medium", "fr": "fr_FR-siwis-medium", "es": "es_ES-sharvard-medium",
          "nl": "nl_NL-mls-medium", "pl": "pl_PL-gosia-medium", "cs": "cs_CZ-jirka-medium",
          "sk": "sk_SK-lili-medium", "hu": "hu_HU-anna-medium", "sr": "sr_RS-serbski_institut-medium"}


def voice_name(lang):
    """The same choice as the Private Assistant's voice.py."""
    return VOICES.get(language_of(lang), VOICES["en"])


def read(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return None


# --------------------------------------------------------------------------
# The page
# --------------------------------------------------------------------------

TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".js": "application/javascript; charset=utf-8", ".png": "image/png", ".svg": "image/svg+xml"}


def make_handler(voice, public=PUBLIC):
    class Handler(BaseHTTPRequestHandler):
        server_version = "avado-voice"
        sys_version = ""

        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json; charset=utf-8"):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "same-origin")
            self.send_header("Content-Security-Policy", CSP)
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path in ("/avado", "/avado/voice"):
                self.send_response(302)
                self.send_header("Location", "/avado/")
                self.end_headers()
                return
            if path == "/avado/api/status":
                self._send(200, voice.status())
                return
            name = path[len("/avado/"):] if path.startswith("/avado/") else None
            name = name or "index.html"
            if not path.startswith("/avado/") or "/" in name or name.startswith(".") or \
                    os.path.splitext(name)[1] not in TYPES or not os.path.isfile(os.path.join(public, name)):
                self._send(404, {"error": "Not found"})
                return
            with open(os.path.join(public, name), "rb") as f:
                self._send(200, f.read(), TYPES[os.path.splitext(name)[1]])

        def do_POST(self):
            path = self.path.split("?", 1)[0]
            origin = self.headers.get("Origin")
            # A custom header and our own page (or the Admin around it): another site cannot press it
            if self.headers.get("X-Avado-Request") != "1" or (origin and origin not in ALLOWED_ORIGINS):
                self._send(403, {"error": "Please use the switch on this page."})
                return
            if path != "/avado/api/voice":
                self._send(404, {"error": "Not found"})
                return
            try:
                n = min(int(self.headers.get("Content-Length") or 0), 1024)
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                body = {}
            problem = voice.request(bool(isinstance(body, dict) and body.get("on")))
            if problem:
                self._send(409, {"error": problem})
                return
            self._send(200, voice.status())

    return Handler


def main():
    v = Voice()
    server = ThreadingHTTPServer(LISTEN, make_handler(v))
    server.daemon_threads = True

    def checks():
        time.sleep(60)
        while True:
            v.reconcile()
            time.sleep(600)
    threading.Thread(target=checks, daemon=True).start()
    log("page on %s:%d; voice is %s" % (LISTEN[0], LISTEN[1], "on" if v.state.get("on") else "off"))
    server.serve_forever()


if __name__ == "__main__":
    main()
