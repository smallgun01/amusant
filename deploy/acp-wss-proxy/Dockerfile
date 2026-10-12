# NGINX unprivileged linux/amd64 manifest, pinned and verified 2026-10-12.
FROM public.ecr.aws/nginx/nginx-unprivileged@sha256:0f5f7068c5f5bf7046dbf649a08810ec1693967a9f488b8ff3998be6dd34bf6c

USER root
RUN rm -f /etc/nginx/conf.d/default.conf

COPY --chown=101:101 acp.conf /etc/nginx/conf.d/acp.conf
COPY start-proxy.sh /opt/amusant/start-proxy.sh
RUN chown 101:101 /opt/amusant/start-proxy.sh && chmod 0555 /opt/amusant/start-proxy.sh

USER 101:101
ENTRYPOINT ["/bin/sh", "/opt/amusant/start-proxy.sh"]
CMD ["nginx", "-g", "daemon off;"]

ARG GIT_SHA
LABEL org.opencontainers.image.revision=$GIT_SHA
LABEL org.opencontainers.image.title="Amusant ACP WSS sidecar"
LABEL org.opencontainers.image.source="https://github.com/smallgun01/amusant"
