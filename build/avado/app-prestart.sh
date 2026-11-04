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
