# Recommendation Service - Architecture Documentation

## Overview

The Recommendation Service is a gRPC-based microservice written in Python that provides product recommendations. It demonstrates OpenTelemetry Python auto-instrumentation with manual span enrichment.

**Language:** Python 3.12
**Framework:** gRPC Python
**Service Type:** gRPC Server
**Primary Function:** Product Recommendation Generation

---

## Key Functionality

### Product Recommendations

**Method:** `ListRecommendations(ListRecommendationsRequest) → ListRecommendationsResponse`
**File:** `recommendation_server.py:42-56`

```python
def ListRecommendations(self, request, context):
    max_responses = 5
    product_ids = get_product_list(request.product_ids)

    logger.info(f"[Recv ListRecommendations] product_ids={product_ids}")

    response = demo_pb2.ListRecommendationsResponse()
    response.product_ids.extend(product_ids)

    return response
```

Returns up to 5 recommended products, filtering out currently viewed products.

### Recommendation Logic with Feature Flags

**File:** `recommendation_server.py:67-113`

```python
def get_product_list(request_product_ids):
    with tracer.start_as_current_span("get_product_list") as span:
        # Feature flag for cache failure simulation
        api = openfeature_api.get_instance()
        client = api.get_client()
        cache_fail = client.get_boolean_value("recommendationCacheFailure", False)

        span.set_attribute("app.recommendation.cache_enabled", True)

        if cache_fail:
            # Simulate 50% cache miss rate
            if random.randint(0,1) == 1:
                span.set_attribute("app.cache_hit", False)
                # Call product catalog
                cat_response = product_catalog_stub.ListProducts(demo_pb2.Empty())
                product_ids = [x.id for x in cat_response.products]
            else:
                span.set_attribute("app.cache_hit", True)
                # Use cached products (causes memory leak)
                product_ids = cached_products
        else:
            # Always call product catalog
            span.set_attribute("app.cache_hit", False)
            cat_response = product_catalog_stub.ListProducts(demo_pb2.Empty())
            product_ids = [x.id for x in cat_response.products]

        # Filter and sample products
        filtered_products = list(set(product_ids) - set(request_product_ids))
        num_products = min(5, len(filtered_products))
        prod_list = random.sample(filtered_products, num_products)

        span.set_attribute("app.products.count", len(product_ids))
        span.set_attribute("app.filtered_products.count", num_products)
        span.set_attribute("app.products_recommended.count", len(prod_list))

        # Record metric
        rec_svc_metrics["app_recommendations_counter"].add(
            len(prod_list),
            {'recommendation.type': 'catalog'}
        )

        return prod_list
```

**Feature Flag:** `recommendationCacheFailure` - Simulates cache behavior and memory leaks

---

## Role in the Ecosystem

### Upstream Dependencies
- **Frontend Service** - Requests product recommendations
- **Product Catalog Service** - Lists available products
- **Flagd Service** - Feature flag evaluation
- **OpenTelemetry Collector** - Telemetry aggregation

### Service Interactions

```
┌──────────────┐
│   Frontend   │
│   Service    │
└──────────────┘
       │
       │ gRPC: ListRecommendations()
       ▼
┌──────────────┐         ┌──────────────┐
│Recommend-    │────────▶│Product       │
│ation Svc     │ gRPC    │Catalog Svc   │
└──────────────┘         └──────────────┘
       │
       │                 ┌──────────────┐
       └────────────────▶│    Flagd     │
                  gRPC   │  (Feature    │
                         │   Flags)     │
                         └──────────────┘
```

---

## OpenTelemetry Implementation Details

### Auto-Instrumentation Setup

#### 1. Auto-Instrumentation Bootstrap
**File:** `Dockerfile:15`

```dockerfile
RUN venv/bin/opentelemetry-bootstrap -a install
```

Automatically installs instrumentation for detected packages:
- gRPC (grpcio)
- System metrics (psutil)

#### 2. Service Entry Point
**File:** `Dockerfile:30`

```dockerfile
ENTRYPOINT ["/venv/bin/opentelemetry-instrument", "/venv/bin/python", "recommendation_server.py"]
```

Uses `opentelemetry-instrument` wrapper to activate auto-instrumentation.

#### 3. Dependencies
**File:** `requirements.txt`

```txt
opentelemetry-distro==0.51b0
opentelemetry-exporter-otlp-proto-grpc==1.30.0
opentelemetry-instrumentation-grpc==0.51b0
opentelemetry-instrumentation-system-metrics==0.51b0
openfeature-provider-flagd==1.0.2
python-json-logger==3.3.0
```

### Manual Instrumentation

#### 1. Tracer Setup
**File:** `recommendation_server.py:135`

```python
tracer = trace.get_tracer_provider().get_tracer(service_name)
```

#### 2. Custom Span Creation
**File:** `recommendation_server.py:70`

```python
with tracer.start_as_current_span("get_product_list") as span:
    # Business logic
    span.set_attribute("app.recommendation.cache_enabled", True)
    span.set_attribute("app.cache_hit", cache_hit)
    span.set_attribute("app.products.count", len(product_ids))
    # ... more attributes
```

#### 3. Custom Metrics
**File:** `metrics.py:6-17`

```python
def init_metrics(meter):
    metrics = {}
    metrics["app_recommendations_counter"] = meter.create_counter(
        name="app_recommendations_counter",
        description="Counts the number of products recommended per request",
        unit="1"
    )
    return metrics
```

**Usage:**
```python
rec_svc_metrics["app_recommendations_counter"].add(
    len(prod_list),
    {'recommendation.type': 'catalog'}
)
```

#### 4. Structured Logging with Trace Correlation
**File:** `logger.py:12-18`

```python
class CustomJsonFormatter(jsonlogger.JsonFormatter):
    def add_fields(self, log_record, record, message_dict):
        super(CustomJsonFormatter, self).add_fields(log_record, record, message_dict)
        span = trace.get_current_span()
        if span:
            ctx = span.get_span_context()
            log_record['trace_id'] = format(ctx.trace_id, '032x')
            log_record['span_id'] = format(ctx.span_id, '016x')
```

#### 5. Logger Provider Setup
**File:** `recommendation_server.py:140-154`

```python
logger_provider = LoggerProvider(
    resource=Resource.create({'service.name': service_name})
)
log_exporter = OTLPLogExporter(insecure=True)
logger_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))
set_logger_provider(logger_provider)

handler = LoggingHandler(level=logging.NOTSET, logger_provider=logger_provider)
logger.addHandler(handler)
```

### Environment Variables

```yaml
environment:
  - RECOMMENDATION_PORT=9001
  - PRODUCT_CATALOG_ADDR=product-catalog:3550
  - FLAGD_HOST=flagd
  - FLAGD_PORT=8013
  - OTEL_SERVICE_NAME=recommendation
  - OTEL_PYTHON_LOG_CORRELATION=true
  - OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
  - OTEL_RESOURCE_ATTRIBUTES=service.namespace=opentelemetry-demo,service.version=2.1.3
```

### Observability Outputs

#### Traces
**Exporter:** OTLP/gRPC
**Custom Spans:** `get_product_list`
**Custom Attributes:**
- `app.recommendation.cache_enabled`
- `app.cache_hit`
- `app.products.count`
- `app.filtered_products.count`
- `app.products_recommended.count`

#### Metrics
**Custom Metrics:**
- `app_recommendations_counter` - Counter with `recommendation.type` dimension

#### Logs
**Format:** JSON with trace correlation
**Fields:** `trace_id`, `span_id`, log message, level

---

## Key Files Reference

| File | Purpose |
|------|---------|
| `recommendation_server.py` | Main service implementation |
| `metrics.py` | Custom metrics definition |
| `logger.py` | JSON formatter with trace correlation |
| `requirements.txt` | Python dependencies |
| `Dockerfile` | Container with auto-instrumentation |
| `README.md` | Documentation |

---

## Observability Best Practices Demonstrated

1. **Auto-Instrumentation** - Uses `opentelemetry-instrument` wrapper
2. **Custom Spans** - Manual spans for business logic
3. **Rich Attributes** - Cache hit/miss, product counts
4. **Custom Metrics** - Domain-specific counters
5. **Log Correlation** - Trace context in JSON logs
6. **Feature Flag Integration** - Chaos engineering patterns
7. **gRPC Instrumentation** - Automatic client/server tracing

This service demonstrates effective OpenTelemetry instrumentation in Python combining auto and manual approaches.
