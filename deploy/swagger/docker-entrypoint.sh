#!/bin/sh
set -eu

: "${DOCS_AUTH_USER:?DOCS_AUTH_USER must be set}"
: "${DOCS_AUTH_PASSWORD:?DOCS_AUTH_PASSWORD must be set}"

if [ "$DOCS_AUTH_PASSWORD" = "replace-this-with-a-long-random-password" ]; then
    echo "Set DOCS_AUTH_PASSWORD to a unique value in .env" >&2
    exit 1
fi

if [ "${#DOCS_AUTH_PASSWORD}" -lt 16 ]; then
    echo "DOCS_AUTH_PASSWORD must be at least 16 characters" >&2
    exit 1
fi

umask 077
printf '%s\n' "$DOCS_AUTH_PASSWORD" \
    | htpasswd -B -i -c /etc/nginx/.htpasswd "$DOCS_AUTH_USER" >/dev/null
chmod 644 /etc/nginx/.htpasswd

exec nginx -g 'daemon off;'
