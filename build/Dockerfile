# Home Assistant 2026.10.4, the official container image (Alpine), pinned by digest.
FROM --platform=linux/amd64 ghcr.io/home-assistant/home-assistant:2026.10.4@sha256:23f4dfbb69ce76315dbc4de7915504f1c2dcecd7ca2648113a493a385972e133

RUN apk add --no-cache nginx openssl

# HTTPS front: the *.my.ava.do certificate, refreshed at start from the box.
RUN mkdir -p /etc/avado/certs \
 && curl -fsS -o /etc/avado/certs/build.crt http://iso.ava.do/my.ava.do.crt \
 && curl -fsS -o /etc/avado/certs/build.key http://iso.ava.do/my.ava.do.key \
 && openssl x509 -in /etc/avado/certs/build.crt -noout -subject
COPY avado/ /etc/avado/
RUN chmod +x /etc/avado/*.sh

ENTRYPOINT ["/etc/avado/avado-start.sh"]
CMD ["/init"]
