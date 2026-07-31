# 门店补货 Frontend

React + TypeScript + Ant Design + Recharts demo UI for store replenishment.

## Run locally

```powershell
npm install
npm run dev
```

The app runs on port `3000`. API base defaults to `http://localhost:8000`; override with:

```powershell
$env:VITE_API_BASE="http://localhost:8000"
npm run dev
```

## Build

```powershell
npm run build
```

## Docker

```powershell
docker build -t store-replenishment-frontend .
docker run --rm -p 3000:80 store-replenishment-frontend
```

In local development, ordinary replenishment reads can fall back to mock data. Attribution, review, readiness, and submission APIs never return mock success.
