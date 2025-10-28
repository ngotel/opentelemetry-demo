# Currency Service - Architecture Documentation

## Overview

The Currency Service is a gRPC-based microservice written in C++ that provides currency conversion functionality. It demonstrates OpenTelemetry C++ SDK instrumentation with manual span creation, metrics, and logging.

**Language:** C++ (C++17)
**Framework:** gRPC with Protocol Buffers
**Service Type:** gRPC Server
**Primary Function:** Currency Conversion and Supported Currencies Listing

---

## Key Functionality

### 1. Get Supported Currencies

Returns a list of all supported currency codes.

**Method:** `GetSupportedCurrencies(Empty) → GetSupportedCurrenciesResponse`
**File:** `src/server.cpp:105-142`

```cpp
Status GetSupportedCurrencies(ServerContext *context,
                               const Empty *request,
                               GetSupportedCurrenciesResponse *response) override {
    // Create span with trace context extraction
    StartSpanOptions options;
    options.kind = SpanKind::kServer;
    GrpcServerCarrier carrier(context);

    auto prop = context::propagation::GlobalTextMapPropagator::GetGlobalPropagator();
    auto current_ctx = context::RuntimeContext::GetCurrent();
    auto new_context = prop->Extract(carrier, current_ctx);
    options.parent = GetSpan(new_context)->GetContext();

    std::string span_name = "Currency/GetSupportedCurrencies";
    auto span = get_tracer("currency")->StartSpan(span_name,
        {{semconv::rpc::kRpcSystem, "grpc"},
         {semconv::rpc::kRpcService, "oteldemo.CurrencyService"},
         {semconv::rpc::kRpcMethod, "GetSupportedCurrencies"},
         {semconv::rpc::kRpcGrpcStatusCode, semconv::rpc::RpcGrpcStatusCodeValues::kOk}},
        options);
    auto scope = get_tracer("currency")->WithActiveSpan(span);

    span->AddEvent("Processing supported currencies request");

    // Return 33 supported currency codes
    for (auto &i : currency_conversion) {
        response->add_currency_codes(i.first);
    }

    logger->Info(std::string(__func__) + " successful");
    span->AddEvent("Currencies fetched, response sent back");
    span->SetStatus(StatusCode::kOk);
    span->End();
    return Status::OK;
}
```

**Supported Currencies (33 total):**
EUR, USD, JPY, BGN, CZK, DKK, GBP, HUF, PLN, RON, SEK, CHF, ISK, NOK, HRK, RUB, TRY, AUD, BRL, CAD, CNY, HKD, IDR, ILS, INR, KRW, MXN, MYR, NZD, PHP, SGD, THB, ZAR

### 2. Currency Conversion

Converts an amount from one currency to another using hardcoded exchange rates.

**Method:** `Convert(CurrencyConversionRequest) → Money`
**File:** `src/server.cpp:166-229`

```cpp
Status Convert(ServerContext *context,
               const CurrencyConversionRequest *request,
               Money *response) override {
    auto span = get_tracer("currency")->StartSpan("Currency/Convert",
        {{semconv::rpc::kRpcSystem, "grpc"},
         {semconv::rpc::kRpcService, "oteldemo.CurrencyService"},
         {semconv::rpc::kRpcMethod, "Convert"},
         {semconv::rpc::kRpcGrpcStatusCode, semconv::rpc::RpcGrpcStatusCodeValues::kOk}},
        options);

    span->AddEvent("Processing currency conversion request");

    try {
        Money from = request->from();
        std::string to_code = request->to_code();
        std::string from_code = from.currency_code();

        // Convert to EUR first, then to target currency
        double rate = currency_conversion[from_code];
        double one_euro = getDouble(from) / rate;
        double to_rate = currency_conversion[to_code];
        double final = one_euro * to_rate;

        // Build response
        Money *money = new Money();
        getUnitsAndNanos(money, final);
        money->set_currency_code(to_code);

        *response = *money;

        // Add custom attributes
        span->SetAttribute("app.currency.conversion.from", from_code);
        span->SetAttribute("app.currency.conversion.to", to_code);

        // Record metric
        CurrencyCounter(to_code);

        logger->Info(std::string(__func__) + " conversion successful");
        span->AddEvent("Conversion successful, response sent back");
        span->SetStatus(StatusCode::kOk);
    } catch (...) {
        logger->Error(std::string(__func__) + " conversion failure");
        span->AddEvent("Conversion failed");
        span->SetStatus(StatusCode::kError);
    }

    span->End();
    return Status::OK;
}
```

**Conversion Algorithm:**
1. Get source currency rate against EUR
2. Convert source amount to EUR: `euros = amount / source_rate`
3. Get target currency rate against EUR
4. Convert EUR to target currency: `result = euros * target_rate`

**Exchange Rates:**
**File:** `src/server.cpp:48-83`

All rates are relative to EUR (1.0):
- EUR: 1.0 (base)
- USD: 1.1305
- JPY: 126.40
- GBP: 0.85970
- And 29 more currencies...

### 3. Money Type Utilities

**Convert Money to Double:**
```cpp
double getDouble(const Money &money) {
    return money.units() + (static_cast<double>(money.nanos()) / 1000000000);
}
```

**Convert Double to Money:**
```cpp
void getUnitsAndNanos(Money *money, double value) {
    money->set_units(static_cast<int64_t>(value));
    money->set_nanos(
        static_cast<int32_t>((value - static_cast<double>(money->units())) * 1000000000));
}
```

**Money Message (Proto):**
```protobuf
message Money {
    string currency_code = 1;  // 3-letter ISO 4217 code
    int64 units = 2;           // Whole currency units
    int32 nanos = 3;           // Fractional units (1/1,000,000,000)
}
```

---

## Role in the Ecosystem

### Upstream Dependencies
- **Checkout Service** - Primary caller for currency conversion
- **Frontend Service** - Displays supported currencies
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
       │ gRPC: Convert(from, to_code)
       │ (W3C Trace Context via metadata)
       ▼
┌──────────────┐
│   Currency   │
│   Service    │
└──────────────┘
       │
       │ OTLP (Traces, Metrics, Logs)
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
service CurrencyService {
    rpc GetSupportedCurrencies(Empty) returns (GetSupportedCurrenciesResponse) {}
    rpc Convert(CurrencyConversionRequest) returns (Money) {}
}

message CurrencyConversionRequest {
    Money from = 1;
    string to_code = 2;  // 3-letter currency code
}

message GetSupportedCurrenciesResponse {
    repeated string currency_codes = 1;
}
```

### Incoming Calls

**Port:** 7000 (configurable via `CURRENCY_PORT`)

**Method 1: GetSupportedCurrencies**
- Request: Empty
- Response: List of 33 currency codes

**Method 2: Convert**
- Request: `{ from: { currency_code: "USD", units: 100, nanos: 0 }, to_code: "EUR" }`
- Response: `{ currency_code: "EUR", units: 88, nanos: 468000000 }`

---

## OpenTelemetry Implementation Details

### Manual Instrumentation - All Components

The Currency Service uses fully manual OpenTelemetry instrumentation (no auto-instrumentation in C++).

#### 1. Tracer Provider Initialization
**File:** `src/tracer_common.h:73-93`

```cpp
void initTracer() {
    // Create OTLP gRPC exporter
    auto exporter = opentelemetry::exporter::otlp::OtlpGrpcExporterFactory::Create();

    // Create simple span processor
    auto processor =
        opentelemetry::sdk::trace::SimpleSpanProcessorFactory::Create(std::move(exporter));
    std::vector<std::unique_ptr<opentelemetry::sdk::trace::SpanProcessor>> processors;
    processors.push_back(std::move(processor));

    // Create tracer context and provider
    auto context =
        opentelemetry::sdk::trace::TracerContextFactory::Create(std::move(processors));
    std::shared_ptr<opentelemetry::trace::TracerProvider> provider =
        opentelemetry::sdk::trace::TracerProviderFactory::Create(std::move(context));

    // Set global tracer provider
    opentelemetry::trace::Provider::SetTracerProvider(provider);

    // Set global W3C Trace Context propagator
    opentelemetry::context::propagation::GlobalTextMapPropagator::SetGlobalPropagator(
        opentelemetry::nostd::shared_ptr<opentelemetry::context::propagation::TextMapPropagator>(
            new opentelemetry::trace::propagation::HttpTraceContext()));
}
```

**Key Configuration:**
- **Exporter:** OTLP gRPC (endpoint from `OTEL_EXPORTER_OTLP_ENDPOINT`)
- **Processor:** Simple span processor (synchronous export)
- **Propagator:** W3C Trace Context (HTTP headers format)

#### 2. Span Creation with Context Extraction
**File:** `src/server.cpp:105-126, 166-197`

```cpp
// Extract trace context from gRPC metadata
StartSpanOptions options;
options.kind = SpanKind::kServer;
GrpcServerCarrier carrier(context);

auto prop = context::propagation::GlobalTextMapPropagator::GetGlobalPropagator();
auto current_ctx = context::RuntimeContext::GetCurrent();
auto new_context = prop->Extract(carrier, current_ctx);
options.parent = GetSpan(new_context)->GetContext();

// Create span with semantic conventions
std::string span_name = "Currency/GetSupportedCurrencies";
auto span = get_tracer("currency")->StartSpan(span_name,
    {{semconv::rpc::kRpcSystem, "grpc"},
     {semconv::rpc::kRpcService, "oteldemo.CurrencyService"},
     {semconv::rpc::kRpcMethod, "GetSupportedCurrencies"},
     {semconv::rpc::kRpcGrpcStatusCode, semconv::rpc::RpcGrpcStatusCodeValues::kOk}},
    options);

// Activate span in current context
auto scope = get_tracer("currency")->WithActiveSpan(span);

// Business logic...

// End span
span->End();
```

**Trace Context Propagation:**
- Uses `GrpcServerCarrier` to extract W3C Trace Context from gRPC metadata
- Creates child span linked to parent via `options.parent`
- All downstream calls will be children of this span

#### 3. Propagation Carriers
**File:** `src/tracer_common.h:27-71`

**GrpcClientCarrier** - For client-side context injection:
```cpp
class GrpcClientCarrier : public TextMapCarrier {
public:
    GrpcClientCarrier(ClientContext *context) : context_(context) {}

    nostd::string_view Get(nostd::string_view key) const noexcept override {
        auto it = context_->GetServerMetadata().find(std::string(key).c_str());
        if (it != context_->GetServerMetadata().end()) {
            return it->second.data();
        }
        return "";
    }

    void Set(nostd::string_view key, nostd::string_view value) noexcept override {
        context_->AddMetadata(std::string(key), std::string(value));
    }
};
```

**GrpcServerCarrier** - For server-side context extraction:
```cpp
class GrpcServerCarrier : public TextMapCarrier {
public:
    GrpcServerCarrier(ServerContext *context) : context_(context) {}

    nostd::string_view Get(nostd::string_view key) const noexcept override {
        auto it = context_->client_metadata().find(std::string(key).c_str());
        if (it != context_->client_metadata().end()) {
            return it->second.data();
        }
        return "";
    }

    void Set(nostd::string_view key, nostd::string_view value) noexcept override {
        // Not used in server carrier
    }
};
```

#### 4. Span Attributes and Events
**File:** `src/server.cpp`

**Semantic Conventions (RPC):**
```cpp
{{semconv::rpc::kRpcSystem, "grpc"},
 {semconv::rpc::kRpcService, "oteldemo.CurrencyService"},
 {semconv::rpc::kRpcMethod, "GetSupportedCurrencies"},
 {semconv::rpc::kRpcGrpcStatusCode, semconv::rpc::RpcGrpcStatusCodeValues::kOk}}
```

**Custom Attributes:**
```cpp
span->SetAttribute("app.currency.conversion.from", from_code);
span->SetAttribute("app.currency.conversion.to", to_code);
```

**Span Events:**
```cpp
span->AddEvent("Processing supported currencies request");
span->AddEvent("Currencies fetched, response sent back");
span->AddEvent("Processing currency conversion request");
span->AddEvent("Conversion successful, response sent back");
span->AddEvent("Conversion failed");
```

**Span Status:**
```cpp
span->SetStatus(StatusCode::kOk);    // Success
span->SetStatus(StatusCode::kError);  // Failure
```

#### 5. Meter Provider Initialization
**File:** `src/meter_common.h:19-33`

```cpp
void initMeter() {
    // Build OTLP gRPC MetricExporter
    otlp_exporter::OtlpGrpcMetricExporterOptions otlpOptions;
    auto exporter = otlp_exporter::OtlpGrpcMetricExporterFactory::Create(otlpOptions);

    // Build PeriodicExportingMetricReader
    metric_sdk::PeriodicExportingMetricReaderOptions options;
    std::unique_ptr<metric_sdk::MetricReader> reader{
        new metric_sdk::PeriodicExportingMetricReader(std::move(exporter), options)
    };

    // Create MeterProvider
    auto provider = std::shared_ptr<metrics_api::MeterProvider>(new metric_sdk::MeterProvider());
    auto p = std::static_pointer_cast<metric_sdk::MeterProvider>(provider);
    p->AddMetricReader(std::move(reader));

    // Set global meter provider
    metrics_api::Provider::SetMeterProvider(provider);
}
```

#### 6. Custom Metrics - Counter
**File:** `src/meter_common.h:35-42, src/server.cpp:231-236`

**Counter Initialization:**
```cpp
nostd::unique_ptr<metrics_api::Counter<uint64_t>> initIntCounter(
    std::string name, std::string version) {
    std::string counter_name = name + "_counter";
    auto provider = metrics_api::Provider::GetMeterProvider();
    nostd::shared_ptr<metrics_api::Meter> meter = provider->GetMeter(name, version);
    auto int_counter = meter->CreateUInt64Counter(counter_name);
    return int_counter;
}
```

**Recording Metric:**
```cpp
void CurrencyCounter(const std::string& currency_code) {
    std::map<std::string, std::string> labels = { {"currency_code", currency_code} };
    auto labelkv = common::KeyValueIterableView<decltype(labels)>{ labels };
    currency_counter->Add(1, labelkv);
}
```

**Metric Details:**
- **Name:** `app.currency_counter`
- **Type:** Counter (monotonically increasing)
- **Attributes:** `currency_code` (target currency)
- **Purpose:** Track currency conversion requests by target currency

#### 7. Logger Provider Initialization
**File:** `src/logger_common.h:20-29`

```cpp
void initLogger() {
    // Create OTLP gRPC log exporter
    otlp::OtlpGrpcLogRecordExporterOptions loggerOptions;
    auto exporter = otlp::OtlpGrpcLogRecordExporterFactory::Create(loggerOptions);

    // Create simple log processor
    auto processor = logs_sdk::SimpleLogRecordProcessorFactory::Create(std::move(exporter));
    std::vector<std::unique_ptr<logs_sdk::LogRecordProcessor>> processors;
    processors.push_back(std::move(processor));

    // Create logger context and provider
    auto context = logs_sdk::LoggerContextFactory::Create(std::move(processors));
    std::shared_ptr<logs::LoggerProvider> provider =
        logs_sdk::LoggerProviderFactory::Create(std::move(context));

    // Set global logger provider
    opentelemetry::logs::Provider::SetLoggerProvider(provider);
}
```

#### 8. Structured Logging
**File:** `src/logger_common.h:31-34, src/server.cpp`

**Get Logger:**
```cpp
nostd::shared_ptr<opentelemetry::logs::Logger> getLogger(std::string name) {
    auto provider = logs::Provider::GetLoggerProvider();
    return provider->GetLogger(name + "_logger", name, OPENTELEMETRY_SDK_VERSION);
}
```

**Logging Calls:**
```cpp
logger->Info("GetSupportedCurrencies successful");
logger->Info("Convert conversion successful");
logger->Error("Convert conversion failure");
logger->Info("Overwriting Localhost IP: " + ip);
logger->Info("Currency Server listening on port: " + address);
```

### Telemetry Data Flow

```
┌──────────────────────────────────────────────────────┐
│ Currency Service (C++)                               │
│                                                       │
│ ┌─────────────────────────────────────────────────┐  │
│ │ main()                                          │  │
│ │   ↓                                             │  │
│ │ initTracer()  → OTLP gRPC Exporter             │  │
│ │ initMeter()   → PeriodicExportingMetricReader  │  │
│ │ initLogger()  → OTLP gRPC Log Exporter         │  │
│ │   ↓                                             │  │
│ │ RunServer(port)                                 │  │
│ │   ↓                                             │  │
│ │ ┌───────────────────────────────────────────┐   │  │
│ │ │ GetSupportedCurrencies() or Convert()     │   │  │
│ │ │                                           │   │  │
│ │ │ 1. Extract trace context from gRPC       │   │  │
│ │ │    metadata via GrpcServerCarrier        │   │  │
│ │ │    ↓                                      │   │  │
│ │ │ 2. Create server span                    │   │  │
│ │ │    - Span name: Currency/[method]        │   │  │
│ │ │    - Attributes: RPC semantic conventions│   │  │
│ │ │    - Parent: Extracted from metadata     │   │  │
│ │ │    ↓                                      │   │  │
│ │ │ 3. Activate span scope                   │   │  │
│ │ │    ↓                                      │   │  │
│ │ │ 4. Add event: "Processing request"       │   │  │
│ │ │    ↓                                      │   │  │
│ │ │ 5. Execute business logic                │   │  │
│ │ │    - Get exchange rates                  │   │  │
│ │ │    - Calculate conversion                │   │  │
│ │ │    ↓                                      │   │  │
│ │ │ 6. Add custom attributes                 │   │  │
│ │ │    - app.currency.conversion.from        │   │  │
│ │ │    - app.currency.conversion.to          │   │  │
│ │ │    ↓                                      │   │  │
│ │ │ 7. Record metric                         │   │  │
│ │ │    CurrencyCounter(to_code)              │   │  │
│ │ │    ↓                                      │   │  │
│ │ │ 8. Log success/failure                   │   │  │
│ │ │    logger->Info() or logger->Error()     │   │  │
│ │ │    ↓                                      │   │  │
│ │ │ 9. Add event: "Conversion successful"    │   │  │
│ │ │    ↓                                      │   │  │
│ │ │ 10. Set span status                      │   │  │
│ │ │     span->SetStatus(kOk or kError)       │   │  │
│ │ │    ↓                                      │   │  │
│ │ │ 11. End span                             │   │  │
│ │ │     span->End()                          │   │  │
│ │ └───────────────────────────────────────────┘   │  │
│ │                                                 │  │
│ │ [Export via OTLP/gRPC to otel-collector:4317]  │  │
│ │   • Traces (spans with attributes, events)     │  │
│ │   • Metrics (currency conversion counter)      │  │
│ │   • Logs (Info/Error messages)                 │  │
│ └─────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────┘
                         ↓
              Traces, Metrics, Logs
                         ↓
            ┌──────────────────────┐
            │ OpenTelemetry        │
            │ Collector            │
            │ (otel-collector:4317)│
            └──────────────────────┘
```

### Observability Outputs

#### Traces
**Exporter:** OTLP/gRPC
**Endpoint:** `OTEL_EXPORTER_OTLP_ENDPOINT`
**Backend:** Jaeger (via OTel Collector)

**Span Types:**
- `Currency/GetSupportedCurrencies`
- `Currency/Convert`

**Attributes:**
- RPC semantic conventions (system, service, method, status)
- Custom: `app.currency.conversion.from`, `app.currency.conversion.to`

**Events:**
- "Processing supported currencies request"
- "Currencies fetched, response sent back"
- "Processing currency conversion request"
- "Conversion successful, response sent back"
- "Conversion failed"

#### Metrics
**Exporter:** OTLP/gRPC
**Endpoint:** `OTEL_EXPORTER_OTLP_ENDPOINT`
**Backend:** Prometheus (via OTel Collector)

**Custom Metrics:**
- `app.currency_counter` - Counter tracking conversions by target currency

#### Logs
**Exporter:** OTLP/gRPC
**Endpoint:** `OTEL_EXPORTER_OTLP_ENDPOINT`

**Log Levels:** Info, Error
**Correlation:** Logs can be correlated with traces via OTel Collector

---

## Configuration Reference

### Environment Variables
```bash
CURRENCY_PORT=7000
IPV6_ENABLED=false
VERSION=2.1.3
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=cumulative
OTEL_RESOURCE_ATTRIBUTES=service.namespace=opentelemetry-demo,service.version=2.1.3
OTEL_SERVICE_NAME=currency
```

---

## Key Files Reference

| File | Purpose |
|------|---------|
| `src/server.cpp` | Main service implementation |
| `src/tracer_common.h` | Tracer initialization and carriers |
| `src/meter_common.h` | Meter initialization and counter |
| `src/logger_common.h` | Logger initialization |
| `CMakeLists.txt` | Build configuration |
| `Dockerfile` | Container build |
| `README.md` | Documentation |

---

## Dependencies

### Build Dependencies
```cmake
find_package(Protobuf REQUIRED)
find_package(gRPC CONFIG REQUIRED)
find_package(opentelemetry-cpp CONFIG REQUIRED)
```

### OpenTelemetry Libraries
- `opentelemetry-cpp` SDK (v1.x.x)
- OTLP gRPC exporter
- Trace, Metrics, Logs APIs and SDKs

---

## Build and Development

### Local Build
```bash
mkdir build && cd build
cmake ..
make
./currency 7000
```

### Docker Build
```bash
docker compose build currency
docker compose up currency
```

---

## Observability Best Practices Demonstrated

1. **Manual Instrumentation** - Complete control over span lifecycle and attributes
2. **Context Propagation** - W3C Trace Context extraction from gRPC metadata
3. **Semantic Conventions** - Uses official RPC semantic conventions
4. **Custom Attributes** - Business context (from/to currencies)
5. **Span Events** - Marks processing milestones
6. **Error Handling** - Proper span status for errors
7. **Custom Metrics** - Domain-specific counter
8. **Structured Logging** - Correlated with telemetry
9. **OTLP Export** - Standard protocol for all signals

This service demonstrates production-ready OpenTelemetry instrumentation in C++ with comprehensive manual instrumentation patterns.
