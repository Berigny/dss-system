# DSS Basic Setup Guide & Sandbox Exploration

This guide covers how to spin up the Dual-Substrate System (DSS) monorepo locally, explore its architecture, and test the coordinate routing math in the dedicated sandbox environment.

## 1. The Coordinate Sandbox (Coord-Demo)

Before diving into the full chat surface, developers can explore the **Coordinate Sandbox (`coord-demo`)**. This is a dedicated developer sandbox and demonstration environment for testing, visualising, and pushing the limits of the prime-lattice coordinate routing math that powers DSS coherence.

*When the stack is running, the sandbox is accessible at `http://localhost:3002`.*

---

## 2. Architecture Overview

When you run the setup, you are spinning up the following containerized stack:

```mermaid
graph LR
    CP[Control Plane<br/>localhost:3000] --> MW[Middleware<br/>localhost:8001]
    CS[Chat Surface<br/>localhost:3001] --> MW
    CD[Coord Demo<br/>localhost:3002] --> MW
    MW --> BE[Backend<br/>localhost:8000]
    MW --> DI[DID Issuer<br/>localhost:8080]
    BE --> DB[(RocksDB<br/>backend-data)]
    BE --> ST[shared-types]
    MW --> ST
    CP --> ST

```

* **control-plane:** Trust-anchor, identity, governance, benchmark, and surface management dashboard.
* **chat-surface:** End-user chat UI.
* **coord-demo:** Minimal COORD resolver demo and coordinate sandbox.
* **middleware:** Auth, proxy, orchestration, and OpenRouter model library gateway.
* **backend:** Ledger storage, coordinate resolution, retrieval, ingestion, governance, and admin APIs.
* **did-issuer:** walt.id-based `DssIdentity` credential issuance.
* **shared-types:** Reusable Pydantic models and clients imported by the Python apps.

---

## 3. Quick Start (Development)

1. **Clone the repository:**
```bash
git clone https://github.com/berigny/dss-system.git
cd dss-system

```


2. **Configure your environment:**
Copy the example environment file and fill in all secrets and adjust public URLs for your deployment.
```bash
cp .env.example .env

```


*At a minimum, ensure you have set:*
* `PUBLIC_BASE_URL`, `DEFAULT_DID_HOST`
* `FASTHTML_SECRET_KEY`
* `AUTH_SESSION_TOKEN_SECRET`
* `OPENROUTER_API_KEY` (if using online models)
* `ADMIN_TOKEN` / `TRUST_ANCHOR_ADMIN_TOKEN` / `BACKEND_ADMIN_TOKEN` / `MIDDLEWARE_ADMIN_TOKEN`
* `CHAT_BASE_URL`, `COORD_DEMO_BASE_URL`


3. **Start the stack:**
```bash
make dev

```


4. **Verify services are healthy:**
```bash
docker compose ps

```


5. **Open the local services:**
* **Control Plane:** `http://localhost:3000`
* **Chat Surface:** `http://localhost:3001`
* **Coord Demo (Sandbox):** `http://localhost:3002`
* **Middleware:** `http://localhost:8001`
* **Backend:** `http://localhost:8000`
* **DID Issuer:** `http://localhost:8080`



---

## 4. First-Time Onboarding

The first wallet-verified signup is auto-approved so a new user can complete `register -> setup -> Control Plane auth` without waiting for a human operator.

> **Note:** Set `AUTO_APPROVE_FIRST_SIGNUP=false` in your `.env` file to disable this behaviour if you want to test the manual approval flow.

---

## 5. Helpful Make Targets

While exploring and modifying the sandbox or other services, you can use the following commands to manage the stack:

* `make dev` — Build and start all services in Docker Compose.
* `make down` — Stop all running services.
* `make logs` — Follow Docker Compose logs for debugging.
* `make test` — Run test suites inside the containers.

---

## License

DSS is available under a custom non-commercial license — see [LICENSE](LICENSE). It is free for research and non-commercial use. 

For commercial licensing inquiries, please contact the maintainer via [Google Forms](forms.gle/hV4ejmk3i5J411am9).
