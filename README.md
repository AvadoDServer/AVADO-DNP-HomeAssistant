# AVADO Home Assistant

`homeassistant.avado.dnp.dappnode.eth`: [Home Assistant](https://www.home-assistant.io) 2026.10.4
(the "Container" installation type), packaged for AVADO.

- Page: **https://homeassistant.my.ava.do** (plain http works too).
- Image: the official `ghcr.io/home-assistant/home-assistant:2026.10.4`, pinned by digest, plus
  nginx for HTTPS.

## How it is put together

| Part | What |
|---|---|
| `build/Dockerfile` | Official image + nginx + openssl from Alpine. Home Assistant listens on 8123 inside the container; nginx serves 80 and 443. No host ports. Home Assistant's own `/init` (s6) stays PID 1. |
| `build/avado/app-prestart.sh` + `avado-http-store.py` | Run before Home Assistant starts. Home Assistant answers 400 to requests through a proxy it does not trust, and since 2026.x it keeps its HTTP settings in `/config/.storage/http` (an `http:` section in `configuration.yaml` is imported once as a change the owner must confirm in the app within 5 minutes, otherwise it is undone). So the script switches on `use_x_forwarded_for` and adds `127.0.0.1/32` and `::1/128` (the nginx in this container) to `trusted_proxies` in that store, keeping every other setting; on the first start it creates the store with Home Assistant's defaults plus these two settings. It leaves a store of an unknown format version alone. |
| `build/avado/avado-start.sh` | Start wrapper shared by the AVADO app packages: nginx, the certificate check (15 s after start, then daily), `nice 10` for Home Assistant. |
| `build/avado/avado-certs.sh` | The `*.my.ava.do` certificate: the box's DAPPMANAGER copy (`http://dappmanager.my.ava.do/my.ava.do.crt`, as AVADO-DNP-Nethermind uses), else `http://iso.ava.do`, else the copy taken at build time. |
| `build/avado/nginx.conf.in` | nginx: websockets (the Home Assistant page and apps need them), uploads up to 4 GB (backups). |

Volume: `config` (`/config`: settings, database, automations, backups).

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
