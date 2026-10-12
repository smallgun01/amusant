#!/bin/sh
set -eu

if ! printenv TLS_CERT_PEM >/dev/null; then
    echo "TLS_CERT_PEM is required" >&2
    exit 1
fi
if ! printenv TLS_KEY_PEM >/dev/null; then
    echo "TLS_KEY_PEM is required" >&2
    exit 1
fi

umask 077
mkdir -p /tmp/amusant-acp
chmod 0700 /tmp/amusant-acp
printf '%s' "$TLS_CERT_PEM" > /tmp/amusant-acp/server.crt
printf '%s' "$TLS_KEY_PEM" > /tmp/amusant-acp/server.key
unset TLS_CERT_PEM TLS_KEY_PEM

nginx -t
exec "$@"
