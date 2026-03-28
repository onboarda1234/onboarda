# Onboarda / RegMind — Deployment Runbook

## Environments

| Env | Platform | URL | DB |
|-----|----------|-----|----|
| Demo | Render | demo.regmind.co | SQLite |
| Staging | AWS ECS (af-south-1) | staging.regmind.co | RDS PostgreSQL |
| Production | AWS ECS (af-south-1) | app.regmind.co | RDS PostgreSQL |

## Staging Deployment

### Prerequisites
- Docker Desktop running
- AWS CLI configured (`aws configure` with af-south-1)
- ECR login valid (expires every 12 hours)

### Deploy Steps

```bash
# 1. Run tests locally
cd ~/Desktop/Onboarda/arie-backend
python3.11 -m pytest tests/ -x -q --tb=short --ignore=tests/test_pdf_generator.py

# 2. Copy HTML files into backend directory
cd ~/Desktop/Onboarda
cp arie-portal.html arie-backend/arie-portal.html
cp arie-backoffice.html arie-backend/arie-backoffice.html

# 3. Build Docker image (amd64 for ECS)
cd arie-backend
docker build --platform linux/amd64 -t regmind-backend .

# 4. Tag and push to ECR
docker tag regmind-backend:latest 782913119880.dkr.ecr.af-south-1.amazonaws.com/regmind-backend:latest
aws ecr get-login-password --region af-south-1 | docker login --username AWS --password-stdin 782913119880.dkr.ecr.af-south-1.amazonaws.com
docker push 782913119880.dkr.ecr.af-south-1.amazonaws.com/regmind-backend:latest

# 5. Deploy to ECS (scale down, then up with new image)
aws ecs update-service --cluster regmind-staging --service regmind-backend --desired-count 0 --region af-south-1
sleep 30
aws ecs update-service --cluster regmind-staging --service regmind-backend --task-definition regmind-staging:3 --desired-count 1 --force-new-deployment --region af-south-1

# 6. Wait for deployment (90-120 seconds)
sleep 120

# 7. Verify health
curl -s https://staging.regmind.co/api/health | python3 -m json.tool
```

### Rollback

```bash
# Roll back to previous task definition revision
aws ecs update-service --cluster regmind-staging --service regmind-backend \
  --task-definition regmind-staging:2 \
  --force-new-deployment --region af-south-1
```

### Database Reset (staging only)

```bash
curl -s -X POST https://staging.regmind.co/api/admin/reset-db \
  -H "Content-Type: application/json" \
  -d '{"confirm":"WIPE_STAGING_2026"}'

# Get new admin password from logs
aws logs filter-log-events --log-group-name /ecs/regmind-staging \
  --region af-south-1 --filter-pattern "INITIAL ADMIN" \
  --start-time $(( $(date +%s) - 120 ))000 --limit 1 \
  --query 'events[0].message' --output text
```

## Incident Response

### Connection Pool Exhaustion
Symptoms: `"connection pool exhausted"` in health check, 500 errors on all endpoints.

```bash
# Option 1: Restart ECS task (clears pool)
aws ecs update-service --cluster regmind-staging --service regmind-backend --desired-count 0 --region af-south-1
sleep 20
aws ecs update-service --cluster regmind-staging --service regmind-backend --desired-count 1 --force-new-deployment --region af-south-1

# Option 2: Reboot RDS (if pool restart doesn't help)
aws rds reboot-db-instance --db-instance-identifier regmind-staging-db --region af-south-1
# Wait 60s for RDS, then restart ECS
```

### ECR Login Expired
Symptom: `403 Forbidden` on docker push.

```bash
aws ecr get-login-password --region af-south-1 | docker login --username AWS --password-stdin 782913119880.dkr.ecr.af-south-1.amazonaws.com
```

### Check Logs

```bash
# Recent logs
aws logs get-log-events --log-group-name /ecs/regmind-staging \
  --log-stream-name $(aws logs describe-log-streams --log-group-name /ecs/regmind-staging --region af-south-1 --order-by LastEventTime --descending --limit 1 --query 'logStreams[0].logStreamName' --output text) \
  --region af-south-1 --limit 20 --query 'events[*].message' --output text

# Filter for errors
aws logs filter-log-events --log-group-name /ecs/regmind-staging \
  --region af-south-1 --filter-pattern "ERROR" \
  --start-time $(( $(date +%s) - 3600 ))000 --limit 10 \
  --query 'events[*].message' --output text
```

## AWS Resource IDs

| Resource | ID |
|----------|-----|
| VPC | vpc-038cb4259f199a4da |
| RDS | regmind-staging-db.chg464k26znk.af-south-1.rds.amazonaws.com |
| S3 Bucket | regmind-documents-staging |
| ECS Cluster | regmind-staging |
| ECR Repo | 782913119880.dkr.ecr.af-south-1.amazonaws.com/regmind-backend |
| ALB | regmind-staging-alb |
| Secrets Manager | regmind/staging |
| Task Definition | regmind-staging:3 |
| CloudWatch Logs | /ecs/regmind-staging |

## Staging Credentials

- **Portal:** https://staging.regmind.co/portal — register new account
- **Back Office:** https://staging.regmind.co/backoffice — `asudally@ariefinance.mu` + password from DB reset logs
