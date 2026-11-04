# Home Assistant on AVADO: sourced by avado-start.sh before the app starts.
#
# Home Assistant refuses requests that come through a proxy it does not
# trust. The HTTPS front (nginx) runs in this container, so Home Assistant
# must trust 127.0.0.1. On the first start we write Home Assistant's own
# default configuration plus that setting; later we only add the setting if
# the owner's configuration has no "http:" section at all.

HA_CONFIG=/config/configuration.yaml
AVADO_HTTP_BLOCK='
# Added by AVADO: the HTTPS page at homeassistant.my.ava.do is served by a
# small web server inside this app, which Home Assistant must trust.
http:
  use_x_forwarded_for: true
  trusted_proxies:
    - 127.0.0.1
    - ::1
'

mkdir -p /config
if [ ! -f "$HA_CONFIG" ]; then
  if ! python3 -c "from homeassistant.config import _write_default_config as w; import sys; sys.exit(0 if w('/config') else 1)" 2>/dev/null; then
    cat > "$HA_CONFIG" <<'YAML'
# Loads default set of integrations. Do not remove.
default_config:

# Load frontend themes from the themes folder
frontend:
  themes: !include_dir_merge_named themes

automation: !include automations.yaml
script: !include scripts.yaml
scene: !include scenes.yaml
YAML
    [ -f /config/automations.yaml ] || echo "[]" > /config/automations.yaml
    [ -f /config/scripts.yaml ] || : > /config/scripts.yaml
    [ -f /config/scenes.yaml ] || : > /config/scenes.yaml
  fi
  printf '%s' "$AVADO_HTTP_BLOCK" >> "$HA_CONFIG"
  echo "[avado] wrote the first configuration.yaml (Home Assistant defaults + trusted local proxy)"
elif ! grep -q '^http:' "$HA_CONFIG"; then
  printf '%s' "$AVADO_HTTP_BLOCK" >> "$HA_CONFIG"
  echo "[avado] added the trusted local proxy to configuration.yaml"
elif ! grep -q '127\.0\.0\.1' "$HA_CONFIG"; then
  echo "[avado] configuration.yaml has its own http: section without 127.0.0.1 in trusted_proxies;" >&2
  echo "[avado] the page at homeassistant.my.ava.do will answer 400 until it is added." >&2
fi
