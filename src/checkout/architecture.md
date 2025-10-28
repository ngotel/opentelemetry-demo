# Checkout Service - Architecture Documentation

## Overview

The Checkout Service is the central orchestration service that coordinates order placement across multiple microservices. Built in Go with gRPC, it demonstrates comprehensive OpenTelemetry instrumentation including distributed tracing across gRPC, HTTP, and Kafka.

**Language:** Go 1.24.2
**Framework:** gRPC with Protocol Buffers
**Service Type:** gRPC Server with Orchestration Logic
**Primary Function:** Order Processing and Service Orchestration

---

## Key Functionality

### 1. Order Placement Orchestration

The service exposes a single gRPC endpoint that orchestrates a complex multi-step order placement workflow.

**gRPC Method:** `PlaceOrder(PlaceOrderRequest) → PlaceOrderResponse`
**File:** `main.go:289-393`

```go
func (cs *checkout) PlaceOrder(ctx context.Context, req *pb.PlaceOrderRequest) (*pb.PlaceOrderResponse, error) {
    span := trace.SpanFromContext(ctx)

    // Set user attributes
    span.SetAttributes(
        attribute.String("app.user.id", req.UserId),
        attribute.String("app.user.currency", req.UserCurrency),
    )

    // Step 1: Prepare order items and shipping quote
    prep, err := cs.prepareOrderItemsAndShippingQuoteFromCart(
        ctx, req.UserId, req.UserCurrency, req.Address)
    span.AddEvent("prepared")

    // Step 2: Process payment
    txID, err := cs.chargeCard(ctx, prep.cardPayment, req.CreditCard)
    span.AddEvent("charged",
        trace.WithAttributes(
            attribute.String("app.payment.transaction.id", txID)))

    // Step 3: Ship order
    shippingTrackingID, err := cs.shipOrder(ctx, req.Address, prep.cartItems)
    span.AddEvent("shipped",
        trace.WithAttributes(
            attribute.String("app.shipping.tracking.id", shippingTrackingID)))

    // Step 4: Empty cart
    _ = cs.emptyUserCart(ctx, req.UserId)

    // Step 5: Send confirmation email
    _ = cs.sendOrderConfirmation(ctx, req.Email, result)

    // Step 6: Publish to Kafka (async)
    cs.sendToPostProcessor(ctx, result)

    // Set final attributes
    span.SetAttributes(
        attribute.String("app.order.id", orderID.String()),
        attribute.Float64("app.shipping.amount", shippingCostFloat),
        attribute.Float64("app.order.amount", totalPriceFloat),
        attribute.Int("app.order.items.count", len(prep.orderItems)),
    )

    return &pb.PlaceOrderResponse{Order: result}, nil
}
```

### 2. Order Preparation

**Method:** `prepareOrderItemsAndShippingQuoteFromCart`
**File:** `main.go:403-479`

Orchestrates multiple service calls to prepare order:

```go
func (cs *checkout) prepareOrderItemsAndShippingQuoteFromCart(ctx context.Context, userID, userCurrency string, address *pb.Address) (orderPrep, error) {
    ctx, span := tracer.Start(ctx, "prepareOrderItemsAndShippingQuoteFromCart")
    defer span.End()

    // 1. Get cart from Cart service
    cart, err := cs.getUserCart(ctx, userID)

    // 2. Get product details from Product Catalog
    orderItems := make([]*pb.OrderItem, 0, len(cart))
    for _, item := range cart {
        product, err := cs.catalogSvc.GetProduct(ctx, &pb.GetProductRequest{Id: item.ProductId})
        orderItems = append(orderItems, &pb.OrderItem{
            Item: item,
            Cost: product.PriceUsd,
        })
    }

    // 3. Get shipping quote from Shipping service (HTTP)
    shippingQuote, err := cs.quoteShipping(ctx, address, cart)

    // 4. Convert currency if needed
    shippingCostLocalized, err := cs.convertCurrency(ctx, shippingQuote, userCurrency)

    // 5. Calculate total price
    totalPrice := pb.Money{
        CurrencyCode: userCurrency,
        Units: 0,
        Nanos: 0,
    }
    for _, item := range orderItems {
        itemPrice, _ := cs.convertCurrency(ctx, item.Cost, userCurrency)
        multPrice := money.MultiplySlow(*itemPrice, uint32(item.Item.Quantity))
        totalPrice = money.Must(money.Sum(totalPrice, multPrice))
    }
    totalPrice = money.Must(money.Sum(totalPrice, *shippingCostLocalized))

    span.SetAttributes(
        attribute.Float64("app.shipping.amount", shippingCostFloat),
        attribute.Int("app.cart.items.count", int(totalCart)),
        attribute.Int("app.order.items.count", len(orderItems)),
    )

    return orderPrep{
        orderItems: orderItems,
        cartItems: cart,
        shippingCostLocalized: shippingCostLocalized,
        cardPayment: &totalPrice,
    }, nil
}
```

### 3. Payment Processing

**Method:** `chargeCard`
**File:** `main.go:491-523`

```go
func (cs *checkout) chargeCard(ctx context.Context, amount *pb.Money, card *pb.CreditCardInfo) (string, error) {
    paymentService := cs.paymentSvc

    // Feature flag for testing payment failures
    if cs.isFeatureFlagEnabled(ctx, "paymentUnreachable") {
        badAddress := "badAddress:50051"
        c := mustCreateClient(badAddress)
        paymentService = pb.NewPaymentServiceClient(c)
    }

    req := &pb.ChargeRequest{
        Amount: amount,
        CreditCard: card,
    }

    resp, err := paymentService.Charge(ctx, req)
    if err != nil {
        return "", fmt.Errorf("charge card error: %w", err)
    }

    return resp.TransactionId, nil
}
```

### 4. Kafka Event Publishing

**Method:** `sendToPostProcessor`
**File:** `main.go:611-675`

Publishes order completion events to Kafka with full distributed tracing:

```go
func (cs *checkout) sendToPostProcessor(ctx context.Context, result *pb.OrderResult) {
    // Marshal order to protobuf
    message, err := proto.Marshal(result)

    msg := sarama.ProducerMessage{
        Topic: kafka.Topic,  // "orders"
        Value: sarama.ByteEncoder(message),
    }

    // Create producer span with trace context injection
    span := createProducerSpan(ctx, &msg)
    defer span.End()

    startTime := time.Now()

    // Async send
    select {
    case cs.KafkaProducerClient.Input() <- &msg:
        select {
        case successMsg := <-cs.KafkaProducerClient.Successes():
            span.SetAttributes(
                attribute.Bool("messaging.kafka.producer.success", true),
                attribute.Int("messaging.kafka.producer.duration_ms",
                    int(time.Since(startTime).Milliseconds())),
                semconv.MessagingKafkaMessageOffset(int(successMsg.Offset)),
            )
            logger.Info(fmt.Sprintf("Successful to write message. offset: %v", successMsg.Offset))

        case errMsg := <-cs.KafkaProducerClient.Errors():
            span.SetAttributes(
                attribute.Bool("messaging.kafka.producer.success", false),
            )
            span.SetStatus(otelcodes.Error, errMsg.Err.Error())
            logger.Error(fmt.Sprintf("Failed to write message: %v", errMsg.Err))
        }
    }
}
```

**Trace Context Injection:**
**File:** `main.go:677-701`

```go
func createProducerSpan(ctx context.Context, msg *sarama.ProducerMessage) trace.Span {
    spanContext, span := tracer.Start(
        ctx,
        fmt.Sprintf("%s publish", msg.Topic),
        trace.WithSpanKind(trace.SpanKindProducer),
        trace.WithAttributes(
            semconv.PeerService("kafka"),
            semconv.NetworkTransportTCP,
            semconv.MessagingSystemKafka,
            semconv.MessagingDestinationName(msg.Topic),
            semconv.MessagingOperationPublish,
            semconv.MessagingKafkaDestinationPartition(int(msg.Partition)),
        ),
    )

    // Inject W3C trace context into Kafka message headers
    carrier := propagation.MapCarrier{}
    propagator := otel.GetTextMapPropagator()
    propagator.Inject(spanContext, carrier)

    for key, value := range carrier {
        msg.Headers = append(msg.Headers,
            sarama.RecordHeader{Key: []byte(key), Value: []byte(value)})
    }

    return span
}
```

---

## Role in the Ecosystem

### Upstream Dependencies
- **Frontend Service** - Initiates checkout process
- **Cart Service** (gRPC) - Retrieves and clears cart
- **Product Catalog Service** (gRPC) - Fetches product details
- **Currency Service** (gRPC) - Converts prices
- **Payment Service** (gRPC) - Processes payment
- **Shipping Service** (HTTP) - Quotes and ships orders
- **Email Service** (HTTP) - Sends confirmations
- **Kafka Broker** - Publishes order events
- **Flagd Service** - Feature flag evaluation
- **OpenTelemetry Collector** - Telemetry aggregation

### Downstream Consumers
- **Accounting Service** - Consumes order events from Kafka

### Service Interaction Flow

```
┌──────────────┐
│   Frontend   │
│   Service    │
└──────────────┘
       │
       │ gRPC: PlaceOrder(user, cart, address, card)
       │ (W3C Trace Context + Baggage)
       ▼
┌──────────────────────────────────────────────────────┐
│              Checkout Service                        │
│                                                      │
│  1. Get Cart ──────────▶ Cart Service (gRPC)       │
│                                                      │
│  2. Get Products ──────▶ Product Catalog (gRPC)    │
│                                                      │
│  3. Quote Shipping ────▶ Shipping Service (HTTP)   │
│                                                      │
│  4. Convert Currency ──▶ Currency Service (gRPC)   │
│                                                      │
│  5. Charge Card ───────▶ Payment Service (gRPC)    │
│                                                      │
│  6. Ship Order ────────▶ Shipping Service (HTTP)   │
│                                                      │
│  7. Empty Cart ────────▶ Cart Service (gRPC)       │
│                                                      │
│  8. Send Email ────────▶ Email Service (HTTP)      │
│                                                      │
│  9. Publish Event ─────▶ Kafka (orders topic)      │
│                         (W3C Trace Context)         │
└──────────────────────────────────────────────────────┘
       │                              │
       │                              ▼
       │                    ┌──────────────────┐
       │                    │ Accounting Svc   │
       │                    │ (Kafka Consumer) │
       │                    └──────────────────┘
       │
       │ OTLP (Traces, Metrics, Logs)
       ▼
┌──────────────┐
│     OTel     │
│  Collector   │
└──────────────┘
```

**Critical Path:**
1. Frontend → Checkout (gRPC)
2. Checkout → Cart (gRPC)
3. Checkout → Product Catalog (gRPC)
4. Checkout → Shipping (HTTP)
5. Checkout → Currency (gRPC)
6. Checkout → Payment (gRPC) **[Blocking]**
7. Checkout → Shipping (HTTP) **[Blocking]**
8. Checkout → Cart (gRPC)
9. Checkout → Email (HTTP) [Non-blocking]
10. Checkout → Kafka [Async]

---

## High-Level API/Interface

### gRPC Service Definition
**Proto File:** `pb/demo.proto:224-239`

```protobuf
service CheckoutService {
    rpc PlaceOrder(PlaceOrderRequest) returns (PlaceOrderResponse) {}
}

message PlaceOrderRequest {
    string user_id = 1;
    string user_currency = 2;
    Address address = 3;
    string email = 5;
    CreditCardInfo credit_card = 6;
}

message PlaceOrderResponse {
    OrderResult order = 1;
}

message OrderResult {
    string order_id = 1;
    string shipping_tracking_id = 2;
    Money shipping_cost = 3;
    Address shipping_address = 4;
    repeated OrderItem items = 5;
}
```

### Incoming Calls

**Port:** 5050 (gRPC)
**Method:** `PlaceOrder`

**Example Request:**
```json
{
  "user_id": "user-123",
  "user_currency": "USD",
  "address": {
    "street_address": "123 Main St",
    "city": "San Francisco",
    "state": "CA",
    "country": "US",
    "zip_code": "94102"
  },
  "email": "user@example.com",
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
  "order": {
    "order_id": "a1b2c3d4-e5f6-4g7h-8i9j-0k1l2m3n4o5p",
    "shipping_tracking_id": "ship-789xyz",
    "shipping_cost": {
      "currency_code": "USD",
      "units": 8,
      "nanos": 990000000
    },
    "shipping_address": { ... },
    "items": [
      {
        "item": {
          "product_id": "OLJCESPC7Z",
          "quantity": 1
        },
        "cost": {
          "currency_code": "USD",
          "units": 64,
          "nanos": 990000000
        }
      }
    ]
  }
}
```

### Outgoing Calls

#### gRPC Calls
| Service | Method | Purpose |
|---------|--------|---------|
| Cart | `GetCart(userId)` | Retrieve cart items |
| Cart | `EmptyCart(userId)` | Clear cart after order |
| Product Catalog | `GetProduct(id)` | Fetch product details |
| Currency | `Convert(from, to, amount)` | Convert currency |
| Payment | `Charge(amount, card)` | Process payment |

#### HTTP Calls
| Service | Endpoint | Method | Purpose |
|---------|----------|--------|---------|
| Shipping | `/get-quote` | POST | Get shipping quote |
| Shipping | `/ship-order` | POST | Ship order |
| Email | `/send_order_confirmation` | POST | Send confirmation |

#### Kafka Publishing
| Topic | Message Type | Purpose |
|-------|--------------|---------|
| `orders` | `OrderResult` (Protobuf) | Order completion event |

---

## OpenTelemetry Implementation Details

### Auto-Instrumentation Setup

#### 1. Tracer Provider Initialization
**File:** `main.go:87-101`

```go
func initTracerProvider() *sdktrace.TracerProvider {
    ctx := context.Background()

    // Create OTLP gRPC exporter
    exporter, err := otlptracegrpc.New(ctx)
    if err != nil {
        logger.Error(fmt.Sprintf("new otlp trace grpc exporter failed: %v", err))
    }

    // Create tracer provider with batch span processor
    tp := sdktrace.NewTracerProvider(
        sdktrace.WithBatcher(exporter),
        sdktrace.WithResource(initResource()),
    )

    // Set as global tracer provider
    otel.SetTracerProvider(tp)

    // Configure propagators for W3C Trace Context and Baggage
    otel.SetTextMapPropagator(propagation.NewCompositeTextMapPropagator(
        propagation.TraceContext{},  // W3C Trace Context
        propagation.Baggage{},        // W3C Baggage
    ))

    return tp
}
```

**Key Configuration:**
- **Exporter:** OTLP over gRPC
- **Processor:** Batch span processor (efficient for high throughput)
- **Propagators:** W3C Trace Context + W3C Baggage
- **Resource:** Auto-detected OS, process, container, host metadata

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

**Metrics Collection:**
- Periodic export (default: 60 seconds)
- Cumulative temporality (configured via `OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE`)
- Runtime metrics via `go.opentelemetry.io/contrib/instrumentation/runtime`

#### 3. Logger Provider Initialization
**File:** `main.go:119-133`

```go
func initLoggerProvider() *sdklog.LoggerProvider {
    ctx := context.Background()

    // Create OTLP gRPC exporter for logs
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

**Structured Logging:**
```go
logger = otelslog.NewLogger("checkout")
slog.SetDefault(logger)

// Usage
logger.LogAttrs(
    ctx,
    slog.LevelInfo, "order placed",
    slog.String("app.order.id", orderID.String()),
    slog.Float64("app.order.amount", totalPriceFloat),
)
```

**Log Correlation:**
- Logs automatically include `trace_id` and `span_id`
- Context-aware logging bridges slog to OTel

#### 4. Resource Configuration
**File:** `main.go:70-85`

```go
func initResource() *sdkresource.Resource {
    initResourcesOnce.Do(func() {
        extraResources, _ := sdkresource.New(
            context.Background(),
            sdkresource.WithOS(),         // OS name, type, version
            sdkresource.WithProcess(),    // Process ID, executable name, runtime
            sdkresource.WithContainer(),  // Container ID
            sdkresource.WithHost(),       // Host name, architecture
        )
        resource, _ = sdkresource.Merge(
            sdkresource.Default(),
            extraResources,
        )
    })
    return resource
}
```

**Resource Attributes:**
- `service.name`: `checkout` (from `OTEL_SERVICE_NAME`)
- `service.namespace`: `opentelemetry-demo` (from `OTEL_RESOURCE_ATTRIBUTES`)
- `service.version`: Image version (from `OTEL_RESOURCE_ATTRIBUTES`)
- `os.type`, `os.description`: Auto-detected
- `process.pid`, `process.executable.name`: Auto-detected
- `container.id`: Auto-detected
- `host.name`, `host.arch`: Auto-detected

#### 5. Environment Variables
**File:** `docker-compose.yml` (checkout service section)

```yaml
environment:
  - CHECKOUT_PORT=5050
  - CART_ADDR=cart:8080
  - CURRENCY_ADDR=currency:8080
  - EMAIL_ADDR=http://email:6060
  - PAYMENT_ADDR=payment:50051
  - PRODUCT_CATALOG_ADDR=product-catalog:3550
  - SHIPPING_ADDR=http://shipping:50050
  - KAFKA_ADDR=kafka:9092
  - FLAGD_HOST=flagd
  - FLAGD_PORT=8013
  - OTEL_SERVICE_NAME=checkout
  - OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
  - OTEL_RESOURCE_ATTRIBUTES=service.namespace=opentelemetry-demo,service.version=2.1.3
  - OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=cumulative
```

### Manual Instrumentation

#### 1. gRPC Server Instrumentation
**File:** `main.go:248`

```go
var srv = grpc.NewServer(
    grpc.StatsHandler(otelgrpc.NewServerHandler()),
)
```

**Auto-captures:**
- Server spans for incoming gRPC calls
- RPC method, status code, error messages
- Request and response sizes
- Trace context extraction from metadata

#### 2. gRPC Client Instrumentation
**File:** `main.go:445`

```go
func mustCreateClient(svcAddr string) *grpc.ClientConn {
    c, err := grpc.NewClient(svcAddr,
        grpc.WithTransportCredentials(insecure.NewCredentials()),
        grpc.WithStatsHandler(otelgrpc.NewClientHandler()),
    )
    if err != nil {
        logger.Error(fmt.Sprintf("could not connect to %s: %v", svcAddr, err))
        panic(err)
    }
    return c
}
```

**Auto-captures:**
- Client spans for outgoing gRPC calls
- Target service, method, status
- Trace context injection into metadata

#### 3. HTTP Client Instrumentation
**File:** `main.go:463,561,583`

```go
// All HTTP calls wrapped with otelhttp
resp, err := otelhttp.Post(ctx, cs.shippingSvcAddr+"/get-quote",
                           "application/json", bytes.NewBuffer(quotePayload))

resp, err := otelhttp.Post(ctx, cs.emailSvcAddr+"/send_order_confirmation",
                           "application/json", bytes.NewBuffer(emailPayload))

resp, err := otelhttp.Post(ctx, cs.shippingSvcAddr+"/ship-order",
                           "application/json", bytes.NewBuffer(shipPayload))
```

**Auto-captures:**
- HTTP client spans
- URL, method, status code
- Request and response sizes
- Trace context injection via HTTP headers

#### 4. Custom Span Creation
**File:** `main.go:403-404`

```go
func (cs *checkout) prepareOrderItemsAndShippingQuoteFromCart(ctx context.Context, userID, userCurrency string, address *pb.Address) (orderPrep, error) {
    ctx, span := tracer.Start(ctx, "prepareOrderItemsAndShippingQuoteFromCart")
    defer span.End()

    // Business logic...

    span.SetAttributes(
        attribute.Float64("app.shipping.amount", shippingCostFloat),
        attribute.Int("app.cart.items.count", int(totalCart)),
        attribute.Int("app.order.items.count", len(orderItems)),
    )

    return out, nil
}
```

#### 5. Span Attributes - Business Context
**File:** `main.go:290-377`

```go
span := trace.SpanFromContext(ctx)

// User context
span.SetAttributes(
    attribute.String("app.user.id", req.UserId),
    attribute.String("app.user.currency", req.UserCurrency),
)

// Order attributes
span.SetAttributes(
    attribute.String("app.order.id", orderID.String()),
    attribute.Float64("app.shipping.amount", shippingCostFloat),
    attribute.Float64("app.order.amount", totalPriceFloat),
    attribute.Int("app.order.items.count", len(prep.orderItems)),
    attribute.String("app.shipping.tracking.id", shippingTrackingID),
)
```

**Custom Attributes:**
| Attribute | Type | Description |
|-----------|------|-------------|
| `app.user.id` | string | User identifier |
| `app.user.currency` | string | Preferred currency |
| `app.order.id` | string | Order UUID |
| `app.order.amount` | float64 | Total order amount |
| `app.order.items.count` | int | Number of items |
| `app.cart.items.count` | int | Cart item count |
| `app.shipping.amount` | float64 | Shipping cost |
| `app.shipping.tracking.id` | string | Tracking ID |
| `app.payment.transaction.id` | string | Payment transaction ID |

#### 6. Span Events - Workflow Milestones
**File:** `main.go:324,334,346`

```go
span.AddEvent("prepared")

span.AddEvent("charged",
    trace.WithAttributes(
        attribute.String("app.payment.transaction.id", txID)))

span.AddEvent("shipped",
    trace.WithAttributes(
        attribute.String("app.shipping.tracking.id", shippingTrackingID)))
```

**Events mark key milestones:**
1. `prepared` - Order items and shipping quote ready
2. `charged` - Payment processed successfully
3. `shipped` - Order shipped with tracking ID

#### 7. Error Handling
**File:** `main.go:295-301`

```go
var err error
defer func() {
    if err != nil {
        span.AddEvent("error", trace.WithAttributes(
            semconv.ExceptionMessageKey.String(err.Error())))
    }
}()
```

**Error Recording:**
- Errors recorded as span events with exception semantics
- Uses OpenTelemetry semantic conventions for exceptions
- Preserves stack traces and error messages

#### 8. Kafka Producer Instrumentation
**File:** `main.go:677-701`

```go
func createProducerSpan(ctx context.Context, msg *sarama.ProducerMessage) trace.Span {
    spanContext, span := tracer.Start(
        ctx,
        fmt.Sprintf("%s publish", msg.Topic),
        trace.WithSpanKind(trace.SpanKindProducer),
        trace.WithAttributes(
            semconv.PeerService("kafka"),
            semconv.NetworkTransportTCP,
            semconv.MessagingSystemKafka,
            semconv.MessagingDestinationName(msg.Topic),
            semconv.MessagingOperationPublish,
            semconv.MessagingKafkaDestinationPartition(int(msg.Partition)),
        ),
    )

    // Inject trace context into Kafka message headers
    carrier := propagation.MapCarrier{}
    propagator := otel.GetTextMapPropagator()
    propagator.Inject(spanContext, carrier)

    for key, value := range carrier {
        msg.Headers = append(msg.Headers,
            sarama.RecordHeader{Key: []byte(key), Value: []byte(value)})
    }

    return span
}
```

**Kafka Instrumentation Details:**
- Span kind: `Producer`
- Semantic conventions: Uses `semconv` for messaging attributes
- Trace context propagation: W3C Trace Context injected into message headers
- Attributes: Topic, partition, offset, success status, duration

#### 9. Runtime Metrics Collection
**File:** `main.go:184`

```go
err := runtime.Start(runtime.WithMinimumReadMemStatsInterval(time.Second))
```

**Metrics Collected:**
- `process.runtime.go.mem.heap_alloc` - Heap memory allocated
- `process.runtime.go.mem.heap_sys` - Heap memory from OS
- `process.runtime.go.gc.count` - GC cycles
- `process.runtime.go.gc.pause_ns` - GC pause time
- `process.runtime.go.goroutines` - Goroutine count

#### 10. Feature Flag Integration
**File:** `main.go:189-195`

```go
provider, err := flagd.NewProvider()
openfeature.SetProvider(provider)
openfeature.AddHooks(otelhooks.NewTracesHook())
```

**OTel Integration:**
```go
func (cs *checkout) isFeatureFlagEnabled(ctx context.Context, featureFlagName string) bool {
    client := openfeature.NewClient("checkout")
    featureEnabled, _ := client.BooleanValue(
        ctx,
        featureFlagName,
        false,
        openfeature.EvaluationContext{},
    )
    return featureEnabled
}
```

**Feature Flags Used:**
- `paymentUnreachable` - Simulates payment service failure
- `kafkaQueueProblems` - Simulates Kafka queue overload

**Observability:**
- Feature flag evaluations create child spans
- Span attributes include flag key, value, reason
- Helps correlate behavior changes with feature flags

### Telemetry Data Flow

```
┌──────────────────────────────────────────────────────────────────┐
│ Checkout Service (Go)                                            │
│                                                                   │
│ ┌─────────────────────────────────────────────────────────────┐  │
│ │ gRPC Server: PlaceOrder()                                   │  │
│ │   [Auto: Server span created]                               │  │
│ │   [Trace context extracted from metadata]                   │  │
│ │         ↓                                                    │  │
│ │ 1. Access current span                                      │  │
│ │    span := trace.SpanFromContext(ctx)                       │  │
│ │    span.SetAttributes(user_id, currency)                    │  │
│ │         ↓                                                    │  │
│ │ 2. Custom span: prepareOrderItemsAndShippingQuoteFromCart() │  │
│ │    ctx, span := tracer.Start(ctx, "prepare...")             │  │
│ │         ↓                                                    │  │
│ │    ├─ gRPC Client: cart.GetCart() ◄─────────────────────────┼──┤
│ │    │  [Auto: Client span with trace context injection]      │  │
│ │    │                                                         │  │
│ │    ├─ gRPC Client: productCatalog.GetProduct() ◄────────────┼──┤
│ │    │  [Auto: Client span]                                   │  │
│ │    │                                                         │  │
│ │    ├─ HTTP Client: POST shipping/get-quote ◄────────────────┼──┤
│ │    │  [Auto: HTTP client span with headers]                 │  │
│ │    │                                                         │  │
│ │    └─ gRPC Client: currency.Convert() ◄─────────────────────┼──┤
│ │       [Auto: Client span]                                   │  │
│ │       span.SetAttributes(shipping_amount, items_count)      │  │
│ │       span.AddEvent("prepared")                             │  │
│ │         ↓                                                    │  │
│ │ 3. gRPC Client: payment.Charge() ◄──────────────────────────┼──┤
│ │    [Auto: Client span]                                      │  │
│ │    span.AddEvent("charged", transaction_id)                 │  │
│ │         ↓                                                    │  │
│ │ 4. HTTP Client: POST shipping/ship-order ◄──────────────────┼──┤
│ │    [Auto: HTTP client span]                                 │  │
│ │    span.AddEvent("shipped", tracking_id)                    │  │
│ │         ↓                                                    │  │
│ │ 5. gRPC Client: cart.EmptyCart() ◄──────────────────────────┼──┤
│ │    [Auto: Client span]                                      │  │
│ │         ↓                                                    │  │
│ │ 6. HTTP Client: POST email/send_order_confirmation ◄────────┼──┤
│ │    [Auto: HTTP client span]                                 │  │
│ │         ↓                                                    │  │
│ │ 7. Kafka Producer: orders topic                             │  │
│ │    span := createProducerSpan(ctx, msg)                     │  │
│ │    [Manual: Producer span with trace context injection]     │  │
│ │    Inject W3C trace context into Kafka headers ◄────────────┼──┤
│ │    span.SetAttributes(kafka attributes)                     │  │
│ │         ↓                                                    │  │
│ │ 8. Structured logging                                       │  │
│ │    logger.LogAttrs(ctx, order_id, amount, ...)              │  │
│ │    [Auto: Logs include trace_id, span_id]                   │  │
│ │         ↓                                                    │  │
│ │ 9. Set final span attributes                                │  │
│ │    span.SetAttributes(order_id, total_amount, ...)          │  │
│ └─────────────────────────────────────────────────────────────┘  │
│                                                                   │
│ [Export via OTLP/gRPC to otel-collector:4317]                    │
│   • Traces (spans with attributes, events)                       │
│   • Metrics (runtime, custom)                                    │
│   • Logs (with trace correlation)                                │
└───────────────────────────────────────────────────────────────────┘
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
frontend: Checkout button clicked
  └─ http.client: POST /api/checkout
      │
      └─ grpc.client: checkout.PlaceOrder() [Frontend Service]
          │ [W3C Trace Context + Baggage propagated]
          │
          └─ grpc.server: checkout.PlaceOrder() [Checkout Service]
              │ span attributes:
              │   - app.user.id: user-123
              │   - app.user.currency: USD
              │
              ├─ prepareOrderItemsAndShippingQuoteFromCart (span)
              │   │
              │   ├─ grpc.client: cart.GetCart()
              │   │   └─ grpc.server: cart.GetCart() [Cart Service]
              │   │       └─ redis.command: HGET user-123 cart
              │   │
              │   ├─ grpc.client: productCatalog.GetProduct()
              │   │   └─ grpc.server: productCatalog.GetProduct() [Product Catalog]
              │   │
              │   ├─ http.client: POST shipping/get-quote
              │   │   └─ http.server: POST /get-quote [Shipping Service]
              │   │
              │   └─ grpc.client: currency.Convert()
              │       └─ grpc.server: currency.Convert() [Currency Service]
              │
              │ event: "prepared"
              │
              ├─ grpc.client: payment.Charge()
              │   └─ grpc.server: payment.Charge() [Payment Service]
              │
              │ event: "charged" (transaction_id: xyz-789)
              │
              ├─ http.client: POST shipping/ship-order
              │   └─ http.server: POST /ship-order [Shipping Service]
              │
              │ event: "shipped" (tracking_id: ship-456)
              │
              ├─ grpc.client: cart.EmptyCart()
              │   └─ grpc.server: cart.EmptyCart() [Cart Service]
              │
              ├─ http.client: POST email/send_order_confirmation
              │   └─ http.server: POST /send_order_confirmation [Email Service]
              │
              └─ orders publish (span, kind: producer)
                  │ Kafka headers:
                  │   - traceparent: 00-{trace_id}-{span_id}-01
                  │   - baggage: session.id=abc-123
                  │
                  └─ kafka.consume: orders [Accounting Service]
                      └─ order-consumed (span)
                          └─ entity_framework: SaveChanges()
                              └─ postgres.query: INSERT INTO orders...
```

### Observability Outputs

#### Traces
**Exporter:** OTLP/gRPC
**Endpoint:** `http://otel-collector:4317/v1/traces`
**Backend:** Jaeger (via OTel Collector)

**Span Types Generated:**
1. **gRPC Server:** Incoming `PlaceOrder` call
2. **gRPC Clients:** Outgoing calls to cart, product-catalog, currency, payment
3. **HTTP Clients:** Outgoing calls to shipping, email
4. **Custom Spans:** `prepareOrderItemsAndShippingQuoteFromCart`
5. **Kafka Producer:** Message publish to `orders` topic

**Span Attributes:**
- User context (user_id, currency)
- Order details (order_id, amount, item count)
- Payment (transaction_id)
- Shipping (tracking_id, amount)
- Kafka (topic, partition, offset, success)

**Span Events:**
- `prepared` - Order ready for payment
- `charged` - Payment successful
- `shipped` - Order shipped
- `error` - Errors with exception details

#### Metrics
**Exporter:** OTLP/gRPC
**Endpoint:** `http://otel-collector:4317/v1/metrics`
**Backend:** Prometheus (via OTel Collector)

**Custom Metrics:** None (relies on auto-instrumentation)

**Auto-collected Metrics:**
- **gRPC:** Request duration, request count, response sizes
- **HTTP:** Request duration, request count, response sizes
- **Runtime:** Heap allocation, GC stats, goroutine count
- **Process:** CPU usage, memory usage

#### Logs
**Exporter:** OTLP/gRPC
**Endpoint:** `http://otel-collector:4317/v1/logs`
**Format:** Structured JSON with trace correlation

**Log Features:**
- Automatic trace context (`trace_id`, `span_id`)
- Structured attributes (user_id, order_id, amounts)
- Context-aware via `logger.LogAttrs(ctx, ...)`

---

## Configuration Reference

### Required Environment Variables
```bash
# Service ports
CHECKOUT_PORT=5050

# Dependent services
CART_ADDR=cart:8080
CURRENCY_ADDR=currency:8080
EMAIL_ADDR=http://email:6060
PAYMENT_ADDR=payment:50051
PRODUCT_CATALOG_ADDR=product-catalog:3550
SHIPPING_ADDR=http://shipping:50050

# Optional services
KAFKA_ADDR=kafka:9092
FLAGD_HOST=flagd
FLAGD_PORT=8013

# OpenTelemetry
OTEL_SERVICE_NAME=checkout
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
OTEL_RESOURCE_ATTRIBUTES=service.namespace=opentelemetry-demo,service.version=2.1.3
OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=cumulative

# Go runtime
GOMEMLIMIT=16MiB
```

---

## Key Files Reference

| File | Purpose |
|------|---------|
| `main.go` | Service implementation, OTel setup, orchestration logic |
| `kafka/producer.go` | Kafka producer with logging bridge |
| `money/money.go` | Money type operations (Sum, Multiply, etc.) |
| `money/money_test.go` | Unit tests for money operations |
| `go.mod` | Go dependencies and versions |
| `Dockerfile` | Multi-stage build with distroless base |
| `README.md` | Build and development instructions |

---

## Dependencies

### Key Go Modules
```go
require (
    github.com/IBM/sarama v1.44.0                     // Kafka client
    github.com/open-feature/flagd/core v0.11.7        // Feature flags
    github.com/open-feature/go-sdk v1.15.0            // OpenFeature SDK
    github.com/open-feature/go-sdk-contrib/hooks/open-telemetry v0.3.6
    go.opentelemetry.io/contrib/bridges/otelslog v0.13.0
    go.opentelemetry.io/contrib/instrumentation/google.golang.org/grpc/otelgrpc v0.63.0
    go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp v0.63.0
    go.opentelemetry.io/contrib/instrumentation/runtime v0.63.0
    go.opentelemetry.io/otel v1.34.0
    go.opentelemetry.io/otel/exporters/otlp/otlplog/otlploggrpc v0.13.0
    go.opentelemetry.io/otel/exporters/otlp/otlpmetric/otlpmetricgrpc v1.34.0
    go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracegrpc v1.34.0
    go.opentelemetry.io/otel/log v0.13.0
    go.opentelemetry.io/otel/sdk v1.34.0
    go.opentelemetry.io/otel/sdk/log v0.13.0
    go.opentelemetry.io/otel/sdk/metric v1.34.0
    google.golang.org/grpc v1.70.0
    google.golang.org/protobuf v1.36.4
)
```

### External Services
- **Cart** (gRPC) - Required
- **Product Catalog** (gRPC) - Required
- **Currency** (gRPC) - Required
- **Payment** (gRPC) - Required
- **Shipping** (HTTP) - Required
- **Email** (HTTP) - Required
- **Kafka** - Optional
- **Flagd** - Optional
- **OpenTelemetry Collector** - Required

---

## Build and Development

### Local Build
```bash
# Build binary
go build -o /go/bin/checkout/

# Run locally (requires all dependencies)
export CHECKOUT_PORT=5050
export CART_ADDR=localhost:8080
# ... set other env vars
./checkout
```

### Docker Build
```bash
# From repository root
docker compose build checkout
docker compose up checkout
```

### Regenerate Protobuf
```bash
make docker-generate-protobuf
```

### Update Dependencies
```bash
go get -u -t ./...
go mod tidy
```

---

## Observability Best Practices Demonstrated

1. **Comprehensive Auto-Instrumentation** - gRPC, HTTP, Kafka all automatically instrumented
2. **Context Propagation** - W3C Trace Context and Baggage propagated across all boundaries
3. **Custom Spans** - Strategic manual spans for business logic (order preparation)
4. **Rich Span Attributes** - Business context (user, order, payment, shipping)
5. **Span Events** - Workflow milestones (prepared, charged, shipped)
6. **Error Recording** - Proper exception handling with semantic conventions
7. **Kafka Instrumentation** - Manual producer spans with trace context injection
8. **Structured Logging** - Context-aware logs with trace correlation
9. **Runtime Metrics** - Go runtime metrics for performance monitoring
10. **Feature Flag Integration** - OpenFeature hooks for observability
11. **Resource Detection** - Auto-discovery of OS, process, container, host metadata
12. **Graceful Shutdown** - Provider flushing on shutdown

This service is the most complex orchestration service in the demo, demonstrating end-to-end distributed tracing across multiple protocols (gRPC, HTTP, Kafka) with comprehensive OpenTelemetry instrumentation in Go.
