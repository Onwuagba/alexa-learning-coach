# Deploy to AWS (App Runner)

Legend: **[MANUAL]** you must do it (needs your AWS/Amazon account). **[TODO-verify]** not checked against current docs.

1. **[MANUAL]** Create an AWS account, install the AWS CLI, `aws configure`.
2. **[MANUAL]** Create a Postgres database (RDS, or a free-tier host such as Neon) and note its `postgresql://user:pass@host/db` URL. App Runner has no persistent disk, so SQLite would reset. The Postgres code path is untested against a real server: run `DATABASE_URL=... python scripts/demo.py`-style smoke checks first.
3. Build and push (commands are standard, adjust region/account):
   ```bash
   aws ecr create-repository --repository-name learning-coach
   aws ecr get-login-password | docker login --username AWS --password-stdin <acct>.dkr.ecr.<region>.amazonaws.com
   docker build -t learning-coach .
   docker tag learning-coach <acct>.dkr.ecr.<region>.amazonaws.com/learning-coach:latest
   docker push <acct>.dkr.ecr.<region>.amazonaws.com/learning-coach:latest
   ```
4. **[MANUAL]** Create an IAM role App Runner can assume to pull ECR (`AWSAppRunnerServicePolicyForECRAccess`) [TODO-verify].
5. Deploy `infra/apprunner.yaml` (CloudFormation; unvalidated, [TODO-verify]):
   ```bash
   aws cloudformation deploy --stack-name learning-coach --template-file infra/apprunner.yaml \
     --parameter-overrides ImageUri=... EcrAccessRoleArn=... DatabaseUrl=... ParentPin=... \
       McpAuthToken=$(openssl rand -hex 24) SessionSecret=$(openssl rand -hex 32)
   ```
   Secrets as plain env vars is hackathon-grade; move to Secrets Manager for real use.
6. Check `https://<service-url>/healthz` returns `ok`, `/parent/` shows the sign-in, `/mcp/` answers 401 without the bearer token.

Local container test: `docker build -t lc . && docker run -p 8000:8000 -e PARENT_PIN=1234 lc` (Docker was not run while building this).
