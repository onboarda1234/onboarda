# Route Gating Matrix

| Surface | Route / URL | Pilot behavior |
| --- | --- | --- |
| SAR/STR list/create | `/api/sar` | 403 controlled disabled response when inactive |
| SAR/STR detail/update | `/api/sar/:id` | 403 controlled disabled response when inactive |
| SAR/STR workflow | `/api/sar/:id/workflow` | 403 controlled disabled response when inactive |
| SAR auto-trigger | `/api/sar/auto-trigger` | 403 controlled disabled response; no SAR row created |
| AI Supervisor run | `/api/applications/:id/supervisor/run` | 403 controlled disabled response; no pipeline row created |
| AI Supervisor result | `/api/applications/:id/supervisor/result` | 403 controlled disabled response when inactive |
| Supervisor Audit export | `/api/audit/supervisor/export` | 403 controlled disabled response when inactive |
| Regulatory Intelligence API | `/api/regulatory-intelligence*` | 403 controlled disabled response when inactive |
| Agent 8/9/10 config enable | `/api/config/ai-agents` | 400 validation error if enabling enterprise roadmap agents |
| KPI Dashboard UI | `/backoffice/kpis`, `/backoffice/kpi-dashboard`, `/backoffice/enterprise-analytics` | Coming Soon only |
| Regulatory Intelligence UI | `/backoffice/regulatory-intelligence`, `/backoffice/reg-intel` | Coming Soon only |
| AI Supervisor UI | `/backoffice/ai-compliance-supervisor`, `/backoffice/supervisor-dashboard`, Application Review tab | Coming Soon only |
| Supervisor Audit UI | `/backoffice/audit-chain`, `/backoffice/supervisor-audit` | Coming Soon only |
| AI Agents UI | `/backoffice/ai-agents` | Agent 1 remains active; Agents 8/9/10 Coming Soon |

