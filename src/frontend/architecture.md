# Frontend Service - Architecture Documentation

## Overview

The Frontend Service is a full-stack Next.js application that provides the web UI for the OpenTelemetry demo astronomy shop. It demonstrates comprehensive OpenTelemetry instrumentation across both server-side (Node.js) and client-side (browser) environments.

**Language:** TypeScript
**Framework:** Next.js 16.0.0 / React 19.2.0
**Service Type:** Web Application (SSR/CSR) + API Gateway
**Primary Function:** E-commerce Web Interface and Backend Service Proxy

---

## Key Functionality

### 1. Web Pages (Customer-Facing UI)

**Home Page** (`pages/index.tsx`)
- Displays featured products
- Product catalog browsing
- Currency switcher in header

**Product Detail** (`pages/product/[productId]/index.tsx`)
- Product information and pricing
- Add to cart functionality
- Product recommendations
- Contextual advertisements

**Shopping Cart** (`pages/cart/index.tsx`)
- Cart item display
- Remove items functionality
- Proceed to checkout

**Order Confirmation** (`pages/cart/checkout/[orderId]/index.tsx`)
- Order summary
- Shipping information
- Order tracking ID

### 2. API Routes (Backend Proxy Layer)

The frontend acts as a proxy/gateway to backend microservices:

| API Route | Backend Service | Protocol |
|-----------|----------------|----------|
| `/api/products` | Product Catalog | gRPC |
| `/api/products/[productId]` | Product Catalog | gRPC |
| `/api/cart` | Cart Service | gRPC |
| `/api/checkout` | Checkout Service | gRPC |
| `/api/currency` | Currency Service | gRPC |
| `/api/recommendations` | Recommendation Service | gRPC |
| `/api/data` | Ad Service | gRPC |
| `/api/shipping` | Shipping Service | HTTP |

### 3. State Management

**React Context Providers:**
- **CartProvider** - Shopping cart state with TanStack React Query
- **CurrencyProvider** - Selected currency management
- **AdProvider** - Advertisement and banner state
- **OpenFeatureProvider** - Feature flags

---

## Role in the Ecosystem

### Upstream Dependencies
- All backend microservices (product-catalog, cart, checkout, currency, shipping, recommendation, ad)
- **Flagd Service** - Feature flags
- **OpenTelemetry Collector** - Telemetry aggregation

### Service Interactions

```
┌─────────────────────────────────────────────────┐
│         Browser (User)                          │
└─────────────────────────────────────────────────┘
              │
              │ HTTP(S)
              ▼
┌─────────────────────────────────────────────────┐
│      Frontend (Next.js)                         │
│  ┌───────────────┐  ┌────────────────┐          │
│  │  Client-Side  │  │  Server-Side   │          │
│  │  React App    │  │  API Routes    │          │
│  └───────────────┘  └────────────────┘          │
│         │                   │                    │
│         │ OTel (OTLP/HTTP) │ OTel (OTLP/gRPC)  │
└─────────┼───────────────────┼────────────────────┘
          │                   │
          │                   ├─ gRPC → Product Catalog
          │                   ├─ gRPC → Cart Service
          │                   ├─ gRPC → Checkout Service
          │                   ├─ gRPC → Currency Service
          │                   ├─ gRPC → Recommendation
          │                   ├─ gRPC → Ad Service
          │                   └─ HTTP → Shipping Service
          │
          └─────────────┬─────────────────┘
                        ▼
                ┌──────────────┐
                │     OTel     │
                │  Collector   │
                └──────────────┘
```

---

## OpenTelemetry Implementation Details

### Server-Side Instrumentation (Node.js)

#### 1. Auto-Instrumentation Setup
**File:** `utils/telemetry/Instrumentation.js`

```javascript
const opentelemetry = require('@opentelemetry/sdk-node');
const { getNodeAutoInstrumentations } = require('@opentelemetry/auto-instrumentations-node');
const { OTLPTraceExporter } = require('@opentelemetry/exporter-trace-otlp-grpc');
const { OTLPMetricExporter } = require('@opentelemetry/exporter-metrics-otlp-grpc');
const { PeriodicExportingMetricReader } = require('@opentelemetry/sdk-metrics');

const sdk = new opentelemetry.NodeSDK({
  traceExporter: new OTLPTraceExporter(),
  metricReader: new PeriodicExportingMetricReader({
    exporter: new OTLPMetricExporter(),
  }),
  instrumentations: [
    getNodeAutoInstrumentations({
      '@opentelemetry/instrumentation-fs': {
        enabled: false,  // Reduce noise
      },
    })
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

**Invoked via:**
```json
// package.json
{
  "scripts": {
    "dev": "NODE_OPTIONS='--require ./utils/telemetry/Instrumentation.js' next dev",
    "start": "node --require ./Instrumentation.js server.js"
  }
}
```

**Auto-instrumented:**
- HTTP server requests (Next.js)
- gRPC client calls
- Outbound HTTP requests
- Next.js routing

#### 2. API Route Middleware
**File:** `utils/telemetry/InstrumentationMiddleware.ts`

```typescript
import { NextApiHandler } from 'next';
import { context, trace, SpanStatusCode } from '@opentelemetry/api';
import { metrics } from '@opentelemetry/api';

const meter = metrics.getMeter('frontend');
const requestCounter = meter.createCounter('app.frontend.requests');

const InstrumentationMiddleware = (handler: NextApiHandler): NextApiHandler => {
  return async (request, response) => {
    const { method, url = '' } = request;
    const [target] = url.split('?');
    const span = trace.getSpan(context.active());

    let httpStatus = 200;
    try {
      await runWithSpan(span, async () => handler(request, response));
      httpStatus = response.statusCode;
    } catch (error) {
      span.recordException(error);
      span.setStatus({ code: SpanStatusCode.ERROR });
      httpStatus = 500;
      throw error;
    } finally {
      requestCounter.add(1, { method, target, status: httpStatus });
      span.setAttribute(SemanticAttributes.HTTP_STATUS_CODE, httpStatus);
    }
  };
};
```

**Usage:**
```typescript
// pages/api/products.ts
export default InstrumentationMiddleware(handler);
```

### Client-Side Instrumentation (Browser)

#### 1. Web Tracer Provider Setup
**File:** `utils/telemetry/FrontendTracer.ts`

```typescript
import { WebTracerProvider } from '@opentelemetry/sdk-trace-web';
import { BatchSpanProcessor } from '@opentelemetry/sdk-trace-base';
import { getWebAutoInstrumentations } from '@opentelemetry/auto-instrumentations-web';
import { OTLPTraceExporter } from '@opentelemetry/exporter-trace-otlp-http';
import { SessionIdProcessor } from './SessionIdProcessor';

const FrontendTracer = async () => {
  const { ZoneContextManager } = await import('@opentelemetry/context-zone');

  // Create resource with browser detection
  let resource = resourceFromAttributes({
    [ATTR_SERVICE_NAME]: NEXT_PUBLIC_OTEL_SERVICE_NAME,
  });
  const detectedResources = detectResources({ detectors: [browserDetector] });
  resource = resource.merge(detectedResources);

  // Create provider with custom processors
  const provider = new WebTracerProvider({
    resource,
    spanProcessors: [
      new SessionIdProcessor(),  // Add session.id to all spans
      new BatchSpanProcessor(
        new OTLPTraceExporter({
          url: NEXT_PUBLIC_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT,
        }),
        { scheduledDelayMillis: 500 }
      ),
    ],
  });

  // Use Zone context manager for async operations
  provider.register({
    contextManager: new ZoneContextManager(),
    propagator: new CompositePropagator({
      propagators: [
        new W3CBaggagePropagator(),
        new W3CTraceContextPropagator()
      ],
    }),
  });

  // Register auto-instrumentations
  registerInstrumentations({
    tracerProvider: provider,
    instrumentations: [
      getWebAutoInstrumentations({
        '@opentelemetry/instrumentation-fetch': {
          propagateTraceHeaderCorsUrls: /.*/,  // All URLs
          clearTimingResources: true,
          applyCustomAttributesOnSpan(span) {
            span.setAttribute('app.synthetic_request', IS_SYNTHETIC_REQUEST);
          },
        },
      }),
    ],
  });
};
```

**Invoked in:** `pages/_app.tsx`
```typescript
if (typeof window !== 'undefined') {
  FrontendTracer();
}
```

**Auto-instrumented:**
- Fetch API calls
- XMLHttpRequest
- User interactions
- Document load events
- Navigation

#### 2. Custom Session ID Processor
**File:** `utils/telemetry/SessionIdProcessor.ts`

```typescript
import { Span, SpanProcessor } from "@opentelemetry/sdk-trace-web";
import SessionGateway from "../../gateways/Session.gateway";

const { userId } = SessionGateway.getSession();

export class SessionIdProcessor implements SpanProcessor {
    onStart(span: Span): void {
        span.setAttribute('session.id', userId);
    }
    onEnd(): void {}
    forceFlush(): Promise<void> { return Promise.resolve(); }
    shutdown(): Promise<void> { return Promise.resolve(); }
}
```

Adds `session.id` attribute to every client-side span.

#### 3. Baggage Propagation
**File:** `gateways/Api.gateway.ts`

```typescript
const ApiGateway = new Proxy(Apis(), {
  get(target, prop, receiver) {
    const originalFunction = Reflect.get(target, prop, receiver);
    if (typeof originalFunction !== 'function') return originalFunction;

    return function (...args: any[]) {
      // Add session.id to baggage for all API calls
      const baggage = propagation.getActiveBaggage() || propagation.createBaggage();
      const newBaggage = baggage.setEntry('session.id', { value: userId });
      const newContext = propagation.setBaggage(context.active(), newBaggage);

      return context.with(newContext, () => {
        return Reflect.apply(originalFunction, undefined, args);
      });
    };
  },
});
```

Automatically adds baggage to all backend API calls.

### Environment Variables

**Server-Side:**
```yaml
OTEL_SERVICE_NAME=frontend
OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://otel-collector:4317
OTEL_EXPORTER_OTLP_METRICS_ENDPOINT=http://otel-collector:4317
OTEL_RESOURCE_ATTRIBUTES=service.namespace=opentelemetry-demo,service.version=2.1.3
```

**Client-Side:**
```yaml
NEXT_PUBLIC_OTEL_SERVICE_NAME=frontend-web
NEXT_PUBLIC_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://localhost:4318/v1/traces
```

### Telemetry Data Flow

```
┌────────────────────────────────────────────────────┐
│ Browser                                            │
│  ↓                                                 │
│  User Interaction (click, navigate, etc.)         │
│  ↓                                                 │
│  [Auto: Fetch API span created]                   │
│    - URL, method, status                          │
│    - session.id (via SessionIdProcessor)          │
│    - app.synthetic_request attribute              │
│  ↓                                                 │
│  Batch Export (500ms intervals)                   │
│  ↓                                                 │
│  OTLP/HTTP → otel-collector:4318                  │
└────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────┐
│ Next.js Server                                     │
│  ↓                                                 │
│  HTTP Request (pages, API routes)                 │
│  ↓                                                 │
│  [Auto: HTTP server span created]                 │
│  ↓                                                 │
│  InstrumentationMiddleware                        │
│    - Access current span                          │
│    - Record exceptions                            │
│    - Set HTTP status code                         │
│    - Increment request counter metric             │
│  ↓                                                 │
│  gRPC Gateway Call                                 │
│  [Auto: gRPC client span]                         │
│    - Baggage propagation (session.id)             │
│  ↓                                                 │
│  OTLP/gRPC → otel-collector:4317                  │
└────────────────────────────────────────────────────┘
```

### Observability Outputs

#### Traces
**Server-Side:**
- Exporter: OTLP/gRPC
- Endpoint: `http://otel-collector:4317/v1/traces`
- Spans: HTTP server, gRPC client, API routes

**Client-Side:**
- Exporter: OTLP/HTTP
- Endpoint: `http://localhost:4318/v1/traces`
- Spans: Fetch API, navigation, user interactions

**Custom Attributes:**
- `session.id` - User session identifier
- `app.synthetic_request` - Load generator flag
- `http.status_code` - HTTP response status

#### Metrics
**Custom Metrics:**
- `app.frontend.requests` - Counter (method, target, status)

**Auto-collected:**
- HTTP request duration
- gRPC client metrics
- Node.js runtime metrics

---

## Configuration Reference

### Environment Variables
```bash
# Server-side
OTEL_SERVICE_NAME=frontend
OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://otel-collector:4317
OTEL_EXPORTER_OTLP_METRICS_ENDPOINT=http://otel-collector:4317

# Client-side
NEXT_PUBLIC_OTEL_SERVICE_NAME=frontend-web
NEXT_PUBLIC_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://localhost:4318/v1/traces

# Backend services
AD_ADDR=ad:9555
CART_ADDR=cart:8080
CHECKOUT_ADDR=checkout:5050
CURRENCY_ADDR=currency:7000
PRODUCT_CATALOG_ADDR=product-catalog:3550
RECOMMENDATION_ADDR=recommendation:9001
SHIPPING_ADDR=http://shipping:50050
```

---

## Key Files Reference

| File | Purpose |
|------|---------|
| `pages/_app.tsx` | App root, client tracer initialization |
| `pages/_document.tsx` | SSR, environment injection |
| `pages/api/**/*.ts` | API routes with middleware |
| `utils/telemetry/Instrumentation.js` | Server-side OTel SDK |
| `utils/telemetry/FrontendTracer.ts` | Client-side OTel SDK |
| `utils/telemetry/SessionIdProcessor.ts` | Custom span processor |
| `utils/telemetry/InstrumentationMiddleware.ts` | API route middleware |
| `gateways/Api.gateway.ts` | Baggage propagation proxy |
| `next.config.js` | Next.js configuration |

---

## Build and Development

### Local Build
```bash
npm install
npm run dev
```

### Docker Build
```bash
docker compose build frontend
docker compose up frontend
```

### Access
- **Web UI:** http://localhost:8080
- **Development:** http://localhost:8080 (with hot reload)

---

## Observability Best Practices Demonstrated

1. **Dual Instrumentation** - Server (Node.js) and client (browser) both instrumented
2. **Auto-Instrumentation** - Minimal code changes via SDK
3. **Custom Processors** - Session ID injection into all spans
4. **Baggage Propagation** - Session tracking across distributed system
5. **Proxy Pattern** - Automatic baggage injection using JavaScript Proxy
6. **API Middleware** - Centralized error handling and metrics
7. **Resource Detection** - Cloud/container/browser metadata
8. **Context Propagation** - W3C standards (Trace Context + Baggage)
9. **Conditional Export** - Different endpoints for synthetic vs real traffic
10. **Zone Context Manager** - Proper async context in browsers

This service demonstrates production-ready full-stack OpenTelemetry instrumentation in a modern Next.js/React application.
