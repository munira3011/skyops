skyops/
├── .claude/skills/langgraph-node/SKILL.md
├── .claude/agents/deploy-helper.md
├── .claude/agents/code-reviewer.md
├── docs/progress.md
├── docs/architecture.md
├── backend/
│   ├── main.py
│   ├── chroma_db/            (gitignored, generated — don't create empty)
│   └── app/
│       ├── main.py
│       ├── data/{flights.json,pnr.json}
│       ├── data/policies/{baggage_allowance.md,loyalty_tiers.md,rebooking_policy.md,checkin_and_documents.md}
│       ├── graph/state.py
│       ├── graph/supervisor.py
│       ├── graph/graph.py
│       ├── graph/nodes/{flight_status_agent.py,rag_policy_agent.py,booking_disruption_agent.py,human_approval.py}
│       ├── rag/ingest.py
│       ├── rag/retriever.py
│       ├── guardrails/{input_guard.py,output_guard.py}
│       ├── gateway/client.py
│       ├── middleware/{auth.py,rate_limit.py,logging.py,error_handler.py}
│       └── routes/{chat.py,ops.py}
├── frontend/app.py
├── litellm-proxy/config.yaml
├── evals/datasets/{golden_qa.jsonl,adversarial_prompts.jsonl}
├── evals/{test_agent_accuracy.py,test_guardrails.py,run_ragas_eval.py}
├── infra/bicep/
├── .github/workflows/deploy.yml
└── docker-compose.yml