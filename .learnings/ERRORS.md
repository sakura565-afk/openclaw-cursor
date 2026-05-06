# Error Learning Patterns

This catalog is parsed by `scripts/error_learning.py`. Keep each error in this shape.

## Timeout contacting upstream API
- category: timeout
- patterns: timeout, timed out, ETIMEDOUT, deadline exceeded, request took too long
- keywords: api, upstream, http, latency, retry
- tags: network, reliability
- solution: Increase timeout budget, add retries with backoff, and check upstream availability.

## DNS resolution failure
- category: network
- patterns: ENOTFOUND, getaddrinfo, Name or service not known, Temporary failure in name resolution
- keywords: dns, resolve, hostname, network
- tags: networking
- solution: Validate DNS records and resolver settings; use fallback endpoint if available.

## TLS handshake or certificate failure
- category: ssl
- patterns: certificate verify failed, TLS handshake failed, x509, self signed certificate
- keywords: tls, cert, x509, ssl
- tags: security, networking
- solution: Fix trust chain and certificate validity; verify hostname and CA bundle.

## Authentication or token expired
- category: auth
- patterns: unauthorized, invalid token, token expired, 401, permission denied
- keywords: auth, token, login, credentials
- tags: security
- solution: Refresh credentials, verify scopes/roles, and rotate secrets if needed.

## Rate limit exceeded
- category: rate_limit
- patterns: rate limit exceeded, too many requests, 429, quota exceeded
- keywords: throttle, quota, requests
- tags: reliability
- solution: Add request throttling, jittered backoff, and cache repeated calls.

## Resource exhausted memory
- category: resource
- patterns: out of memory, MemoryError, cannot allocate memory, heap space
- keywords: memory, heap, allocation
- tags: performance
- solution: Reduce batch size, stream data, and profile memory hotspots.

## Disk full or inode exhaustion
- category: resource
- patterns: no space left on device, disk quota exceeded, ENOSPC
- keywords: disk, storage, inode
- tags: operations
- solution: Free disk, rotate logs, and monitor storage usage.

## Database connection refused or reset
- category: database
- patterns: connection refused, connection reset by peer, could not connect to server, ECONNRESET
- keywords: database, postgres, mysql, connection
- tags: backend
- solution: Validate DB host/port, pool sizing, and server health.

## Schema mismatch or missing column
- category: database
- patterns: relation does not exist, column does not exist, migration failed, schema mismatch
- keywords: migration, schema, column
- tags: backend
- solution: Apply migrations in order and verify app/DB schema compatibility.

## Import or module not found
- category: dependency
- patterns: ModuleNotFoundError, ImportError, cannot import name, package not found
- keywords: import, dependency, package
- tags: build
- solution: Install missing dependencies and align runtime environment.

## Build or compilation failure
- category: build
- patterns: failed to compile, compilation failed, linker error, syntax error
- keywords: build, compile, linker
- tags: ci
- solution: Fix compile errors, pin toolchain versions, and verify build flags.

## File path missing or inaccessible
- category: filesystem
- patterns: FileNotFoundError, no such file or directory, permission denied, EACCES
- keywords: file, path, permission
- tags: io
- solution: Validate path existence and permissions before access.
