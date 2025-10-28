# Accounting Service - Architecture Documentation

## Overview

The Accounting Service is a background worker service that consumes order completion messages from Kafka and persists them to a PostgreSQL database for accounting and record-keeping purposes.

**Language:** C# / .NET 8.0
**Service Type:** Console Application / Background Worker
**Primary Function:** Kafka Consumer → Database Persistence

---

## Key Functionality

### 1. Order Message Consumption
The service continuously listens to the `orders` Kafka topic for `OrderResult` messages containing:
- Order ID and shipping tracking ID
- Order items with product details and pricing
- Shipping address and cost information

**Key File:** `Consumer.cs:49-69`

```csharp
while (_listening)
{
    using var activity = MyActivitySource.StartActivity("order-consumed", ActivityKind.Internal);
    var consumeResult = _consumer.Consume();
    ProcessMessage(consumeResult.Message);
}
```

### 2. Data Persistence
Messages are deserialized from Protocol Buffer format and persisted to PostgreSQL using Entity Framework Core into three tables:
- **OrderEntity** - Order headers
- **OrderItemEntity** - Line items
- **ShippingEntity** - Shipping details

**Key File:** `Consumer.cs:71-108`

### 3. Structured Logging
Orders are logged with full structured data using source-generated logging for performance.

**Key File:** `Log.cs:8-11`

```csharp
[LoggerMessage(Level = LogLevel.Information, Message = "Order details: {@OrderResult}.")]
public static partial void OrderReceivedMessage(ILogger logger, OrderResult orderResult);
```

---

## Role in the Ecosystem

### Upstream Dependencies
- **Kafka Broker** (`orders` topic) - Receives `OrderResult` messages published by the Checkout service
- **PostgreSQL Database** - Stores persisted order records

### Downstream Consumers
- None (terminal service - data is stored for reporting/analytics)

### Service Interactions

```
┌─────────────┐         OrderResult          ┌──────────────┐
│  Checkout   │────────Message (Protobuf)────▶│    Kafka     │
│  Service    │                               │    Broker    │
└─────────────┘                               └──────────────┘
                                                      │
                                                      │ Consume
                                                      ▼
                                              ┌──────────────┐
                                              │  Accounting  │
                                              │   Service    │
                                              └──────────────┘
                                                      │
                                                      │ Persist
                                                      ▼
                                              ┌──────────────┐
                                              │  PostgreSQL  │
                                              │   Database   │
                                              └──────────────┘
```

---

## High-Level API/Interface

### Incoming Calls
**Type:** Kafka Consumer
**Topic:** `orders`
**Message Format:** Protocol Buffer (`OrderResult` from `pb/demo.proto`)
**Consumer Group:** `accounting`

**Message Schema:**
```protobuf
message OrderResult {
  string order_id = 1;
  string shipping_tracking_id = 2;
  Money shipping_cost = 3;
  Address shipping_address = 4;
  repeated OrderItem items = 5;
}
```

### Outgoing Calls
**Type:** Database Operations
**Target:** PostgreSQL (`Host=postgresql; Database=otel`)
**Operations:** INSERT statements via Entity Framework Core

---

## OpenTelemetry Implementation Details

### Auto-Instrumentation Setup

#### 1. Package Configuration
**File:** `Accounting.csproj:13`

```xml
<PackageReference Include="OpenTelemetry.AutoInstrumentation" Version="1.12.0" />
```

The `OpenTelemetry.AutoInstrumentation` package provides automatic instrumentation for:
- .NET Runtime metrics
- Kafka consumer operations
- Entity Framework Core database operations
- HTTP client calls (if present)

#### 2. Docker Configuration
**File:** `Dockerfile:28-31`

```dockerfile
ENV OTEL_DOTNET_AUTO_TRACES_ADDITIONAL_SOURCES=Accounting.Consumer
RUN mkdir -p "/var/log/opentelemetry/dotnet"
RUN chown app "/var/log/opentelemetry/dotnet"
ENTRYPOINT ["./instrument.sh", "dotnet", "Accounting.dll"]
```

**How it works:**
- `instrument.sh` is provided by the auto-instrumentation package
- It loads the OpenTelemetry .NET profiler before the application starts
- The profiler intercepts framework calls to automatically create spans
- Logs are written to `/var/log/opentelemetry/dotnet`

#### 3. Environment Variables
**File:** `docker-compose.yml` (accounting service section)

| Variable | Value | Purpose |
|----------|-------|---------|
| `OTEL_SERVICE_NAME` | `accounting` | Service identifier in all telemetry |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://otel-collector:4318` | OTLP HTTP endpoint for traces, metrics, logs |
| `OTEL_RESOURCE_ATTRIBUTES` | `service.namespace=opentelemetry-demo,service.version=...` | Additional resource attributes |
| `OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE` | `cumulative` | Metrics aggregation strategy |
| `OTEL_DOTNET_AUTO_TRACES_ADDITIONAL_SOURCES` | `Accounting.Consumer` | Enable custom ActivitySource |
| `OTEL_DOTNET_AUTO_TRACES_ENTITYFRAMEWORKCORE_INSTRUMENTATION_ENABLED` | `false` | Disable EF Core auto-instrumentation to reduce overhead |

### Manual Instrumentation

#### 1. Custom Span Creation
**File:** `Consumer.cs:35,61-62`

```csharp
// Define custom ActivitySource
private static readonly ActivitySource MyActivitySource = new("Accounting.Consumer");

// Create custom span for each message consumption
using var activity = MyActivitySource.StartActivity("order-consumed", ActivityKind.Internal);
var consumeResult = _consumer.Consume();
ProcessMessage(consumeResult.Message);
```

**Details:**
- **ActivitySource Name:** `"Accounting.Consumer"` (must match `OTEL_DOTNET_AUTO_TRACES_ADDITIONAL_SOURCES`)
- **Span Name:** `"order-consumed"`
- **Span Kind:** `Internal` (represents internal processing, not RPC)
- **Lifecycle:** Span automatically closed when `using` block exits
- **Context Propagation:** Automatically links to parent span from Kafka message headers

#### 2. Structured Logging Integration
**File:** `Log.cs:8-11`

```csharp
[LoggerMessage(
    Level = LogLevel.Information,
    Message = "Order details: {@OrderResult}.")]
public static partial void OrderReceivedMessage(ILogger logger, OrderResult orderResult);
```

**Usage in code:** `Consumer.cs:103`
```csharp
Log.OrderReceivedMessage(_logger, order);
```

**OpenTelemetry Integration:**
- Logs are automatically correlated with active spans
- The `{@OrderResult}` syntax serializes the entire object as structured data
- Source generator creates optimized logging code at compile-time
- When exported via OTLP, logs include `trace_id` and `span_id` for correlation

### Automatic Instrumentation Points

#### 1. Kafka Consumer Operations
**Auto-instrumented by:** OpenTelemetry .NET Auto-Instrumentation
**Library:** Confluent.Kafka client
**Location:** `Consumer.cs:62`

```csharp
var consumeResult = _consumer.Consume();
```

**Automatically captures:**
- Span name: `kafka.consume` or similar
- Span kind: `Consumer`
- Attributes:
  - `messaging.system`: `kafka`
  - `messaging.destination`: `orders`
  - `messaging.operation`: `receive`
  - `messaging.message.id`: message key/offset
  - `messaging.kafka.partition`: partition number
- Trace context: Extracted from message headers (W3C Trace Context)

#### 2. Entity Framework Core Operations (Disabled)
**File:** `docker-compose.yml`

```yaml
OTEL_DOTNET_AUTO_TRACES_ENTITYFRAMEWORKCORE_INSTRUMENTATION_ENABLED=false
```

While EF Core auto-instrumentation is available, it's explicitly **disabled** in this service to reduce telemetry overhead for high-volume database operations. The manual span `order-consumed` provides sufficient observability for the entire operation.

**If enabled, would capture:**
- SQL statements executed
- Database connection info
- Query execution time
- Parameters (if configured)

#### 3. Runtime Metrics
**Auto-instrumented by:** OpenTelemetry .NET Auto-Instrumentation

**Automatically exported metrics:**
- `process.runtime.dotnet.gc.collections.count` - GC collection counts by generation
- `process.runtime.dotnet.gc.heap.size` - Heap memory by generation
- `process.runtime.dotnet.gc.objects.size` - Object sizes
- `process.runtime.dotnet.thread_pool.threads.count` - Thread pool metrics
- `process.runtime.dotnet.monitor.lock_contentions.count` - Lock contention
- `process.runtime.dotnet.exceptions.count` - Exception counts

### Telemetry Data Flow

```
┌─────────────────────────────────────────────────────────┐
│ Accounting Service Container                            │
│                                                          │
│ ┌────────────────────────────────────────────────────┐  │
│ │ instrument.sh (OTel .NET Auto-Instrumentation)    │  │
│ │         ↓                                          │  │
│ │ Application Startup                                │  │
│ │         ↓                                          │  │
│ │ ┌─────────────────────────────────────────────┐   │  │
│ │ │ Consumer Loop                               │   │  │
│ │ │                                             │   │  │
│ │ │ 1. Receive Kafka Message ─────────────────┐│   │  │
│ │ │    [Auto: Extract trace context from     ││   │  │
│ │ │     message headers]                     ││   │  │
│ │ │                                           ││   │  │
│ │ │ 2. Start Custom Span ─────────────────┐  ││   │  │
│ │ │    MyActivitySource.StartActivity()   │  ││   │  │
│ │ │    Name: "order-consumed"             │  ││   │  │
│ │ │    Kind: Internal                     │  ││   │  │
│ │ │    [Manual Instrumentation]           │  ││   │  │
│ │ │                                       │  ││   │  │
│ │ │ 3. Kafka Consume() ───────────────┐   │  ││   │  │
│ │ │    [Auto: Create consumer span]  │   │  ││   │  │
│ │ │    Attributes: topic, partition  │   │  ││   │  │
│ │ │                                  │   │  ││   │  │
│ │ │ 4. Parse Protobuf                │   │  ││   │  │
│ │ │                                  │   │  ││   │  │
│ │ │ 5. Log Order Details ────────────┐   │  ││   │  │
│ │ │    [Auto: Correlate with span]  │   │  ││   │  │
│ │ │    Include: trace_id, span_id   │   │  ││   │  │
│ │ │                                  │   │  ││   │  │
│ │ │ 6. EF Core SaveChanges()         │   │  ││   │  │
│ │ │    [Instrumentation disabled]    │   │  ││   │  │
│ │ │                                  │   │  ││   │  │
│ │ │ 7. Close Custom Span ────────────┘   │  ││   │  │
│ │ │                                      │  ││   │  │
│ │ │ 8. Close Consumer Span ──────────────┘  ││   │  │
│ │ │                                          ││   │  │
│ │ │ 9. Export to OTLP Endpoint ─────────────┘│   │  │
│ │ └─────────────────────────────────────────────┘   │  │
│ └────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
                         ↓
              Traces, Metrics, Logs
                         ↓
            ┌──────────────────────┐
            │ OpenTelemetry        │
            │ Collector            │
            │ (otel-collector:4318)│
            └──────────────────────┘
                         ↓
         ┌───────────────┴───────────────┐
         ↓                               ↓
  ┌─────────────┐                ┌─────────────┐
  │   Jaeger    │                │  Prometheus │
  │  (Traces)   │                │  (Metrics)  │
  └─────────────┘                └─────────────┘
```

### Trace Context Propagation

1. **Checkout Service** creates order and publishes to Kafka with trace context in message headers (W3C Trace Context format)
2. **Kafka message** carries:
   - `traceparent` header: `00-{trace_id}-{parent_span_id}-{flags}`
   - `tracestate` header: vendor-specific data
3. **Auto-instrumentation** extracts context from headers when consuming message
4. **Custom span** `order-consumed` becomes child of the extracted parent span
5. **Complete trace** shows the full journey: Frontend → Checkout → Kafka → Accounting → Database

### Observability Outputs

#### Traces
**Exporter:** OTLP/HTTP
**Endpoint:** `http://otel-collector:4318/v1/traces`
**Backend:** Jaeger (via OTel Collector)

**Example Trace Structure:**
```
checkout-service: POST /api/checkout
  ├─ kafka.send (topic: orders)
  │   └─ [Message published]
  │
  └─ [Kafka broker propagates context]
      │
      └─ accounting: order-consumed (span)
          └─ kafka.consume (auto-instrumented)
```

#### Metrics
**Exporter:** OTLP/HTTP
**Endpoint:** `http://otel-collector:4318/v1/metrics`
**Backend:** Prometheus (via OTel Collector)

**Exported Metrics:**
- Runtime metrics (GC, memory, threads)
- Process metrics (CPU, memory usage)
- Custom metrics (if added via Meter API)

#### Logs
**Exporter:** OTLP/HTTP (if configured)
**Endpoint:** `http://otel-collector:4318/v1/logs`
**Format:** Structured JSON with trace correlation

**Log Entry Example:**
```json
{
  "timestamp": "2024-01-15T10:30:45.123Z",
  "level": "Information",
  "message": "Order details: {@OrderResult}.",
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
  "span_id": "00f067aa0ba902b7",
  "service.name": "accounting",
  "OrderResult": {
    "order_id": "abc-123",
    "shipping_tracking_id": "ship-456",
    ...
  }
}
```

---

## Configuration Reference

### Kafka Configuration
**File:** `Consumer.cs:42-47`

```csharp
var config = new ConsumerConfig
{
    BootstrapServers = Environment.GetEnvironmentVariable("KAFKA_ADDR"),
    GroupId = "accounting",
    AutoOffsetReset = AutoOffsetReset.Earliest,
    EnableAutoCommit = true
};
```

### Database Configuration
**File:** `Entities.cs:13-16`

```csharp
protected override void OnConfiguring(DbContextOptionsBuilder options)
{
    options.UseNpgsql(Environment.GetEnvironmentVariable("DB_CONNECTION_STRING"));
}
```

**Connection String:** `Host=postgresql;Username=otelu;Password=otelp;Database=otel`

### Environment Variables
```bash
# Kafka
KAFKA_ADDR=kafka:9092

# Database
DB_CONNECTION_STRING=Host=postgresql;Username=otelu;Password=otelp;Database=otel

# OpenTelemetry
OTEL_SERVICE_NAME=accounting
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318
OTEL_RESOURCE_ATTRIBUTES=service.namespace=opentelemetry-demo,service.version=1.0.0
OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=cumulative
OTEL_DOTNET_AUTO_TRACES_ADDITIONAL_SOURCES=Accounting.Consumer
OTEL_DOTNET_AUTO_TRACES_ENTITYFRAMEWORKCORE_INSTRUMENTATION_ENABLED=false
```

---

## Key Files Reference

| File | Purpose |
|------|---------|
| `Program.cs` | Application entry point, host configuration |
| `Consumer.cs` | Kafka consumer logic, manual span creation |
| `Entities.cs` | EF Core entities and DbContext |
| `Log.cs` | Structured logging with source generators |
| `Helpers.cs` | Environment variable utilities |
| `Accounting.csproj` | NuGet dependencies, build configuration |
| `Dockerfile` | Container image with OTel auto-instrumentation |
| `README.md` | Build and development instructions |

---

## Dependencies

### NuGet Packages
```xml
<PackageReference Include="Confluent.Kafka" Version="2.11.0" />
<PackageReference Include="Google.Protobuf" Version="3.29.3" />
<PackageReference Include="Grpc.Tools" Version="2.72.0" />
<PackageReference Include="Microsoft.EntityFrameworkCore" Version="9.0.4" />
<PackageReference Include="Npgsql.EntityFrameworkCore.PostgreSQL" Version="9.0.4" />
<PackageReference Include="OpenTelemetry.AutoInstrumentation" Version="1.12.0" />
```

### External Services
- **Kafka Broker** - Message queue (required)
- **PostgreSQL** - Database (required)
- **OpenTelemetry Collector** - Telemetry aggregation (required)

---

## Build and Development

### Local Build
```bash
# Generate protobuf code
make generate-protobuf

# Build application
dotnet build

# Run locally
dotnet run
```

### Docker Build
```bash
# From repository root
docker compose build accounting

# Run service
docker compose up accounting
```

### Dependency Updates
```powershell
# Update all NuGet packages
Update-Package -ProjectName Accounting
```

---

## Observability Best Practices Demonstrated

1. **Minimal Manual Instrumentation** - Relies on auto-instrumentation for framework operations
2. **Strategic Custom Spans** - Only adds manual spans for domain-specific operations (`order-consumed`)
3. **Trace Context Propagation** - Automatically propagates context from Kafka messages
4. **Structured Logging** - Uses source generators for performance and structure
5. **Resource Attributes** - Properly identifies service with namespace and version
6. **Selective Instrumentation** - Disables EF Core auto-instrumentation to reduce overhead
7. **OTLP Export** - Uses standard OTLP protocol for interoperability
8. **Configuration Visibility** - Logs all OTel environment variables at startup for debugging

This service exemplifies a lightweight, production-ready approach to OpenTelemetry instrumentation in .NET applications.
