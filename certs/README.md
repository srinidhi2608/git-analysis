# Certificates

Files placed here are used by `docker/entrypoint.sh` when the container starts.
They are copied into the image by the Dockerfile **and** mounted read-only at
runtime by `docker-compose.yml` (`./certs:/app/certs:ro`), so you can rotate them
with a restart instead of a rebuild. Everything except this README and the
`.gitkeep` placeholders is git-ignored: never commit keys.

```
certs/
├── tls/
│   ├── server.crt      HTTPS certificate (full chain: server cert first, then intermediates)
│   ├── server.key      its private key (PEM; encrypted keys need TLS_KEY_PASSWORD)
│   └── client-ca.crt   optional: require client certificates signed by this CA (mutual TLS)
└── ca/
    └── *.crt / *.pem   extra CA certificates to trust for outbound HTTPS
                        (corporate proxy, GitHub Enterprise, Azure DevOps Server,
                        internal LLM endpoints, PostgreSQL server CA)
```

- **HTTPS** turns on automatically when both `tls/server.crt` and `tls/server.key`
  exist (`TLS_ENABLED=auto`). Set `TLS_ENABLED=true` or `REQUIRE_TLS=true` to make
  the container refuse to start without them.
- **Extra CAs** are appended to the system trust store bundle, which is then used by
  the GitHub, Azure DevOps and LLM clients (`SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`).
- Baking certificates into the image (via `COPY`) means anyone who can pull the
  image can read them. Only push such images to a private registry, or leave this
  folder empty at build time and rely on the runtime mount.

See the README section "Production deployment" for details.
