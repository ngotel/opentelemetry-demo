# Fraud Detection Service - Architecture Documentation

## Overview

The Fraud Detection Service is a Kafka consumer application written in Kotlin that processes order events for fraud detection. It demonstrates OpenTelemetry Java agent auto-instrumentation with minimal manual code.

**Language:** Kotlin (JVM)
**Framework:** Apache Kafka Consumer
**Service Type:** Event-Driven Consumer (No HTTP/gRPC Server)
**Primary Function:** Order Fraud Detection Processing

---

## Key Functionality

### 1. Kafka Message Consumption

**Topic:** `orders`
**Consumer Group:** `fraud-detection`
**Message Format:** Protocol Buffer (`OrderResult`)

**File:** `src/main/kotlin/frauddetection/main.kt`

```kotlin
fun main() {
    // Initialize OpenFeature with flagd provider
    val options = FlagdOptions.builder()
        .withGlobalTelemetry(true)
        .build()
    val flagdProvider = FlagdProvider(options)
    OpenFeatureAPI.getInstance().setProvider(flagdProvider)

    // Create Kafka consumer
    val consumer = KafkaConsumer<String, ByteArray>(kafkaProperties())
    consumer.subscribe(listOf("orders"))

    // Polling loop
    while (true) {
        val records: ConsumerRecords<String, ByteArray> = consumer.poll(Duration.ofMillis(100))
        for (record in records) {
            processRecord(record)
        }
    }
}
```

### 2. Order Processing

**File:** `src/main/kotlin/frauddetection/FraudDetectionService.kt`

```kotlin
fun processRecord(record: ConsumerRecord<String, ByteArray>) {
    val order = Oteldemo.OrderResult.parseFrom(record.value())

    logger.info(
        "Processing order: ${order.orderId}, " +
        "tracking: ${order.shippingTrackingId}, " +
        "items: ${order.itemsList.size}"
    )

    // Feature flag check
    val client = OpenFeatureAPI.getInstance().client
    val evalContext = MutableContext(UUID.randomUUID().toString())
    val ffValue = client.getIntegerValue("kafkaQueueProblems", 0, evalContext)

    if (ffValue > 0) {
        logger.info("Feature flag 'kafkaQueueProblems' enabled, inducing delay")
        Thread.sleep(1000)
    }

    messageCounter++
    logger.info("Total messages processed: $messageCounter")
}
```

### 3. Feature Flag Integration

**Feature Flag:** `kafkaQueueProblems`
- **Type:** Integer
- **Purpose:** Simulates processing delays
- **Behavior:** When > 0, induces 1-second delay per message

---

## Role in the Ecosystem

### Upstream Dependencies
- **Kafka Broker** - Consumes `orders` topic
- **Checkout Service** - Publishes order events
- **Flagd Service** - Feature flag evaluation
- **OpenTelemetry Collector** - Telemetry aggregation

### Downstream Consumers
- None (terminal consumer)

### Service Interactions

```
┌──────────────┐
│   Checkout   │
│   Service    │
└──────────────┘
       │
       │ Kafka Publish: orders topic
       │ (W3C Trace Context in headers)
       ▼
┌──────────────┐
│    Kafka     │
│    Broker    │
└──────────────┘
       │
       │ Kafka Consume
       ▼
┌──────────────┐         ┌──────────────┐
│Fraud         │────────▶│    Flagd     │
│Detection     │ gRPC    │  (Feature    │
│Service       │         │   Flags)     │
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

## High-Level API/Interface

### Incoming Messages

**Kafka Topic:** `orders`
**Message Type:** `OrderResult` (Protocol Buffer)

```protobuf
message OrderResult {
    string order_id = 1;
    string shipping_tracking_id = 2;
    Money shipping_cost = 3;
    Address shipping_address = 4;
    repeated OrderItem items = 5;
}
```

**Consumer Configuration:**
```kotlin
fun kafkaProperties(): Properties {
    return Properties().apply {
        put(ConsumerConfig.BOOTSTRAP_SERVERS_CONFIG,
            System.getenv("KAFKA_ADDR") ?: "kafka:9092")
        put(ConsumerConfig.GROUP_ID_CONFIG, "fraud-detection")
        put(ConsumerConfig.KEY_DESERIALIZER_CLASS_CONFIG,
            "org.apache.kafka.common.serialization.StringDeserializer")
        put(ConsumerConfig.VALUE_DESERIALIZER_CLASS_CONFIG,
            "org.apache.kafka.common.serialization.ByteArrayDeserializer")
        put(ConsumerConfig.AUTO_OFFSET_RESET_CONFIG, "earliest")
    }
}
```

---

## OpenTelemetry Implementation Details

### Auto-Instrumentation Setup

#### 1. Java Agent Injection
**File:** `Dockerfile`

```dockerfile
ARG OTEL_JAVA_AGENT_VERSION
WORKDIR /usr/src/app/
COPY --from=builder /usr/src/app/ ./
ADD --chmod=644 \
    https://github.com/open-telemetry/opentelemetry-java-instrumentation/releases/download/v$OTEL_JAVA_AGENT_VERSION/opentelemetry-javaagent.jar \
    /usr/src/app/opentelemetry-javaagent.jar
ENV JAVA_TOOL_OPTIONS=-javaagent:/usr/src/app/opentelemetry-javaagent.jar

ENTRYPOINT [ "java", "-jar", "fraud-detection-all.jar" ]
```

**Agent Version:** 2.21.0 (from `.env`)

**How it works:**
- Downloads OpenTelemetry Java agent at build time
- Sets `JAVA_TOOL_OPTIONS` to inject agent into JVM
- Agent automatically instruments all supported libraries
- No code changes required for auto-instrumentation

#### 2. Auto-Instrumented Components

The Java agent automatically instruments:

**Kafka Consumer:**
- Consumer spans for each `poll()` operation
- Message receive spans for each record
- Trace context extraction from message headers
- Attributes: topic, partition, offset, group ID

**gRPC Client (Flagd):**
- Client spans for feature flag evaluations
- Method, status code, duration
- Trace context injection into metadata

**Logging (Log4j2):**
- Automatic MDC context injection
- Trace ID, span ID, trace flags in logs

#### 3. Dependencies
**File:** `build.gradle.kts`

```kotlin
dependencies {
    // OpenTelemetry API (for manual instrumentation if needed)
    implementation("io.opentelemetry:opentelemetry-api:1.55.0")
    implementation("io.opentelemetry:opentelemetry-sdk:1.55.0")
    implementation("io.opentelemetry:opentelemetry-extension-annotations:1.18.0")

    // Kafka client (auto-instrumented by agent)
    implementation("org.apache.kafka:kafka-clients:3.9.0")

    // Logging (auto-instrumented by agent)
    implementation("org.apache.logging.log4j:log4j-api:2.25.2")
    implementation("org.apache.logging.log4j:log4j-core:2.25.2")

    // OpenFeature with telemetry
    implementation("dev.openfeature:sdk:1.13.1")
    implementation("dev.openfeature.contrib.providers:flagd:0.8.14")

    // Protocol Buffers
    implementation("com.google.protobuf:protobuf-java:4.33.0")
}
```

#### 4. Log4j2 Configuration with Trace Context
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
2024-01-15 10:30:45 - frauddetection.FraudDetectionServiceKt - Processing order: abc-123, tracking: ship-456, items: 3 trace_id=4bf92f3577b34da6a3ce929d0e0e4736 span_id=00f067aa0ba902b7 trace_flags=01
```

### Manual Instrumentation

The service uses **minimal manual instrumentation**, relying primarily on auto-instrumentation:

#### OpenFeature with Telemetry
**File:** `src/main/kotlin/frauddetection/main.kt`

```kotlin
val options = FlagdOptions.builder()
    .withGlobalTelemetry(true)  // Enables OTel instrumentation for flagd
    .build()

val flagdProvider = FlagdProvider(options)
OpenFeatureAPI.getInstance().setProvider(flagdProvider)
```

**What this enables:**
- Feature flag evaluation spans
- Span attributes for flag keys, values, reasons
- Automatic parent-child span relationships

### Environment Variables

```yaml
environment:
  - KAFKA_ADDR=kafka:9092
  - FLAGD_HOST=flagd
  - FLAGD_PORT=8013
  - OTEL_SERVICE_NAME=fraud-detection
  - OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
  - OTEL_RESOURCE_ATTRIBUTES=service.namespace=opentelemetry-demo,service.version=2.1.3
  - OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=cumulative
  - OTEL_LOGS_EXPORTER=otlp
```

| Variable | Value | Purpose |
|----------|-------|---------|
| `OTEL_SERVICE_NAME` | `fraud-detection` | Service identifier |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://otel-collector:4317` | OTLP gRPC endpoint |
| `OTEL_LOGS_EXPORTER` | `otlp` | Export logs via OTLP |
| `OTEL_RESOURCE_ATTRIBUTES` | `service.namespace=...` | Additional resource attributes |

### Telemetry Data Flow

```
┌─────────────────────────────────────────────────────────┐
│ Fraud Detection Service (Kotlin/JVM)                    │
│                                                          │
│ ┌────────────────────────────────────────────────────┐  │
│ │ JVM Startup                                        │  │
│ │   ↓                                                │  │
│ │ Java Agent Loaded (via JAVA_TOOL_OPTIONS)        │  │
│ │   ↓                                                │  │
│ │ Auto-Instrumentation Active:                      │  │
│ │   • Kafka Consumer Interceptors                   │  │
│ │   • gRPC Client Interceptors                      │  │
│ │   • Log4j2 MDC Context Injection                  │  │
│ │   ↓                                                │  │
│ │ Main Loop                                          │  │
│ │   ↓                                                │  │
│ │ while (true) {                                     │  │
│ │   ┌──────────────────────────────────────────┐    │  │
│ │   │ consumer.poll(100ms)                     │    │  │
│ │   │   [Auto: Kafka consumer span created]   │    │  │
│ │   │   [Auto: Extract trace context from     │    │  │
│ │   │          message headers]                │    │  │
│ │   │   ↓                                      │    │  │
│ │   │ for each record:                         │    │  │
│ │   │   [Auto: Message receive span]          │    │  │
│ │   │   ↓                                      │    │  │
│ │   │   Parse Protobuf                         │    │  │
│ │   │   ↓                                      │    │  │
│ │   │   logger.info("Processing order...")     │    │  │
│ │   │   [Auto: Log includes trace context]    │    │  │
│ │   │   ↓                                      │    │  │
│ │   │   Feature flag evaluation                │    │  │
│ │   │   [Auto: gRPC client span to flagd]     │    │  │
│ │   │   [Auto: Flag evaluation span]          │    │  │
│ │   │   ↓                                      │    │  │
│ │   │   if (ffValue > 0) Thread.sleep(1000)   │    │  │
│ │   │   ↓                                      │    │  │
│ │   │   messageCounter++                       │    │  │
│ │   └──────────────────────────────────────────┘    │  │
│ │ }                                                  │  │
│ │                                                    │  │
│ │ [Export via OTLP/gRPC to otel-collector:4317]     │  │
│ │   • Traces (Kafka consumer, gRPC client spans)    │  │
│ │   • Metrics (JVM metrics, Kafka metrics)          │  │
│ │   • Logs (with trace correlation)                 │  │
│ └────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
                         ↓
              Traces, Metrics, Logs
                         ↓
            ┌──────────────────────┐
            │ OpenTelemetry        │
            │ Collector            │
            │ (otel-collector:4317)│
            └──────────────────────┘
```

### Example Distributed Trace

```
checkout: PlaceOrder()
  └─ orders publish (span, kind: producer)
      │ Kafka headers:
      │   - traceparent: 00-{trace_id}-{span_id}-01
      │
      └─ kafka.consume: orders [Fraud Detection Service]
          │ [Auto: Extract trace context from headers]
          │ span attributes:
          │   - messaging.system: kafka
          │   - messaging.destination: orders
          │   - messaging.operation: receive
          │   - messaging.kafka.partition: 0
          │   - messaging.kafka.offset: 12345
          │
          ├─ log: "Processing order: abc-123..."
          │   │ log attributes:
          │   │   - trace_id: {trace_id}
          │   │   - span_id: {span_id}
          │
          └─ grpc.client: flagd.GetIntegerValue()
              │ [Auto: Feature flag evaluation]
              │ span attributes:
              │   - rpc.system: grpc
              │   - rpc.service: flagd.evaluation.v1.Service
              │   - rpc.method: GetIntegerValue
```

### Observability Outputs

#### Traces
**Exporter:** OTLP/gRPC
**Endpoint:** `http://otel-collector:4317/v1/traces`
**Backend:** Jaeger (via OTel Collector)

**Auto-instrumented Spans:**
- `kafka.consume` - Kafka consumer poll operation
- `kafka.receive` - Individual message processing
- `grpc.client` - Feature flag gRPC calls

**Span Attributes (from Kafka instrumentation):**
- `messaging.system`: `kafka`
- `messaging.destination`: `orders`
- `messaging.operation`: `receive`
- `messaging.kafka.consumer.group`: `fraud-detection`
- `messaging.kafka.partition`: partition number
- `messaging.kafka.offset`: message offset

#### Metrics
**Exporter:** OTLP/gRPC
**Endpoint:** `http://otel-collector:4317/v1/metrics`
**Backend:** Prometheus (via OTel Collector)

**Auto-collected Metrics:**
- JVM metrics (heap, GC, threads)
- Kafka consumer metrics (lag, throughput)
- gRPC client metrics (request count, latency)

#### Logs
**Exporter:** OTLP/gRPC
**Endpoint:** `http://otel-collector:4317/v1/logs`
**Format:** Log4j2 with trace correlation

---

## Configuration Reference

### Environment Variables
```bash
KAFKA_ADDR=kafka:9092
FLAGD_HOST=flagd
FLAGD_PORT=8013
OTEL_SERVICE_NAME=fraud-detection
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
OTEL_RESOURCE_ATTRIBUTES=service.namespace=opentelemetry-demo,service.version=2.1.3
OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=cumulative
OTEL_LOGS_EXPORTER=otlp
OTEL_JAVA_AGENT_VERSION=2.21.0
```

---

## Key Files Reference

| File | Purpose |
|------|---------|
| `src/main/kotlin/frauddetection/main.kt` | Main entry point and consumer loop |
| `src/main/kotlin/frauddetection/FraudDetectionService.kt` | Order processing logic |
| `src/main/resources/log4j2.xml` | Logging with trace context |
| `build.gradle.kts` | Gradle build configuration |
| `Dockerfile` | Container build with Java agent |
| `README.md` | Documentation |

---

## Dependencies

### Key Dependencies
```kotlin
// Kafka
implementation("org.apache.kafka:kafka-clients:3.9.0")

// OpenTelemetry (for API access)
implementation("io.opentelemetry:opentelemetry-api:1.55.0")
implementation("io.opentelemetry:opentelemetry-sdk:1.55.0")

// Feature Flags
implementation("dev.openfeature:sdk:1.13.1")
implementation("dev.openfeature.contrib.providers:flagd:0.8.14")

// Protocol Buffers
implementation("com.google.protobuf:protobuf-java:4.33.0")

// Logging
implementation("org.apache.logging.log4j:log4j-api:2.25.2")
implementation("org.apache.logging.log4j:log4j-core:2.25.2")
```

### Runtime
- **OpenTelemetry Java Agent:** 2.21.0 (injected at runtime)

---

## Build and Development

### Local Build
```bash
./gradlew build
java -jar build/libs/fraud-detection-all.jar
```

### Docker Build
```bash
docker compose build fraud-detection
docker compose up fraud-detection
```

---

## Observability Best Practices Demonstrated

1. **100% Auto-Instrumentation** - Zero manual span creation required
2. **Java Agent Pattern** - Runtime bytecode instrumentation
3. **Trace Context Propagation** - Automatic extraction from Kafka message headers
4. **Log Correlation** - MDC injection for trace context in logs
5. **Kafka Consumer Instrumentation** - Complete visibility into message processing
6. **Feature Flag Observability** - Integrated OpenFeature telemetry
7. **Minimal Code Changes** - Observability without invasive code modifications

This service demonstrates the power of OpenTelemetry Java agent auto-instrumentation, achieving comprehensive observability with minimal code changes.
