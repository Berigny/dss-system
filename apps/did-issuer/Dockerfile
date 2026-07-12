# DSS DID issuer — walt.id Issuer API wrapper for the monorepo.
FROM waltid/issuer-api:stable

WORKDIR /waltid-issuer-api

COPY config/ /waltid-issuer-api/config/

ENV WEB_PORT=8080
ENV WALT_ID_BASE_URL=http://localhost:8080
ENV BACKEND_URL=http://backend:8000

EXPOSE 8080
