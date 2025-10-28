# Cart Service - Architecture Documentation

## Overview

The Cart Service is a gRPC-based microservice built with ASP.NET Core 8.0 that manages shopping cart operations using Valkey (Redis) as the persistence layer. It demonstrates comprehensive OpenTelemetry instrumentation combining auto and manual approaches.

**Language:** C# / .NET 8.0
**Framework:** ASP.NET Core with gRPC
**Cache:** Valkey (Redis-compatible)
**Service Type:** gRPC Server
**Primary Function:** Shopping Cart Management with Distributed Caching

---

## Key Functionality

### 1. Cart Management via gRPC

The service exposes three gRPC endpoints for cart operations.

#### AddItem - Add Product to Cart
**File:** `services/CartService.cs:28-47`

```csharp
public override async Task<Empty> AddItem(AddItemRequest request, ServerCallContext context)
{
    var activity = Activity.Current;
    activity?.SetTag("app.user.id", request.UserId);
    activity?.SetTag("app.product.id", request.Item.ProductId);
    activity?.SetTag("app.product.quantity", request.Item.Quantity);

    try
    {
        await _cartStore.AddItemAsync(request.UserId, request.Item.ProductId, request.Item.Quantity);
        return Empty;
    }
    catch (RpcException ex)
    {
        activity?.AddException(ex);
        activity?.SetStatus(ActivityStatusCode.Error, ex.Message);
        throw;
    }
}
```

**Functionality:**
- Adds or updates product quantity in user's cart
- Sets 60-minute expiration on cart data
- Records custom span attributes for userId, productId, and quantity
- Tracks operation latency via custom histogram

#### GetCart - Retrieve User's Cart
**File:** `services/CartService.cs:49-73`

```csharp
public override async Task<Cart> GetCart(GetCartRequest request, ServerCallContext context)
{
    var activity = Activity.Current;
    activity?.SetTag("app.user.id", request.UserId);
    activity?.AddEvent(new("Fetch cart"));

    try
    {
        var cart = await _cartStore.GetCartAsync(request.UserId);
        var totalCart = 0;
        foreach (var item in cart.Items)
        {
            totalCart += item.Quantity;
        }
        activity?.SetTag("app.cart.items.count", totalCart);

        return cart;
    }
    catch (RpcException ex)
    {
        activity?.AddException(ex);
        activity?.SetStatus(ActivityStatusCode.Error, ex.Message);
        throw;
    }
}
```

**Functionality:**
- Retrieves cart from Valkey
- Calculates total item count
- Adds span event "Fetch cart"
- Sets cart item count as span attribute

#### EmptyCart - Clear User's Cart
**File:** `services/CartService.cs:75-100`

```csharp
public override async Task<Empty> EmptyCart(EmptyCartRequest request, ServerCallContext context)
{
    var activity = Activity.Current;
    activity?.SetTag("app.user.id", request.UserId);

    await _featureClient.GetBooleanValueAsync("cartFailure", defaultValue: false)
        .ContinueWith(task =>
        {
            if (task.Result)
            {
                // Chaos engineering: use bad store
                _badCartStore.EmptyCartAsync(request.UserId);
            }
            else
            {
                _cartStore.EmptyCartAsync(request.UserId);
            }
        });

    return Empty;
}
```

**Functionality:**
- Clears cart for specified user
- Feature flag integration for chaos engineering (`cartFailure` flag)
- Demonstrates failure injection capability

### 2. Valkey (Redis) Storage

**File:** `cartstore/ValkeyCartStore.cs`

**Storage Model:**
- **Data Structure:** Redis Hash
- **Key:** `{userId}`
- **Field:** `"cart"`
- **Value:** Serialized Protocol Buffer (`Cart` message)
- **TTL:** 60 minutes (3600 seconds)

**Key Operations:**

```csharp
// AddItemAsync (lines 138-171)
public async Task AddItemAsync(string userId, string productId, int quantity)
{
    var stopwatch = Stopwatch.StartNew();
    try
    {
        var cart = await GetCartAsync(userId);
        var existingItem = cart.Items.FirstOrDefault(i => i.ProductId == productId);

        if (existingItem != null)
        {
            existingItem.Quantity += quantity;
        }
        else
        {
            cart.Items.Add(new CartItem { ProductId = productId, Quantity = quantity });
        }

        await _redis.HashSetAsync(userId, "cart", cart.ToByteArray());
        await _redis.KeyExpireAsync(userId, TimeSpan.FromMinutes(60));
    }
    finally
    {
        addItemHistogram.Record(stopwatch.Elapsed.TotalSeconds);
    }
}
```

**Redis Commands Used:**
- `HGET {userId} cart` - Retrieve cart
- `HSET {userId} cart {protobuf}` - Store cart
- `EXPIRE {userId} 3600` - Set TTL
- `PING` - Health check

### 3. Custom Metrics

**File:** `cartstore/ValkeyCartStore.cs:28-36`

Two histogram metrics track cart operation latency:

```csharp
private static readonly Histogram<double> addItemHistogram = CartMeter.CreateHistogram(
    "app.cart.add_item.latency",
    unit: "s",
    advice: new InstrumentAdvice<double>
    {
        HistogramBucketBoundaries = [ 0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1, 2.5, 5, 7.5, 10 ]
    });

private static readonly Histogram<double> getCartHistogram = CartMeter.CreateHistogram(
    "app.cart.get_cart.latency",
    unit: "s",
    advice: new InstrumentAdvice<double>
    {
        HistogramBucketBoundaries = [ 0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1, 2.5, 5, 7.5, 10 ]
    });
```

**Metrics Details:**
- **Name:** `app.cart.add_item.latency` and `app.cart.get_cart.latency`
- **Type:** Histogram
- **Unit:** Seconds
- **Buckets:** Custom boundaries optimized for sub-second operations
- **Purpose:** Track performance characteristics of cart operations

---

## Role in the Ecosystem

### Upstream Dependencies
- **Frontend Service** - Primary caller for cart operations
- **Checkout Service** - Retrieves cart for order processing
- **Valkey (Redis)** - Persistent cart storage
- **Flagd Service** - Feature flag evaluation
- **OpenTelemetry Collector** - Telemetry aggregation

### Downstream Consumers
- None (leaf service - stores data only)

### Service Interactions

```
┌──────────────┐
│   Frontend   │
│   Service    │
└──────────────┘
       │
       │ gRPC: AddItem, GetCart, EmptyCart
       │ (with trace context + baggage)
       ▼
┌──────────────┐         ┌──────────────┐
│Cart Service  │────────▶│    Flagd     │
│              │ gRPC    │  (Feature    │
│              │ GetBool │   Flags)     │
└──────────────┘         └──────────────┘
       │
       │ Redis Protocol
       ▼
┌──────────────┐
│    Valkey    │
│   (Redis)    │
└──────────────┘
       │
       │ OTLP (Traces, Metrics, Logs)
       ▼
┌──────────────┐
│     OTel     │
│  Collector   │
└──────────────┘
       │
       ▼
┌──────────────┐         ┌──────────────┐
│    Jaeger    │         │  Prometheus  │
│   (Traces)   │         │   (Metrics)  │
└──────────────┘         └──────────────┘
```

---

## High-Level API/Interface

### gRPC Service Definition
**Proto File:** `pb/demo.proto`

```protobuf
service CartService {
    rpc AddItem(AddItemRequest) returns (Empty) {}
    rpc GetCart(GetCartRequest) returns (Cart) {}
    rpc EmptyCart(EmptyCartRequest) returns (Empty) {}
}

message CartItem {
    string product_id = 1;
    int32 quantity = 2;
}

message Cart {
    string user_id = 1;
    repeated CartItem items = 2;
}

message AddItemRequest {
    string user_id = 1;
    CartItem item = 2;
}

message GetCartRequest {
    string user_id = 1;
}

message EmptyCartRequest {
    string user_id = 1;
}
```

### Incoming Calls

**Port:** 8080 (HTTP/2 for gRPC)

**Methods:**
1. `AddItem(userId, item)` → Empty
2. `GetCart(userId)` → Cart
3. `EmptyCart(userId)` → Empty

**Example Usage:**
```json
// AddItem Request
{
  "user_id": "user-123",
  "item": {
    "product_id": "OLJCESPC7Z",
    "quantity": 1
  }
}

// GetCart Response
{
  "user_id": "user-123",
  "items": [
    {
      "product_id": "OLJCESPC7Z",
      "quantity": 1
    },
    {
      "product_id": "66VCHSJNUP",
      "quantity": 2
    }
  ]
}
```

### Outgoing Calls

**Valkey (Redis):**
- Protocol: Redis RESP
- Commands: HGET, HSET, EXPIRE, PING
- Connection: StackExchange.Redis client

**Flagd:**
- Protocol: gRPC
- Method: `GetBooleanValue(flag, default, context)`
- Feature Flags: `cartFailure`

---

## OpenTelemetry Implementation Details

### Auto-Instrumentation Setup

#### 1. SDK Initialization
**File:** `Program.cs:30-80`

```csharp
// Logging with OTLP export
builder.Logging
    .AddOpenTelemetry(options => options.AddOtlpExporter())
    .AddConsole();

// Resource configuration
Action<ResourceBuilder> appResourceBuilder =
    resource => resource
        .AddService(builder.Environment.ApplicationName)
        .AddContainerDetector()
        .AddHostDetector();

// Tracing and Metrics
builder.Services.AddOpenTelemetry()
    .ConfigureResource(appResourceBuilder)
    .WithTracing(tracerBuilder => tracerBuilder
        .AddSource("OpenTelemetry.Demo.Cart")
        .AddRedisInstrumentation(
            options => options.SetVerboseDatabaseStatements = true)
        .AddAspNetCoreInstrumentation()
        .AddGrpcClientInstrumentation()
        .AddHttpClientInstrumentation()
        .AddOtlpExporter())
    .WithMetrics(meterBuilder => meterBuilder
        .AddMeter("OpenTelemetry.Demo.Cart")
        .AddMeter("OpenFeature")
        .AddProcessInstrumentation()
        .AddRuntimeInstrumentation()
        .AddAspNetCoreInstrumentation()
        .SetExemplarFilter(ExemplarFilterType.TraceBased)
        .AddOtlpExporter());
```

**Key Components:**

1. **Resource Builders:**
   - `AddService()` - Sets service name from application name
   - `AddContainerDetector()` - Adds container metadata (ID, image)
   - `AddHostDetector()` - Adds host metadata (hostname, OS)

2. **Tracing Sources:**
   - `OpenTelemetry.Demo.Cart` - Custom ActivitySource
   - Redis instrumentation with verbose database statements
   - ASP.NET Core (HTTP/gRPC server)
   - gRPC client
   - HTTP client

3. **Metrics Sources:**
   - `OpenTelemetry.Demo.Cart` - Custom Meter
   - `OpenFeature` - Feature flag metrics
   - Process metrics (CPU, memory)
   - .NET runtime metrics (GC, threads, exceptions)
   - ASP.NET Core metrics (request duration, active requests)

#### 2. Instrumentation Packages
**File:** `cart.csproj:17-35`

```xml
<PackageReference Include="OpenTelemetry.Exporter.OpenTelemetryProtocol" Version="1.11.2" />
<PackageReference Include="OpenTelemetry.Extensions.Hosting" Version="1.11.2" />
<PackageReference Include="OpenTelemetry.Instrumentation.AspNetCore" Version="1.11.1" />
<PackageReference Include="OpenTelemetry.Instrumentation.GrpcNetClient" Version="1.11.0-beta.2" />
<PackageReference Include="OpenTelemetry.Instrumentation.Http" Version="1.11.1" />
<PackageReference Include="OpenTelemetry.Instrumentation.Process" Version="1.11.0-beta.2" />
<PackageReference Include="OpenTelemetry.Instrumentation.StackExchangeRedis" Version="1.11.0-beta.2" />
<PackageReference Include="OpenTelemetry.Instrumentation.Runtime" Version="1.11.1" />
<PackageReference Include="OpenTelemetry.Resources.Container" Version="1.11.0-beta.2" />
<PackageReference Include="OpenTelemetry.Resources.Host" Version="1.11.0-beta.2" />
```

**Package Details:**

| Package | Version | Purpose |
|---------|---------|---------|
| `OpenTelemetry.Exporter.OpenTelemetryProtocol` | 1.11.2 | OTLP exporter for traces, metrics, logs |
| `OpenTelemetry.Instrumentation.AspNetCore` | 1.11.1 | Auto-instruments ASP.NET Core HTTP/gRPC |
| `OpenTelemetry.Instrumentation.StackExchangeRedis` | 1.11.0-beta.2 | Auto-instruments Redis operations |
| `OpenTelemetry.Instrumentation.GrpcNetClient` | 1.11.0-beta.2 | Auto-instruments gRPC client calls |
| `OpenTelemetry.Instrumentation.Http` | 1.11.1 | Auto-instruments HttpClient |
| `OpenTelemetry.Instrumentation.Process` | 1.11.0-beta.2 | Collects process metrics |
| `OpenTelemetry.Instrumentation.Runtime` | 1.11.1 | Collects .NET runtime metrics |
| `OpenTelemetry.Resources.Container` | 1.11.0-beta.2 | Detects container metadata |
| `OpenTelemetry.Resources.Host` | 1.11.0-beta.2 | Detects host metadata |

#### 3. Redis Instrumentation Configuration
**File:** `Program.cs:67-68, 87-88`

```csharp
// Enable verbose database statements
.AddRedisInstrumentation(
    options => options.SetVerboseDatabaseStatements = true)

// Register Redis connection for instrumentation
var ValkeyCartStore = (ValkeyCartStore)app.Services.GetRequiredService<ICartStore>();
app.Services.GetRequiredService<StackExchangeRedisInstrumentation>()
    .AddConnection(ValkeyCartStore.GetConnection());
```

**What gets captured:**
- Redis command: `HGET`, `HSET`, `EXPIRE`, `PING`
- Full command text (e.g., `HSET user-123 cart ...`)
- Connection endpoint
- Database index
- Operation duration
- Errors and exceptions

#### 4. Environment Variables
**File:** `docker-compose.yml` (cart service section)

```yaml
environment:
  - FLAGD_HOST=flagd
  - FLAGD_PORT=8013
  - VALKEY_ADDR=valkey-cart:6379
  - OTEL_SERVICE_NAME=cart
  - OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
  - OTEL_RESOURCE_ATTRIBUTES=service.namespace=opentelemetry-demo,service.version=2.1.3
  - OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=cumulative
```

| Variable | Value | Purpose |
|----------|-------|---------|
| `OTEL_SERVICE_NAME` | `cart` | Service identifier in all telemetry |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://otel-collector:4317` | OTLP gRPC endpoint |
| `OTEL_RESOURCE_ATTRIBUTES` | `service.namespace=opentelemetry-demo,service.version=...` | Additional resource attributes |
| `OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE` | `cumulative` | Cumulative metrics aggregation |

### Manual Instrumentation

#### 1. Custom ActivitySource and Meter
**File:** `cartstore/ValkeyCartStore.cs:28-36`

```csharp
private static readonly ActivitySource CartActivitySource = new("OpenTelemetry.Demo.Cart");
private static readonly Meter CartMeter = new Meter("OpenTelemetry.Demo.Cart");
```

**Important:** The names must match the sources registered in `Program.cs`:
- `AddSource("OpenTelemetry.Demo.Cart")`
- `AddMeter("OpenTelemetry.Demo.Cart")`

#### 2. Span Attributes
**File:** `services/CartService.cs`

```csharp
// AddItem method
var activity = Activity.Current;
activity?.SetTag("app.user.id", request.UserId);
activity?.SetTag("app.product.id", request.Item.ProductId);
activity?.SetTag("app.product.quantity", request.Item.Quantity);

// GetCart method
activity?.SetTag("app.user.id", request.UserId);
activity?.AddEvent(new("Fetch cart"));
activity?.SetTag("app.cart.items.count", totalCart);
```

**Custom Attributes:**
| Attribute | Type | Description |
|-----------|------|-------------|
| `app.user.id` | string | User identifier |
| `app.product.id` | string | Product identifier (AddItem only) |
| `app.product.quantity` | int | Quantity added (AddItem only) |
| `app.cart.items.count` | int | Total items in cart (GetCart only) |

#### 3. Span Events
**File:** `services/CartService.cs:54`

```csharp
activity?.AddEvent(new("Fetch cart"));
```

**Events** are timestamped log entries attached to spans, useful for:
- Marking important points in span lifecycle
- Adding contextual information
- Debugging specific operations

#### 4. Error Handling
**File:** `services/CartService.cs:42-45, 68-71`

```csharp
catch (RpcException ex)
{
    activity?.AddException(ex);
    activity?.SetStatus(ActivityStatusCode.Error, ex.Message);
    throw;
}
```

**Error Instrumentation:**
- `AddException()` - Records exception details as span event
- `SetStatus(Error)` - Marks span as failed
- Exception message included for debugging

#### 5. Custom Histograms with Bucket Boundaries
**File:** `cartstore/ValkeyCartStore.cs:29-44`

```csharp
private static readonly Histogram<double> addItemHistogram = CartMeter.CreateHistogram(
    "app.cart.add_item.latency",
    unit: "s",
    advice: new InstrumentAdvice<double>
    {
        HistogramBucketBoundaries = [ 0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1, 2.5, 5, 7.5, 10 ]
    });
```

**Recording:**
```csharp
var stopwatch = Stopwatch.StartNew();
try
{
    // Operation
}
finally
{
    addItemHistogram.Record(stopwatch.Elapsed.TotalSeconds);
}
```

**Bucket Design:**
- Fine-grained buckets for fast operations (5ms - 100ms)
- Coarser buckets for slower operations (100ms - 10s)
- Optimized for percentile calculation (p50, p95, p99)

### OpenFeature Integration

#### Configuration
**File:** `Program.cs:41-46`

```csharp
builder.Services.AddOpenFeature(openFeatureBuilder =>
{
    openFeatureBuilder
        .AddProvider(_ => new FlagdProvider())
        .AddHook<MetricsHook>()
        .AddHook<TraceEnricherHook>();
});
```

**Hooks:**
- `MetricsHook` - Automatically emits metrics for flag evaluations
  - `feature_flag.evaluation.total`
  - `feature_flag.evaluation.duration`
- `TraceEnricherHook` - Adds span attributes for flag evaluations
  - `feature_flag.key`
  - `feature_flag.value`
  - `feature_flag.variant`

#### Feature Flag Usage
**File:** `services/CartService.cs:83-90`

```csharp
await _featureClient.GetBooleanValueAsync("cartFailure", defaultValue: false)
    .ContinueWith(task =>
    {
        if (task.Result)
        {
            _badCartStore.EmptyCartAsync(request.UserId);
        }
        else
        {
            _cartStore.EmptyCartAsync(request.UserId);
        }
    });
```

**Observability Benefits:**
- Feature flag evaluations create child spans
- Flag values visible in distributed traces
- Metrics track flag evaluation patterns
- A/B testing and rollout impacts observable

### Telemetry Data Flow

```
┌─────────────────────────────────────────────────────────────┐
│ Cart Service Container (ASP.NET Core)                       │
│                                                              │
│ ┌────────────────────────────────────────────────────────┐  │
│ │ Kestrel Server (HTTP/2)                                │  │
│ │         ↓                                              │  │
│ │ ASP.NET Core Middleware                                │  │
│ │   [Auto: HTTP request span created]                    │  │
│ │         ↓                                              │  │
│ │ gRPC Service Handler                                   │  │
│ │   [Auto: gRPC server span created]                     │  │
│ │         ↓                                              │  │
│ │ ┌──────────────────────────────────────────────────┐   │  │
│ │ │ CartService.AddItem()                            │   │  │
│ │ │                                                   │   │  │
│ │ │ 1. Access current span                           │   │  │
│ │ │    Activity.Current                              │   │  │
│ │ │                                                   │   │  │
│ │ │ 2. Add custom attributes                         │   │  │
│ │ │    SetTag("app.user.id", userId)                 │   │  │
│ │ │    SetTag("app.product.id", productId)           │   │  │
│ │ │    SetTag("app.product.quantity", quantity)      │   │  │
│ │ │                                                   │   │  │
│ │ │ 3. Call ValkeyCartStore ──────────────────────┐  │   │  │
│ │ │    [Creates child operation context]          │  │   │  │
│ │ │                                               │  │   │  │
│ │ │    ValkeyCartStore.AddItemAsync() ────────┐   │  │   │  │
│ │ │      Start Stopwatch                     │   │  │   │  │
│ │ │      HGET user-123 cart ◄────────────────┼───┼──┼───┼──┤
│ │ │      [Auto: Redis span created]          │   │  │   │  │
│ │ │      Parse protobuf                      │   │  │   │  │
│ │ │      Update cart                         │   │  │   │  │
│ │ │      HSET user-123 cart ... ◄────────────┼───┼──┼───┼──┤
│ │ │      [Auto: Redis span created]          │   │  │   │  │
│ │ │      EXPIRE user-123 3600 ◄──────────────┼───┼──┼───┼──┤
│ │ │      [Auto: Redis span created]          │   │  │   │  │
│ │ │      Record histogram ────────────────────┘   │  │   │  │
│ │ │        app.cart.add_item.latency              │  │   │  │
│ │ │                                               │  │   │  │
│ │ │ 4. Handle errors (if any)                     │  │   │  │
│ │ │    AddException(ex)                           │  │   │  │
│ │ │    SetStatus(Error)                           │  │   │  │
│ │ │                                               │  │   │  │
│ │ │ 5. Return response ◄──────────────────────────┘  │   │  │
│ │ └──────────────────────────────────────────────────┘   │  │
│ │                                                         │  │
│ │ [Export via OTLP/gRPC to otel-collector:4317]          │  │
│ │   • Traces (spans with attributes)                     │  │
│ │   • Metrics (histograms, counters, gauges)             │  │
│ │   • Logs (with trace correlation)                      │  │
│ └────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
                         ↓
              Traces, Metrics, Logs
                         ↓
            ┌──────────────────────┐
            │ OpenTelemetry        │
            │ Collector            │
            │ (otel-collector:4317)│
            └──────────────────────┘
                         ↓
         ┌───────────────┴───────────────┐
         ↓                               ↓
  ┌─────────────┐                ┌─────────────┐
  │   Jaeger    │                │  Prometheus │
  │  (Traces)   │                │  (Metrics)  │
  └─────────────┘                └─────────────┘
```

### Example Distributed Trace

```
frontend: Render product page
  └─ grpc.client: cart.GetCart(user-123)
      │ [W3C trace context propagated via gRPC metadata]
      │
      └─ grpc.server: cart.GetCart() [Cart Service]
          │ span attributes:
          │   - app.user.id: user-123
          │   - rpc.system: grpc
          │   - rpc.service: CartService
          │   - rpc.method: GetCart
          │
          ├─ event: "Fetch cart"
          │
          └─ redis.command: HGET user-123 cart
              │ span attributes:
              │   - db.system: redis
              │   - db.operation: HGET
              │   - db.statement: HGET user-123 cart
              │   - net.peer.name: valkey-cart
              │   - net.peer.port: 6379
              │
              └─ span attributes (after):
                  - app.cart.items.count: 3
                  - rpc.grpc.status_code: 0
```

### Observability Outputs

#### Traces
**Exporter:** OTLP/gRPC
**Endpoint:** `http://otel-collector:4317/v1/traces`
**Backend:** Jaeger (via OTel Collector)

**Span Types:**
1. **Auto-instrumented:**
   - HTTP request spans (ASP.NET Core)
   - gRPC server spans
   - Redis command spans (HGET, HSET, EXPIRE)
   - gRPC client spans (to flagd)
2. **Enriched with manual attributes:**
   - User ID, product ID, quantity
   - Cart item counts
   - Business events

#### Metrics
**Exporter:** OTLP/gRPC
**Endpoint:** `http://otel-collector:4317/v1/metrics`
**Backend:** Prometheus (via OTel Collector)

**Custom Metrics:**
- `app.cart.add_item.latency` - Histogram
- `app.cart.get_cart.latency` - Histogram

**Auto-collected Metrics:**
- `process.runtime.dotnet.gc.collections.count`
- `process.runtime.dotnet.gc.heap.size`
- `process.runtime.dotnet.thread_pool.threads.count`
- `process.runtime.dotnet.monitor.lock_contentions.count`
- `aspnetcore.http.server.request.duration`
- `feature_flag.evaluation.total` (via OpenFeature hook)

#### Logs
**Exporter:** OTLP/gRPC
**Endpoint:** `http://otel-collector:4317/v1/logs`
**Format:** Structured JSON with trace correlation

**Log Features:**
- Automatic trace context correlation
- Log levels: Trace, Debug, Information, Warning, Error, Critical
- Structured data with scope and event IDs

---

## Configuration Reference

### Service Configuration
```bash
# Service port
CART_PORT=8080

# Dependencies
VALKEY_ADDR=valkey-cart:6379
FLAGD_HOST=flagd
FLAGD_PORT=8013

# OpenTelemetry
OTEL_SERVICE_NAME=cart
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
OTEL_RESOURCE_ATTRIBUTES=service.namespace=opentelemetry-demo,service.version=2.1.3
OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=cumulative
```

### Redis Configuration
**File:** `cartstore/ValkeyCartStore.cs:71-96`

```csharp
var options = ConfigurationOptions.Parse(valkeyAddress);
options.ConnectRetry = 30;
options.ConnectTimeout = 1000;
options.ReconnectRetryPolicy = new ExponentialRetry(1000);
options.KeepAlive = 180;
```

**Connection Settings:**
- Retry attempts: 30
- Connect timeout: 1000ms
- Reconnect: Exponential backoff starting at 1000ms
- Keep-alive: 180 seconds

---

## Key Files Reference

| File | Purpose |
|------|---------|
| `src/Program.cs` | Application entry, OTel configuration |
| `src/services/CartService.cs` | gRPC service implementation with manual instrumentation |
| `src/cartstore/ValkeyCartStore.cs` | Redis operations with custom metrics |
| `src/cartstore/ICartStore.cs` | Cart store interface |
| `src/cart.csproj` | NuGet dependencies and build config |
| `src/Dockerfile` | Container image definition |
| `README.md` | Build and development instructions |

---

## Dependencies

### NuGet Packages
```xml
<PackageReference Include="Grpc.AspNetCore" Version="2.71.0" />
<PackageReference Include="Grpc.AspNetCore.Server.Reflection" Version="2.71.0" />
<PackageReference Include="Grpc.Tools" Version="2.72.0" />
<PackageReference Include="StackExchange.Redis" Version="2.9.3" />
<PackageReference Include="OpenFeature" Version="2.0.0" />
<PackageReference Include="OpenFeature.Contrib.Providers.Flagd" Version="0.5.3" />
<PackageReference Include="OpenTelemetry.Exporter.OpenTelemetryProtocol" Version="1.11.2" />
<PackageReference Include="OpenTelemetry.Extensions.Hosting" Version="1.11.2" />
<PackageReference Include="OpenTelemetry.Instrumentation.*" Version="1.11.x" />
```

### External Services
- **Valkey (Redis)** - Cart persistence (required)
- **Flagd** - Feature flags (optional)
- **OpenTelemetry Collector** - Telemetry aggregation (required)

---

## Build and Development

### Local Build
```bash
# Build
dotnet restore
dotnet build

# Run locally
export VALKEY_ADDR=localhost:6379
export FLAGD_HOST=localhost
export FLAGD_PORT=8013
dotnet run
```

### Docker Build
```bash
# From repository root
docker compose build cart
docker compose up cart
```

### Testing
```bash
# Unit tests
dotnet test src/cart/tests/

# gRPC testing with grpcurl
grpcurl -plaintext -d '{"user_id":"test"}' localhost:8080 oteldemo.CartService/GetCart
```

---

## Observability Best Practices Demonstrated

1. **Comprehensive Auto-Instrumentation** - Covers HTTP, gRPC, Redis, and runtime
2. **Strategic Manual Enrichment** - Adds business context to auto-instrumented spans
3. **Custom Metrics** - Tracks domain-specific performance (cart operations)
4. **Error Recording** - Properly captures and reports exceptions
5. **Resource Detection** - Automatically captures container and host metadata
6. **Feature Flag Observability** - Integrates OpenFeature with tracing and metrics
7. **Verbose Database Statements** - Captures full Redis commands for debugging
8. **Histogram Bucket Optimization** - Custom buckets for accurate percentile calculation
9. **Span Events** - Marks important operations within spans
10. **Exemplar Support** - Links metrics to traces for deeper investigation

This service exemplifies production-ready OpenTelemetry instrumentation in .NET applications with a focus on both breadth (many auto-instrumentations) and depth (rich manual instrumentation).
