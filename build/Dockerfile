# Home Assistant 2026.11.1, the official container image (Alpine), pinned by digest.
FROM --platform=linux/amd64 ghcr.io/home-assistant/home-assistant:2026.11.1@sha256:6785d1eca68747a524c156faae1b139ed415c07357386ae6fe17291986e59e82

RUN apk add --no-cache nginx openssl

# HTTPS front: the *.my.ava.do certificate, refreshed at start from the box.
RUN mkdir -p /etc/avado/certs \
 && curl -fsS -o /etc/avado/certs/build.crt http://iso.ava.do/my.ava.do.crt \
 && curl -fsS -o /etc/avado/certs/build.key http://iso.ava.do/my.ava.do.key \
 && openssl x509 -in /etc/avado/certs/build.crt -noout -subject
COPY avado/ /etc/avado/
# The voice helper's tests run in the build (a failing test fails the image); they are not kept.
RUN chmod +x /etc/avado/*.sh \
 && cd /etc/avado/voice && python3 -I -m unittest discover -s tests 2>&1 | tail -n 3 \
 && python3 -I -m unittest discover -s tests >/dev/null 2>&1 \
 && rm -rf tests __pycache__ && find /etc/avado -name __pycache__ -prune -exec rm -rf {} +

ENTRYPOINT ["/etc/avado/avado-start.sh"]
CMD ["/init"]
