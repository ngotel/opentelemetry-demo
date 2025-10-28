# Shipping Service - Architecture Documentation

## Overview

The Shipping Service is an HTTP REST API written in Rust using Actix-web that provides shipping quotes and tracking IDs. It demonstrates OpenTelemetry instrumentation in Rust with both auto and manual approaches.

**Language:** Rust (Edition 2021)
**Framework:** Actix-web 4
**Service Type:** HTTP REST API
**Primary Function:** Shipping Quote Calculation and Order Shipping

---

## Key Functionality

### 1. Get Shipping Quote

**Endpoint:** `POST /get-quote`
**File:** `src/shipping_service.rs:18-45`

```rust
#[post("/get-quote")]
pub async fn get_quote(data: web::Json<GetQuoteRequest>) -> actix_web::Result<impl Responder> {
    let request = data.into_inner();

    // Calculate total item count
    let count: u32 = request.items.iter().map(|item| item.quantity).sum();

    // Call quote service
    let cost_usd = create_quote_from_count(count).await?;

    Ok(web::Json(GetQuoteResponse {
        cost_usd: Money {
            currency_code: "USD".to_string(),
            units: cost_usd.dollars as i64,
            nanos: cost_usd.cents * 10_000_000,
        },
    }))
}
```

### 2. Ship Order

**Endpoint:** `POST /ship-order`
**File:** `src/shipping_service.rs:47-56`

```rust
#[post("/ship-order")]
pub async fn ship_order(_data: web::Json<ShipOrderRequest>) -> actix_web::Result<impl Responder> {
    let tracking_id = create_tracking_id();

    Ok(web::Json(ShipOrderResponse { tracking_id }))
}

fn create_tracking_id() -> String {
    uuid::Uuid::new_v4().to_string()
}
```

### 3. Quote Service Integration

**File:** `src/shipping_service/quote.rs:39-80`

```rust
pub async fn request_quote(number_of_items: u32) -> Result<Quote, Error> {
    let quote_service_addr = get_env_var("QUOTE_ADDR", "http://quote:8090/getquote");

    // Create metric
    let meter = global::meter("otel_demo.shipping.quote");
    let counter = meter.u64_counter("app.shipping.items_count").build();
    counter.add(number_of_items as u64, &[]);

    // Get active span and add attributes
    Ok(get_active_span(|span| {
        let q = create_quote_from_float(f);
        span.add_event(
            "Received Quote".to_string(),
            vec![KeyValue::new("app.shipping.cost.total", format!("{}", q))],
        );
        span.set_attribute(KeyValue::new("app.shipping.cost.total", format!("{}", q)));
        q
    }))
}
```

---

## Role in the Ecosystem

### Upstream Dependencies
- **Checkout Service** - Requests shipping quotes and order shipping
- **Quote Service** (HTTP) - Provides cost calculations
- **OpenTelemetry Collector** - Telemetry aggregation

### Service Interactions

```
┌──────────────┐
│   Checkout   │
│   Service    │
└──────────────┘
       │
       │ HTTP POST: /get-quote, /ship-order
       │ (W3C Trace Context via headers)
       ▼
┌──────────────┐         ┌──────────────┐
│   Shipping   │────────▶│    Quote     │
│   Service    │ HTTP    │   Service    │
└──────────────┘         └──────────────┘
       │
       │ OTLP
       ▼
┌──────────────┐
│     OTel     │
│  Collector   │
└──────────────┘
```

---

## OpenTelemetry Implementation Details

### Auto-Instrumentation Setup

#### 1. Actix-web Middleware
**File:** `src/main.rs:46-52`

```rust
HttpServer::new(|| {
    App::new()
        .wrap(RequestTracing::new())      // Trace all HTTP requests
        .wrap(RequestMetrics::default())  // Collect HTTP metrics
        .service(get_quote)
        .service(ship_order)
})
```

**What gets auto-instrumented:**
- HTTP server requests (method, path, status code)
- Request/response sizes
- Request duration
- Trace context extraction from headers

#### 2. HTTP Client Instrumentation
**File:** `src/shipping_service/quote.rs:59-64`

```rust
let mut response = client
    .post(quote_service_addr)
    .trace_request()  // Auto-instrument outgoing HTTP request
    .send_json(&reqbody)
    .await
```

### Manual Instrumentation

#### 1. Telemetry Provider Configuration
**File:** `src/telemetry_conf.rs:24-71`

```rust
pub fn init_otel() {
    // Trace Provider
    let tracer_provider = opentelemetry_sdk::trace::SdkTracerProvider::builder()
        .with_resource(get_resource())
        .with_batch_exporter(
            opentelemetry_otlp::SpanExporter::builder()
                .with_tonic()
                .build()
                .unwrap(),
        )
        .build();

    // Meter Provider
    let meter_provider = opentelemetry_sdk::metrics::SdkMeterProvider::builder()
        .with_resource(get_resource())
        .with_periodic_exporter(
            opentelemetry_otlp::MetricExporter::builder()
                .with_tonic()
                .build()
                .unwrap(),
        )
        .build()
        .unwrap();

    // Logger Provider
    let logger_provider = opentelemetry_sdk::logs::SdkLoggerProvider::builder()
        .with_resource(get_resource())
        .with_batch_exporter(
            opentelemetry_otlp::LogExporter::builder()
                .with_tonic()
                .build()
                .unwrap(),
        )
        .build();

    // Set global providers
    global::set_tracer_provider(tracer_provider);
    global::set_meter_provider(meter_provider);
    opentelemetry::logs::set_logger_provider(logger_provider);
}
```

#### 2. Resource Detection
**File:** `src/telemetry_conf.rs:15-22`

```rust
fn get_resource() -> Resource {
    Resource::from_detectors(
        Duration::from_secs(0),
        vec![
            Box::new(OsResourceDetector),
            Box::new(ProcessResourceDetector),
        ],
    )
}
```

#### 3. Custom Metrics
**File:** `src/shipping_service/quote.rs:24-26`

```rust
let meter = global::meter("otel_demo.shipping.quote");
let counter = meter.u64_counter("app.shipping.items_count").build();
counter.add(number_of_items as u64, &[]);
```

#### 4. Custom Span Attributes
**File:** `src/shipping_service/quote.rs:28-36`

```rust
Ok(get_active_span(|span| {
    let q = create_quote_from_float(f);
    span.add_event(
        "Received Quote".to_string(),
        vec![KeyValue::new("app.shipping.cost.total", format!("{}", q))],
    );
    span.set_attribute(KeyValue::new("app.shipping.cost.total", format!("{}", q)));
    q
}))
```

### Dependencies
**File:** `Cargo.toml`

```toml
[dependencies]
actix-web = "4"
opentelemetry = { version = "0.27", features = ["trace", "logs"] }
opentelemetry_sdk = { version = "0.27", features = ["rt-tokio", "logs"] }
opentelemetry-otlp = { version = "0.27", features = ["grpc-tonic", "logs"] }
opentelemetry-instrumentation-actix-web = "0.23"
opentelemetry-resource-detectors = { version = "0.27", features = ["os", "process"] }
opentelemetry-semantic-conventions = "0.27"
```

### Environment Variables

```yaml
environment:
  - SHIPPING_PORT=50050
  - QUOTE_ADDR=http://quote:8090
  - OTEL_SERVICE_NAME=shipping
  - OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
  - OTEL_RESOURCE_ATTRIBUTES=service.namespace=opentelemetry-demo,service.version=2.1.3
```

### Telemetry Data Flow

```
┌─────────────────────────────────────────────────┐
│ Shipping Service (Rust/Actix-web)              │
│                                                  │
│ ┌────────────────────────────────────────────┐  │
│ │ main()                                     │  │
│ │   ↓                                        │  │
│ │ init_otel()                                │  │
│ │   ├─ Trace Provider (OTLP/gRPC)           │  │
│ │   ├─ Meter Provider (OTLP/gRPC)           │  │
│ │   └─ Logger Provider (OTLP/gRPC)          │  │
│ │   ↓                                        │  │
│ │ HttpServer with Middleware:                │  │
│ │   ├─ RequestTracing (auto-instrument)     │  │
│ │   └─ RequestMetrics (auto-instrument)     │  │
│ │   ↓                                        │  │
│ │ POST /get-quote                            │  │
│ │   [Auto: HTTP server span created]        │  │
│ │   ↓                                        │  │
│ │   request_quote()                          │  │
│ │     ├─ Create counter metric              │  │
│ │     ├─ HTTP POST to quote service         │  │
│ │     │  [Auto: HTTP client span]           │  │
│ │     ├─ Get active span                    │  │
│ │     ├─ Add event: "Received Quote"        │  │
│ │     └─ Set attribute: cost.total          │  │
│ │                                            │  │
│ │ [Export via OTLP/gRPC]                    │  │
│ │   • Traces                                │  │
│ │   • Metrics                               │  │
│ │   • Logs                                  │  │
│ └────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────┘
```

### Observability Outputs

#### Traces
**Exporter:** OTLP/gRPC (tonic)
**Auto-instrumented Spans:**
- HTTP server requests (`POST /get-quote`, `POST /ship-order`)
- HTTP client requests (to quote service)

**Custom Attributes:**
- `app.shipping.cost.total` - Shipping cost

**Span Events:**
- "Received Quote"

#### Metrics
**Custom Metrics:**
- `app.shipping.items_count` - Counter for items shipped

**Auto-collected Metrics:**
- HTTP request duration
- HTTP request count
- HTTP response sizes

#### Logs
**Exporter:** OTLP/gRPC
**Format:** Structured with trace correlation

---

## Key Files Reference

| File | Purpose |
|------|---------|
| `src/main.rs` | Service entry point, HTTP server setup |
| `src/shipping_service.rs` | HTTP endpoint handlers |
| `src/shipping_service/quote.rs` | Quote service integration with OTel |
| `src/telemetry_conf.rs` | OpenTelemetry configuration |
| `Cargo.toml` | Rust dependencies |
| `Dockerfile` | Container build |
| `README.md` | Documentation |

---

## Build and Development

### Local Build
```bash
cargo build --release
./target/release/shipping
```

### Docker Build
```bash
docker compose build shipping
docker compose up shipping
```

### Testing
```bash
cargo test
```

---

## Observability Best Practices Demonstrated

1. **Hybrid Instrumentation** - Auto + manual approaches
2. **Actix-web Middleware** - Request tracing and metrics
3. **HTTP Client Tracing** - Automatic outbound request instrumentation
4. **Custom Metrics** - Domain-specific counters
5. **Span Attributes** - Business context (shipping cost)
6. **Span Events** - Processing milestones
7. **Resource Detection** - OS and process metadata
8. **OTLP Export** - Standard protocol via gRPC

This service demonstrates production-ready OpenTelemetry instrumentation in Rust combining Actix-web middleware with manual instrumentation.
