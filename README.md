# rpm-autopay-service

Collectus hackathon backend — **Tamara-like shop + installments + Smart Autocharge**.

## Flow

1. Browse products → checkout with **Pay in 3/4**
2. First installment paid immediately; remaining created as upcoming
3. Customer pays a due/overdue installment
4. RPM event `repayment.succeeded` → **AI decision** → Autopay may settle other overdue

## Quick start

```bash
cd rpm-autopay-service
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --port 8000
```

Docs: http://127.0.0.1:8000/docs

## Key APIs

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/products` | Catalog |
| GET | `/api/customers` | Demo personas |
| POST | `/api/checkout` | Buy with installments (first payment now) |
| GET | `/api/customers/{id}/orders` | Purchases + installment schedule |
| GET | `/api/customers/{id}/installments` | Flat installment list |
| POST | `/api/installments/{id}/pay` | Pay installment → RPM → AI → Autopay |
| POST | `/api/admin/reset` | Reset seed data |

## AI

`USE_MOCK_AI=true` uses built-in scorecard. Set `USE_MOCK_AI=false` + `AI_DECISION_URL` for teammate service.
