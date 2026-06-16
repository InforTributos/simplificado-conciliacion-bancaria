# Security Policy

## Supported Versions

| Version | Supported          |
|---------|--------------------|
| latest  | :white_check_mark: |

Only the latest version is supported with security updates. This is a single-endpoint microservice that follows the parent project (`conciliacion-bancaria`) for `concilia_engine/` updates.

---

## Reporting a Vulnerability

If you discover a security vulnerability, please **do not** open a public issue. Instead, report it privately to the maintainers.

**Process:**

1. Send a detailed report to the project maintainers including:
   - Description of the vulnerability
   - Steps to reproduce
   - Potential impact
   - Suggested fix (if any)

2. You will receive an acknowledgment within **48 hours**.

3. We will investigate and provide a timeline for the fix.

4. Once the fix is released, we will coordinate disclosure timing with you.

---

## API Key Safety

### In Production

- **Never** commit `.env` files to version control. The `.env` file is already listed in `.gitignore`.
- Use `.env.example` as a template with placeholder values only.
- API keys (`LLM_API_KEY`, `NVIDIA_API_KEY`, `HF_API_KEY`) are read at startup via `pydantic-settings`. They are never logged or exposed in responses.
- Rotate keys regularly and use the minimum required permissions.

### In Docker

- The Docker image does **not** embed API keys. They are injected at runtime via environment variables or a mounted `.env` file.
- Use Docker secrets or a secrets manager (e.g., HashiCorp Vault, AWS Secrets Manager) in production deployments.
- The container runs as a **non-root user** (`appuser`) — no privileged access.

---

## Input Validation

All inputs are validated before processing:

| Input | Validation |
|-------|------------|
| `extracto` (PDF) | MIME type check (`application/pdf`), size limit (`MAX_FILE_SIZE_MB`), content validation (not empty, not corrupt, not encrypted) |
| `movimientos_detalle` (JSON) | Must be valid JSON array; validated against Pydantic schema (date formats, required fields, value types) |
| `periodo` (string) | Must match `AAAAMM` format if provided |
| `cuenta_bancaria` (JSON) | Must be valid JSON if provided; non-blocking field validation |

### Structured Error Taxonomy

Parse errors are returned with standardized codes:

| Code | Cause |
|------|-------|
| `ERR_ARCHIVO_VACIO` | PDF with 0 bytes |
| `ERR_PDF_CORRUPTO` | No %PDF header or cannot open |
| `ERR_PDF_ENCRIPTADO` | Password-protected PDF |
| `ERR_PDF_SOLO_IMAGEN` | 0 extractable text (scanned) |
| `ERR_NO_ES_EXTRACTO` | 0 movements + <2 banking keywords |
| `ERR_CONCILIACION_MATEMATICA` | Movement sum doesn't cuadre with saldos |

These are returned as part of the 200 response with `estado: "error"` — never as raw exceptions.

---

## Dependencies

- The `requirements.txt` pins minimum versions for all core dependencies.
- Optional dependencies (LiteLLM, MarkItDown, PyMuPDF, PyYAML) are commented out by default. Uncomment only if LLM features are needed.
- The Dockerfile runs `apt-get update && apt-get upgrade -y` during build to patch base image CVEs.
- Regularly run `pip-audit` or `safety check` to scan for known vulnerabilities:

```bash
pip install pip-audit
pip-audit
```

---

## Docker Security

The Docker image follows security best practices:

- **Non-root user**: All processes run as `appuser` (UID 1000).
- **Minimal base image**: `python:3.12-slim` — no unnecessary system packages.
- **No secrets in image**: `.env` and other sensitive files are excluded via `.dockerignore`.
- **Health check**: `HEALTHCHECK` monitors the `/docs` endpoint.
- **Pinned base image**: Specific Python version, not `:latest`.

---

## Rate Limiting & DoS

This microservice has **no built-in rate limiting**. In production:

- Place the service behind a **reverse proxy** (nginx, Traefik, Caddy) or **API gateway** with rate limiting enabled.
- Set `MAX_FILE_SIZE_MB` appropriately to prevent memory exhaustion from large PDF uploads.
- Consider adding request timeout limits at the reverse proxy level.
