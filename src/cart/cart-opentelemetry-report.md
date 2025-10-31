# OpenTelemetry Implementation in Cart App - Investigation Report

## Overview

The Cart Service is a gRPC-based microservice built with ASP.NET Core 8.0 that demonstrates comprehensive OpenTelemetry instrumentation. It manages shopping cart operations using Valkey (Redis) as the persistence layer and showcases both automatic and manual instrumentation approaches across all three pillars of observability: traces, metrics, and logs.

## Architecture

- **Technology**: ASP.NET Core 8.0, gRPC service
- **Data Store**: Valkey (Redis-compatible) for cart persistence
- **OpenTelemetry SDK**: Version 1.11.x series
- **Export Protocol**: OTLP (OpenTelemetry Protocol)

## OpenTelemetry Implementation Details

### 1. SDK Configuration and Initialization

**Location**: `src/cart/src/Program.cs` (lines 30-80)

The OpenTelemetry SDK is configured with a comprehensive setup covering all three observability pillars:

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
        .AddRedisInstrumentation(options => options.SetVerboseDatabaseStatements = true)
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

### 2. Instrumentation Packages

**Location**: `src/cart/src/cart.csproj` (lines 21-30)

The application uses 10 OpenTelemetry packages for comprehensive instrumentation:

| Package | Version | Purpose |
|---------|---------|---------|
| `OpenTelemetry.Exporter.OpenTelemetryProtocol` | 1.11.2 | OTLP exporter for traces, metrics, logs |
| `OpenTelemetry.Extensions.Hosting` | 1.11.2 | ASP.NET Core integration |
| `OpenTelemetry.Instrumentation.AspNetCore` | 1.11.1 | Auto-instruments HTTP/gRPC server |
| `OpenTelemetry.Instrumentation.GrpcNetClient` | 1.11.0-beta.2 | Auto-instruments gRPC client calls |
| `OpenTelemetry.Instrumentation.Http` | 1.11.1 | Auto-instruments HttpClient |
| `OpenTelemetry.Instrumentation.Process` | 1.11.0-beta.2 | Collects process metrics |
| `OpenTelemetry.Instrumentation.StackExchangeRedis` | 1.11.0-beta.2 | Auto-instruments Redis operations |
| `OpenTelemetry.Instrumentation.Runtime` | 1.11.1 | Collects .NET runtime metrics |
| `OpenTelemetry.Resources.Container` | 1.11.0-beta.2 | Detects container metadata |
| `OpenTelemetry.Resources.Host` | 1.11.0-beta.2 | Detects host metadata |

### 3. Auto-Instrumentation Features

The application leverages extensive auto-instrumentation:

- **ASP.NET Core**: Automatically traces HTTP requests and gRPC calls
- **Redis/Valkey**: Captures database operations with verbose statements enabled
- **gRPC Client**: Instruments outbound gRPC calls
- **HTTP Client**: Instruments outbound HTTP requests
- **Process Metrics**: CPU usage, memory consumption
- **Runtime Metrics**: .NET GC, thread pool, exception counters
- **Resource Detection**: Automatically detects container and host metadata

### 4. Manual Instrumentation

#### 4.1 ValkeyCartStore Implementation

**Location**: `src/cart/src/cartstore/ValkeyCartStore.cs`

##### Custom ActivitySource and Meter
```csharp
private static readonly ActivitySource CartActivitySource = new("OpenTelemetry.Demo.Cart");
private static readonly Meter CartMeter = new Meter("OpenTelemetry.Demo.Cart");
```

##### Custom Metrics with Histogram Buckets
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

##### Performance Measurement
The store measures operation latency using `Stopwatch` and records metrics:
```csharp
var stopwatch = Stopwatch.StartNew();
// ... operation logic ...
finally
{
    addItemHistogram.Record(stopwatch.Elapsed.TotalSeconds);
}
```

#### 4.2 CartService Implementation

**Location**: `src/cart/src/services/CartService.cs`

##### Activity Enrichment
```csharp
var activity = Activity.Current;
activity?.SetTag("app.user.id", request.UserId);
activity?.SetTag("app.product.id", request.Item.ProductId);
activity?.SetTag("app.product.quantity", request.Item.Quantity);
activity?.AddEvent(new("Fetch cart"));
```

##### Error Handling
```csharp
catch (RpcException ex)
{
    activity?.AddException(ex);
    activity?.SetStatus(ActivityStatusCode.Error, ex.Message);
    throw;
}
```

##### Business Context Tracking
```csharp
var totalCart = 0;
foreach (var item in cart.Items)
{
    totalCart += item.Quantity;
}
activity?.SetTag("app.cart.items.count", totalCart);
```

### 5. Configuration and Environment Variables

The service uses standard OpenTelemetry environment variables:

| Variable | Value | Purpose |
|----------|-------|---------|
| `OTEL_SERVICE_NAME` | `cart` | Service identifier |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://otel-collector:4317` | OTLP gRPC endpoint |
| `OTEL_RESOURCE_ATTRIBUTES` | `service.namespace=opentelemetry-demo,service.version=2.1.3` | Additional resource attributes |
| `OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE` | `cumulative` | Metrics aggregation preference |

### 6. Redis Connection Instrumentation

**Location**: `src/cart/src/Program.cs` (lines 87-88)

Special handling for Redis instrumentation:
```csharp
var ValkeyCartStore = (ValkeyCartStore)app.Services.GetRequiredService<ICartStore>();
app.Services.GetRequiredService<StackExchangeRedisInstrumentation>()
    .AddConnection(ValkeyCartStore.GetConnection());
```

This ensures Redis operations are properly traced with verbose database statements enabled, capturing:
- Redis commands: `HGET`, `HSET`, `EXPIRE`, `PING`
- Full command text (e.g., `HSET user-123 cart ...`)
- Connection endpoint and database index

## Key Observability Features

### Traces
- **Automatic**: HTTP/gRPC requests, Redis operations, outbound calls
- **Manual**: Business logic spans with custom tags and events
- **Context**: User IDs, product information, cart item counts
- **Error Tracking**: Exception details and error status codes

### Metrics
- **Custom Business Metrics**: Cart operation latencies with custom histogram buckets
- **System Metrics**: Process CPU/memory, .NET runtime statistics
- **Infrastructure Metrics**: ASP.NET Core request metrics
- **Feature Flag Metrics**: OpenFeature integration
- **Exemplars**: Trace-based exemplar filtering for metric-to-trace correlation

### Logs
- **Structured Logging**: Integration with .NET logging framework
- **OTLP Export**: Logs exported alongside traces and metrics
- **Correlation**: Automatic correlation with trace context

## Integration Points

1. **OpenFeature**: Feature flag integration with telemetry for A/B testing observability
2. **gRPC Health Checks**: Service health monitoring with automatic instrumentation
3. **Dependency Injection**: Full integration with ASP.NET Core DI container
4. **Error Simulation**: Intentional failure injection via feature flags for testing observability

## Best Practices Demonstrated

### 1. Comprehensive Coverage
- All three pillars of observability (traces, metrics, logs) implemented
- Both automatic and manual instrumentation approaches used

### 2. Resource Attribution
- Automatic service identification via environment variables
- Container and host metadata detection
- Service namespace and version tracking

### 3. Custom Instrumentation
- Business-specific metrics with meaningful names (`app.cart.add_item.latency`)
- Custom histogram bucket boundaries optimized for expected latency ranges
- Rich trace enrichment with business context

### 4. Error Handling
- Proper exception tracking with `AddException()`
- Activity status management with error codes
- Graceful degradation when instrumentation fails

### 5. Performance Monitoring
- Custom histogram buckets designed for cart operation latencies
- Trace-based exemplars linking metrics to specific traces
- Detailed Redis command instrumentation for database performance

### 6. Correlation
- Automatic trace context propagation
- Metric exemplars linking to traces
- Structured logging with trace correlation

## Technical Implementation Highlights

### Custom Histogram Buckets
The application uses carefully designed histogram buckets for latency measurements:
```
[0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1, 2.5, 5, 7.5, 10]
```
These buckets provide good resolution for typical cart operation latencies from 5ms to 10 seconds.

### Activity Source Pattern
Uses a consistent naming pattern for the custom ActivitySource:
- Name: `"OpenTelemetry.Demo.Cart"`
- Registered in tracing configuration
- Used across all manual instrumentation

### Meter Pattern
Custom meter follows the same naming convention:
- Name: `"OpenTelemetry.Demo.Cart"`
- Registered in metrics configuration
- Used for all business metrics

## Conclusion

The Cart app serves as an excellent example of production-ready OpenTelemetry instrumentation in .NET applications. It demonstrates:

- **Breadth**: Comprehensive auto-instrumentation across HTTP, gRPC, Redis, and system metrics
- **Depth**: Rich manual instrumentation with business-specific context and metrics
- **Best Practices**: Proper error handling, resource attribution, and correlation
- **Performance**: Optimized histogram buckets and efficient instrumentation patterns

The implementation follows OpenTelemetry best practices and provides comprehensive visibility into application performance, errors, and business metrics, making it an ideal reference for implementing observability in microservice architectures.
