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
| GET | `/api/insights/backtest` | AI service's 30-day backtest (cached) for the Why AI? tab |
| GET | `/api/insights/model` | Live model weights and metrics (cached) |
| POST | `/api/playground/decide` | Ask the AI about any customer profile — read-only, nothing charged |

## AI

Decisions come from [smart-charge-decision-service](https://github.com/krutik0204/collectus-smart-charge-decision-service)
(trained model + expected-value policy), deployed at
`https://collectus-smart-charge-decision-service.onrender.com`.

| Env | Value |
| --- | --- |
| `USE_MOCK_AI` | `false` — call the AI service (`true` = built-in scorecard) |
| `AI_DECISION_URL` | `https://collectus-smart-charge-decision-service.onrender.com/v1/decide` (local: `http://127.0.0.1:8001/v1/decide`) |

If the AI service is unreachable (e.g. Render free-tier cold start), the backend falls back to the
built-in scorecard and labels the decision `mock-fallback`. Warm both services before a demo.

## Demo personas

Each customer has a card (network, wallet, funding) and a monthly scheme decline budget by network
(Mada 3, Visa/Mastercard 6). Both are sent to the AI. A failed autocharge uses one decline; with
none left, neither Smart Autocharge nor cron will attempt.

| Customer | Card | Declines left | Smart Autocharge after paying one overdue installment |
| --- | --- | --- | --- |
| Aisha Al-Harbi | Mada debit, Apple Pay | 3 | **charge** full overdue |
| Omar Nasser | Visa prepaid card | 2 | **skip** — 1–2% likely, ask to update card |
| Layla Hassan | Mastercard credit card | 2 | **charge** full overdue |
| Fahad Al-Qahtani | Mada debit card | 0 | **skip** by hard gate — model not called |
| Noura Al-Otaibi | Visa credit, Apple Pay | 3 | **charge** the remaining SAR 1,049.50 |
| Khalid Al-Shehri | Mada debit card | 3 | **charge** — still worth it at the repayment moment |
| Reem Al-Dosari | Mada debit card | 1 of 3 | **skip** — last decline needs 30%, ~17–26% likely |
| Huda Al-Zahrani | Visa debit card | 3 of 6 | **charge** — same profile as Reem, but declines to spare |
| Yusuf Al-Ghamdi | Mastercard debit, Apple Pay | 3 | **charge** remaining overdue across both orders (pay a **Nike** installment) |
| Sara Al-Mutairi | Visa debit card | 3 | no autocharge — upcoming is never charged |
