# Swedbank Spend Analyzer

Personal finance dashboard for importing bank transactions through Enable Banking,
categorizing expenses, planning monthly budgets, and exposing the same data through
an OAuth-protected MCP endpoint.

Production service: [finance.passi.cloud](https://finance.passi.cloud/)

## Development

Requirements: Docker Compose and Node.js 24 or newer.

```bash
cp .env.example .env
docker compose up --build
npm ci
npm test
```

Never commit `.env`, bank certificates, private keys, access tokens, production
exports, or generated `graphify-out` data. See [SECURITY.md](SECURITY.md) for
vulnerability reporting.

## Documentation

- [Deployment](DEPLOYMENT.md)
- [MCP integration](MCP.md)
- [Network security](SECURITY-NETWORK.md)
- [Container security](SECURITY-IMAGES.md)

## License

No license has been selected yet. Public availability does not grant permission to
copy, modify, or redistribute the code.
