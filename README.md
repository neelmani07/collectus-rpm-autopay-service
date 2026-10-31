# rpm-autopay-service

Collectus hackathon backend: **RPM (simulate repayment) + Autopay + orchestration**.

Calls the AI Decision service (`POST /v1/decide`). Ships with a **mock AI** so Frontend + Backend work before the teammate service is ready.

## Quick start

```bash
cd rpm-autopay-service
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --port 8000
```

Open API docs: http://127.0.0.1:8000/docs

## Main endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | Health + whether mock AI is on |
| GET | `/api/customers` | Seed demo customers |
| POST | `/api/rpm/repay` | Simulate repayment → AI → autopay |
| GET | `/api/decisions` | Recent audit log |
| POST | `/api/admin/reset` | Reset seed data |

### Example

```bash
curl -X POST http://127.0.0.1:8000/api/rpm/repay \
  -H 'Content-Type: application/json' \
  -d '{"customer_id":"c_charge_001","amount":400,"currency":"SAR"}'
```

## AI integration

| Env | Meaning |
| --- | --- |
| `USE_MOCK_AI=true` | Use built-in Option A-lite scorecard |
| `USE_MOCK_AI=false` | Call `AI_DECISION_URL` (teammate service) |
| On remote failure | Automatic mock fallback |

Expected AI contract: see one-pager / `DecisionRequest` + `DecisionResponse` in `app/models.py`.

## Deploy (Render)

- Root: `rpm-autopay-service`
- Build: `pip install -r requirements.txt`
- Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Set `CORS_ORIGINS` to your frontend URL
- Set `USE_MOCK_AI` / `AI_DECISION_URL` as needed

## Production path (later)

Swap simulated RPM event → real Tamara `repayment.succeeded`; swap Autopay mock → real Collections/Payments API; keep the same decision contract.
