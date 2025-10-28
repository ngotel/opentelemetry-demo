# Ad Service - Architecture Documentation

## Overview

The Ad Service is a gRPC-based microservice that serves contextual advertisements based on page context keywords. It demonstrates OpenTelemetry instrumentation in Java with both auto and manual instrumentation patterns.

**Language:** Java 21
**Framework:** gRPC with Protocol Buffers
**Build Tool:** Gradle
**Service Type:** gRPC Server
**Primary Function:** Contextual Ad Serving with Feature-Flag-Based Problem Patterns

---

## Key Functionality

### 1. Contextual Ad Serving
Returns targeted advertisements based on context keywords provided by the client.

**gRPC Method:** `GetAds(AdRequest) → AdResponse`
**Implementation:** `AdService.java:150-225`

```java
public void getAds(AdRequest req, StreamObserver<AdResponse> responseObserver) {
    Span span = Span.current();

    // Extract session context from baggage
    Baggage baggage = Baggage.fromContextOrNull(Context.current());
    if (baggage != null) {
        final String sessionId = baggage.getEntryValue("session.id");
        span.setAttribute("session.id", sessionId);
        evaluationContext.setTargetingKey(sessionId);
    }

    // Get ads by context or random
    Collection<Ad> allAds = new ArrayList<>();
    if (req.getContextKeysCount() > 0) {
        for (String contextKey : req.getContextKeysList()) {
            Collection<Ad> ads = getAdsByCategory(contextKey);
            allAds.addAll(ads);
        }
    } else {
        allAds = getRandomAds();
    }

    // Record metrics and return response
    adRequestsCounter.add(1, Attributes.of(
        adRequestTypeKey, adRequestType.name(),
        adResponseTypeKey, adResponseType.name()));

    responseObserver.onNext(AdResponse.newBuilder().addAllAds(allAds).build());
    responseObserver.onCompleted();
}
```

### 2. Feature-Flag-Driven Problem Patterns
Simulates realistic production issues for observability testing.

**Problem Patterns:**

#### a) High CPU Load Simulation
**File:** `problempattern/CPULoad.java`
**Feature Flag:** `AD_HIGH_CPU_FEATURE_FLAG`

Creates 4 daemon threads performing intensive logarithmic calculations:
```java
public void execute(boolean flag) {
    if (flag) {
        for (int i = 0; i < 4; i++) {
            Thread thread = new Thread(() -> {
                while (true) {
                    double value = 0;
                    for (int j = 0; j < 1_000_000; j++) {
                        value += Math.log(Math.random() * Math.random());
                    }
                }
            });
            thread.setDaemon(true);
            thread.start();
        }
    }
}
```

#### b) Garbage Collection Trigger
**File:** `problempattern/GarbageCollectionTrigger.java`
**Feature Flag:** `AD_MANUAL_GC_FEATURE_FLAG`

Forces manual GC every 10 seconds to demonstrate memory pressure:
```java
Thread thread = new Thread(() -> {
    while (true) {
        MemoryUtils.createGarbage();
        long start = System.currentTimeMillis();
        System.gc();
        logger.info("GC took " + (System.currentTimeMillis() - start) + "ms");
        Thread.sleep(10000);
    }
});
```

#### c) Random Request Failures
**Feature Flag:** `AD_FAILURE`
**Implementation:** `AdService.java:204-207`

Randomly fails 1 in 10 requests:
```java
ffClient.getBooleanValue(AD_FAILURE, false, evaluationContext, fContext -> {
    if (fContext.getValue()) {
        throw Status.RESOURCE_EXHAUSTED
            .withDescription("Dummy error triggered by feature flag")
            .asRuntimeException();
    }
});
```

### 3. Health Checking
Implements gRPC health check protocol for orchestration.

**File:** `AdService.java:102-108`

```java
HealthStatusManager health = new HealthStatusManager();
server = ServerBuilder.forPort(port)
    .addService(new AdServiceImpl(client))
    .addService(health.getHealthService())
    .build()
    .start();

health.setStatus("", ServingStatus.SERVING);
```

---

## Role in the Ecosystem

### Upstream Dependencies
- **Frontend Service** - Requests ads for product pages
- **Flagd Service** - Feature flag evaluation for problem patterns
- **OpenTelemetry Collector** - Receives telemetry data

### Downstream Consumers
- None (leaf service - returns ad data to callers)

### Service Interactions

```
┌──────────────┐
│   Frontend   │
│   Service    │
└──────────────┘
       │
       │ gRPC: GetAds(context_keys)
       │ (with trace context + baggage)
       ▼
┌──────────────┐         ┌──────────────┐
│  Ad Service  │────────▶│    Flagd     │
│              │ gRPC    │  (Feature    │
└──────────────┘ GetBool │   Flags)     │
       │                 └──────────────┘
       │ OTLP
       │ (Traces, Metrics, Logs)
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
**Proto File:** `pb/demo.proto:241-262`

```protobuf
service AdService {
    rpc GetAds(AdRequest) returns (AdResponse) {}
}

message AdRequest {
    // List of important key words from the current page describing the context.
    repeated string context_keys = 1;
}

message AdResponse {
    repeated Ad ads = 1;
}

message Ad {
    // url to redirect to when an ad is clicked.
    string redirect_url = 1;

    // short advertisement text to display.
    string text = 2;
}
```

### Incoming Calls
**Method:** `GetAds`
**Type:** gRPC (Protocol Buffers)
**Port:** 9555 (configurable via `AD_PORT`)

**Request Example:**
```json
{
  "context_keys": ["binoculars", "telescopes"]
}
```

**Response Example:**
```json
{
  "ads": [
    {
      "redirect_url": "/product/66VCHSJNUP",
      "text": "Roof Binoculars for sale. 50% off."
    },
    {
      "redirect_url": "/product/6E92ZMYYFZ",
      "text": "Starsense Explorer Refractor Telescope for sale. 20% off."
    }
  ]
}
```

### Outgoing Calls
**Target:** Flagd Service (gRPC)
**Methods:**
- `GetBooleanValue(flag_key, default, context)`
- Multiple feature flags evaluated per request

**Feature Flags:**
- `AD_FAILURE` - Triggers failures (1/10 requests)
- `AD_MANUAL_GC_FEATURE_FLAG` - Triggers garbage collection
- `AD_HIGH_CPU_FEATURE_FLAG` - Triggers CPU load

---

## OpenTelemetry Implementation Details

### Auto-Instrumentation Setup

#### 1. Java Agent Injection
**File:** `Dockerfile:25-32`

```dockerfile
ARG OTEL_JAVA_AGENT_VERSION
WORKDIR /usr/src/app/
COPY --from=builder /usr/src/app/ ./
ADD --chmod=644 \
    https://github.com/open-telemetry/opentelemetry-java-instrumentation/releases/download/v$OTEL_JAVA_AGENT_VERSION/opentelemetry-javaagent.jar \
    /usr/src/app/opentelemetry-javaagent.jar
ENV JAVA_TOOL_OPTIONS=-javaagent:/usr/src/app/opentelemetry-javaagent.jar

ENTRYPOINT [ "./build/install/opentelemetry-demo-ad/bin/Ad" ]
```

**Agent Version:** 2.21.0 (from `.env:8`)

**How it works:**
- Downloads OpenTelemetry Java agent at build time
- Sets `JAVA_TOOL_OPTIONS` to inject agent into JVM
- Agent uses bytecode manipulation to instrument libraries automatically
- No code changes required for auto-instrumentation

**Auto-Instrumented Components:**
- gRPC server/client (inbound and outbound calls)
- HTTP clients (if used)
- JDBC database connections (if used)
- Common Java libraries

#### 2. SDK Dependencies
**File:** `build.gradle:19-52`

```gradle
def opentelemetryVersion = "1.55.0"
def opentelemetryInstrumentationVersion = "2.21.0"

dependencies {
    implementation platform("io.opentelemetry:opentelemetry-bom:${opentelemetryVersion}")
    implementation platform("io.opentelemetry.instrumentation:opentelemetry-instrumentation-bom:${opentelemetryInstrumentationVersion}")

    implementation "io.opentelemetry:opentelemetry-api"
    implementation "io.opentelemetry:opentelemetry-sdk"
    implementation "io.opentelemetry.instrumentation:opentelemetry-instrumentation-annotations"
}
```

**Versions:**
- OpenTelemetry SDK: 1.55.0
- Instrumentation: 2.21.0
- gRPC: 1.76.0

#### 3. Environment Variables
**File:** `docker-compose.yml` (ad service section)

```yaml
environment:
  - AD_PORT=9555
  - FLAGD_HOST=flagd
  - FLAGD_PORT=8013
  - OTEL_SERVICE_NAME=ad
  - OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318
  - OTEL_LOGS_EXPORTER=otlp
  - OTEL_RESOURCE_ATTRIBUTES=service.namespace=opentelemetry-demo,service.version=2.1.3
  - OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=cumulative
```

| Variable | Value | Purpose |
|----------|-------|---------|
| `OTEL_SERVICE_NAME` | `ad` | Service identifier in all telemetry |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://otel-collector:4318` | OTLP HTTP endpoint |
| `OTEL_LOGS_EXPORTER` | `otlp` | Export logs via OTLP protocol |
| `OTEL_RESOURCE_ATTRIBUTES` | `service.namespace=opentelemetry-demo,service.version=...` | Resource attributes |
| `OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE` | `cumulative` | Cumulative metrics aggregation |

### Manual Instrumentation

#### 1. Tracer and Meter Initialization
**File:** `AdService.java:62-69`

```java
// Global tracer and meter instances
private static final Tracer tracer = GlobalOpenTelemetry.getTracer("ad");
private static final Meter meter = GlobalOpenTelemetry.getMeter("ad");

// Custom metric counter
private static final LongCounter adRequestsCounter =
    meter
        .counterBuilder("app.ads.ad_requests")
        .setDescription("Counts ad requests by request and response type")
        .build();
```

**Key Points:**
- Uses `GlobalOpenTelemetry` to access SDK instances
- Tracer name: `"ad"` - identifies spans from this service
- Meter name: `"ad"` - identifies metrics from this service
- Counter tracks ad request/response types

#### 2. Annotation-Based Spans
**File:** `AdService.java:230-234`

```java
@WithSpan("getAdsByCategory")
private Collection<Ad> getAdsByCategory(@SpanAttribute("app.ads.category") String category) {
    Collection<Ad> ads = adsMap.get(category);
    Span.current().setAttribute("app.ads.count", ads.size());
    return ads;
}
```

**Details:**
- `@WithSpan` - Creates span automatically when method is called
- Span name: `"getAdsByCategory"`
- `@SpanAttribute` - Automatically adds parameter as span attribute
- `Span.current()` - Accesses active span to add more attributes
- Less boilerplate than manual span creation

#### 3. Manual Span Creation
**File:** `AdService.java:239-259`

```java
private List<Ad> getRandomAds() {
    List<Ad> ads = new ArrayList<>(MAX_ADS_TO_SERVE);

    // Create and start a new span manually
    Span span = tracer.spanBuilder("getRandomAds").startSpan();

    // Put the span into context, so if any child span is started
    // the parent will be set properly
    try (Scope ignored = span.makeCurrent()) {
        Collection<Ad> allAds = adsMap.values();
        for (int i = 0; i < MAX_ADS_TO_SERVE; i++) {
            ads.add(Iterables.get(allAds, random.nextInt(allAds.size())));
        }
        span.setAttribute("app.ads.count", ads.size());
    } finally {
        span.end();
    }

    return ads;
}
```

**Key Concepts:**
- `tracer.spanBuilder(name).startSpan()` - Creates new span
- `span.makeCurrent()` - Sets span as active in current context
- `try-with-resources` - Automatically closes scope
- `span.setAttribute()` - Adds custom attributes
- `span.end()` - Closes span in finally block (ensures cleanup)

#### 4. Custom Span Attributes
**File:** `AdService.java:154-197`

```java
Span span = Span.current();  // Access auto-instrumented gRPC span

// Session ID from baggage
Baggage baggage = Baggage.fromContextOrNull(Context.current());
if (baggage != null) {
    final String sessionId = baggage.getEntryValue("session.id");
    span.setAttribute("session.id", sessionId);
}

// Request attributes
span.setAttribute("app.ads.contextKeys", req.getContextKeysList().toString());
span.setAttribute("app.ads.contextKeys.count", req.getContextKeysCount());

// Response attributes
span.setAttribute("app.ads.count", allAds.size());
span.setAttribute("app.ads.ad_request_type", adRequestType.name());
span.setAttribute("app.ads.ad_response_type", adResponseType.name());
```

**Custom Attributes:**
| Attribute | Type | Description |
|-----------|------|-------------|
| `session.id` | string | User session ID from baggage |
| `app.ads.contextKeys` | string | List of context keywords |
| `app.ads.contextKeys.count` | int | Number of context keywords |
| `app.ads.count` | int | Number of ads returned |
| `app.ads.ad_request_type` | enum | TARGETED or RANDOM |
| `app.ads.ad_response_type` | enum | TARGETED, RANDOM, or RANDOM_BUT_CONTEXT_PRESENT |
| `app.ads.category` | string | Ad category (from annotation) |

#### 5. Custom Metrics
**File:** `AdService.java:63-69, 199-202`

```java
// Metric definition
private static final LongCounter adRequestsCounter =
    meter
        .counterBuilder("app.ads.ad_requests")
        .setDescription("Counts ad requests by request and response type")
        .build();

// Metric attributes
private static final AttributeKey<String> adRequestTypeKey =
    AttributeKey.stringKey("app.ads.ad_request_type");
private static final AttributeKey<String> adResponseTypeKey =
    AttributeKey.stringKey("app.ads.ad_response_type");

// Recording metric
adRequestsCounter.add(
    1,
    Attributes.of(
        adRequestTypeKey, adRequestType.name(),
        adResponseTypeKey, adResponseType.name()));
```

**Metric Details:**
- **Name:** `app.ads.ad_requests`
- **Type:** Counter (monotonically increasing)
- **Description:** Tracks ad request patterns
- **Dimensions:**
  - `app.ads.ad_request_type`: TARGETED vs RANDOM requests
  - `app.ads.ad_response_type`: How ads were selected
- **Use Case:** Monitor ad serving patterns and effectiveness

#### 6. Error Handling and Status Codes
**File:** `AdService.java:218-223`

```java
catch (StatusRuntimeException e) {
    span.addEvent(
        "Error",
        Attributes.of(
            AttributeKey.stringKey("exception.message"),
            e.getMessage()));
    span.setStatus(StatusCode.ERROR);
    logger.log(Level.WARN, "GetAds Failed with status {}", e.getStatus());
    responseObserver.onError(e);
}
```

**Error Instrumentation:**
- `span.addEvent()` - Records error as span event with attributes
- `span.setStatus(StatusCode.ERROR)` - Marks span as failed
- Event includes exception message for debugging
- Errors are visible in trace visualization (Jaeger)

### Baggage Context Propagation

#### Baggage Extraction
**File:** `AdService.java:160-169`

```java
Baggage baggage = Baggage.fromContextOrNull(Context.current());
MutableContext evaluationContext = new MutableContext();

if (baggage != null) {
    final String sessionId = baggage.getEntryValue("session.id");
    span.setAttribute("session.id", sessionId);
    evaluationContext.setTargetingKey(sessionId);
    evaluationContext.add("session", sessionId);
} else {
    logger.info("no baggage found in context");
}
```

**Baggage Flow:**
```
Frontend Service
  └─ Creates baggage with session.id
      └─ Propagates in gRPC metadata
          └─ Ad Service extracts baggage
              ├─ Adds to span attributes
              └─ Uses for feature flag targeting
```

**Use Cases:**
1. **Session Tracking** - Correlate requests across services
2. **Feature Flag Targeting** - User-specific feature flag evaluation
3. **Span Attribution** - Add user context to all spans in trace

### Logging Integration

#### Log4j2 Configuration with Trace Context
**File:** `src/main/resources/log4j2.xml`

```xml
<?xml version="1.0" encoding="UTF-8"?>
<Configuration status="WARN">
    <Appenders>
        <Console name="STDOUT" target="SYSTEM_OUT">
            <PatternLayout pattern="%d{yyyy-MM-dd HH:mm:ss} - %logger{36} - %msg trace_id=%X{trace_id} span_id=%X{span_id} trace_flags=%X{trace_flags} %n"/>
        </Console>
    </Appenders>
    <Loggers>
        <Root level="INFO">
            <AppenderRef ref="STDOUT"/>
        </Root>
    </Loggers>
</Configuration>
```

**Trace Context in Logs:**
- `%X{trace_id}` - OpenTelemetry trace ID (injected via MDC)
- `%X{span_id}` - Current span ID (injected via MDC)
- `%X{trace_flags}` - Sampling flags (injected via MDC)

**Example Log Output:**
```
2024-01-15 10:30:45 - oteldemo.AdService - Received ad request trace_id=4bf92f3577b34da6a3ce929d0e0e4736 span_id=00f067aa0ba902b7 trace_flags=01
```

**How it works:**
- OpenTelemetry Java agent automatically injects trace context into Log4j2 MDC
- Logs are automatically correlated with traces
- When exported via OTLP, logs include structured trace context

### OpenFeature Integration

#### Feature Flag Provider with Telemetry
**File:** `AdService.java:86-94`

```java
FlagdOptions options = FlagdOptions.builder()
    .withGlobalTelemetry(true)  // Enables OpenTelemetry instrumentation
    .build();

FlagdProvider flagdProvider = new FlagdProvider(options);
OpenFeatureAPI.getInstance().setProvider(flagdProvider);
```

**Feature Flag Evaluation with Context:**
```java
ffClient.getBooleanValue(
    AD_HIGH_CPU_FEATURE_FLAG,
    false,
    evaluationContext);  // Includes session ID from baggage
```

**Telemetry Benefits:**
- Feature flag evaluations automatically create spans
- Spans show which flags were evaluated and their values
- Latency of flag evaluation is tracked
- Feature flag changes are visible in distributed traces

### Telemetry Data Flow

```
┌─────────────────────────────────────────────────────────────┐
│ Ad Service Container (Java)                                 │
│                                                              │
│ ┌────────────────────────────────────────────────────────┐  │
│ │ JVM Startup                                            │  │
│ │   ↓                                                    │  │
│ │ Java Agent Loaded (via JAVA_TOOL_OPTIONS)            │  │
│ │   ↓                                                    │  │
│ │ Auto-Instrumentation Active:                          │  │
│ │   • gRPC Server Interceptors                          │  │
│ │   • gRPC Client Interceptors                          │  │
│ │   • Log4j2 MDC Context Injection                      │  │
│ │   ↓                                                    │  │
│ │ Application Code                                       │  │
│ │                                                         │  │
│ │ ┌──────────────────────────────────────────────────┐   │  │
│ │ │ GetAds() RPC Handler                             │   │  │
│ │ │                                                   │   │  │
│ │ │ 1. [Auto] gRPC Server Span Created ────────────┐ │   │  │
│ │ │    - Extracts W3C trace context from metadata │ │   │  │
│ │ │    - Sets up parent-child relationship         │ │   │  │
│ │ │                                                 │ │   │  │
│ │ │ 2. Access current span                         │ │   │  │
│ │ │    Span span = Span.current()                  │ │   │  │
│ │ │                                                 │ │   │  │
│ │ │ 3. Extract baggage context                     │ │   │  │
│ │ │    session.id from baggage                     │ │   │  │
│ │ │    span.setAttribute("session.id", sessionId)  │ │   │  │
│ │ │                                                 │ │   │  │
│ │ │ 4. Add request attributes                      │ │   │  │
│ │ │    span.setAttribute("app.ads.contextKeys",...)│ │   │  │
│ │ │                                                 │ │   │  │
│ │ │ 5. [Auto] Feature flag gRPC call ───────────┐  │ │   │  │
│ │ │    FlagdProvider creates child span        │  │ │   │  │
│ │ │                                             │  │ │   │  │
│ │ │ 6. Business logic                           │  │ │   │  │
│ │ │    if (contextKeys) {                       │  │ │   │  │
│ │ │      @WithSpan getAdsByCategory() ──────┐   │  │ │   │  │
│ │ │      [Manual annotation creates span]   │   │  │ │   │  │
│ │ │    } else {                             │   │  │ │   │  │
│ │ │      Manual span: getRandomAds() ───┐   │   │  │ │   │  │
│ │ │      [Explicit span creation]       │   │   │  │ │   │  │
│ │ │    }                                │   │   │  │ │   │  │
│ │ │                                     │   │   │  │ │   │  │
│ │ │ 7. Record custom metric             │   │   │  │ │   │  │
│ │ │    adRequestsCounter.add(1, attrs)  │   │   │  │ │   │  │
│ │ │                                     │   │   │  │ │   │  │
│ │ │ 8. Log with trace context           │   │   │  │ │   │  │
│ │ │    logger.info(...) ──> MDC injected│   │   │  │ │   │  │
│ │ │                                     │   │   │  │ │   │  │
│ │ │ 9. Close child spans ◀──────────────┴───┴───┘  │ │   │  │
│ │ │                                                 │ │   │  │
│ │ │ 10. Add response attributes                    │ │   │  │
│ │ │     span.setAttribute("app.ads.count", ...)    │ │   │  │
│ │ │                                                 │ │   │  │
│ │ │ 11. Return response & close span ◀─────────────┘ │   │  │
│ │ └──────────────────────────────────────────────────┘   │  │
│ │                                                         │  │
│ │ [Export via OTLP/HTTP to otel-collector:4318]          │  │
│ │   • Traces (spans with attributes)                     │  │
│ │   • Metrics (counters, histograms)                     │  │
│ │   • Logs (with trace correlation)                      │  │
│ └────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
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

### Example Distributed Trace

```
frontend: GET /product/66VCHSJNUP
  ├─ span: render product page
  │   └─ span: fetch product details
  │       └─ grpc.client: product-catalog.GetProduct()
  │
  └─ span: fetch recommendations
      └─ grpc.client: recommendation.ListRecommendations()
          │
          └─ span: fetch ads (frontend)
              └─ grpc.client: ad.GetAds(context_keys=["binoculars"])
                  │ [W3C trace context propagated via gRPC metadata]
                  │ [Baggage: session.id=xyz123]
                  │
                  └─ grpc.server: ad.GetAds() [Ad Service]
                      ├─ span: getAdsByCategory("binoculars")
                      │   └─ attribute: app.ads.category=binoculars
                      │   └─ attribute: app.ads.count=2
                      │
                      ├─ grpc.client: flagd.GetBooleanValue(AD_FAILURE)
                      │   └─ [OpenFeature creates child span]
                      │
                      └─ event: metric recorded
                          └─ app.ads.ad_requests{
                                 ad_request_type=TARGETED,
                                 ad_response_type=TARGETED
                             }
```

### Observability Outputs

#### Traces
**Exporter:** OTLP/HTTP
**Endpoint:** `http://otel-collector:4318/v1/traces`
**Backend:** Jaeger (via OTel Collector)

**Span Types Generated:**
1. **Auto-instrumented:**
   - `grpc.server.ad.AdService/GetAds` (gRPC server span)
   - `grpc.client.flagd.*` (feature flag calls)
2. **Manual spans:**
   - `getAdsByCategory` (annotation-based)
   - `getRandomAds` (explicit creation)

#### Metrics
**Exporter:** OTLP/HTTP
**Endpoint:** `http://otel-collector:4318/v1/metrics`
**Backend:** Prometheus (via OTel Collector)

**Custom Metrics:**
- `app.ads.ad_requests` - Counter with request/response type dimensions

**Auto-collected Metrics:**
- JVM metrics (heap, GC, threads)
- gRPC server metrics (request count, latency, errors)
- Runtime metrics (CPU, memory)

#### Logs
**Exporter:** OTLP/HTTP (via `OTEL_LOGS_EXPORTER=otlp`)
**Endpoint:** `http://otel-collector:4318/v1/logs`
**Format:** JSON with trace correlation

**Log Features:**
- Automatic trace context injection (`trace_id`, `span_id`, `trace_flags`)
- Structured logging via Log4j2
- Correlated with spans for easy debugging

---

## Configuration Reference

### Service Configuration
```bash
# Service settings
AD_PORT=9555

# Feature flags
FLAGD_HOST=flagd
FLAGD_PORT=8013

# OpenTelemetry
OTEL_SERVICE_NAME=ad
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318
OTEL_LOGS_EXPORTER=otlp
OTEL_RESOURCE_ATTRIBUTES=service.namespace=opentelemetry-demo,service.version=2.1.3
OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=cumulative

# JVM
_JAVA_OPTIONS=-Xmx512m -Xms256m
OTEL_JAVA_AGENT_VERSION=2.21.0
```

### Ad Categories
**File:** `AdService.java:262-298`

Pre-loaded categories:
- `binoculars` - 2 ads
- `telescopes` - 3 ads
- `accessories` - 1 ad
- `assembly` - 1 ad
- `travel` - 1 ad
- `books` - 0 ads (forces random fallback)

---

## Key Files Reference

| File | Purpose |
|------|---------|
| `src/main/java/oteldemo/AdService.java` | Main service with OTel instrumentation |
| `src/main/java/oteldemo/problempattern/CPULoad.java` | CPU load simulation |
| `src/main/java/oteldemo/problempattern/GarbageCollectionTrigger.java` | GC trigger simulation |
| `src/main/java/oteldemo/problempattern/MemoryUtils.java` | Memory utilities |
| `src/main/resources/log4j2.xml` | Logging with trace context |
| `build.gradle` | Dependencies and build config |
| `Dockerfile` | Container with Java agent injection |
| `README.md` | Build and development instructions |

---

## Dependencies

### Key Dependencies
```gradle
dependencies {
    // gRPC
    implementation "io.grpc:grpc-protobuf:1.76.0"
    implementation "io.grpc:grpc-stub:1.76.0"
    implementation "io.grpc:grpc-netty:1.76.0"
    implementation "io.grpc:grpc-services:1.76.0"

    // OpenTelemetry
    implementation "io.opentelemetry:opentelemetry-api:1.55.0"
    implementation "io.opentelemetry:opentelemetry-sdk:1.55.0"
    implementation "io.opentelemetry.instrumentation:opentelemetry-instrumentation-annotations:2.21.0"

    // Feature Flags
    implementation "dev.openfeature:sdk:1.13.1"
    implementation "dev.openfeature.contrib.providers:flagd:0.8.14"

    // Logging
    implementation "org.apache.logging.log4j:log4j-api:2.25.2"
    implementation "org.apache.logging.log4j:log4j-core:2.25.2"

    // Protocol Buffers
    implementation "com.google.protobuf:protobuf-java:4.33.0"
}
```

### External Services
- **Flagd** - Feature flag service (required)
- **OpenTelemetry Collector** - Telemetry aggregation (required)

---

## Build and Development

### Local Build
```bash
# Build with Gradle
./gradlew installDist

# Run locally
export AD_PORT=9555
export FLAGD_HOST=localhost
export FLAGD_PORT=8013
./build/install/opentelemetry-demo-ad/bin/Ad
```

### Docker Build
```bash
# From repository root
docker build --file ./src/ad/Dockerfile ./

# With docker-compose
docker compose build ad
docker compose up ad
```

### Testing
```bash
# Unit tests
./gradlew test

# Integration tests
./gradlew integrationTest
```

---

## Observability Best Practices Demonstrated

1. **Hybrid Instrumentation** - Combines auto and manual instrumentation effectively
2. **Annotation-Based Spans** - Uses `@WithSpan` for simpler span creation
3. **Manual Span Control** - Demonstrates explicit span creation when needed
4. **Rich Span Attributes** - Adds business context (session ID, ad types, counts)
5. **Custom Metrics** - Tracks domain-specific metrics (ad request patterns)
6. **Baggage Propagation** - Uses baggage for cross-service context (session ID)
7. **Error Recording** - Properly records errors with events and status codes
8. **Log Correlation** - Automatically correlates logs with traces via MDC
9. **Feature Flag Telemetry** - Instruments feature flag evaluations
10. **Problem Patterns** - Includes realistic production issues for testing observability

This service exemplifies production-ready OpenTelemetry instrumentation in Java/gRPC applications with comprehensive observability coverage.
