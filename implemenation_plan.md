AutoHeal/
├── apps/
│   ├── backend/                 # Operations control plane
│   │   ├── app/
│   │   │   ├── incidents/
│   │   │   ├── approvals/
│   │   │   ├── remediation/
│   │   │   └── audit/
│   │   ├── tests/
│   │   └── Dockerfile
│   │
│   ├── agent/                   # Independent RCA engine
│   │   ├── app/
│   │   │   ├── graph.py
│   │   │   ├── nodes.py
│   │   │   ├── schemas.py
│   │   │   └── tools/
│   │   ├── tests/
│   │   └── Dockerfile
│   │
│   ├── dashboard/               # React UI
│   │
│   └── mock-service/            # Simulated production app
│
├── integrations/
│   ├── alertmanager/
│   ├── prometheus/
│   └── agent_client/
│
├── contracts/                   # Shared request/response schemas
├── monitoring/
│   ├── prometheus.yml
│   ├── alert-rules.yml
│   └── alertmanager.yml
├── tests/                       # End-to-end tests
├── docker-compose.yml
├── .env.example
├── .gitignore
└── README.md