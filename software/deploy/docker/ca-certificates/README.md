Put the bank's internal root/intermediate CA certificates here (PEM, extension `.crt`)
before `docker compose build`. They are added to the image's trust store, so Bix trusts
the TLS-inspecting proxy, the internal SMTP relay and private endpoints.
