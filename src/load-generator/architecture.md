# Load Generator Service - Architecture Documentation

## Overview

The Load Generator Service is a Python-based traffic simulation tool using Locust that generates realistic user traffic against the OpenTelemetry demo application. It demonstrates comprehensive OpenTelemetry instrumentation including manual spans, baggage propagation, and browser-based testing with Playwright.

**Language:** Python 3.12
**Framework:** Locust (load testing) + Playwright (browser automation)
**Service Type:** Traffic Generator / Load Testing
**Primary Function:** Synthetic User Traffic Generation with Observability

---

## Key Functionality

### 1. HTTP Traffic Generation

**Class:** `WebsiteUser(HttpUser)`
**File:** `locustfile.py:113-217`

Simulates realistic user behavior with weighted tasks:

| Task | Weight | Description |
|------|--------|-------------|
| `index()` | 1 | Access homepage |
| `browse_product()` | 10 | View product details (most common) |
| `get_recommendations()` | 3 | Fetch product recommendations |
| `get_ads()` | 3 | Retrieve advertisements |
| `view_cart()` | 3 | View shopping cart |
| `add_to_cart()` | 2 | Add items to cart |
| `checkout()` | 1 | Complete single-item checkout |
| `checkout_multi()` | 1 | Complete multi-item checkout |
| `flood_home()` | 5 | Feature flag controlled load spike |

### 2. Example Task: Browse Product

**File:** `locustfile.py:126-131`

```python
@task(10)
def browse_product(self):
    product = random.choice(products)
    with self.tracer.start_as_current_span("user_browse_product",
                                          context=Context(),
                                          attributes={"product.id": product}):
        logging.info(f"User browsing product: {product}")
        self.client.get("/api/products/" + product)
```

**Instrumentation:**
- Creates manual span `user_browse_product`
- Adds custom attribute `product.id`
- Logs with automatic trace correlation
- HTTP request auto-instrumented

### 3. Checkout Flow

**File:** `locustfile.py:178-186`

```python
@task(1)
def checkout(self):
    user = str(uuid.uuid1())
    with self.tracer.start_as_current_span("user_checkout_single",
                                          context=Context(),
                                          attributes={"user.id": user}):
        self.add_to_cart(user=user)
        checkout_person = random.choice(people)
        checkout_person["userId"] = user
        self.client.post("/api/checkout", json=checkout_person)
        logging.info(f"Checkout completed for user {user}")
```

**Flow:**
1. Generate unique user ID
2. Create checkout span
3. Add item to cart (nested span)
4. Load random user profile from `people.json`
5. Submit checkout request
6. Log completion with trace context

### 4. Feature Flag Integration

**File:** `locustfile.py:200-207`

```python
@task(5)
def flood_home(self):
    flood_count = get_flagd_value("loadGeneratorFloodHomepage")
    if flood_count > 0:
        with self.tracer.start_as_current_span("user_flood_home",
                                              context=Context(),
                                              attributes={"flood.count": flood_count}):
            logging.info(f"User flooding homepage {flood_count} times")
            for _ in range(0, flood_count):
                self.client.get("/")
```

**Feature Flag:** `loadGeneratorFloodHomepage`
- Controls traffic spike intensity
- Allows runtime chaos engineering
- Demonstrates feature flag observability

### 5. Session Initialization with Baggage

**File:** `locustfile.py:209-216`

```python
def on_start(self):
    with self.tracer.start_as_current_span("user_session_start", context=Context()):
        session_id = str(uuid.uuid4())
        logging.info(f"Starting user session: {session_id}")
        ctx = baggage.set_baggage("session.id", session_id)
        ctx = baggage.set_baggage("synthetic_request", "true", context=ctx)
        context.attach(ctx)
        self.index()
```

**Baggage Propagation:**
- `session.id` - Unique user session identifier
- `synthetic_request=true` - Marks traffic as synthetic

This baggage propagates to all downstream services, allowing filtering of real vs synthetic traffic in observability backends.

### 6. Browser-Based Traffic (Playwright)

**Class:** `WebsiteBrowserUser(PlaywrightUser)`
**File:** `locustfile.py:222-258`
**Enabled:** `LOCUST_BROWSER_TRAFFIC_ENABLED=true`

#### Change Currency Task

```python
@task
@pw
async def open_cart_page_and_change_currency(self, page: PageWithRetry):
    with self.tracer.start_as_current_span("browser_change_currency", context=Context()):
        try:
            page.on("console", lambda msg: print(msg.text))
            await page.route('**/*', add_baggage_header)
            await page.goto("/cart", wait_until="domcontentloaded")
            await page.select_option('[name="currency_code"]', 'CHF')
            await page.wait_for_timeout(2000)  # Allow browser to export traces
            logging.info("Currency changed to CHF")
        except Exception as e:
            logging.error(f"Error in change currency task: {str(e)}")
```

**Features:**
- Headless Chrome browser automation
- Injects baggage headers into all browser requests
- Waits for client-side OpenTelemetry export
- Captures console logs and errors

#### Baggage Header Injection

**File:** `locustfile.py:260-266`

```python
async def add_baggage_header(route: Route, request: Request):
    existing_baggage = request.headers.get('baggage', '')
    headers = {
        **request.headers,
        'baggage': ', '.join(filter(None, (existing_baggage, 'synthetic_request=true')))
    }
    await route.continue_(headers=headers)
```

Ensures all browser-initiated requests include `synthetic_request=true` baggage.

---

## Role in the Ecosystem

### Upstream Dependencies
- **Frontend Service** - Generates traffic against all frontend endpoints
- **Flagd Service** - Feature flag evaluation
- **OpenTelemetry Collector** - Telemetry aggregation

### Downstream Impact
- Triggers entire service call graph
- Generates telemetry across all services
- Creates realistic load patterns

### Service Interactions

```
┌────────────────┐         ┌──────────────┐
│ Load Generator │────────▶│   Frontend   │
│   (Locust +    │  HTTP   │   Service    │
│   Playwright)  │         └──────────────┘
└────────────────┘                │
       │                          │
       │ gRPC                     └─▶ [Triggers entire service graph]
       ▼
┌──────────────┐
│    Flagd     │
│ (Feature     │
│  Flags)      │
└──────────────┘
       │
       │ OTLP/gRPC
       ▼
┌──────────────┐
│     OTel     │
│  Collector   │
└──────────────┘
```

**Traffic Pattern:**
- Load Generator → Frontend → Product Catalog
- Load Generator → Frontend → Cart → Redis
- Load Generator → Frontend → Checkout → (Payment, Shipping, Email, Kafka)
- Load Generator → Frontend → Currency
- Load Generator → Frontend → Recommendation → Product Catalog
- Load Generator → Frontend → Ad

---

## OpenTelemetry Implementation Details

### Initialization and Configuration

#### 1. Tracer Provider Setup
**File:** `locustfile.py:41-44`

```python
tracer_provider = TracerProvider()
trace.set_tracer_provider(tracer_provider)
tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(insecure=True)))
```

**Components:**
- **TracerProvider** - Global trace provider
- **BatchSpanProcessor** - Batches spans before export
- **OTLPSpanExporter** - Exports via OTLP/gRPC

#### 2. Logger Provider Setup
**File:** `locustfile.py:46-60`

```python
logger_provider = LoggerProvider()
set_logger_provider(logger_provider)

log_exporter = OTLPLogExporter(insecure=True)
logger_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))

handler = LoggingHandler(level=logging.INFO, logger_provider=logger_provider)

root_logger = logging.getLogger()
root_logger.addHandler(handler)
root_logger.setLevel(logging.INFO)
```

**Log Correlation:**
- Logs automatically include trace context (trace_id, span_id)
- JSON formatted logs
- OTLP/gRPC export

#### 3. Meter Provider Setup
**File:** `locustfile.py:62-64`

```python
metric_exporter = OTLPMetricExporter(insecure=True)
set_meter_provider(MeterProvider([PeriodicExportingMetricReader(metric_exporter)]))
```

**Metrics Export:**
- Periodic export to OTel Collector
- System metrics included

#### 4. Auto-Instrumentation Libraries
**File:** `locustfile.py:66-73`

```python
# Instrument logging to automatically inject trace context
LoggingInstrumentor().instrument(set_logging_format=True)

# Instrumenting manually to avoid error with locust gevent monkey
Jinja2Instrumentor().instrument()
RequestsInstrumentor().instrument()
SystemMetricsInstrumentor().instrument()
URLLib3Instrumentor().instrument()
```

**What Gets Auto-Instrumented:**
- **Jinja2** - Template rendering
- **Requests** - HTTP client calls (creates spans)
- **SystemMetrics** - CPU, memory, disk
- **URLLib3** - Lower-level HTTP client
- **Logging** - Trace context injection

#### 5. Feature Flag Instrumentation
**File:** `locustfile.py:78-80`

```python
base_url = f"http://{os.environ.get('FLAGD_HOST', 'localhost')}:{os.environ.get('FLAGD_OFREP_PORT', 8016)}"
api.set_provider(OFREPProvider(base_url=base_url))
api.add_hooks([TracingHook()])
```

**TracingHook()** - Automatically creates spans for feature flag evaluations

### Manual Instrumentation Patterns

#### 1. Creating Custom Spans
**File:** `locustfile.py:116-118`

```python
def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self.tracer = trace.get_tracer(__name__)
```

Each user gets a tracer instance for creating spans.

#### 2. Span with Attributes
**File:** `locustfile.py:127-131`

```python
with self.tracer.start_as_current_span("user_browse_product",
                                      context=Context(),
                                      attributes={"product.id": product}):
    logging.info(f"User browsing product: {product}")
    self.client.get("/api/products/" + product)
```

**Pattern:**
- Use context manager for automatic span lifecycle
- Set attributes during span creation
- Logs automatically correlated to span

#### 3. Nested Spans
**File:** `locustfile.py:178-185`

```python
with self.tracer.start_as_current_span("user_checkout_single", ...):
    self.add_to_cart(user=user)  # Creates nested span
    checkout_person = random.choice(people)
    checkout_person["userId"] = user
    self.client.post("/api/checkout", json=checkout_person)
```

`add_to_cart()` creates a child span, forming a trace hierarchy.

#### 4. Baggage Propagation

**File:** `locustfile.py:211-215`

```python
session_id = str(uuid.uuid4())
ctx = baggage.set_baggage("session.id", session_id)
ctx = baggage.set_baggage("synthetic_request", "true", context=ctx)
context.attach(ctx)
```

**Baggage Keys:**
- `session.id` - User session identifier
- `synthetic_request` - Marks traffic as load test generated

**Propagation:**
- HTTP headers automatically injected by `RequestsInstrumentor`
- Propagates to all downstream services
- Available in all service logs/traces

### Dependencies

**File:** `requirements.txt`

```txt
Flask==3.1.2
locust-plugins[playwright]==5.0.0
python-json-logger==4.0.0
openfeature-provider-ofrep==0.2.0
openfeature-hooks-opentelemetry==0.2.0
opentelemetry-api==1.38.0
opentelemetry-exporter-otlp-proto-grpc==1.38.0
opentelemetry-instrumentation-jinja2==0.59b0
opentelemetry-instrumentation-requests==0.59b0
opentelemetry-instrumentation-system-metrics==0.59b0
opentelemetry-instrumentation-urllib3==0.59b0
opentelemetry-sdk==1.38.0
opentelemetry-semantic-conventions==0.59b0
opentelemetry-instrumentation-logging==0.59b0
```

### Environment Variables

```yaml
environment:
  - LOCUST_WEB_PORT=8089
  - LOCUST_USERS=10
  - LOCUST_SPAWN_RATE=1
  - LOCUST_HOST=http://frontend:8080
  - LOCUST_HEADLESS=false
  - LOCUST_AUTOSTART=true
  - LOCUST_BROWSER_TRAFFIC_ENABLED=true
  - OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
  - OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://otel-collector:4317/v1/traces
  - OTEL_EXPORTER_OTLP_METRICS_ENDPOINT=http://otel-collector:4317/v1/metrics
  - OTEL_SERVICE_NAME=load-generator
  - FLAGD_HOST=flagd
  - FLAGD_OFREP_PORT=8016
```

### Telemetry Data Flow

```
┌──────────────────────────────────────────────────────┐
│ Load Generator (Python/Locust)                       │
│                                                       │
│ ┌─────────────────────────────────────────────────┐  │
│ │ Startup                                         │  │
│ │   ↓                                             │  │
│ │ TracerProvider + BatchSpanProcessor             │  │
│ │ MeterProvider + PeriodicExportingMetricReader   │  │
│ │ LoggerProvider + BatchLogRecordProcessor        │  │
│ │   ↓                                             │  │
│ │ Auto-Instrumentation:                           │  │
│ │   ├─ RequestsInstrumentor (HTTP client)        │  │
│ │   ├─ URLLib3Instrumentor                       │  │
│ │   ├─ SystemMetricsInstrumentor                 │  │
│ │   ├─ Jinja2Instrumentor                        │  │
│ │   └─ LoggingInstrumentor (trace correlation)   │  │
│ │   ↓                                             │  │
│ │ OpenFeature + TracingHook                       │  │
│ │   ↓                                             │  │
│ │ User Simulation (Locust Tasks)                  │  │
│ │   ↓                                             │  │
│ │ on_start():                                     │  │
│ │   ├─ Create session.id                         │  │
│ │   ├─ Set baggage: session.id, synthetic_request│  │
│ │   └─ Attach context                            │  │
│ │   ↓                                             │  │
│ │ Task Execution (e.g., browse_product):         │  │
│ │   ├─ Manual span: "user_browse_product"       │  │
│ │   ├─ Set attribute: product.id                │  │
│ │   ├─ Log with trace context                   │  │
│ │   ├─ HTTP GET: /api/products/XYZ              │  │
│ │   │   [Auto: HTTP client span]                │  │
│ │   │   [Auto: Inject baggage headers]          │  │
│ │   │   [Auto: Inject trace context]            │  │
│ │   └─ End manual span                          │  │
│ │   ↓                                             │  │
│ │ Browser Tasks (if enabled):                    │  │
│ │   ├─ Playwright automation                     │  │
│ │   ├─ Inject baggage into all requests         │  │
│ │   ├─ Wait for browser telemetry export        │  │
│ │   └─ Capture browser-side traces              │  │
│ │   ↓                                             │  │
│ │ [Export via OTLP/gRPC]                         │  │
│ │   • Traces (manual + auto spans)              │  │
│ │   • Metrics (system + custom)                 │  │
│ │   • Logs (with trace correlation)             │  │
│ └─────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────┘
       │
       │ OTLP/gRPC
       ▼
┌──────────────┐
│     OTel     │
│  Collector   │
└──────────────┘
```

### Observability Outputs

#### Traces
**Exporter:** OTLP/gRPC
**Endpoint:** `http://otel-collector:4317/v1/traces`

**Custom Spans:**
- `user_session_start` - Session initialization
- `user_index` - Homepage access
- `user_browse_product` - Product browsing
- `user_get_recommendations` - Recommendation requests
- `user_get_ads` - Ad requests
- `user_view_cart` - Cart viewing
- `user_add_to_cart` - Add to cart operation
- `user_checkout_single` - Single item checkout
- `user_checkout_multi` - Multi-item checkout
- `user_flood_home` - Load spike simulation
- `browser_change_currency` - Browser currency change
- `browser_add_to_cart` - Browser add to cart

**Auto-Instrumented Spans:**
- HTTP client requests (via RequestsInstrumentor)
- Feature flag evaluations (via TracingHook)

**Custom Attributes:**
- `product.id` - Product being accessed
- `user.id` - User identifier
- `quantity` - Cart quantity
- `category` - Ad category
- `item.count` - Multi-checkout count
- `flood.count` - Load spike size

**Baggage:**
- `session.id` - Propagates to all services
- `synthetic_request=true` - Filters synthetic traffic

#### Metrics
**Exporter:** OTLP/gRPC
**Endpoint:** `http://otel-collector:4317/v1/metrics`

**Auto-Collected Metrics:**
- `system.cpu.utilization` - CPU usage
- `system.memory.usage` - Memory consumption
- `system.disk.io` - Disk I/O
- `process.runtime.python.*` - Python runtime metrics

#### Logs
**Exporter:** OTLP/gRPC
**Format:** JSON with trace correlation

**Example Log:**
```json
{
  "timestamp": "2025-10-27T10:15:30Z",
  "level": "INFO",
  "message": "User browsing product: OLJCESPC7Z",
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
  "span_id": "00f067aa0ba902b7",
  "service.name": "load-generator"
}
```

---

## Configuration Reference

### Environment Variables
```bash
LOCUST_WEB_PORT=8089
LOCUST_USERS=10
LOCUST_SPAWN_RATE=1
LOCUST_HOST=http://frontend:8080
LOCUST_HEADLESS=false
LOCUST_AUTOSTART=true
LOCUST_BROWSER_TRAFFIC_ENABLED=true
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
OTEL_SERVICE_NAME=load-generator
FLAGD_HOST=flagd
FLAGD_OFREP_PORT=8016
```

---

## Key Files Reference

| File | Purpose |
|------|---------|
| `locustfile.py` | Main load test definition with OTel instrumentation |
| `people.json` | User profiles for checkout |
| `requirements.txt` | Python dependencies |
| `Dockerfile` | Container build with Playwright |

---

## Build and Development

### Local Build
```bash
pip install -r requirements.txt
playwright install --with-deps chromium
locust -f locustfile.py --host=http://localhost:8080
```

### Docker Build
```bash
docker compose build load-generator
docker compose up load-generator
```

### Access Locust UI
- **Web UI:** http://localhost:8089
- Configure users, spawn rate, and start load test

---

## Observability Best Practices Demonstrated

1. **Comprehensive Instrumentation** - Traces, metrics, logs all configured
2. **Baggage Propagation** - Session tracking across distributed system
3. **Synthetic Request Marking** - `synthetic_request=true` for filtering
4. **Manual + Auto Instrumentation** - Custom business spans + auto HTTP spans
5. **Trace Correlation** - Logs automatically include trace context
6. **Feature Flag Observability** - TracingHook for flag evaluations
7. **Browser Instrumentation** - Playwright with baggage injection
8. **Custom Span Attributes** - Rich business context (product ID, user ID)
9. **Realistic Traffic Patterns** - Weighted tasks simulate real users
10. **Chaos Engineering** - Feature flag controlled load spikes
11. **End-to-End Testing** - Validates entire service graph observability

This service demonstrates advanced OpenTelemetry instrumentation patterns for load testing tools, including baggage propagation, trace correlation, and browser-based testing with comprehensive observability.
