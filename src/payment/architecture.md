# Payment Service - Architecture Documentation

## Overview

The Payment Service is a gRPC-based microservice built with Node.js that processes payment transactions with credit card validation. It demonstrates comprehensive OpenTelemetry instrumentation using both auto and manual approaches.

**Language:** JavaScript (Node.js)
**Framework:** gRPC (@grpc/grpc-js)
**Service Type:** gRPC Server
**Primary Function:** Payment Processing and Credit Card Validation

---

## Key Functionality

### 1. Payment Charge Processing

**gRPC Method:** `Charge(ChargeRequest) → ChargeResponse`
**File:** `charge.js`

```javascript
async function charge(call, callback) {
  const tracer = trace.getTracer('payment');
  const span = tracer.startSpan('charge');

  try {
    const amount = call.request.amount;
    const creditCard = call.request.credit_card;

    // Validate credit card
    const cardNumber = creditCard.credit_card_number.replace(/\s+/g, '');
    const card = cardValidator.number(cardNumber);

    if (!card.isValid) {
      throw new Error(`Invalid credit card number: ${cardNumber}`);
    }

    if (card.card.type !== 'visa' && card.card.type !== 'mastercard') {
      throw new Error(`Unsupported card type: ${card.card.type}`);
    }

    // Validate expiration
    const currentMonth = new Date().getMonth() + 1;
    const currentYear = new Date().getFullYear();
    const expirationYear = creditCard.credit_card_expiration_year;
    const expirationMonth = creditCard.credit_card_expiration_month;

    if (currentYear > expirationYear ||
        (currentYear === expirationYear && currentMonth > expirationMonth)) {
      throw new Error('Card expired');
    }

    // Check baggage for synthetic requests
    const baggage = propagation.getBaggage(context.active());
    const synthetic = baggage?.getEntry('synthetic_request')?.value === 'true';

    // Set span attributes
    span.setAttributes({
      'app.payment.card_type': card.card.type,
      'app.payment.card_valid': card.isValid,
      'app.payment.charged': !synthetic,
      'app.loyalty.level': determineLoyaltyLevel(amount)
    });

    // Feature flag for payment failure simulation
    const ffClient = OpenFeature.getClient();
    const ffContext = { targetingKey: uuidv4() };
    const ffValue = await ffClient.getBooleanValue('paymentServiceFailure', false, ffContext);

    if (ffValue) {
      throw new Error('Feature flag triggered payment failure');
    }

    // Record metric
    const transactionsCounter = meter.createCounter('app.payment.transactions');
    transactionsCounter.add(1, { 'app.payment.currency': amount.currency_code });

    // Generate transaction ID
    const transactionId = uuidv4();

    logger.info('Payment processed successfully', {
      transactionId,
      amount: `${amount.units}.${amount.nanos}`,
      currency: amount.currency_code
    });

    span.end();

    callback(null, { transaction_id: transactionId });
  } catch (err) {
    span.recordException(err);
    span.setStatus({ code: SpanStatusCode.ERROR, message: err.message });
    span.end();

    logger.error('Payment processing failed', { error: err.message });
    callback(err);
  }
}
```

### 2. Credit Card Validation

**Library:** `simple-card-validator`

**Validation Checks:**
- Card number format and Luhn algorithm
- Card type (VISA and MasterCard only)
- Expiration date validation

**Supported Card Types:**
- VISA
- MasterCard

### 3. Loyalty Level Assignment

```javascript
function determineLoyaltyLevel(amount) {
  const total = parseFloat(`${amount.units}.${amount.nanos}`);

  if (total >= 1000) return 'platinum';
  if (total >= 500) return 'gold';
  if (total >= 100) return 'silver';
  return 'bronze';
}
```

### 4. Feature Flag Integration

**Feature Flag:** `paymentServiceFailure`
- **Type:** Boolean
- **Purpose:** Simulates payment processing failures
- **Behavior:** When enabled, throws error for testing error handling

---

## Role in the Ecosystem

### Upstream Dependencies
- **Checkout Service** - Initiates payment charges
- **Flagd Service** - Feature flag evaluation
- **OpenTelemetry Collector** - Telemetry aggregation

### Downstream Consumers
- None (leaf service)

### Service Interactions

```
┌──────────────┐
│   Checkout   │
│   Service    │
└──────────────┘
       │
       │ gRPC: Charge(amount, creditCard)
       │ (W3C Trace Context + Baggage via metadata)
       ▼
┌──────────────┐         ┌──────────────┐
│   Payment    │────────▶│    Flagd     │
│   Service    │ gRPC    │  (Feature    │
└──────────────┘         │   Flags)     │
       │                 └──────────────┘
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
service PaymentService {
    rpc Charge(ChargeRequest) returns (ChargeResponse) {}
}

message CreditCardInfo {
    string credit_card_number = 1;
    int32 credit_card_cvv = 2;
    int32 credit_card_expiration_year = 3;
    int32 credit_card_expiration_month = 4;
}

message ChargeRequest {
    Money amount = 1;
    CreditCardInfo credit_card = 2;
}

message ChargeResponse {
    string transaction_id = 1;
}
```

### Incoming Calls

**Port:** 50051 (configurable via `PAYMENT_PORT`)

**Example Request:**
```json
{
  "amount": {
    "currency_code": "USD",
    "units": 99,
    "nanos": 990000000
  },
  "credit_card": {
    "credit_card_number": "4432-8015-6152-0454",
    "credit_card_cvv": 672,
    "credit_card_expiration_year": 2025,
    "credit_card_expiration_month": 12
  }
}
```

**Example Response:**
```json
{
  "transaction_id": "a1b2c3d4-e5f6-7g8h-9i0j-1k2l3m4n5o6p"
}
```

---

## OpenTelemetry Implementation Details

### Auto-Instrumentation Setup

#### 1. OpenTelemetry SDK Initialization
**File:** `opentelemetry.js`

```javascript
const opentelemetry = require('@opentelemetry/sdk-node');
const { getNodeAutoInstrumentations } = require('@opentelemetry/auto-instrumentations-node');
const { RuntimeNodeInstrumentation } = require('@opentelemetry/instrumentation-runtime-node');
const { OTLPTraceExporter } = require('@opentelemetry/exporter-trace-otlp-grpc');
const { OTLPMetricExporter } = require('@opentelemetry/exporter-metrics-otlp-grpc');
const { PeriodicExportingMetricReader } = require('@opentelemetry/sdk-metrics');
const { Resource } = require('@opentelemetry/resources');
const {
  ATTR_SERVICE_NAME,
  ATTR_SERVICE_VERSION,
} = require('@opentelemetry/semantic-conventions');

// Resource detectors for multi-cloud environments
const {
  containerDetector,
  envDetector,
  hostDetector,
  osDetector,
  processDetector,
} = require('@opentelemetry/resources');

const {
  alibabaCloudEcsDetector,
} = require('@opentelemetry/resource-detector-alibaba-cloud');

const {
  awsEksDetector,
  awsEc2Detector,
} = require('@opentelemetry/resource-detector-aws');

const { gcpDetector } = require('@opentelemetry/resource-detector-gcp');

// SDK configuration
const sdk = new opentelemetry.NodeSDK({
  traceExporter: new OTLPTraceExporter(),
  metricReader: new PeriodicExportingMetricReader({
    exporter: new OTLPMetricExporter(),
  }),
  instrumentations: [
    getNodeAutoInstrumentations({
      '@opentelemetry/instrumentation-fs': {
        enabled: false,  // Disable filesystem instrumentation for performance
      },
    }),
    new RuntimeNodeInstrumentation(),
  ],
  resourceDetectors: [
    containerDetector,
    envDetector,
    hostDetector,
    osDetector,
    processDetector,
    alibabaCloudEcsDetector,
    awsEksDetector,
    awsEc2Detector,
    gcpDetector,
  ],
});

sdk.start();
```

**Entry Point:** Application started with:
```bash
node --require ./opentelemetry.js index.js
```

**Auto-Instrumented Libraries:**
- **gRPC** - Server and client operations
- **HTTP/HTTPS** - HTTP client requests
- **DNS** - DNS lookups
- **Net** - TCP/socket operations
- **Process** - Process metrics
- **Runtime** - Node.js runtime metrics (event loop lag, GC, memory)

#### 2. Resource Detection

The SDK automatically detects:
- **Container** - Container ID, image name
- **Environment** - From `OTEL_RESOURCE_ATTRIBUTES`
- **Host** - Hostname, architecture
- **OS** - OS type, version
- **Process** - PID, executable name, runtime version
- **Cloud Providers** - Alibaba Cloud ECS, AWS EKS/EC2, GCP

#### 3. Dependencies
**File:** `package.json`

```json
{
  "dependencies": {
    "@grpc/grpc-js": "^1.12.4",
    "@grpc/proto-loader": "^0.7.16",
    "@openfeature/flagd-provider": "^1.4.0",
    "@openfeature/server-sdk": "^1.18.0",
    "pino": "^9.7.0",
    "pino-opentelemetry-transport": "^1.2.1",
    "simple-card-validator": "^1.2.2",
    "uuid": "^11.0.4"
  },
  "devDependencies": {
    "@opentelemetry/api": "^1.9.0",
    "@opentelemetry/auto-instrumentations-node": "^0.66.0",
    "@opentelemetry/exporter-metrics-otlp-grpc": "^0.58.0",
    "@opentelemetry/exporter-trace-otlp-grpc": "^0.58.0",
    "@opentelemetry/instrumentation-runtime-node": "^0.21.0",
    "@opentelemetry/resource-detector-alibaba-cloud": "^0.29.16",
    "@opentelemetry/resource-detector-aws": "^1.9.0",
    "@opentelemetry/resource-detector-gcp": "^0.29.16",
    "@opentelemetry/resources": "^1.31.0",
    "@opentelemetry/sdk-node": "^0.58.0"
  }
}
```

### Manual Instrumentation

#### 1. Custom Span Creation
**File:** `charge.js`

```javascript
const { trace } = require('@opentelemetry/api');

async function charge(call, callback) {
  const tracer = trace.getTracer('payment');
  const span = tracer.startSpan('charge');

  try {
    // Business logic...
    span.end();
    callback(null, response);
  } catch (err) {
    span.recordException(err);
    span.setStatus({ code: SpanStatusCode.ERROR, message: err.message });
    span.end();
    callback(err);
  }
}
```

#### 2. Span Attributes
**File:** `charge.js`

```javascript
span.setAttributes({
  'app.payment.card_type': card.card.type,
  'app.payment.card_valid': card.isValid,
  'app.payment.charged': !synthetic,
  'app.loyalty.level': determineLoyaltyLevel(amount)
});
```

**Custom Attributes:**
| Attribute | Type | Description |
|-----------|------|-------------|
| `app.payment.card_type` | string | Credit card type (visa, mastercard) |
| `app.payment.card_valid` | boolean | Card validation result |
| `app.payment.charged` | boolean | Whether payment was actually charged |
| `app.loyalty.level` | string | Customer loyalty tier (platinum, gold, silver, bronze) |
| `app.payment.currency` | string | Currency code |
| `app.payment.amount` | float | Payment amount |

#### 3. Custom Metrics
**File:** `charge.js`

```javascript
const { metrics } = require('@opentelemetry/api');

const meter = metrics.getMeter('payment');
const transactionsCounter = meter.createCounter('app.payment.transactions');

// Record metric with attributes
transactionsCounter.add(1, {
  'app.payment.currency': amount.currency_code,
  'app.payment.card_type': card.card.type,
  'app.payment.status': 'success'
});
```

**Metric Details:**
- **Name:** `app.payment.transactions`
- **Type:** Counter
- **Dimensions:** currency_code, card_type, status

#### 4. Baggage Context Propagation
**File:** `charge.js`

```javascript
const { propagation, context } = require('@opentelemetry/api');

// Extract baggage from current context
const baggage = propagation.getBaggage(context.active());
const synthetic = baggage?.getEntry('synthetic_request')?.value === 'true';

// Use baggage value in business logic
span.setAttribute('app.payment.charged', !synthetic);
```

**Baggage Use Case:**
- Load generator sets `synthetic_request=true` in baggage
- Payment service detects synthetic requests
- Skips actual charge processing for test requests
- Visible in span attributes for debugging

#### 5. Error Handling
**File:** `charge.js`

```javascript
catch (err) {
  span.recordException(err);
  span.setStatus({
    code: SpanStatusCode.ERROR,
    message: err.message
  });
  span.end();

  logger.error('Payment processing failed', { error: err.message });
  callback(err);
}
```

#### 6. Structured Logging with OpenTelemetry
**File:** `logger.js`

```javascript
const pino = require('pino');

const transport = pino.transport({
  target: 'pino-opentelemetry-transport',
  options: {
    logRecordProcessorOptions: [
      {
        recordProcessorType: 'batch',
        exporterOptions: {
          protocol: 'grpc'
        }
      },
      {
        recordProcessorType: 'simple',
        exporterOptions: {
          protocol: 'console'
        }
      }
    ],
    loggerName: 'payment-logger'
  }
});

const logger = pino(transport);

module.exports = logger;
```

**Logging Features:**
- Automatic trace context correlation
- Dual export: OTLP gRPC + console
- Batch processor for efficiency
- Structured JSON logs

**Usage:**
```javascript
logger.info('Payment processed successfully', {
  transactionId,
  amount: `${amount.units}.${amount.nanos}`,
  currency: amount.currency_code
});
```

### Environment Variables

```yaml
environment:
  - PAYMENT_PORT=50051
  - FLAGD_HOST=flagd
  - FLAGD_PORT=8013
  - OTEL_SERVICE_NAME=payment
  - OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
  - OTEL_RESOURCE_ATTRIBUTES=service.namespace=opentelemetry-demo,service.version=2.1.3
  - OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=cumulative
```

### Telemetry Data Flow

```
┌──────────────────────────────────────────────────────────────┐
│ Payment Service (Node.js)                                    │
│                                                               │
│ ┌─────────────────────────────────────────────────────────┐  │
│ │ node --require ./opentelemetry.js index.js              │  │
│ │   ↓                                                     │  │
│ │ OpenTelemetry SDK Auto-Instrumentation Active:         │  │
│ │   • gRPC Server Instrumentation                        │  │
│ │   • HTTP Client Instrumentation                        │  │
│ │   • Runtime Metrics Collection                         │  │
│ │   ↓                                                     │  │
│ │ gRPC Server: Charge()                                   │  │
│ │   [Auto: Server span created]                          │  │
│ │   [Auto: Extract trace context from metadata]          │  │
│ │   [Auto: Extract baggage from metadata]                │  │
│ │   ↓                                                     │  │
│ │ ┌───────────────────────────────────────────────────┐   │  │
│ │ │ charge() handler                                  │   │  │
│ │ │   ↓                                               │   │  │
│ │ │ 1. Create custom span                             │   │  │
│ │ │    const span = tracer.startSpan('charge')        │   │  │
│ │ │    ↓                                               │   │  │
│ │ │ 2. Validate credit card                           │   │  │
│ │ │    - Card number, type, expiration                │   │  │
│ │ │    ↓                                               │   │  │
│ │ │ 3. Check baggage for synthetic requests           │   │  │
│ │ │    const synthetic = baggage?.getEntry(...)       │   │  │
│ │ │    ↓                                               │   │  │
│ │ │ 4. Set span attributes                            │   │  │
│ │ │    span.setAttributes({                           │   │  │
│ │ │      card_type, card_valid, charged, loyalty...}) │   │  │
│ │ │    ↓                                               │   │  │
│ │ │ 5. Feature flag check                             │   │  │
│ │ │    [Auto: gRPC client span to flagd]             │   │  │
│ │ │    ↓                                               │   │  │
│ │ │ 6. Record metrics                                 │   │  │
│ │ │    transactionsCounter.add(1, { currency... })    │   │  │
│ │ │    ↓                                               │   │  │
│ │ │ 7. Log success/failure                            │   │  │
│ │ │    logger.info() with trace context               │   │  │
│ │ │    ↓                                               │   │  │
│ │ │ 8. End span                                       │   │  │
│ │ │    span.end()                                     │   │  │
│ │ └───────────────────────────────────────────────────┘   │  │
│ │                                                         │  │
│ │ [Export via OTLP/gRPC to otel-collector:4317]          │  │
│ │   • Traces (spans with attributes)                     │  │
│ │   • Metrics (counters, runtime metrics)                │  │
│ │   • Logs (with trace correlation via pino-otel)        │  │
│ └─────────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────┘
```

### Observability Outputs

#### Traces
**Exporter:** OTLP/gRPC
**Endpoint:** `http://otel-collector:4317/v1/traces`
**Backend:** Jaeger (via OTel Collector)

**Span Types:**
1. `grpc.server: Charge` (auto-instrumented)
2. `charge` (manual)
3. `grpc.client: flagd.*` (auto-instrumented)

**Custom Attributes:**
- Payment details (card_type, card_valid, charged)
- Business context (loyalty_level)
- Amount and currency

#### Metrics
**Exporter:** OTLP/gRPC
**Endpoint:** `http://otel-collector:4317/v1/metrics`
**Backend:** Prometheus (via OTel Collector)

**Custom Metrics:**
- `app.payment.transactions` - Counter with currency/card_type dimensions

**Auto-collected Metrics:**
- `nodejs_eventloop_lag_seconds` - Event loop lag
- `nodejs_heap_size_total_bytes` - Heap memory
- `nodejs_heap_size_used_bytes` - Used heap memory
- `nodejs_external_memory_bytes` - External memory
- `nodejs_gc_duration_seconds` - GC duration
- `process_cpu_user_seconds_total` - CPU usage

#### Logs
**Exporter:** OTLP/gRPC (via pino-opentelemetry-transport)
**Endpoint:** `http://otel-collector:4317/v1/logs`
**Format:** Structured JSON with trace correlation

---

## Configuration Reference

### Environment Variables
```bash
PAYMENT_PORT=50051
FLAGD_HOST=flagd
FLAGD_PORT=8013
OTEL_SERVICE_NAME=payment
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
OTEL_RESOURCE_ATTRIBUTES=service.namespace=opentelemetry-demo,service.version=2.1.3
OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=cumulative
```

---

## Key Files Reference

| File | Purpose |
|------|---------|
| `index.js` | gRPC server setup and main entry |
| `charge.js` | Payment processing logic with OTel instrumentation |
| `logger.js` | Pino logger with OpenTelemetry transport |
| `opentelemetry.js` | OTel SDK initialization |
| `package.json` | Dependencies |
| `Dockerfile` | Container image build |
| `README.md` | Documentation |

---

## Build and Development

### Local Build
```bash
npm ci
node --require ./opentelemetry.js index.js
```

### Docker Build
```bash
docker compose build payment
docker compose up payment
```

---

## Observability Best Practices Demonstrated

1. **Hybrid Instrumentation** - Combines auto and manual approaches
2. **Comprehensive Auto-Instrumentation** - gRPC, HTTP, runtime metrics
3. **Custom Span Creation** - Manual spans for business logic
4. **Rich Span Attributes** - Business context (card type, loyalty level)
5. **Custom Metrics** - Domain-specific transaction counter
6. **Baggage Propagation** - Uses baggage for test request detection
7. **Error Recording** - Proper exception handling with span status
8. **Structured Logging** - Pino with OpenTelemetry transport
9. **Multi-Cloud Resource Detection** - Detects AWS, GCP, Alibaba Cloud
10. **Feature Flag Integration** - Chaos engineering with observability

This service demonstrates production-ready OpenTelemetry instrumentation in Node.js with comprehensive coverage of traces, metrics, and logs.
