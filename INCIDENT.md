# Incident DB-001: Inventory drift under concurrent orders

This branch is an intentionally faulty exercise derived from the immutable
'l1-baseline-v1' tag. It preserves the API contract, database constraints, and independent
Oracle from L1.

## Operator brief

The storefront and health checks may look normal. Use the admin control room at
[http://localhost:8000/admin](http://localhost:8000/admin) and run the default probe:

- initial stock: 10
- concurrent requests: 40
- quantity per order: 1
- expected responses: 10 × HTTP 201 and 30 × HTTP 409
- expected final stock: 0

The incident is confirmed when the dashboard reports 'INCIDENT DETECTED' or when
'python -m oracle.check' reports that the stock equation failed.

## Investigation boundary

Only one primary fault is injected: concurrent inventory decrement is no longer atomic.
Do not change 'oracle/', the API contract, database constraints, or test expectations while
investigating. Compare this branch with 'l1-baseline-v1' to identify and repair the fault.
