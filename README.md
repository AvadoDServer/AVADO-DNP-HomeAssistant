# AVADO Home Assistant

`homeassistant.avado.dnp.dappnode.eth`: [Home Assistant](https://www.home-assistant.io) 2026.11.0
(the "Container" installation type), packaged for AVADO.

- Page: **https://homeassistant.my.ava.do** (plain http works too).
- AVADO page with the voice switch: **http://homeassistant.my.ava.do/avado/** (the Admin's Setup tab).
- Image: the official `ghcr.io/home-assistant/home-assistant:2026.11.0`, pinned by digest, plus
  nginx for HTTPS.

## How it is put together

| Part | What |
|---|---|
| `build/Dockerfile` | Official image + nginx + openssl from Alpine. Home Assistant listens on 8123 inside the container; nginx serves 80 and 443. No host ports. Home Assistant's own `/init` (s6) stays PID 1. |
| `build/avado/app-prestart.sh` + `avado-http-store.py` | Run before Home Assistant starts. Home Assistant answers 400 to requests through a proxy it does not trust, and since 2026.x it keeps its HTTP settings in `/config/.storage/http` (an `http:` section in `configuration.yaml` is imported once as a change the owner must confirm in the app within 5 minutes, otherwise it is undone). So the script switches on `use_x_forwarded_for` and adds `127.0.0.1/32` and `::1/128` (the nginx in this container) to `trusted_proxies` in that store, keeping every other setting; on the first start it creates the store with Home Assistant's defaults plus these two settings. It edits store formats 2 (Home Assistant 2026.10) and 3 (2026.11) and leaves a store of an unknown format alone. |
| `build/avado/avado-start.sh` | Start wrapper shared by the AVADO app packages: nginx, the certificate check (15 s after start, then daily), `nice 10` for Home Assistant. |
| `build/avado/avado-certs.sh` | The `*.my.ava.do` certificate: the box's DAPPMANAGER copy (`http://dappmanager.my.ava.do/my.ava.do.crt`, as AVADO-DNP-Nethermind uses), else `http://iso.ava.do`, else the copy taken at build time. |
| `build/avado/nginx.conf.in` | nginx: websockets (the Home Assistant page and apps need them), uploads up to 4 GB (backups), a "Home Assistant is starting" page (`build/avado/www/`) instead of a 502 while it starts, and 404 for the service worker (`/sw-modern.js`, `/sw-legacy.js`): it keeps the first-run set-up's redirect for `/` and replays it after "Finish", so the browser showed "This site can't be reached" at the end of the set-up (plain Home Assistant 2026.10.4 does the same). Without it the page works as it always does over http; the phone app does not use it. |

Volume: `config` (`/config`: settings, database, automations, backups).

## Voice with the Private Assistant (0.2.0)

One switch on the AVADO page (`build/avado/voice/`): **Use my AVADO's assistant for voice**. It needs
the Private Assistant (AVADO-DNP-Private-Assistant 0.2.0 or newer) with a model. Turning it on makes
Home Assistant listen, answer and speak with this box only, no cloud:

| Part | What | Where |
|---|---|---|
| Speech to text | Wyoming integration, faster-whisper (`base-int8`, about 80 MB) | `assistant.my.ava.do:10300` |
| Conversation agent "AVADO assistant" | Ollama integration, the Private Assistant's model (e.g. Qwen3.5 4B), Assist API on | `http://assistant.my.ava.do:11434` with a key |
| Text to speech | Wyoming integration, Piper (`en_US-lessac-medium`, `de_DE-thorsten-medium`, `sl_SI-artur-medium`, ... by Home Assistant's language) | `assistant.my.ava.do:10200` |
| Assist pipeline "AVADO voice" | the three above, "prefer handling commands locally" on, made the preferred pipeline | Home Assistant |

- **Commands first, model second.** With "prefer handling commands locally", Home Assistant's own
  sentences ("turn on the kitchen light", "what time is it") are answered at once; anything else goes to
  the model, which can also control the entities exposed to Assist.
- **Memory.** Speech takes about 0.7 GB in the Private Assistant, which counts it in its sizing (the
  free memory it keeps for the node, from the box's Prometheus). The agent keeps the model in memory for
  10 minutes after the last question (`keep_alive` 600, instead of Ollama's "forever"), thinking off, an
  8k context, the same as the assistant's own chats, so the model is never loaded twice.
- **The Private Assistant lets only Home Assistant in**: its doors check the caller's address on the
  AVADO network (`homeassistant.my.ava.do`), and the model door also wants a key that it hands out once,
  to this app (`POST /avado/connect`). Wyoming has no keys; the address check is all it gets.
- **Off** removes the pipeline, the three integration entries and puts back the pipeline that was
  preferred before; the Private Assistant then stops its speech servers.
- While on, the helper checks every 10 minutes that the set-up is still there and follows the Private
  Assistant's model (a new agent when the owner picks another model there).

How the helper reaches Home Assistant's API: `avado_ha_auth.py` runs before Home Assistant starts and
keeps one **system user "AVADO"** in `/config/.storage/auth` (administrator, local only, hidden in
Settings > People, like the users Home Assistant makes for add-ons) with one system refresh token. Its
id and signing key are in `/config/.avado/ha-api.json` (mode 600). The helper signs its own 30-minute
access tokens with it (HS256, the way Home Assistant checks them) and calls the REST API (config flows)
and the WebSocket API (subentries, entity registry, `assist_pipeline/pipeline/*`) on 127.0.0.1:8123 only.
A user store of an unknown format is left alone (the switch then cannot reach Home Assistant).

The page (`build/avado/voice/public/`, the Admin's look, about 100 words) and its API are served by the
helper on 127.0.0.1:8124, behind nginx at `/avado/`. The switch needs the `X-Avado-Request: 1` header and
an Origin of this app or the Admin, so another web page cannot press it. Who can open the page can flip
the switch (as with other AVADO app pages). Helper state: `/config/.avado/voice.json`. The helper's unit
tests (`build/avado/voice/tests/`) run during the image build.

## Networking: what works and what does not

AVADO apps run in the box's own Docker network (`dncore_network`, a bridge), not on the home
network. The AVADO manifest could ask for `network_mode: host`, but then the app would leave the
AVADO network: `homeassistant.my.ava.do` and the HTTPS page would stop working and Home Assistant
would listen on the home network unprotected. So this package stays on the bridge, and:

- **Works**: devices and services Home Assistant reaches by address or through the internet:
  Philips Hue, Shelly, ESPHome, MQTT, many Wi-Fi plugs and cameras added by IP address, and cloud
  integrations (Google, Tuya, Netatmo, ...). Outgoing connections from the bridge to the home
  network are routed normally.
- **Does not work**: anything that needs multicast or broadcast on the home network, which does
  not cross the bridge: automatic discovery (mDNS/zeroconf, SSDP, DHCP discovery), Apple HomeKit
  Bridge and HomeKit devices, some Chromecast/Sonos set-ups, Matter (which also needs IPv6
  link-local). Devices that need to call Home Assistant from the home network (webhooks, ESPHome
  in API-push mode, Shelly "CoIoT") cannot reach it, since no port is opened on the home network.
- **Not possible in this version**: Bluetooth and USB radios (Zigbee, Z-Wave, Thread sticks). The
  DAPPMANAGER's compose generator has no `devices` support, and making the container privileged
  for this is not worth the risk on a staking box.

The store text says this in plain words. A later version could add a host-network or USB option
once the DAPPMANAGER supports it.

## Resource use and the Ethereum node

AVADO's package format has no memory or CPU limits (the DAPPMANAGER's compose generator does not
pass `mem_limit`/`cpus`). Home Assistant runs at `nice 10`. Measured on local Docker: about 520 MB
of memory idle after onboarding.

## Who can reach it

Every device that can open `my.ava.do`: on the AVADO network (AVADO WiFi) or through Remote
Access. No port is opened on the home network. Home Assistant has its own accounts; the first one
is made in the onboarding on the page.

## Updating

Bump the digest and tag in `build/Dockerfile`, `upstream` and `version` in
`dappnode_package.json` and the image tag in `docker-compose.yml`. Read the release notes first
(breaking changes are listed per release).

## Licence

The packaging files in this repository are GPL-3.0 (see `LICENSE`). Home Assistant is Apache-2.0
and is used unchanged from its official image. The avatar is the Home Assistant logo.
