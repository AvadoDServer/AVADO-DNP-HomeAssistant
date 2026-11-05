"""Tests for the AVADO voice helper (standard library only): python3 -m unittest discover -s tests"""

import base64
import hashlib
import hmac
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import avado_ha_auth as auth  # noqa: E402
import avado_voice as v  # noqa: E402


class AuthStore(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.store = os.path.join(self.dir, ".storage", "auth")
        self.ours = os.path.join(self.dir, ".avado", "ha-api.json")

    def test_new_store_has_groups_and_a_hidden_admin(self):
        self.assertIn("new user store", auth.ensure(self.store, self.ours))
        st = json.load(open(self.store))
        self.assertEqual(st["version"], 1)
        self.assertEqual({g["id"] for g in st["data"]["groups"]}, {"system-admin", "system-users", "system-read-only"})
        (u,) = st["data"]["users"]
        self.assertTrue(u["system_generated"])
        self.assertTrue(u["local_only"])
        self.assertFalse(u["is_owner"])
        self.assertEqual(u["group_ids"], ["system-admin"])
        (t,) = st["data"]["refresh_tokens"]
        self.assertEqual(t["token_type"], "system")
        self.assertEqual(os.stat(self.store).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(self.ours).st_mode & 0o777, 0o600)
        self.assertIsNone(auth.ensure(self.store, self.ours))   # nothing to do the next time

    def test_existing_users_are_kept(self):
        os.makedirs(os.path.dirname(self.store))
        owner = {"id": "o", "group_ids": ["system-admin"], "is_owner": True, "is_active": True, "name": "Me",
                 "system_generated": False, "local_only": False}
        tok = {"id": "t1", "user_id": "o", "token_type": "normal", "jwt_key": "k", "created_at": "2026-01-01T00:00:00+00:00"}
        json.dump({"version": 1, "minor_version": 1, "key": "auth", "data": {
            "users": [owner], "groups": [{"id": "system-admin", "name": "Administrators"}], "credentials": [],
            "refresh_tokens": [tok]}}, open(self.store, "w"))
        self.assertEqual(auth.ensure(self.store, self.ours), "AVADO system user ready")
        st = json.load(open(self.store))
        self.assertEqual(st["data"]["users"][0], owner)
        self.assertEqual(st["data"]["refresh_tokens"][0], tok)
        self.assertEqual(len(st["data"]["users"]), 2)
        os.remove(self.ours)   # our key file lost: a new token, the old one of ours removed
        auth.ensure(self.store, self.ours)
        st = json.load(open(self.store))
        self.assertEqual(len(st["data"]["users"]), 2)
        self.assertEqual(len(st["data"]["refresh_tokens"]), 2)

    def test_unknown_format_is_left_alone(self):
        os.makedirs(os.path.dirname(self.store))
        json.dump({"version": 2, "data": {}}, open(self.store, "w"))
        self.assertIn("not changed", auth.ensure(self.store, self.ours))
        self.assertFalse(os.path.exists(self.ours))

    def test_access_token_is_signed_like_home_assistants(self):
        auth.ensure(self.store, self.ours)
        tok = v.access_token(self.ours, now=1000)
        head, body, sig = tok.split(".")
        pad = lambda x: x + "=" * (-len(x) % 4)
        claims = json.loads(base64.urlsafe_b64decode(pad(body)))
        ours = json.load(open(self.ours))
        self.assertEqual(claims, {"iss": ours["refresh_token_id"], "iat": 1000, "exp": 2800})
        want = hmac.new(ours["jwt_key"].encode(), ("%s.%s" % (head, body)).encode(), hashlib.sha256).digest()
        self.assertEqual(base64.urlsafe_b64decode(pad(sig)), want)
        self.assertEqual(json.loads(base64.urlsafe_b64decode(pad(head))), {"alg": "HS256", "typ": "JWT"})


class FakeHA:
    """Just enough of Home Assistant's config entries, entity registry and pipelines."""

    def __init__(self):
        self.entries = {"ollama": [], "wyoming": []}
        self.subentries = {}
        self.registry = []
        self.pipelines = [{"id": "default", "name": "Home Assistant"}]
        self.preferred = "default"
        self.flows = {}
        self.n = 0
        self.deleted = []

    def up(self):
        return True

    def onboarded(self):
        return True

    def get(self, path):
        if path == "/api/config":
            return {"language": "en"}
        raise AssertionError(path)

    def _id(self, p):
        self.n += 1
        return "%s%d" % (p, self.n)

    def flow(self, handler, steps, subentry=False):
        data = steps[0]
        if subentry:
            entry_id, kind = handler
            sid = self._id("sub")
            self.subentries.setdefault(entry_id, []).append({"subentry_id": sid, "title": data["name"], "data": data})
            self.registry.append({"entity_id": "conversation.avado_assistant", "config_entry_id": entry_id,
                                  "config_subentry_id": sid})
            return {"type": "create_entry"}
        eid = self._id(handler)
        if handler == "ollama":
            self.entries["ollama"].append({"entry_id": eid, "title": data["url"], "data": data})
        else:
            name = "faster-whisper" if data["port"] == 10300 else "piper"
            self.entries["wyoming"].append({"entry_id": eid, "title": name, "data": data})
            dom = "stt" if data["port"] == 10300 else "tts"
            self.registry.append({"entity_id": "%s.%s" % (dom, name.replace("-", "_")), "config_entry_id": eid})
        return {"type": "create_entry", "result": {"entry_id": eid}}

    def delete(self, path):
        eid = path.rsplit("/", 1)[1]
        self.deleted.append(eid)
        for d in self.entries:
            self.entries[d] = [e for e in self.entries[d] if e["entry_id"] != eid]
        self.registry = [r for r in self.registry if r["config_entry_id"] != eid]

    def ws(self, *cmds):
        out = []
        for c in cmds:
            t = c["type"]
            if t == "config_entries/subentries/list":
                out.append([{k: x[k] for k in ("subentry_id", "title")} for x in self.subentries.get(c["entry_id"], [])])
            elif t == "config_entries/subentries/delete":
                self.subentries[c["entry_id"]] = [x for x in self.subentries[c["entry_id"]]
                                                  if x["subentry_id"] != c["subentry_id"]]
                out.append(None)
            elif t == "config/entity_registry/list":
                out.append(list(self.registry))
            elif t == "assist_pipeline/pipeline/list":
                out.append({"pipelines": list(self.pipelines), "preferred_pipeline": self.preferred})
            elif t == "assist_pipeline/pipeline/create":
                p = dict(c, id=self._id("pipe"))
                p.pop("type")
                self.pipelines.append(p)
                out.append(p)
            elif t == "assist_pipeline/pipeline/update":
                out.append(None)
            elif t == "assist_pipeline/pipeline/set_preferred":
                self.preferred = c["pipeline_id"]
                out.append(None)
            elif t == "assist_pipeline/pipeline/delete":
                self.pipelines = [p for p in self.pipelines if p["id"] != c["pipeline_id"]]
                out.append(None)
            else:
                raise AssertionError(t)
        return out


class FakeDoor:
    def __init__(self, model="qwen3.5:4b"):
        self.model = model
        self.voice = False
        self.calls = []
        self.key = "k" * 43

    def __call__(self, path, data=None, key=None, timeout=15):
        self.calls.append((path, data, key))
        if path == "/avado/connect":
            return {"key": self.key, "model": self.model, "model_name": "Qwen3.5 4B", "voice": {"ready": self.voice}}
        if key != self.key:
            raise v.AssistantError("no", "key")
        if path == "/avado/voice":
            self.voice = bool(data["on"])
        return {"model": self.model, "model_name": "Qwen3.5 4B", "voice": {"ready": self.voice, "on": self.voice}}


class Switch(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.ha = FakeHA()
        ha = self.ha

        class HAView:
            def __getattr__(self, name):
                return getattr(ha, name)

            def entries(self, domain):
                return ha.entries[domain]
        self.door = FakeDoor()
        self.v = v.Voice(ha=HAView(), state_path=os.path.join(self.dir, "voice.json"), door_fn=self.door,
                         sleep=lambda s: None)

    def test_on_then_off(self):
        self.v.turn_on()
        st = self.v.state
        self.assertTrue(st["on"])
        self.assertTrue(self.door.voice)
        self.assertEqual(self.ha.preferred, st["pipeline_id"])
        pipe = next(p for p in self.ha.pipelines if p["id"] == st["pipeline_id"])
        self.assertEqual(pipe["name"], "AVADO voice")
        self.assertEqual(pipe["conversation_engine"], "conversation.avado_assistant")
        self.assertEqual(pipe["stt_engine"], "stt.faster_whisper")
        self.assertEqual(pipe["tts_engine"], "tts.piper")
        self.assertEqual(pipe["tts_voice"], "en_US-lessac-medium")
        self.assertTrue(pipe["prefer_local_intents"])
        (oll,) = self.ha.entries["ollama"]
        self.assertEqual(oll["data"], {"url": "http://assistant.my.ava.do:11434", "api_key": self.door.key})
        sub = self.ha.subentries[oll["entry_id"]][0]["data"]
        self.assertEqual(sub["model"], "qwen3.5:4b")
        self.assertEqual(sub["llm_hass_api"], ["assist"])
        self.assertEqual(sub["keep_alive"], 600)
        self.assertFalse(sub["think"])
        self.assertEqual(st["previous_pipeline"], "default")
        # once more: nothing is made twice
        self.v.turn_on()
        self.assertEqual(len(self.ha.entries["wyoming"]), 2)
        self.assertEqual(len(self.ha.pipelines), 2)
        # off: all of it gone, the old pipeline preferred again, speech off
        self.v.turn_off()
        self.assertEqual(self.ha.preferred, "default")
        self.assertEqual([p["id"] for p in self.ha.pipelines], ["default"])
        self.assertEqual(self.ha.entries, {"ollama": [], "wyoming": []})
        self.assertFalse(self.door.voice)
        self.assertFalse(self.v.state["on"])

    def test_model_change_makes_a_new_agent(self):
        self.v.turn_on()
        first = self.v.state["agent_subentry"]
        self.door.model = "qwen3.5:9b"
        self.v.turn_on()
        self.assertNotEqual(self.v.state["agent_subentry"], first)
        self.assertEqual(self.v.state["model"], "qwen3.5:9b")
        oll = self.ha.entries["ollama"][0]["entry_id"]
        self.assertEqual([x["data"]["model"] for x in self.ha.subentries[oll]], ["qwen3.5:9b"])

    def test_no_model_says_so(self):
        self.door.model = None
        with self.assertRaises(v.AssistantError) as e:
            self.v.turn_on()
        self.assertIn("no AI model", str(e.exception))
        self.assertFalse(self.v.state["on"])

    def test_new_key_after_the_assistant_was_reinstalled(self):
        self.v.turn_on()
        self.door.key = "n" * 43
        self.assertEqual(self.v._key(), "n" * 43)

    def test_voices(self):
        self.assertEqual(v.voice_name("de-AT"), "de_DE-thorsten-medium")
        self.assertEqual(v.tts_language("sl"), "sl_SI")
        self.assertEqual(v.voice_name("hr"), "en_US-lessac-medium")


if __name__ == "__main__":
    unittest.main()
