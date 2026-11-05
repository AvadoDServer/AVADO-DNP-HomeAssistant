# Home Assistant on AVADO: sourced by avado-start.sh before the app starts.
#
# The HTTPS page at homeassistant.my.ava.do is served by a small nginx inside
# this container, and Home Assistant answers "400: Bad Request" to requests
# through a proxy it does not trust. Since 2026.x Home Assistant keeps its
# HTTP settings in /config/.storage/http (an "http:" section in
# configuration.yaml is only imported once, as a change the owner must confirm
# in the app within 5 minutes, otherwise it is undone). So the trusted local
# proxy goes straight into that file, before Home Assistant starts.
mkdir -p /config/.storage
python3 /etc/avado/avado-http-store.py /config/.storage/http ||
  echo "[avado] could not prepare Home Assistant's HTTP settings; the page may answer 400" >&2

# Voice with the Private Assistant (voice/): the AVADO system user for Home Assistant's API
# (only changes the user store before Home Assistant starts), then the page and switch behind
# /avado/ on 127.0.0.1:8124, started again if it ever stops.
python3 /etc/avado/voice/avado_ha_auth.py ||
  echo "[avado] voice helper: could not prepare its Home Assistant user; the voice switch will say so" >&2
(
  while :; do
    nice -n 5 python3 -I /etc/avado/voice/avado_voice.py
    echo "[avado] voice helper stopped; starting it again in 10 s" >&2
    sleep 10
  done
) &
