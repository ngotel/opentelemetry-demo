# Image Provider Service - Architecture Documentation

## Overview

The Image Provider Service is an Nginx-based static file server that serves product images and other static assets. It demonstrates OpenTelemetry instrumentation using the native Nginx OpenTelemetry module.

**Platform:** Nginx 1.29.0 (Alpine Linux)
**Service Type:** HTTP Static File Server
**Primary Function:** Static Asset Delivery (Images, CSS, JavaScript)

---

## Key Functionality

### Static File Serving

**Root Directory:** `/static`
**Port:** Configurable via `IMAGE_PROVIDER_PORT`

Serves static files including:
- Product images (`.jpg`, `.png`)
- CSS stylesheets
- JavaScript files
- Other web assets

**Features:**
- **Gzip Compression** - Pre-compressed files served automatically
- **MIME Type Detection** - Automatic content-type headers
- **Index Listing Disabled** - Security: `autoindex off`
- **Server Tokens Off** - Security: hides Nginx version

### Health Check Endpoint

**Endpoint:** `GET /status`
**File:** `nginx.conf.template:34-38`

```nginx
location /status {
    stub_status on;
    access_log  on;
    allow all;
}
```

Returns Nginx server statistics:
- Active connections
- Requests handled
- Reading/Writing/Waiting connections

---

## Role in the Ecosystem

### Upstream Dependencies
- **Frontend Service** - Requests static assets (images, CSS, JS)
- **OpenTelemetry Collector** - Telemetry aggregation

### Downstream Consumers
- None (leaf service)

### Service Interactions

```
┌──────────────┐
│   Browser    │
│   (Users)    │
└──────────────┘
       │
       │ HTTP GET: /static/img/products/*.jpg
       │ HTTP GET: /static/css/*.css
       │ HTTP GET: /static/js/*.js
       ▼
┌──────────────┐
│    Image     │
│   Provider   │
│   (Nginx)    │
└──────────────┘
       │
       │ OTLP/gRPC
       ▼
┌──────────────┐
│     OTel     │
│  Collector   │
└──────────────┘
```

---

## High-Level API/Interface

### HTTP Endpoints

**Static File Access**
- **Pattern:** `GET /*`
- **Response:** File contents with appropriate MIME type
- **Caching:** Browser caching enabled
- **Compression:** Gzip if `.gz` file exists

**Health Check**
- **Endpoint:** `GET /status`
- **Response:** Nginx status page (plain text)

---

## OpenTelemetry Implementation Details

### Native Module Instrumentation

#### 1. OpenTelemetry Module Loading
**File:** `nginx.conf.template:1`

```nginx
load_module modules/ngx_otel_module.so;
```

**Module:** `ngx_otel_module.so`
- Native C module built into Nginx
- Automatic HTTP request/response instrumentation
- Zero code changes required
- High performance (compiled C)

#### 2. Tracer Configuration
**File:** `nginx.conf.template:10-16`

```nginx
http {
    otel_exporter {
        endpoint ${OTEL_COLLECTOR_HOST}:${OTEL_COLLECTOR_PORT_GRPC};
    }
    otel_trace on;
    otel_trace_context propagate;
    otel_service_name ${OTEL_SERVICE_NAME};
    otel_span_name image-provider;

    # ... server configuration ...
}
```

**Configuration Details:**

| Directive | Purpose |
|-----------|---------|
| `otel_exporter.endpoint` | OTLP/gRPC endpoint address |
| `otel_trace on` | Enable tracing for all requests |
| `otel_trace_context propagate` | Extract and inject W3C Trace Context headers |
| `otel_service_name` | Service identifier (set via env var) |
| `otel_span_name` | Default span name for operations |

**What Gets Auto-Instrumented:**
- All incoming HTTP requests
- Response status codes
- Request method, URL, headers
- Response time/duration
- Trace context propagation (W3C Trace Context)

#### 3. Base Image with OTel Support
**File:** `Dockerfile:4`

```dockerfile
FROM nginxinc/nginx-unprivileged:1.29.0-alpine3.22-otel
```

**Base Image:** `nginxinc/nginx-unprivileged:1.29.0-alpine3.22-otel`
- Official Nginx image with OpenTelemetry module pre-built
- Alpine Linux 3.22 base
- Runs as non-root user (UID 101)
- OpenTelemetry module already compiled and included

#### 4. Configuration Templating
**File:** `Dockerfile:16`

```bash
CMD ["/bin/sh" , "-c" , "envsubst '$OTEL_COLLECTOR_HOST $IMAGE_PROVIDER_PORT $OTEL_COLLECTOR_PORT_GRPC $OTEL_SERVICE_NAME' < /nginx.conf.template > /etc/nginx/nginx.conf && cat  /etc/nginx/nginx.conf && exec nginx -g 'daemon off;'"]
```

**Runtime Configuration:**
1. `envsubst` replaces environment variables in template
2. Generates final `/etc/nginx/nginx.conf`
3. Outputs config to logs (for debugging)
4. Starts Nginx in foreground mode

### Environment Variables

```yaml
environment:
  - IMAGE_PROVIDER_PORT=8082
  - OTEL_COLLECTOR_HOST=otel-collector
  - OTEL_COLLECTOR_PORT_GRPC=4317
  - OTEL_SERVICE_NAME=image-provider
```

### Telemetry Data Flow

```
┌─────────────────────────────────────────────────┐
│ Image Provider (Nginx + OTel Module)           │
│                                                  │
│ ┌────────────────────────────────────────────┐  │
│ │ Nginx HTTP Server                          │  │
│ │   ↓                                        │  │
│ │ [Request Received]                         │  │
│ │   ↓                                        │  │
│ │ ngx_otel_module.so (Native C)             │  │
│ │   ├─ Extract W3C Trace Context from headers│  │
│ │   ├─ Create span: "image-provider"        │  │
│ │   ├─ Set attributes:                      │  │
│ │   │   • http.method                       │  │
│ │   │   • http.url                          │  │
│ │   │   • http.status_code                  │  │
│ │   │   • http.request_content_length       │  │
│ │   │   • http.response_content_length      │  │
│ │   ├─ Serve static file                    │  │
│ │   ├─ End span (with duration)             │  │
│ │   └─ Inject trace context into response   │  │
│ │   ↓                                        │  │
│ │ [Export via OTLP/gRPC]                    │  │
│ │   • Traces (request spans)                │  │
│ └────────────────────────────────────────────┘  │
│                                                  │
│   ↓ OTLP/gRPC                                   │
│                                                  │
│ otel-collector:4317                             │
└──────────────────────────────────────────────────┘
```

### Observability Outputs

#### Traces
**Exporter:** OTLP/gRPC (native module)
**Endpoint:** `${OTEL_COLLECTOR_HOST}:${OTEL_COLLECTOR_PORT_GRPC}`

**Span Details:**
- **Span Name:** `image-provider` (configurable)
- **Span Kind:** Server

**Attributes (Auto-collected):**
- `http.method` - GET, POST, etc.
- `http.target` - Request URI
- `http.scheme` - http or https
- `http.status_code` - Response status
- `http.request_content_length` - Request body size
- `http.response_content_length` - Response size
- `http.user_agent` - Client user agent
- `http.flavor` - HTTP version (1.1, 2.0)

**Trace Context Propagation:**
- **Inbound:** Extracts `traceparent` and `baggage` headers
- **Outbound:** Injects trace context into response (for debugging)

---

## Configuration Reference

### Environment Variables
```bash
IMAGE_PROVIDER_PORT=8082
OTEL_COLLECTOR_HOST=otel-collector
OTEL_COLLECTOR_PORT_GRPC=4317
OTEL_SERVICE_NAME=image-provider
```

### Nginx Directives

| Directive | Value | Description |
|-----------|-------|-------------|
| `load_module` | `modules/ngx_otel_module.so` | Load OTel module |
| `otel_trace` | `on` | Enable tracing |
| `otel_trace_context` | `propagate` | Enable context propagation |
| `otel_service_name` | `${OTEL_SERVICE_NAME}` | Service identifier |
| `otel_span_name` | `image-provider` | Default span name |
| `otel_exporter.endpoint` | `${OTEL_COLLECTOR_HOST}:${OTEL_COLLECTOR_PORT_GRPC}` | OTLP endpoint |

---

## Key Files Reference

| File | Purpose |
|------|---------|
| `nginx.conf.template` | Nginx configuration with OTel directives |
| `Dockerfile` | Container build with OTel-enabled Nginx |
| `static/` | Static files served by Nginx |

---

## Build and Development

### Docker Build
```bash
docker compose build image-provider
docker compose up image-provider
```

### Testing
```bash
# Access static file
curl http://localhost:8082/static/img/products/binoculars.jpg

# Check health
curl http://localhost:8082/status
```

### Access
- **Static Files:** http://localhost:8082/*
- **Health Check:** http://localhost:8082/status

---

## Observability Best Practices Demonstrated

1. **Native Instrumentation** - No application code changes required
2. **Zero Overhead** - Compiled C module (minimal performance impact)
3. **Automatic Trace Propagation** - W3C Trace Context support
4. **Infrastructure Observability** - Even static file servers can be traced
5. **Standardized Export** - OTLP/gRPC protocol
6. **Configuration as Code** - Environment-based configuration
7. **Security Hardened** - Runs as non-root user
8. **Production Ready** - Official Nginx image with OTel module

This service demonstrates how infrastructure components like web servers can be instrumented for observability using native modules without custom code.
