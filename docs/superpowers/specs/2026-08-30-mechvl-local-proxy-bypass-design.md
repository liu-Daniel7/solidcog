# MechVL Local Proxy Bypass Design

## Problem

SolidCog calls the local MechVL service at `http://127.0.0.1:8100`. The Windows process inherits `HTTP_PROXY` and `HTTPS_PROXY`, so Python `requests` sends this loopback request through the proxy. The proxy returns an error even though MechVL is healthy.

## Design

Create one module-local `requests.Session` in `app/services/mechvl.py` and set `trust_env = False`. Use that session for both `/health` and `/analyze` calls. This bypasses environment proxies only for MechVL; Qwen and other external requests retain their existing proxy behavior.

Keep the current URLs, timeouts, payloads, and user-facing error messages unchanged.

## Verification

Update the MechVL service tests to patch the module-local session and assert that it does not trust environment proxy settings. Run the focused model-service tests, then call SolidCog's MechVL health route while `HTTP_PROXY` remains configured.
