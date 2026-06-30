# Product Roadmap

This document outlines the planned future features and operational enhancements for the MCX Market Data Platform.

---

## Short-Term Goals (Q3 2026)
* **Real-time Webhook Deliveries**: Allow users to register HTTP endpoints to receive price change callbacks instantly.
* **Historical CSV Exports**: Add REST routes to easily export historical aggregation candles into CSV or JSON files.
* **Additional Commodities**: Add plug-and-play support for Crude Oil, Natural Gas, Copper, and Zinc.

---

## Medium-Term Goals (Q4 2026)
* **Clustered High Availability**: Support multi-instance API deployments with shared session metrics and lock states in Redis.
* **SMS Alert Rules**: Enable users to configure custom price thresholds and receive instant mobile notifications (via Twilio).
* **Billing Integration**: Connect Stripe directly to the API developer dashboard for SaaS self-service subscription renewals.
