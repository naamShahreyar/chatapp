# AWS Runbook: ChatApp

Every AWS step used to deploy ChatApp, in order, with one line on *why* for each. Follow it from top to bottom to rebuild the deployment from scratch.

- **Shell:** PowerShell, run from the repo root.
- **Region:** `ap-south-1` (Mumbai). **Account:** `102076848646`.
- **Placeholders** are written `<like-this>`. Replace them with your values.

> **Never** paste secret keys, passwords or API keys into chat, code, screenshots or Git. If one leaks, rotate it.

---

## Contents

0. [The mental model](#0-the-mental-model)
1. [One-time account setup](#1-one-time-account-setup)
2. [Push images to ECR](#2-push-images-to-ecr)
3. [Option A: Deploy on EC2](#3-option-a-deploy-on-ec2)
4. [Option B: Deploy on ECS Fargate + RDS + ALB](#4-option-b-deploy-on-ecs-fargate--rds--alb)
   - [4.8 Continuous deployment from GitHub Actions (OIDC)](#48-continuous-deployment-from-github-actions-oidc)
5. [Day-to-day operations](#5-day-to-day-operations)
6. [Troubleshooting](#6-troubleshooting)
7. [Teardown](#7-teardown)

---

## 0. The mental model

Every deployment answers the same seven questions:

| Question | Option A: EC2 | Option B: ECS Fargate |
|---|---|---|
| 1. Where does the code live? | Image in ECR | Image in ECR |
| 2. What runs it? | One VM running docker compose | Fargate tasks managed by ECS services |
| 3. How does traffic get in? | Public IP + security group (port 8501) | Load balancer + a chain of security groups |
| 4. Where does data live? | Docker volume on the VM's disk | RDS Postgres |
| 5. Where are the secrets? | `.env` file on the server | SSM Parameter Store (`/chatapp/...`) |
| 6. What is it allowed to do? | IAM role on the instance | IAM task execution role |
| 7. Where are the logs, and what does it cost? | `docker compose logs`, ~$0.55/day | CloudWatch Logs, ~$1.50–2/day |

Option B architecture:

```
Browser ──80──▶ ALB ──8501──▶ frontend (Fargate) ──8000──▶ backend (Fargate) ──5432──▶ RDS
                alb-sg        frontend-sg                  backend-sg │       db-sg
                                         Service Connect: "backend"   └──▶ Gemini
```

---

## 1. One-time account setup

This is mostly done in the console, once per account.

| Step | Why |
|---|---|
| Create the AWS account; turn on **MFA** for the owner/root login | A stolen password alone can't take over the account |
| Billing → **Budgets** → monthly cost budget **$10** with email alerts (in the **management account**) | Warns you before forgotten resources cost real money |
| IAM → create user **`chatapp-admin`** with **`AdministratorAccess`** | Don't work as root day to day |
| Give it a console password (see below) and **MFA** | One everyday identity for the console and CLI |
| Create an **access key** (CLI type) for `chatapp-admin` | The CLI needs credentials |

Install and configure the CLI:

```powershell
winget install Amazon.AWSCLI          # then fully restart VS Code / Windows
aws configure                         # key, secret, region ap-south-1, output json
aws sts get-caller-identity           # "who am I?" — the Arn should end in user/chatapp-admin
```

Give the IAM user a console password. Use your own password, and don't save this command anywhere:

```powershell
aws iam create-login-profile --user-name chatapp-admin --password '<strong-password>' --no-password-reset-required
# change it later with: aws iam update-login-profile --user-name chatapp-admin --password '<new>'
```

**This account's special setup:** it was created with AWS's new "project" sign-up, then upgraded to the Paid plan, and then **advanced features** were activated. You now administer an AWS Organization:

- **Management Account:** organization policies and billing. Use it rarely.
- **Idea Launchpad** (`102076848646`): where the app lives and where `chatapp-admin` exists.
- **Identity Delegated Admin:** created by AWS. Leave it alone.

Regions are restricted by the SCP `AdvancedModeRegionRestrictionSecurityControlPolicy` (statement `RegionFloor`). `ap-south-1` was added to its `aws:RequestedRegion` list. The console's region menu still doesn't list Mumbai, so open console pages with `?region=ap-south-1` in the URL, for example:
`https://ap-south-1.console.aws.amazon.com/ecs/v2/clusters?region=ap-south-1`

---

## 2. Push images to ECR

**Why:** AWS can't reach images on your laptop. ECR is your private registry, so you push there and AWS pulls from there.

```powershell
aws configure get region      # must print ap-south-1
```

Create the repositories (once):

```powershell
aws ecr create-repository --repository-name chatapp-backend  --image-scanning-configuration scanOnPush=true
aws ecr create-repository --repository-name chatapp-frontend --image-scanning-configuration scanOnPush=true
aws ecr describe-repositories --query "repositories[].repositoryUri" --output table
```

`scanOnPush=true` gives a free scan for known security vulnerabilities (CVEs) in every pushed image.

Log Docker in. The token lasts **12 hours**, so run this again whenever a push says "no basic auth credentials":

```powershell
aws ecr get-login-password | docker login --username AWS --password-stdin 102076848646.dkr.ecr.ap-south-1.amazonaws.com
```

Build, tag and push. The tag gives the image a second name containing the registry address, which tells `docker push` where to send it:

```powershell
$REG = "102076848646.dkr.ecr.ap-south-1.amazonaws.com"

docker build -t chatapp-backend ./backend
docker tag chatapp-backend:latest "$REG/chatapp-backend:latest"
docker push "$REG/chatapp-backend:latest"

docker build -t chatapp-frontend ./frontend
docker tag chatapp-frontend:latest "$REG/chatapp-frontend:latest"
docker push "$REG/chatapp-frontend:latest"
```

Check:

```powershell
aws ecr describe-images --repository-name chatapp-backend --query "imageDetails[].[imageTags[0],imageSizeInBytes,imagePushedAt]" --output table
```

Each push creates **three entries**: the tagged `latest` (an image index) plus two untagged children (the real image and a build attestation). **Don't delete the untagged ones**, because `latest` depends on them.

To pick up security fixes in the base image, rebuild with `docker build --pull ...`.

---

## 3. Option A: Deploy on EC2

The simplest path: the same docker compose setup, running on one VM.

### 3.1 IAM role for the instance (console, once)

IAM → Roles → Create role → **AWS service: EC2** → attach:

- `AmazonEC2ContainerRegistryReadOnly`: pull images from ECR
- `AmazonSSMManagedInstanceCore`: browser terminal via Session Manager (no SSH, no port 22)

Name it **`chatapp-ec2-role`**.

**Why:** the server gets temporary, auto-rotating credentials. Never copy access keys onto a server.

### 3.2 Security group: only port 8501, only from your IP

Console: EC2 → Security Groups → Create:

- **Name:** `chatapp-ec2-sg`
- **VPC:** the default VPC
- **Inbound:** Custom TCP **8501** from **My IP**
- **Outbound:** leave the default

**Why:** expose only the frontend. The backend (8000), Postgres (5432) and SSH (22) all stay closed.

### 3.3 Launch the instance (console)

EC2 → Launch instance:

| Setting | Value | Why |
|---|---|---|
| Name | `chatapp-server` | |
| AMI | **Amazon Linux 2023, 64-bit (x86)** | x86 matches the images (`t4g` instances are ARM and won't run them) |
| Type | **t3.small** | 2 GB RAM for three containers |
| Key pair | **Proceed without** | We use Session Manager instead of SSH |
| Network | default VPC, **auto-assign public IP: Enable**, SG `chatapp-ec2-sg` | |
| Storage | **20 GiB gp3** | Room for images and data |
| Advanced → IAM instance profile | **`chatapp-ec2-role`** | Easy to miss |

Check it:

```powershell
aws ec2 describe-instances --filters "Name=tag:Name,Values=chatapp-server" --query "Reservations[].Instances[].[InstanceId,State.Name,PublicIpAddress,IamInstanceProfile.Arn]" --output table
```

### 3.4 On the server (EC2 → Connect → Session Manager)

```bash
bash
aws sts get-caller-identity                      # shows assumed-role/chatapp-ec2-role/...: the role at work

# Docker, started now and at every boot
sudo dnf install -y docker
sudo systemctl enable --now docker

# Docker Compose plugin (not in Amazon Linux's package list)
sudo mkdir -p /usr/local/lib/docker/cli-plugins
sudo curl -sSL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64 -o /usr/local/lib/docker/cli-plugins/docker-compose
sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose

# Log in to ECR, using the role's credentials
aws ecr get-login-password --region ap-south-1 | sudo docker login --username AWS --password-stdin 102076848646.dkr.ecr.ap-south-1.amazonaws.com

# App folder + production compose file (runs ECR images; contains no secrets)
sudo mkdir -p /opt/chatapp && sudo chown ssm-user /opt/chatapp && cd /opt/chatapp
curl -sSLO https://raw.githubusercontent.com/naamShahreyar/chatapp/main/docker-compose.prod.yml

# Secrets, generated ON the server (never travel through your laptop or Git)
cat > .env <<EOF
POSTGRES_PASSWORD=$(openssl rand -hex 24)
JWT_SECRET=$(openssl rand -hex 32)
LLM_MODEL=<your-gemini-model>
EOF
read -s -p "Gemini API key: " K && echo "GOOGLE_API_KEY=$K" >> .env && unset K && echo
chmod 600 .env
cut -d= -f1 .env                                 # show variable names only, never values

# Start the app
sudo docker compose -f docker-compose.prod.yml up -d
sudo docker compose -f docker-compose.prod.yml ps
sudo docker compose -f docker-compose.prod.yml logs backend --tail 20
```

Open `http://<public-ip>:8501`.

- `restart: unless-stopped` and `systemctl enable docker` bring the app back after a reboot.
- The `pgdata` volume keeps the data.
- **Stopping** the instance keeps its disk, but the **public IP changes** when it starts again.

---

## 4. Option B: Deploy on ECS Fargate + RDS + ALB

The production-style setup: no servers to manage, a managed database, a stable URL and secrets in Parameter Store.

### 4.0 Variables

Run these in every new terminal. PowerShell variables disappear when the terminal closes.

```powershell
$VPC     = aws ec2 describe-vpcs --filters "Name=isDefault,Values=true" --query "Vpcs[0].VpcId" --output text
$SUBNETS = (aws ec2 describe-subnets --filters "Name=vpc-id,Values=$VPC" "Name=default-for-az,Values=true" --query "Subnets[].SubnetId" --output text) -replace "\s+", ","
$ALB_SG  = aws ec2 describe-security-groups --filters "Name=group-name,Values=chatapp-alb-sg"      --query "SecurityGroups[0].GroupId" --output text
$FE_SG   = aws ec2 describe-security-groups --filters "Name=group-name,Values=chatapp-frontend-sg" --query "SecurityGroups[0].GroupId" --output text
$BE_SG   = aws ec2 describe-security-groups --filters "Name=group-name,Values=chatapp-backend-sg"  --query "SecurityGroups[0].GroupId" --output text
$DB_SG   = aws ec2 describe-security-groups --filters "Name=group-name,Values=chatapp-db-sg"       --query "SecurityGroups[0].GroupId" --output text
$TG_ARN  = aws elbv2 describe-target-groups --names chatapp-frontend-tg --query "TargetGroups[0].TargetGroupArn" --output text 2>$null
"VPC=$VPC SUBNETS=$SUBNETS ALB=$ALB_SG FE=$FE_SG BE=$BE_SG DB=$DB_SG"
```

### 4.1 Security group chain

**Why:** each layer accepts traffic **only from the security group in front of it**, not from IP addresses. This keeps working when Fargate task IPs change, and the database is only reachable by the backend.

```powershell
$MYIP = (Invoke-RestMethod https://checkip.amazonaws.com).Trim()

$ALB_SG = aws ec2 create-security-group --group-name chatapp-alb-sg --description "ChatApp load balancer" --vpc-id $VPC --query GroupId --output text
aws ec2 authorize-security-group-ingress --group-id $ALB_SG --protocol tcp --port 80 --cidr "$MYIP/32"

$FE_SG = aws ec2 create-security-group --group-name chatapp-frontend-sg --description "ChatApp frontend tasks" --vpc-id $VPC --query GroupId --output text
aws ec2 authorize-security-group-ingress --group-id $FE_SG --protocol tcp --port 8501 --source-group $ALB_SG

$BE_SG = aws ec2 create-security-group --group-name chatapp-backend-sg --description "ChatApp backend tasks" --vpc-id $VPC --query GroupId --output text
aws ec2 authorize-security-group-ingress --group-id $BE_SG --protocol tcp --port 8000 --source-group $FE_SG

$DB_SG = aws ec2 create-security-group --group-name chatapp-db-sg --description "ChatApp RDS Postgres" --vpc-id $VPC --query GroupId --output text
aws ec2 authorize-security-group-ingress --group-id $DB_SG --protocol tcp --port 5432 --source-group $BE_SG
```

Check the chain:

```powershell
aws ec2 describe-security-groups --filters "Name=group-name,Values=chatapp-alb-sg,chatapp-frontend-sg,chatapp-backend-sg,chatapp-db-sg" --query "SecurityGroups[].{Name:GroupName,Id:GroupId,Port:IpPermissions[0].FromPort,FromCidr:IpPermissions[0].IpRanges[0].CidrIp,FromGroup:IpPermissions[0].UserIdGroupPairs[0].GroupId}" --output table
```

### 4.2 RDS Postgres

**Why:** a managed database with backups and patching, kept separate from the app containers. It has no public IP.

```powershell
$DB_PASSWORD = python -c "import secrets; print(secrets.token_hex(24))"   # hex: safe inside a URL

aws rds create-db-instance `
  --db-instance-identifier chatapp-db `
  --engine postgres `
  --db-instance-class db.t4g.micro `
  --allocated-storage 20 `
  --storage-type gp3 `
  --db-name chatapp `
  --master-username chatapp `
  --master-user-password $DB_PASSWORD `
  --vpc-security-group-ids $DB_SG `
  --no-publicly-accessible `
  --no-multi-az `
  --backup-retention-period 1
```

The backtick (`` ` ``) continues a command on the next line. Nothing may follow it on the line, not even a space.

Wait 5–15 minutes, until the status is `available` and an endpoint appears:

```powershell
aws rds describe-db-instances --db-instance-identifier chatapp-db --query "DBInstances[0].[DBInstanceStatus,Endpoint.Address]" --output table
```

`db.t4g.micro` is ARM, which is fine for RDS: AWS runs the database software, so only *your* images need to be x86.

### 4.3 Secrets in SSM Parameter Store

**Why:** Fargate has no server disk for a `.env` file. ECS fetches these encrypted values at container start and injects them as environment variables. The standard tier is free.

```powershell
aws ssm put-parameter --name /chatapp/db-password --type SecureString --value $DB_PASSWORD

$JWT = python -c "import secrets; print(secrets.token_hex(32))"
aws ssm put-parameter --name /chatapp/jwt-secret --type SecureString --value $JWT

$sec  = Read-Host "Gemini API key" -AsSecureString          # typed in hidden
$GKEY = [Net.NetworkCredential]::new('', $sec).Password
aws ssm put-parameter --name /chatapp/google-api-key --type SecureString --value $GKEY
Remove-Variable GKEY, sec

$DB_HOST = aws rds describe-db-instances --db-instance-identifier chatapp-db --query "DBInstances[0].Endpoint.Address" --output text
$DB_URL  = "postgresql+psycopg://chatapp:${DB_PASSWORD}@${DB_HOST}:5432/chatapp"   # ${} because "$x:" means something else in PowerShell
aws ssm put-parameter --name /chatapp/database-url --type SecureString --value $DB_URL
```

Check (names only, no values):

```powershell
aws ssm get-parameters-by-path --path /chatapp --query "Parameters[].[Name,Type,Version]" --output table
```

If you lose `$DB_PASSWORD`, load it back:

```powershell
$DB_PASSWORD = aws ssm get-parameter --name /chatapp/db-password --with-decryption --query Parameter.Value --output text
```

To **change** a secret, add `--overwrite` to `put-parameter`, then restart the service (see [5.2](#52-redeploy-the-same-task-definition)).

### 4.4 IAM: task execution role

**Why:** ECS itself uses this role to pull images, read the `/chatapp/*` secrets and write logs. The app code makes no AWS calls, so it needs no *task role*.

Policy files are in `infra/`: `ecs-task-trust.json` (who may use the role) and `ecs-read-secrets.json` (what it may read).

```powershell
aws iam create-role --role-name chatapp-ecs-execution-role --assume-role-policy-document file://infra/ecs-task-trust.json
aws iam attach-role-policy --role-name chatapp-ecs-execution-role --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy
aws iam put-role-policy --role-name chatapp-ecs-execution-role --policy-name chatapp-read-secrets --policy-document file://infra/ecs-read-secrets.json
```

### 4.5 ECS cluster and log groups

On a brand-new account, ECS's service-linked role may not be ready yet. If `create-cluster` fails with *"Service Linked Role is not ready"*, run this first, wait 30 seconds and retry:

```powershell
aws iam create-service-linked-role --aws-service-name ecs.amazonaws.com    # "has been taken" means it already exists, which is fine
```

```powershell
aws ecs create-cluster --cluster-name chatapp --service-connect-defaults namespace=chatapp
aws ecs describe-clusters --clusters chatapp --query "clusters[0].serviceConnectDefaults.namespace" --output text   # should print an ARN, not None

aws logs create-log-group --log-group-name /ecs/chatapp-backend
aws logs create-log-group --log-group-name /ecs/chatapp-frontend
aws logs put-retention-policy --log-group-name /ecs/chatapp-backend  --retention-in-days 7
aws logs put-retention-policy --log-group-name /ecs/chatapp-frontend --retention-in-days 7
```

- The **namespace** is where services register names for Service Connect, so the frontend can call `http://backend:8000`, exactly as in docker compose.
- **Retention:** CloudWatch keeps logs forever by default, and you keep paying to store them.

### 4.6 Backend service

The task definition (`infra/backend-task.json`) sets the image, 0.25 vCPU and 1 GB, plain `environment` values, `secrets` as Parameter Store ARNs (never the values), and logging.

The Service Connect settings (`infra/backend-service-connect.json`) publish the port as **`backend:8000`** with a **90-second per-request timeout**. The default is 15 seconds, which is too short for LLM calls.

```powershell
aws ecs register-task-definition --cli-input-json file://infra/backend-task.json --query "taskDefinition.[family,revision]" --output text

aws ecs create-service `
  --cluster chatapp `
  --service-name backend `
  --task-definition chatapp-backend `
  --desired-count 1 `
  --launch-type FARGATE `
  --network-configuration "awsvpcConfiguration={subnets=[$SUBNETS],securityGroups=[$BE_SG],assignPublicIp=ENABLED}" `
  --service-connect-configuration file://infra/backend-service-connect.json
```

`assignPublicIp=ENABLED` lets the task reach ECR and Gemini without a NAT gateway (which costs $1+/day). Inbound traffic is still blocked by `chatapp-backend-sg`.

Check:

```powershell
aws ecs describe-services --cluster chatapp --services backend --query "services[0].[status,runningCount,pendingCount]" --output text   # ACTIVE 1 0
aws logs tail /ecs/chatapp-backend --since 10m          # look for "Application startup complete" and no DB errors
```

### 4.7 Load balancer and frontend service

**Why:** the load balancer gives one stable public URL, health-checks the frontend and follows tasks as they're replaced.

```powershell
$ALB_ARN = aws elbv2 create-load-balancer --name chatapp-alb --type application --scheme internet-facing --subnets $SUBNETS.Split(",") --security-groups $ALB_SG --query "LoadBalancers[0].LoadBalancerArn" --output text

$TG_ARN = aws elbv2 create-target-group --name chatapp-frontend-tg --protocol HTTP --port 8501 --vpc-id $VPC --target-type ip --health-check-path /_stcore/health --query "TargetGroups[0].TargetGroupArn" --output text

aws elbv2 create-listener --load-balancer-arn $ALB_ARN --protocol HTTP --port 80 --default-actions "Type=forward,TargetGroupArn=$TG_ARN"

# Draining: wait 30s (default 300s) for old tasks' connections during deploys
aws elbv2 modify-target-group-attributes --target-group-arn $TG_ARN --attributes Key=deregistration_delay.timeout_seconds,Value=30
```

- **Listener:** what to listen for (HTTP on 80) and where to forward it.
- **Target group:** who receives the traffic, plus health checks. `--target-type ip` is required for Fargate.
- `/_stcore/health` is Streamlit's built-in health endpoint.

Now the frontend. `infra/frontend-task.json` sets `API_URL=http://backend:8000`, and `infra/frontend-service-connect.json` makes it a Service Connect client only.

```powershell
aws ecs register-task-definition --cli-input-json file://infra/frontend-task.json --query "taskDefinition.[family,revision]" --output text

aws ecs create-service `
  --cluster chatapp `
  --service-name frontend `
  --task-definition chatapp-frontend `
  --desired-count 1 `
  --launch-type FARGATE `
  --network-configuration "awsvpcConfiguration={subnets=[$SUBNETS],securityGroups=[$FE_SG],assignPublicIp=ENABLED}" `
  --service-connect-configuration file://infra/frontend-service-connect.json `
  --load-balancers "targetGroupArn=$TG_ARN,containerName=frontend,containerPort=8501" `
  --health-check-grace-period-seconds 60
```

The grace period gives Streamlit time to start before failed health checks count against it.

Check and open the app:

```powershell
aws elbv2 describe-target-health --target-group-arn $TG_ARN --query "TargetHealthDescriptions[].[Target.Id,TargetHealth.State]" --output text   # wait for "healthy"
aws elbv2 describe-load-balancers --names chatapp-alb --query "LoadBalancers[0].DNSName" --output text
```

Open **`http://<dns-name>`**. Type the `http://` explicitly, because Chrome may try HTTPS and time out.

### 4.8 Continuous deployment from GitHub Actions (OIDC)

**Why:** every push to `main` is tested, built, pushed and deployed automatically, with **no AWS keys stored in GitHub**. GitHub proves its identity with a short-lived signed token, and AWS swaps it for credentials that last one hour.

```
push to main ─▶ test ─▶ build ─▶ deploy: OIDC ─▶ ECR push (tag = commit SHA) ─▶ new task def revision ─▶ ECS rolling update
```

**1. Register GitHub as an identity provider (once per account):**

```powershell
aws iam create-open-id-connect-provider --url https://token.actions.githubusercontent.com --client-id-list sts.amazonaws.com
```

**2. Find your exact `sub`.** GitHub's token identifies the workflow as:

```
repo:<owner>@<owner-id>/<repo>@<repo-id>:ref:refs/heads/main
```

The numeric IDs make it immune to someone recreating a deleted repo with the same name. Get the IDs with:

```powershell
(Invoke-RestMethod https://api.github.com/repos/naamShahreyar/chatapp) | Select-Object @{n="owner_id";e={$_.owner.id}}, @{n="repo_id";e={$_.id}}
```

Put the exact value in `infra/github-oidc-trust.json` (ours is `repo:naamShahreyar@140060453/chatapp@1424456488:ref:refs/heads/main`). If it's wrong, the deploy fails with `Not authorized to perform sts:AssumeRoleWithWebIdentity`, and CloudTrail shows the `sub` GitHub actually sent (see [Troubleshooting](#6-troubleshooting)).

**3. Create the deploy role.** It's trusted only for this repo's `main` branch and can only push to the two ECR repos, register task definitions, update services in cluster `chatapp` and pass the execution role to ECS:

```powershell
aws iam create-role --role-name chatapp-github-deploy --assume-role-policy-document file://infra/github-oidc-trust.json
aws iam put-role-policy --role-name chatapp-github-deploy --policy-name chatapp-deploy --policy-document file://infra/github-deploy-policy.json
# after editing the trust file later:
aws iam update-assume-role-policy --role-name chatapp-github-deploy --policy-document file://infra/github-oidc-trust.json
```

**4. The workflow** is `.github/workflows/ci.yml`. The key parts of the `deploy` job:

| Part | Purpose |
|---|---|
| `if: github.event_name == 'push' && github.ref == 'refs/heads/main'` | Pull requests test and build but never deploy |
| `permissions: id-token: write` | Lets the job request an OIDC token. This is what makes keyless access work |
| `concurrency: deploy-production` | A second push waits instead of racing the first |
| `aws-actions/configure-aws-credentials` | Exchanges the GitHub token for temporary AWS credentials |
| Images tagged `${{ github.sha }}` (plus `latest`) | You always know which commit is running, and rollback is easy |
| `amazon-ecs-render-task-definition` | Puts the new image into `infra/*-task.json` |
| `amazon-ecs-deploy-task-definition` with `wait-for-service-stability` | Registers a new revision, updates the service and fails the job if the rollout isn't healthy |

**5. Check which commit production runs:**

```powershell
git rev-parse HEAD
aws ecs describe-task-definition --task-definition chatapp-backend --query "taskDefinition.containerDefinitions[0].image" --output text
```

> **The repo is now the source of truth.** Every deploy uses `infra/*-task.json` from Git. Changes made by hand in AWS (like the model in [5.1](#51-change-configuration-eg-the-llm-model)) are **overwritten by the next push**, so make changes in the file and push.

---

## 5. Day-to-day operations

**The normal way to change anything is: edit the code or the `infra/` file → commit → push to `main`.** The pipeline does the rest. The manual commands below are for emergencies, debugging, or when the pipeline isn't set up.

### 5.1 Change configuration (e.g. the LLM model)

Edit `infra/backend-task.json`, then register a new revision and deploy it:

```powershell
aws ecs register-task-definition --cli-input-json file://infra/backend-task.json --query "taskDefinition.revision" --output text
aws ecs update-service --cluster chatapp --service backend --task-definition chatapp-backend
aws ecs describe-services --cluster chatapp --services backend --query "services[0].deployments[].[status,rolloutState,runningCount,taskDefinition]" --output table
```

- This is a **rolling deployment**: the new task starts before the old one stops, so there's no downtime.
- Wait until the rollout shows `COMPLETED`.
- **Roll back** by deploying the previous revision: `--task-definition chatapp-backend:<n>`.

### 5.2 Redeploy the same task definition

Use this after pushing a new `:latest` image, or after changing a secret:

```powershell
aws ecs update-service --cluster chatapp --service backend  --force-new-deployment
aws ecs update-service --cluster chatapp --service frontend --force-new-deployment
```

### 5.3 Change Service Connect settings

```powershell
aws ecs update-service --cluster chatapp --service backend --service-connect-configuration file://infra/backend-service-connect.json
aws ecs update-service --cluster chatapp --service frontend --force-new-deployment     # so the frontend's proxy picks up the change
```

### 5.4 Read logs

```powershell
aws logs tail /ecs/chatapp-backend  --since 15m
aws logs tail /ecs/chatapp-frontend --since 15m
aws logs tail /ecs/chatapp-backend  --follow                      # live; Ctrl+C to stop
aws logs tail /ecs/chatapp-backend  --since 30m | Select-String 'HTTP/1.1"|Error'
```

### 5.5 Your home IP changed (the app times out)

```powershell
$MYIP = (Invoke-RestMethod https://checkip.amazonaws.com).Trim()
aws ec2 authorize-security-group-ingress --group-id $ALB_SG --protocol tcp --port 80 --cidr "$MYIP/32"
```

Later, remove the old address with `revoke-security-group-ingress` and the same flags, using the old IP.

### 5.6 Pause and resume to save credits

Pause:

```powershell
aws ecs update-service --cluster chatapp --service frontend --desired-count 0
aws ecs update-service --cluster chatapp --service backend  --desired-count 0
aws rds stop-db-instance --db-instance-identifier chatapp-db       # AWS auto-restarts a stopped DB after 7 days
```

Resume:

```powershell
aws rds start-db-instance --db-instance-identifier chatapp-db      # wait ~5 min for "available"
aws ecs update-service --cluster chatapp --service backend  --desired-count 1
aws ecs update-service --cluster chatapp --service frontend --desired-count 1
```

The load balancer still costs about $0.60/day while paused.

---

## 6. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `AccessDenied ... explicit deny in a service control policy` | An organization-level policy blocks the action or region | Read the whole message, which names the policy. Check the region is `ap-south-1`. Edit the SCP from the Management Account |
| `aws` is not recognized | The terminal has an old PATH | Restart VS Code fully or Windows. Quick fix: `$env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')` |
| `aws configure` says "Window too small" | The terminal panel is too short | Maximize the panel, or use `aws configure set <key> <value>` |
| Push fails: "no basic auth credentials" or expired token | The ECR login is older than 12 hours | Run `aws ecr get-login-password \| docker login ...` again |
| `create-cluster`: "Service Linked Role is not ready" | It's the first time ECS is used in the account | `aws iam create-service-linked-role --aws-service-name ecs.amazonaws.com`, wait, retry |
| Browser: `ERR_CONNECTION_TIMED_OUT` | The browser forced `https://` (port 443 isn't open), your IP changed, or a VPN | Type `http://` explicitly, or see [5.5](#55-your-home-ip-changed-the-app-times-out). Test with `Invoke-WebRequest http://<dns>/_stcore/health` |
| Chat shows **504** after about 15 seconds | The Service Connect proxy timed out before the LLM answered | `perRequestTimeoutSeconds` in `backend-service-connect.json`, see [5.3](#53-change-service-connect-settings) |
| Chat shows **500**; logs show Gemini `503 UNAVAILABLE` or 429 | The model is overloaded, or you've hit the free tier's requests-per-day limit | Wait, or switch `LLM_MODEL`, see [5.1](#51-change-configuration-eg-the-llm-model) |
| Service `runningCount` stays at 0 | The task keeps crashing | See the command below |
| Target stays `unhealthy` | Streamlit isn't responding on 8501 | Check the frontend logs, and that `chatapp-frontend-sg` allows 8501 from `chatapp-alb-sg` |
| Console region menu has no Mumbai | The console's view lags behind the SCP | Use URLs with `?region=ap-south-1`. The CLI is the source of truth |
| GitHub Actions: `Not authorized to perform sts:AssumeRoleWithWebIdentity` | The token's `sub` doesn't match the trust policy. GitHub's `sub` now includes owner and repo IDs: `repo:owner@<id>/repo@<id>:ref:refs/heads/main` | Find the exact `sub` in CloudTrail (see below), then put it in `infra/github-oidc-trust.json` and run `aws iam update-assume-role-policy` |

Why did the last task stop?

```powershell
aws ecs describe-tasks --cluster chatapp --tasks (aws ecs list-tasks --cluster chatapp --desired-status STOPPED --query "taskArns[0]" --output text) --query "tasks[0].[stoppedReason,containers[0].reason]"
```

What identity did GitHub present? Recent OIDC attempts from CloudTrail:

```powershell
$events = aws cloudtrail lookup-events --lookup-attributes AttributeKey=EventName,AttributeValue=AssumeRoleWithWebIdentity --max-results 3 --output json | ConvertFrom-Json
foreach ($ev in $events.Events) { $e = $ev.CloudTrailEvent | ConvertFrom-Json; "{0} {1} {2}" -f $e.eventTime, $e.errorCode, $e.userIdentity.userName }
```

---

## 7. Teardown

> Day 7 will finalize this section after it's done for real. Delete things **in this order**, because each resource must be released by whatever uses it first.

1. **ECS services:** set desired count 0, then delete the `frontend` and `backend` services. Then delete the cluster `chatapp`.
2. **Load balancer:** delete `chatapp-alb`, then the target group `chatapp-frontend-tg`.
3. **RDS:** delete `chatapp-db` (skip the final snapshot for a learning project).
4. **EC2** (if still around): terminate `chatapp-server`.
5. **Security groups:** delete `chatapp-db-sg`, `chatapp-backend-sg`, `chatapp-frontend-sg`, `chatapp-alb-sg` and `chatapp-ec2-sg`. This only works once nothing uses them.
6. **ECR:** delete the repositories `chatapp-backend` and `chatapp-frontend` (`--force` deletes the images too).
7. **Parameter Store:** delete the `/chatapp/*` parameters.
8. **CloudWatch:** delete the log groups `/ecs/chatapp-*`.
9. **Cloud Map:** delete the namespace `chatapp` if it's still there.
10. **IAM:** delete `chatapp-ecs-execution-role`, `chatapp-ec2-role` and `chatapp-github-deploy` (remove their policies first), and the GitHub OIDC identity provider.
11. **GitHub:** remove or disable the `deploy` job, otherwise the next push fails because the AWS resources are gone.
12. Check **Billing → Bills** over the next few days to confirm charges stopped.
