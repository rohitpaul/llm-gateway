# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Comprehensive CI/CD pipeline with linting, testing, and security scanning
- Issue templates for bug reports, feature requests, and questions
- Pull request template with checklist
- Commit message linting with conventional commits
- Automated release workflow with GitHub releases
- Weekly security audit workflow
- Dependabot configuration for automated dependency updates
- Branch protection documentation

## [0.1.0] - 2024-03-XX

### Added
- Virtual API keys with scoping (provider/model filters, token limits)
- Multi-provider support (OpenAI, Anthropic, Gemini, DeepSeek, Groq, Mistral, xAI, OpenRouter, Together, Fireworks, Perplexity)
- OpenAI-compatible `/v1/chat/completions` and `/v1/models` endpoints
- Full SSE streaming support with transparent pass-through
- Web dashboard with real-time stats and request history
- SQLite database with WAL mode
- Per-model usage tracking with cost calculation
- Token caching support (cache_read_tokens tracking)
- Time to first token (TTFT) and tokens per second (TPS) metrics
- Request/response body logging (truncated at 100KB)
- Admin key authentication
- CORS support
- Docker deployment with multi-architecture support (amd64, arm64)
- Configurable data retention (default 7 days)
- SSE live refresh for dashboard

### Security
- SHA-256 hashing for virtual keys
- Admin key required for server startup
- Request/response body truncation to prevent database bloat

[Unreleased]: https://github.com/rohitpaul/llm-gateway/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/rohitpaul/llm-gateway/releases/tag/v0.1.0
