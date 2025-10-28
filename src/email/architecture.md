# Email Service - Architecture Documentation

## Overview

The Email Service is a lightweight HTTP service built with Ruby/Sinatra that simulates sending order confirmation emails. It demonstrates OpenTelemetry auto-instrumentation with Ruby alongside manual span creation.

**Language:** Ruby
**Framework:** Sinatra (web framework), Puma (web server)
**Service Type:** HTTP REST API
**Primary Function:** Order Confirmation Email Delivery

---

## Key Functionality

### 1. Send Order Confirmation Email

**Endpoint:** `POST /send_order_confirmation`
**File:** `email_server.rb`

```ruby
post "/send_order_confirmation" do
  data = JSON.parse(request.body.read, object_class: OpenStruct)

  # Get current auto-instrumented span from Sinatra middleware
  current_span = OpenTelemetry::Trace.current_span
  current_span.add_attributes({
    "app.order.id" => data.order.order_id,
  })

  send_email(data)

  status 200
end
```

**Request Payload:**
```json
{
  "email": "customer@example.com",
  "order": {
    "order_id": "abc-123",
    "shipping_tracking_id": "ship-456",
    "items": [...],
    "shipping_cost": {...},
    "shipping_address": {...}
  }
}
```

### 2. Email Template Rendering

**Template:** `views/confirmation.erb`

Renders order confirmation with:
- Order ID
- Shipping tracking information
- Itemized order list with costs
- Total order amount

### 3. Feature Flag Integration

**Feature Flag:** `emailMemoryLeak`

Simulates memory leak by appending whitespace to email body:

```ruby
def send_email(data)
  tracer = OpenTelemetry.tracer_provider.tracer('email')
  tracer.in_span("send_email") do |span|
    client = OpenFeature::SDK.build_client
    memory_leak_multiplier = client.fetch_number_value(
      flag_key: "emailMemoryLeak",
      default_value: 0
    )

    confirmation_content = erb(:confirmation, locals: { order: data.order })
    whitespace_length = [0, confirmation_content.length * (memory_leak_multiplier-1)].max

    Pony.mail(
      to: data.email,
      from: "noreply@example.com",
      subject: "Your confirmation email",
      body: confirmation_content + " " * whitespace_length,
      via: :test
    )

    span.set_attribute("app.email.recipient", data.email)
    puts "Order confirmation email sent to: #{data.email}"
  end
end
```

---

## Role in the Ecosystem

### Upstream Dependencies
- **Checkout Service** - Sends order confirmation requests
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
       │ HTTP POST: /send_order_confirmation
       │ (W3C Trace Context via headers)
       ▼
┌──────────────┐         ┌──────────────┐
│Email Service │────────▶│    Flagd     │
│              │         │  (Feature    │
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

### HTTP Endpoints

**POST /send_order_confirmation**
- **Content-Type:** `application/json`
- **Request Body:** JSON with `email` and `order` fields
- **Response:** HTTP 200 on success
- **Port:** Configurable via `EMAIL_PORT` (default: 6060)

---

## OpenTelemetry Implementation Details

### Auto-Instrumentation Setup

#### 1. Dependencies
**File:** `Gemfile`

```ruby
gem "opentelemetry-sdk", "~> 1.8"
gem "opentelemetry-exporter-otlp", "~> 0.30.0"
gem "opentelemetry-instrumentation-all", "~> 0.80.0"
```

The `opentelemetry-instrumentation-all` meta-gem includes:
- `opentelemetry-instrumentation-sinatra` - Auto-instruments Sinatra routes
- `opentelemetry-instrumentation-http` - Auto-instruments HTTP client calls
- Other common Ruby library instrumentations

#### 2. SDK Configuration
**File:** `email_server.rb`

```ruby
require 'opentelemetry/sdk'
require 'opentelemetry/instrumentation/all'
require 'opentelemetry-exporter-otlp'

OpenTelemetry::SDK.configure do |c|
  c.service_name = 'email'
  c.use_all() # Enable all available instrumentations
end
```

**What gets auto-instrumented:**
- **Sinatra routes** - HTTP server spans for each endpoint
- **HTTP requests** - If service makes outbound HTTP calls
- **ERB template rendering** - Template processing spans

#### 3. Trace Context Propagation

Sinatra instrumentation automatically:
- Extracts W3C Trace Context from HTTP headers (`traceparent`, `tracestate`)
- Creates server span for each HTTP request
- Propagates context to child operations

### Manual Instrumentation

#### 1. Accessing Current Span
**File:** `email_server.rb`

```ruby
post "/send_order_confirmation" do
  # Access auto-instrumented span created by Sinatra middleware
  current_span = OpenTelemetry::Trace.current_span

  # Add custom attributes
  current_span.add_attributes({
    "app.order.id" => data.order.order_id,
  })
end
```

#### 2. Custom Span Creation
**File:** `email_server.rb`

```ruby
def send_email(data)
  # Get tracer
  tracer = OpenTelemetry.tracer_provider.tracer('email')

  # Create child span
  tracer.in_span("send_email") do |span|
    # Business logic...

    # Add custom attributes
    span.set_attribute("app.email.recipient", data.email)

    # Span automatically ends when block completes
  end
end
```

**Span Hierarchy:**
```
http.server: POST /send_order_confirmation (auto)
  └─ send_email (manual)
      ├─ erb: render confirmation template (auto)
      └─ pony: send email (auto, if instrumented)
```

#### 3. Error Handling
**File:** `email_server.rb`

```ruby
error do
  # Record exception on current span
  OpenTelemetry::Trace.current_span.record_exception(env['sinatra.error'])

  # Set error status
  OpenTelemetry::Trace.current_span.status =
    OpenTelemetry::Trace::Status.error("Internal Server Error")

  status 500
end
```

### Environment Variables

```yaml
environment:
  - EMAIL_PORT=6060
  - FLAGD_HOST=flagd
  - FLAGD_PORT=8013
  - OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318
  - OTEL_EXPORTER_OTLP_TRACES_PROTOCOL=http/protobuf
  - OTEL_RESOURCE_ATTRIBUTES=service.namespace=opentelemetry-demo,service.version=2.1.3
  - OTEL_SERVICE_NAME=email
```

| Variable | Value | Purpose |
|----------|-------|---------|
| `OTEL_SERVICE_NAME` | `email` | Service identifier |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://otel-collector:4318` | OTLP HTTP endpoint |
| `OTEL_EXPORTER_OTLP_TRACES_PROTOCOL` | `http/protobuf` | OTLP protocol |
| `OTEL_RESOURCE_ATTRIBUTES` | `service.namespace=...` | Additional resource attributes |

### Telemetry Data Flow

```
┌──────────────────────────────────────────────────┐
│ Email Service (Ruby/Sinatra)                     │
│                                                   │
│ ┌─────────────────────────────────────────────┐  │
│ │ Puma Web Server                             │  │
│ │   ↓                                         │  │
│ │ Sinatra Middleware Stack                    │  │
│ │   ↓                                         │  │
│ │ [OpenTelemetry::Instrumentation::Sinatra]   │  │
│ │   - Extract trace context from headers     │  │
│ │   - Create server span                     │  │
│ │   ↓                                         │  │
│ │ POST /send_order_confirmation               │  │
│ │   ↓                                         │  │
│ │ current_span.add_attributes(order_id)      │  │
│ │   ↓                                         │  │
│ │ send_email(data)                            │  │
│ │   ↓                                         │  │
│ │   tracer.in_span("send_email") do          │  │
│ │     ├─ Fetch feature flag                  │  │
│ │     ├─ Render ERB template                 │  │
│ │     ├─ Send email via Pony                 │  │
│ │     └─ Set span attributes (recipient)     │  │
│ │   end                                       │  │
│ │   ↓                                         │  │
│ │ Return HTTP 200                             │  │
│ └─────────────────────────────────────────────┘  │
│                                                   │
│ [Export via OTLP/HTTP to otel-collector:4318]    │
│   • Traces (spans with attributes)               │
└───────────────────────────────────────────────────┘
                         ↓
                     Traces
                         ↓
            ┌──────────────────────┐
            │ OpenTelemetry        │
            │ Collector            │
            │ (otel-collector:4318)│
            └──────────────────────┘
```

### Observability Outputs

#### Traces
**Exporter:** OTLP/HTTP
**Endpoint:** `http://otel-collector:4318/v1/traces`
**Backend:** Jaeger (via OTel Collector)

**Span Types:**
1. `POST /send_order_confirmation` (auto-instrumented)
2. `send_email` (manual)
3. ERB template rendering (auto-instrumented)

**Custom Attributes:**
- `app.order.id` - Order identifier
- `app.email.recipient` - Email recipient address

---

## Configuration Reference

### Environment Variables
```bash
EMAIL_PORT=6060
FLAGD_HOST=flagd
FLAGD_PORT=8013
OTEL_SERVICE_NAME=email
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318
OTEL_EXPORTER_OTLP_TRACES_PROTOCOL=http/protobuf
OTEL_RESOURCE_ATTRIBUTES=service.namespace=opentelemetry-demo,service.version=2.1.3
```

---

## Key Files Reference

| File | Purpose |
|------|---------|
| `email_server.rb` | Main service implementation |
| `views/confirmation.erb` | Email template |
| `Gemfile` | Ruby dependencies |
| `Dockerfile` | Container image build |
| `README.md` | Documentation |

---

## Dependencies

### Ruby Gems
```ruby
gem "sinatra", "~> 4.2"
gem "puma", "~> 6.6"
gem "pony", "~> 1.13"
gem "opentelemetry-sdk", "~> 1.8"
gem "opentelemetry-exporter-otlp", "~> 0.30.0"
gem "opentelemetry-instrumentation-all", "~> 0.80.0"
gem "openfeature-sdk", "~> 0.4.0"
gem "openfeature-sdk-contrib", "~> 0.2.0"
```

---

## Build and Development

### Local Build
```bash
bundle install
bundle exec ruby email_server.rb
```

### Docker Build
```bash
docker compose build email
docker compose up email
```

---

## Observability Best Practices Demonstrated

1. **Auto-Instrumentation** - Uses Sinatra instrumentation for automatic span creation
2. **Context Access** - Accesses current span to add business attributes
3. **Custom Spans** - Creates manual spans for specific operations
4. **Trace Context Propagation** - Automatic extraction from HTTP headers
5. **Error Recording** - Captures exceptions on spans
6. **Feature Flag Integration** - Demonstrates chaos engineering with observability

This service exemplifies simple and effective OpenTelemetry instrumentation in Ruby applications with minimal manual code.
