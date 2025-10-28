# Product Catalog Service - Architecture Documentation

## Overview

The Product Catalog Service is a gRPC-based microservice written in Go that manages the product catalog with search capabilities and feature-flag-based failure injection. It demonstrates comprehensive OpenTelemetry instrumentation in Go.

**Language:** Go 1.24.2
**Framework:** gRPC with Protocol Buffers
**Service Type:** gRPC Server
**Primary Function:** Product Catalog Management and Search

---

## Key Functionality

### 1. List All Products

**Method:** `ListProducts(Empty) → ListProductsResponse`
**File:** `main.go:314-321`

```go
func (p *productCatalog) ListProducts(ctx context.Context, req *pb.Empty) (*pb.ListProductsResponse, error) {
    span := trace.SpanFromContext(ctx)
    span.AddEvent("List Products")
    return &pb.ListProductsResponse{Products: catalog}, nil
}
```

Returns all ~30 products from the catalog loaded from JSON files.

### 2. Get Single Product

**Method:** `GetProduct(GetProductRequest) → Product`
**File:** `main.go:323-366`

```go
func (p *productCatalog) GetProduct(ctx context.Context, req *pb.GetProductRequest) (*pb.Product, error) {
    span := trace.SpanFromContext(ctx)
    span.SetAttributes(attribute.String("app.product.id", req.Id))

    // Feature flag for controlled failure
    if p.checkProductFailure(ctx, req.Id) {
        msg := "Error: Product Catalog Fail Feature Flag Enabled"
        span.SetStatus(otelcodes.Error, msg)
        span.AddEvent(msg)
        return nil, status.Errorf(codes.Internal, msg)
    }

    // Find product
    var found *pb.Product
    for _, product := range catalog {
        if req.Id == product.Id {
            found = product
            break
        }
    }

    if found == nil {
        span.SetStatus(otelcodes.Error, "Product Not Found")
        return nil, status.Errorf(codes.NotFound, "Product not found: %s", req.Id)
    }

    span.AddEvent("Product Found")
    span.SetAttributes(
        attribute.String("app.product.name", found.Name),
    )

    logger.LogAttrs(ctx, slog.LevelInfo, "Product Found",
        slog.String("app.product.name", found.Name),
        slog.String("app.product.id", req.Id))

    return found, nil
}
```

**Feature Flag:** `productCatalogFailure` - Triggers failures for product "OLJCESPC7Z"

### 3. Search Products

**Method:** `SearchProducts(SearchProductsRequest) → SearchProductsResponse`
**File:** `main.go:368-382`

```go
func (p *productCatalog) SearchProducts(ctx context.Context, req *pb.SearchProductsRequest) (*pb.SearchProductsResponse, error) {
    span := trace.SpanFromContext(ctx)

    var result []*pb.Product
    for _, product := range catalog {
        if strings.Contains(strings.ToLower(product.Name), strings.ToLower(req.Query)) ||
            strings.Contains(strings.ToLower(product.Description), strings.ToLower(req.Query)) {
            result = append(result, product)
        }
    }

    span.SetAttributes(
        attribute.Int("app.products_search.count", len(result)),
    )

    return &pb.SearchProductsResponse{Results: result}, nil
}
```

Full-text search across product names and descriptions.

### 4. Product Data Loading

**File:** `main.go:203-241`

Products loaded from JSON files in `./products/` directory:
- Configurable reload interval (default: 10 seconds)
- Background goroutine reloads catalog periodically
- Products contain: id, name, description, picture, priceUsd, categories

---

## Role in the Ecosystem

### Upstream Dependencies
- **Checkout Service** - Fetches product details during order placement
- **Recommendation Service** - Lists all products for recommendations
- **Frontend Service** - Displays product catalog
- **Flagd Service** - Feature flag evaluation
- **OpenTelemetry Collector** - Telemetry aggregation

### Downstream Consumers
- None (leaf service)

### Service Interactions

```
┌──────────────┐         ┌──────────────┐
│   Checkout   │         │ Recommend-   │
│   Service    │         │  ation Svc   │
└──────────────┘         └──────────────┘
       │                        │
       │ gRPC: GetProduct()     │ gRPC: ListProducts()
       │                        │
       └────────┬───────────────┘
                ▼
        ┌──────────────┐         ┌──────────────┐
        │Product       │────────▶│    Flagd     │
        │Catalog Svc   │ gRPC    │  (Feature    │
        └──────────────┘         │   Flags)     │
                │                └──────────────┘
                │ OTLP
                ▼
        ┌──────────────┐
        │     OTel     │
        │  Collector   │
        └──────────────┘
```

---

## High-Level API/Interface

### gRPC Service Definition
**Proto File:** `pb/demo.proto`

```protobuf
service ProductCatalogService {
    rpc ListProducts(Empty) returns (ListProductsResponse) {}
    rpc GetProduct(GetProductRequest) returns (Product) {}
    rpc SearchProducts(SearchProductsRequest) returns (SearchProductsResponse) {}
}

message Product {
    string id = 1;
    string name = 2;
    string description = 3;
    string picture = 4;
    Money price_usd = 5;
    repeated string categories = 6;
}
```

### Incoming Calls

**Port:** 3550 (configurable via `PRODUCT_CATALOG_PORT`)

**Example Product:**
```json
{
  "id": "OLJCESPC7Z",
  "name": "Sunglasses",
  "description": "Add a modern touch to your outfits with these sleek aviator sunglasses.",
  "picture": "/static/img/products/sunglasses.jpg",
  "price_usd": {
    "currency_code": "USD",
    "units": 19,
    "nanos": 990000000
  },
  "categories": ["accessories"]
}
```

---

## OpenTelemetry Implementation Details

### Manual Instrumentation Setup

#### 1. Tracer Provider Initialization
**File:** `main.go:86-101`

```go
func initTracerProvider() *sdktrace.TracerProvider {
    ctx := context.Background()

    // Create OTLP gRPC exporter
    exporter, err := otlptracegrpc.New(ctx)
    if err != nil {
        logger.Error(fmt.Sprintf("OTLP Trace gRPC Creation: %v", err))
    }

    // Create tracer provider with batch processor
    tp := sdktrace.NewTracerProvider(
        sdktrace.WithBatcher(exporter),
        sdktrace.WithResource(initResource()),
    )

    // Set global tracer provider
    otel.SetTracerProvider(tp)

    // Configure propagators
    otel.SetTextMapPropagator(propagation.NewCompositeTextMapPropagator(
        propagation.TraceContext{},  // W3C Trace Context
        propagation.Baggage{},        // W3C Baggage
    ))

    return tp
}
```

#### 2. Meter Provider Initialization
**File:** `main.go:103-117`

```go
func initMeterProvider() *sdkmetric.MeterProvider {
    ctx := context.Background()

    // Create OTLP gRPC exporter for metrics
    exporter, err := otlpmetricgrpc.New(ctx)
    if err != nil {
        logger.Error(fmt.Sprintf("new otlp metric grpc exporter failed: %v", err))
    }

    // Create meter provider with periodic reader
    mp := sdkmetric.NewMeterProvider(
        sdkmetric.WithReader(sdkmetric.NewPeriodicReader(exporter)),
        sdkmetric.WithResource(initResource()),
    )

    otel.SetMeterProvider(mp)
    return mp
}
```

#### 3. Logger Provider Initialization
**File:** `main.go:119-133`

```go
func initLoggerProvider() *sdklog.LoggerProvider {
    ctx := context.Background()

    // Create OTLP gRPC log exporter
    logExporter, err := otlploggrpc.New(ctx)
    if err != nil {
        return nil
    }

    // Create logger provider with batch processor
    loggerProvider := sdklog.NewLoggerProvider(
        sdklog.WithProcessor(sdklog.NewBatchProcessor(logExporter)),
    )

    global.SetLoggerProvider(loggerProvider)
    return loggerProvider
}
```

#### 4. Resource Configuration
**File:** `main.go:69-84`

```go
func initResource() *sdkresource.Resource {
    initResourcesOnce.Do(func() {
        extraResources, _ := sdkresource.New(
            context.Background(),
            sdkresource.WithOS(),         // OS info
            sdkresource.WithProcess(),    // Process info
            sdkresource.WithContainer(),  // Container info
            sdkresource.WithHost(),       // Host info
        )
        resource, _ = sdkresource.Merge(
            sdkresource.Default(),
            extraResources,
        )
    })
    return resource
}
```

#### 5. gRPC Server Instrumentation
**File:** `main.go:185-187`

```go
srv := grpc.NewServer(
    grpc.StatsHandler(otelgrpc.NewServerHandler()),
)
```

Auto-instruments all gRPC server operations with spans.

#### 6. Runtime Metrics Collection
**File:** `main.go:169`

```go
err := runtime.Start(runtime.WithMinimumReadMemStatsInterval(time.Second))
```

Collects Go runtime metrics (memory, GC, goroutines).

### Manual Span Attributes

**File:** `main.go:323-366`

```go
// Access current span
span := trace.SpanFromContext(ctx)

// Set attributes
span.SetAttributes(
    attribute.String("app.product.id", req.Id),
    attribute.String("app.product.name", found.Name),
)

// Add events
span.AddEvent("Product Found")

// Set status
span.SetStatus(otelcodes.Error, "Product Not Found")
```

**Custom Attributes:**
| Attribute | Type | Description |
|-----------|------|-------------|
| `app.product.id` | string | Product identifier |
| `app.product.name` | string | Product name |
| `app.products_search.count` | int | Number of search results |

### Structured Logging with OTel Bridge
**File:** `main.go:63-67`

```go
func init() {
    logger = otelslog.NewLogger("product-catalog")
    loadProductCatalog()
}
```

**Usage:**
```go
logger.LogAttrs(ctx, slog.LevelInfo, "Product Found",
    slog.String("app.product.name", found.Name),
    slog.String("app.product.id", req.Id))
```

Logs automatically include trace context.

### Feature Flag Integration
**File:** `main.go:384-394`

```go
func (p *productCatalog) checkProductFailure(ctx context.Context, id string) bool {
    if id == "OLJCESPC7Z" {
        ffClient := openfeature.NewClient("product-catalog")
        ffValue, _ := ffClient.BooleanValue(
            ctx, "productCatalogFailure", false,
            openfeature.EvaluationContext{},
        )
        return ffValue
    }
    return false
}
```

### Environment Variables

```yaml
environment:
  - PRODUCT_CATALOG_PORT=3550
  - FLAGD_HOST=flagd
  - FLAGD_PORT=8013
  - OTEL_SERVICE_NAME=product-catalog
  - OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
  - OTEL_RESOURCE_ATTRIBUTES=service.namespace=opentelemetry-demo,service.version=2.1.3
  - OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=cumulative
```

### Telemetry Data Flow

```
┌───────────────────────────────────────────────────────┐
│ Product Catalog Service (Go)                          │
│                                                        │
│ ┌────────────────────────────────────────────────┐    │
│ │ main()                                         │    │
│ │   ↓                                            │    │
│ │ initTracerProvider() → OTLP gRPC Exporter     │    │
│ │ initMeterProvider() → Periodic Reader         │    │
│ │ initLoggerProvider() → Batch Processor        │    │
│ │ runtime.Start() → Go Runtime Metrics          │    │
│ │   ↓                                            │    │
│ │ gRPC Server with otelgrpc.NewServerHandler()  │    │
│ │   ↓                                            │    │
│ │ GetProduct() / ListProducts() / SearchProducts()   │
│ │   ├─ [Auto] Server span created               │    │
│ │   ├─ Access span: trace.SpanFromContext(ctx)  │    │
│ │   ├─ Set attributes (product.id, product.name)│    │
│ │   ├─ Add events ("Product Found")             │    │
│ │   ├─ Feature flag check (if applicable)       │    │
│ │   ├─ Structured logging with trace context    │    │
│ │   └─ Set span status (OK or Error)            │    │
│ │                                                │    │
│ │ [Export via OTLP/gRPC to otel-collector:4317] │    │
│ │   • Traces (spans with attributes, events)    │    │
│ │   • Metrics (runtime metrics)                 │    │
│ │   • Logs (with trace correlation)             │    │
│ └────────────────────────────────────────────────┘    │
└────────────────────────────────────────────────────────┘
```

### Observability Outputs

#### Traces
**Exporter:** OTLP/gRPC
**Endpoint:** `http://otel-collector:4317/v1/traces`
**Backend:** Jaeger (via OTel Collector)

**Span Types:**
- `grpc.server: ListProducts` (auto)
- `grpc.server: GetProduct` (auto)
- `grpc.server: SearchProducts` (auto)
- Feature flag evaluation spans (auto, via OpenFeature)

**Custom Attributes:**
- Product ID, name
- Search result count
- Error messages

#### Metrics
**Exporter:** OTLP/gRPC
**Endpoint:** `http://otel-collector:4317/v1/metrics`
**Backend:** Prometheus (via OTel Collector)

**Auto-collected Metrics:**
- `process.runtime.go.mem.heap_alloc`
- `process.runtime.go.gc.count`
- `process.runtime.go.goroutines`
- gRPC server metrics (request count, duration)

#### Logs
**Exporter:** OTLP/gRPC
**Endpoint:** `http://otel-collector:4317/v1/logs`
**Format:** Structured with trace correlation

---

## Configuration Reference

### Environment Variables
```bash
PRODUCT_CATALOG_PORT=3550
FLAGD_HOST=flagd
FLAGD_PORT=8013
OTEL_SERVICE_NAME=product-catalog
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
OTEL_RESOURCE_ATTRIBUTES=service.namespace=opentelemetry-demo,service.version=2.1.3
OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=cumulative
```

---

## Key Files Reference

| File | Purpose |
|------|---------|
| `main.go` | Service implementation, OTel setup |
| `products/*.json` | Product catalog data |
| `go.mod` | Go dependencies |
| `Dockerfile` | Container build |
| `README.md` | Documentation |

---

## Dependencies

### Key Go Modules
```go
require (
    github.com/open-feature/go-sdk v1.15.0
    github.com/open-feature/go-sdk-contrib/hooks/open-telemetry v0.3.6
    github.com/open-feature/go-sdk-contrib/providers/flagd v0.3.4
    go.opentelemetry.io/contrib/bridges/otelslog v0.13.0
    go.opentelemetry.io/contrib/instrumentation/google.golang.org/grpc/otelgrpc v0.63.0
    go.opentelemetry.io/contrib/instrumentation/runtime v0.63.0
    go.opentelemetry.io/otel v1.34.0
    go.opentelemetry.io/otel/exporters/otlp/otlplog/otlploggrpc v0.13.0
    go.opentelemetry.io/otel/exporters/otlp/otlpmetric/otlpmetricgrpc v1.34.0
    go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracegrpc v1.34.0
    google.golang.org/grpc v1.70.0
)
```

---

## Build and Development

### Local Build
```bash
go build -o /go/bin/product-catalog/
./product-catalog
```

### Docker Build
```bash
docker compose build product-catalog
docker compose up product-catalog
```

### Regenerate Proto
```bash
make docker-generate-protobuf
```

---

## Observability Best Practices Demonstrated

1. **Comprehensive OTel Setup** - Traces, metrics, logs all configured
2. **gRPC Instrumentation** - Auto-instrumentation via stats handler
3. **Resource Detection** - Auto-detects OS, process, container, host
4. **Structured Logging** - slog bridge with trace correlation
5. **Custom Span Attributes** - Business context (product ID, name)
6. **Span Events** - Marks processing milestones
7. **Error Handling** - Proper span status for errors
8. **Feature Flag Integration** - Chaos engineering with observability
9. **Runtime Metrics** - Go performance monitoring
10. **OTLP Export** - Standard protocol for all signals

This service demonstrates production-ready OpenTelemetry instrumentation in Go with comprehensive manual instrumentation and auto-instrumentation.
