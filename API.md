# REST & WebSockets API Documentation

This guide provides developer specifications for integrating with the MCX Market Data API.

---

## 1. Request Authorization

All requests to private endpoints must pass an active API Key. This key can be supplied in two ways:

1. **HTTP Header (Recommended)**:
   ```http
   X-API-Key: mcx_pub_dev_key
   ```
2. **Query Parameter**:
   ```http
   GET /api/v1/prices?api_key=mcx_pub_dev_key
   ```

---

## 2. Response Envelope

Every response returns a unified JSON envelope:

```json
{
  "success": true,
  "timestamp": "2026-06-30T10:50:29Z",
  "latency_ms": 12.5,
  "request_id": "98e4d2a13f4...",
  "data": null,
  "error": null
}
```

---

## 3. Endpoints

### 1. Get All Prices
* **Route**: `GET /api/v1/prices`
* **Response `data`**: Array of validated ticks for Gold and Silver.

### 2. Get Gold Price
* **Route**: `GET /api/v1/gold`
* **Response `data`**: Single validated tick object for MCX Gold.

### 3. Get Silver Price
* **Route**: `GET /api/v1/silver`
* **Response `data`**: Single validated tick object for MCX Silver.

### 4. Historical Candles
* **Route**: `GET /api/v1/history/{commodity}`
* **Query Parameters**:
  * `interval`: `1s`, `5s`, `1m` (default), `5m`, `15m`, `1h`, `1d`
  * `limit`: Max candles to return (default 500, max 2000).
* **Response `data`**: Array of candles:
  ```json
  [
    {
      "timestamp": "2026-06-30T10:00:00Z",
      "open": 142500.0,
      "high": 142650.0,
      "low": 142480.0,
      "close": 142610.0,
      "volume": 0.0
    }
  ]
  ```

---

## 4. WebSockets Stream

* **Endpoint**: `ws://localhost/api/v1/ws`
* **Query Params**: `api_key=mcx_pub_dev_key`
* **Subscription Channels**: Client must send a subscription payload upon connecting:
  * Subscribe to Gold: `{"action": "subscribe", "channel": "gold"}`
  * Subscribe to Silver: `{"action": "subscribe", "channel": "silver"}`
  * Subscribe to All: `{"action": "subscribe", "channel": "all"}`
