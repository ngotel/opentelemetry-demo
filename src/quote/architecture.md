# Quote Service - Architecture Documentation

## Overview

The Quote Service is a lightweight HTTP REST API written in PHP using the Slim framework that calculates shipping quotes. It demonstrates OpenTelemetry PHP instrumentation with both automatic and manual approaches.

**Language:** PHP 8.3+
**Framework:** Slim 4 (micro-framework)
**Server:** React HTTP Server (async PHP)
**Service Type:** HTTP REST API
**Primary Function:** Shipping Cost Calculation

---

## Key Functionality

### Calculate Shipping Quote

**Endpoint:** `POST /getquote`
**File:** `app/routes.php:14-48`

```php
$app->post('/getquote', function (Request $request, Response $response) use ($logger) {
    // Get current auto-instrumented span
    $span = Span::getCurrent();
    $span->addEvent('Received get quote request, processing it');

    // Extract request data
    $data = json_decode($request->getBody()->getContents(), true, 512, JSON_THROW_ON_ERROR);
    $numberOfItems = $data['numberOfItems'] ?? 0;

    // Create manual child span
    $childSpan = Globals::tracerProvider()->getTracer('manual-instrumentation')
        ->spanBuilder('calculate-quote')
        ->setSpanKind(SpanKind::KIND_INTERNAL)
        ->startSpan();

    $childSpan->addEvent('Calculating quote');

    try {
        // Calculate quote: random cost per item (40-100) * item count
        $quote = 0.0;
        $costPerItem = (floatval(rand(40, 100)));
        $quote = round(floatval($numberOfItems) * $costPerItem, 2);

        // Set span attributes
        $childSpan->setAttribute('app.quote.items.count', $numberOfItems);
        $childSpan->setAttribute('app.quote.cost.total', $quote);

        $childSpan->addEvent('Quote calculated, returning its value');

        // Record metric
        static $counter;
        $counter ??= Globals::meterProvider()
            ->getMeter('quotes')
            ->createCounter('quotes', 'quotes', 'number of quotes calculated');
        $counter->add(1, ['number_of_items' => $numberOfItems]);

        $childSpan->end();

        $response->getBody()->write((string)$quote);
        return $response->withStatus(200)->withHeader('Content-Type', 'application/json');
    } catch (Throwable $exception) {
        $childSpan->recordException($exception);
        $childSpan->end();
        throw $exception;
    }
});
```

**Quote Calculation:**
- Random cost per item: $40 - $100
- Total: `numberOfItems × costPerItem`

---

## Role in the Ecosystem

### Upstream Dependencies
- **Shipping Service** - Requests shipping cost quotes
- **OpenTelemetry Collector** - Telemetry aggregation

### Downstream Consumers
- None (leaf service)

### Service Interactions

```
┌──────────────┐
│   Shipping   │
│   Service    │
└──────────────┘
       │
       │ HTTP POST: /getquote
       │ (W3C Trace Context via headers)
       ▼
┌──────────────┐
│    Quote     │
│   Service    │
└──────────────┘
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

### HTTP Endpoints

**POST /getquote**
- **Content-Type:** `application/json`
- **Request Body:** `{ "numberOfItems": 3 }`
- **Response:** Float value (e.g., `249.50`)
- **Port:** 8090 (configurable via `QUOTE_PORT`)

**Example:**
```bash
curl --location 'http://localhost:8090/getquote' \
  --header 'Content-Type: application/json' \
  --data '{"numberOfItems":3}'
```

---

## OpenTelemetry Implementation Details

### Auto-Instrumentation Setup

#### 1. Auto-Instrumentation Package
**File:** `composer.json`

```json
{
  "require": {
    "open-telemetry/opentelemetry-auto-slim": "^1.3",
    "open-telemetry/api": "^1.7",
    "open-telemetry/sdk": "^1.9",
    "open-telemetry/exporter-otlp": "^1.3.2"
  }
}
```

**`opentelemetry-auto-slim`** automatically instruments:
- Slim framework routes (HTTP request/response)
- HTTP middleware
- Request routing

#### 2. SDK Configuration
**File:** `public/index.php:17-35`

```php
require __DIR__ . '/../vendor/autoload.php';

// Auto-instrumentation is loaded automatically
// Access global providers
use OpenTelemetry\SDK\Common\Configuration\Configuration;
use OpenTelemetry\SDK\Common\Configuration\Variables;
use OpenTelemetry\API\Globals;

// Get tracer provider (configured via environment variables)
$tracerProvider = Globals::tracerProvider();
$meterProvider = Globals::meterProvider();
$loggerProvider = Globals::loggerProvider();
```

**Environment Variables:**
- `OTEL_SERVICE_NAME=quote`
- `OTEL_EXPORTER_OTLP_ENDPOINT`
- `OTEL_RESOURCE_ATTRIBUTES`

### Manual Instrumentation

#### 1. Accessing Auto-Instrumented Span
**File:** `app/routes.php:14-16`

```php
$app->post('/getquote', function (Request $request, Response $response) {
    // Access current span created by auto-instrumentation
    $span = Span::getCurrent();
    $span->addEvent('Received get quote request, processing it');
    // ...
});
```

#### 2. Creating Manual Child Spans
**File:** `app/routes.php:18-21`

```php
$childSpan = Globals::tracerProvider()->getTracer('manual-instrumentation')
    ->spanBuilder('calculate-quote')
    ->setSpanKind(SpanKind::KIND_INTERNAL)
    ->startSpan();
```

#### 3. Span Attributes and Events
**File:** `app/routes.php:22,32-35`

```php
$childSpan->addEvent('Calculating quote');

// ... business logic ...

$childSpan->setAttribute('app.quote.items.count', $numberOfItems);
$childSpan->setAttribute('app.quote.cost.total', $quote);
$childSpan->addEvent('Quote calculated, returning its value');

$childSpan->end();
```

#### 4. Custom Metrics
**File:** `app/routes.php:38-42`

```php
static $counter;
$counter ??= Globals::meterProvider()
    ->getMeter('quotes')
    ->createCounter('quotes', 'quotes', 'number of quotes calculated');

$counter->add(1, ['number_of_items' => $numberOfItems]);
```

**Metric Details:**
- **Name:** `quotes`
- **Type:** Counter
- **Dimensions:** `number_of_items`
- **Purpose:** Track quote calculation frequency

#### 5. Exception Recording
**File:** `app/routes.php:44`

```php
catch (Throwable $exception) {
    $childSpan->recordException($exception);
    $childSpan->end();
    throw $exception;
}
```

#### 6. Batch Processor Flushing
**File:** `public/index.php:60-74`

```php
use React\EventLoop\Loop;

// Periodic flush of trace batches
if (($tracerProvider = Globals::tracerProvider()) instanceof TracerProviderInterface) {
    Loop::addPeriodicTimer(
        Configuration::getInt(Variables::OTEL_BSP_SCHEDULE_DELAY)/1000,
        function() use ($tracerProvider) {
            $tracerProvider->forceFlush();
        }
    );
}
```

Ensures traces are exported even in long-running async PHP server.

#### 7. Logger Integration
**File:** `app/dependencies.php:20-28`

```php
LoggerInterface::class => function (ContainerInterface $c) {
    $settings = $c->get(SettingsInterface::class);
    $loggerSettings = $settings->get('logger');

    $handler = new Handler(
        Globals::loggerProvider(),
        LogLevel::INFO,
    );

    return new Logger($loggerSettings['name'], [$handler]);
},
```

### Environment Variables

```yaml
environment:
  - QUOTE_PORT=8090
  - OTEL_SERVICE_NAME=quote
  - OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318
  - OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
  - OTEL_RESOURCE_ATTRIBUTES=service.namespace=opentelemetry-demo,service.version=2.1.3
  - OTEL_PHP_AUTOLOAD_ENABLED=true
```

### Telemetry Data Flow

```
┌──────────────────────────────────────────────────┐
│ Quote Service (PHP/Slim)                         │
│                                                   │
│ ┌─────────────────────────────────────────────┐  │
│ │ React HTTP Server                           │  │
│ │   ↓                                         │  │
│ │ Auto-Instrumentation (opentelemetry-auto-slim)│
│ │   ↓                                         │  │
│ │ Slim Framework Middleware                   │  │
│ │   [Auto: HTTP server span created]         │  │
│ │   [Auto: Extract trace context from headers]│  │
│ │   ↓                                         │  │
│ │ POST /getquote handler                      │  │
│ │   ↓                                         │  │
│ │ 1. Access current span                      │  │
│ │    Span::getCurrent()                       │  │
│ │    span->addEvent("Received request")       │  │
│ │    ↓                                         │  │
│ │ 2. Create manual child span                 │  │
│ │    spanBuilder('calculate-quote')           │  │
│ │    ↓                                         │  │
│ │ 3. Calculate quote                          │  │
│ │    Random cost × item count                 │  │
│ │    ↓                                         │  │
│ │ 4. Set span attributes                      │  │
│ │    items.count, cost.total                  │  │
│ │    ↓                                         │  │
│ │ 5. Add event                                │  │
│ │    "Quote calculated"                       │  │
│ │    ↓                                         │  │
│ │ 6. Record metric                            │  │
│ │    counter->add(1, attributes)              │  │
│ │    ↓                                         │  │
│ │ 7. End child span                           │  │
│ │    ↓                                         │  │
│ │ 8. Return response                          │  │
│ │    [Auto: End HTTP server span]            │  │
│ │    ↓                                         │  │
│ │ Batch Processor (periodic flush)            │  │
│ │    ↓                                         │  │
│ │ [Export via OTLP/HTTP]                      │  │
│ │   • Traces                                  │  │
│ │   • Metrics                                 │  │
│ │   • Logs                                    │  │
│ └─────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────┘
```

### Observability Outputs

#### Traces
**Exporter:** OTLP/HTTP
**Endpoint:** `http://otel-collector:4318/v1/traces`

**Span Types:**
1. `POST /getquote` (auto-instrumented by Slim)
2. `calculate-quote` (manual child span)

**Custom Attributes:**
- `app.quote.items.count` - Number of items
- `app.quote.cost.total` - Calculated quote

**Span Events:**
- "Received get quote request, processing it"
- "Calculating quote"
- "Quote calculated, returning its value"

#### Metrics
**Custom Metrics:**
- `quotes` - Counter with `number_of_items` dimension

#### Logs
**Integration:** Monolog with OpenTelemetry handler
**Correlation:** Automatic trace context

---

## Configuration Reference

### Environment Variables
```bash
QUOTE_PORT=8090
OTEL_SERVICE_NAME=quote
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318
OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
OTEL_RESOURCE_ATTRIBUTES=service.namespace=opentelemetry-demo,service.version=2.1.3
OTEL_PHP_AUTOLOAD_ENABLED=true
```

---

## Key Files Reference

| File | Purpose |
|------|---------|
| `public/index.php` | Application entry point, React HTTP server |
| `app/routes.php` | Route handlers with OTel instrumentation |
| `app/dependencies.php` | DI container, logger configuration |
| `composer.json` | PHP dependencies |
| `Dockerfile` | Container build |
| `README.md` | Documentation |

---

## Dependencies

### Composer Packages
```json
{
  "slim/slim": "^4",
  "slim/psr7": "^1.7",
  "php-di/php-di": "^7.0",
  "open-telemetry/opentelemetry-auto-slim": "^1.3",
  "open-telemetry/api": "^1.7",
  "open-telemetry/sdk": "^1.9",
  "open-telemetry/exporter-otlp": "^1.3.2",
  "open-telemetry/detector-container": "^1.1",
  "open-telemetry/opentelemetry-logger-monolog": "^1.1"
}
```

---

## Build and Development

### Local Build
```bash
composer install
php public/index.php
```

### Docker Build
```bash
docker compose build quote
docker compose up quote
```

### Testing
```bash
curl --location 'http://localhost:8090/getquote' \
  --header 'Content-Type: application/json' \
  --data '{"numberOfItems":3}'
```

---

## Observability Best Practices Demonstrated

1. **Auto-Instrumentation** - Uses PHP auto-instrumentation for Slim
2. **Hybrid Approach** - Combines auto and manual instrumentation
3. **Span Hierarchy** - Parent (auto) and child (manual) spans
4. **Custom Attributes** - Business context (item count, cost)
5. **Span Events** - Processing milestones
6. **Custom Metrics** - Domain-specific counter
7. **Exception Recording** - Proper error handling
8. **Batch Flushing** - Periodic export for async server
9. **Logger Integration** - Monolog with OTel handler

This service demonstrates effective OpenTelemetry instrumentation in PHP combining automatic framework instrumentation with manual business logic instrumentation.
