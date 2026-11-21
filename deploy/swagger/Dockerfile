FROM nginx:alpine

RUN apk add --no-cache apache2-utils

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY docker-entrypoint.sh /usr/local/bin/docs-entrypoint
RUN chmod +x /usr/local/bin/docs-entrypoint

EXPOSE 80

ENTRYPOINT ["/usr/local/bin/docs-entrypoint"]
