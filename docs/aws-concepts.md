# AWS Concepts: A Study Guide

The 11 core AWS concepts behind ChatApp. Each one is explained in plain language, with an analogy, where it appeared in this project, the common traps, and review questions.

Read one concept at a time. Answer the questions in your own words **before** opening the answers.

For the exact commands, see [aws-runbook.md](aws-runbook.md). This guide is about the **why**.

---

## Contents

0. [The big picture](#0-the-big-picture)
1. [Accounts, Regions and Availability Zones](#1-accounts-regions-and-availability-zones)
2. [IAM: who can do what](#2-iam-who-can-do-what)
3. [Networking: VPC, subnets, security groups](#3-networking-vpc-subnets-security-groups)
4. [Compute: EC2, ECS/Fargate, Lambda](#4-compute-ec2-ecsfargate-lambda)
5. [Images: ECR, tags, digests](#5-images-ecr-tags-digests)
6. [Traffic: load balancers and health checks](#6-traffic-load-balancers-and-health-checks)
7. [Data: RDS](#7-data-rds)
8. [Secrets: Parameter Store, Secrets Manager, KMS](#8-secrets-parameter-store-secrets-manager-kms)
9. [Observability: CloudWatch and CloudTrail](#9-observability-cloudwatch-and-cloudtrail)
10. [CI/CD and OIDC](#10-cicd-and-oidc)
11. [Cost](#11-cost)

---

## 0. The big picture

### Seven questions every deployment answers

Learn the **questions**, not the console clicks. Every new AWS service answers one of these:

| Question | ChatApp's answer (ECS version) | Concept |
|---|---|---|
| 1. Where does the code live? | Docker images in ECR | [5](#5-images-ecr-tags-digests) |
| 2. What runs it? | ECS services on Fargate | [4](#4-compute-ec2-ecsfargate-lambda) |
| 3. How does traffic get in? | Load balancer + security group chain | [3](#3-networking-vpc-subnets-security-groups), [6](#6-traffic-load-balancers-and-health-checks) |
| 4. Where does data live? | RDS Postgres | [7](#7-data-rds) |
| 5. Where are the secrets? | SSM Parameter Store | [8](#8-secrets-parameter-store-secrets-manager-kms) |
| 6. What is it allowed to do? | IAM roles | [2](#2-iam-who-can-do-what) |
| 7. How do I see it, and what does it cost? | CloudWatch, CloudTrail, Budgets | [9](#9-observability-cloudwatch-and-cloudtrail), [11](#11-cost) |

### The architecture we built

```
                              Region: ap-south-1 (Mumbai)
┌─────────────────────────────── VPC ──────────────────────────────────────┐
│                                                                           │
│  Browser ──:80──▶ [ALB] ──:8501──▶ [frontend task] ──:8000──▶ [backend task] ──:5432──▶ [RDS]
│                  alb-sg            frontend-sg      Service    backend-sg    │          db-sg
│                                                     Connect                  ▼
│                                                                         Gemini API
│        secrets: SSM Parameter Store ──▶ backend at start-up                  │
│        logs:    all tasks ──▶ CloudWatch Logs                                │
└───────────────────────────────────────────────────────────────────────────┘
   Images: ECR ◀── GitHub Actions (OIDC role, image tagged with commit SHA) ──▶ ECS rolling deploy
```

---

## 1. Accounts, Regions and Availability Zones

### The idea

```
AWS Organization   (head office: shared billing + guardrails called SCPs)
 └── AWS Account   (a container for resources: its own billing and security boundary)
      └── Region   (a geographic area, e.g. ap-south-1 = Mumbai)
           └── Availability Zone (separate data centers in that region: 1a, 1b, 1c)
```

- **Account:** every resource belongs to exactly one account. Accounts are **isolated** from each other unless you explicitly connect them.
- **Region:** **most resources live in exactly one region.** Choose regions for latency (close to users), law and compliance, and price.
  - **Global exceptions:** **IAM** (users and roles exist in every region), billing, and a few others.
- **Availability Zone (AZ):** physically separate buildings with independent power and networking. **Spread resources across AZs to survive a data center failure.**
- **Organization:** groups many accounts. **SCPs (service control policies)** set the **maximum** permissions for whole accounts, overriding even administrators.

### Analogy

- **Account** = an office lease: its own keys, its own bill
- **Region** = the city
- **AZ** = separate buildings in the city
- **Organization + SCP** = head office rules that every branch must follow

### In this project

- Resources "disappeared" when the console or CLI pointed at the wrong region.
- The project account was locked to Sydney by an **SCP** (`RegionFloor`). It denied even `AdministratorAccess` in Mumbai until you edited the SCP from the **Management Account**.
- You ended up with **three accounts**: Management, Idea Launchpad (the app), and Identity Delegated Admin.
- IAM showed **0 users** in the Management Account, because `chatapp-admin` lived in Idea Launchpad.
- The load balancer needed **subnets in several AZs**.

### Traps

1. "My resources are gone!" Check the **region and account first** (top right of the console).
2. Assuming everything is regional (IAM isn't).
3. Thinking one AZ is "highly available". Our RDS was single-AZ, which is cheaper but less resilient.

### Check yourself

1. A friend creates a database in `eu-central-1`, switches the console to `ap-south-1`, and says it's gone. What happened?
2. Why did the load balancer need subnets in more than one AZ?
3. You had `AdministratorAccess`, but AWS denied your ECR calls in Mumbai. What overruled you, and where did that rule live?

<details><summary>Answers</summary>

1. Nothing is lost. A database is a **regional** resource, and the console only shows the selected region. Switch back to `eu-central-1`.
2. To survive an AZ failure: the ALB automatically sends traffic only to healthy targets in working zones. AWS also requires at least two AZs for an ALB.
3. A **service control policy (SCP)** at the **AWS Organization** level, above your account. SCPs cap everyone's permissions, and an explicit **Deny always wins** over any Allow.

</details>

---

## 2. IAM: who can do what

### The idea

Every AWS request is checked: **who** is asking (the *principal*), **what** they want to do (the *action*), **on what** (the *resource*), and whether **a policy allows it**.

**Principals:**

| | Credentials | Use for |
|---|---|---|
| **Root user** | Password + MFA, unlimited power | Initial setup only |
| **IAM user** | Password and/or **access keys that never expire** | People (and, avoid this, old-style automation) |
| **IAM role** | **Temporary** credentials from **STS**, expiring automatically | Services, CI/CD, cross-account access. **Prefer roles** |

**A policy** is JSON made of statements: `Effect` (Allow/Deny), `Action`, `Resource`, optional `Condition`.

| Policy type | Attached to | Answers |
|---|---|---|
| **Permission policy** | A user or role | What may this identity do? |
| **Trust policy** | A role | **Who may assume** this role? |
| **SCP** | An account or organization | What's the **maximum** anyone here may do? |
| Resource policy | A resource (e.g. an S3 bucket) | Who may access this resource? |

**Every role needs both** a trust policy and a permission policy.

**How AWS decides:**

1. Everything starts as **denied**.
2. An **Allow** can permit it.
3. An explicit **Deny anywhere wins**.
4. **SCPs set the outer limit.**

**Least privilege:** grant only the actions needed, on only the resources needed.

**`iam:PassRole`:** permission to hand a role to a service (e.g. a task definition naming its execution role). Restrict it, or anyone could give a powerful role to a service and borrow it.

### Analogy

- **IAM user** = a permanent employee key card
- **Role** = a visitor badge that **expires at the end of the day**
- **Trust policy** = reception's list of **who may get the badge**
- **Permission policy** = **which doors** the badge opens
- **SCP** = building rules no badge overrides

### In this project

- `chatapp-admin` (a user, with long-lived keys) was used only for setup, then **deleted**.
- `chatapp-ec2-role` and `chatapp-ecs-execution-role` meant **no keys on any server**.
- `chatapp-github-deploy`:
  - **Trust policy:** only GitHub OIDC tokens from `naamShahreyar/chatapp`, branch `main`.
  - **Permissions:** push to two ECR repositories, register task definitions, update services in cluster `chatapp`, and pass one role, only to ECS.
- The execution role could read **only** `/chatapp/*` parameters.
- `Not authorized to perform sts:AssumeRoleWithWebIdentity` was the **trust policy** refusing a mismatched `sub`.

### Traps

1. Long-lived access keys in code, `.env` files, servers or CI secrets. That's the #1 cause of breaches.
2. `"Action": "*"`, `"Resource": "*"` "just to make it work".
3. Confusing the ECS **execution role** (ECS pulls images and reads secrets) with the **task role** (your code calls AWS).
4. Forgetting that **Deny wins**. If something is mysteriously denied, look for a Deny in an SCP, a boundary or a resource policy.

### Check yourself

1. Why give EC2 an IAM role instead of copying `chatapp-admin`'s keys onto it? (Two reasons.)
2. What did `chatapp-github-deploy`'s **trust** policy say? Its **permission** policy?
3. A user's policy allows `s3:*`, and an SCP denies `s3:DeleteBucket`. Can they delete a bucket?

<details><summary>Answers</summary>

1. (a) **Temporary credentials** that rotate automatically, so a leak is short-lived. (b) **Least privilege**: the role could only read ECR and use Session Manager, while the admin keys could do *anything* in the account. (Bonus: no keys to manage at all.)
2. **Trust:** only GitHub Actions OIDC tokens for repo `naamShahreyar/chatapp` on branch `main` may assume it. **Permissions:** push to the two ECR repos, register task definitions, update services in cluster `chatapp`, and pass the execution role to ECS.
3. **No.** The explicit Deny in the SCP wins over any Allow.

</details>

---

## 3. Networking: VPC, subnets, security groups

### The idea

Networking answers one question: **who can talk to whom?**

- **VPC (Virtual Private Cloud):** your own isolated network in a region, with a private address range (e.g. `172.31.0.0/16`). Each account has a **default VPC** per region.
- **Subnet:** a slice of the VPC, **in exactly one AZ**.
  - **Public subnet:** its **route table** has a route to an **Internet Gateway**. Resources in it can have public IPs.
  - **Private subnet:** no such route. It can't be reached from the internet.
- **Internet Gateway (IGW):** the VPC's door to the internet. It's free.
- **NAT Gateway:** lets **private** resources make **outgoing** connections. It's not reachable from outside, and it costs about **$1+/day**.
- **Security group (SG):** a firewall **around each resource**:
  - **Inbound traffic is blocked by default.** You add *allow* rules only.
  - **Stateful:** responses to allowed traffic are automatically allowed back.
  - The source of a rule can be an IP range **or another security group**.

**Textbook production layout:** only the load balancer is in public subnets. The app and database sit in private subnets, with a NAT gateway for outgoing calls. We used public subnets with strict security groups to avoid the NAT cost.

**Timeout vs refused:**
- **Timeout:** traffic was **silently dropped**, usually by a security group or the wrong port.
- **Refused:** you reached the machine, but nothing was listening on that port.

### Analogy

- **VPC** = a fenced company campus
- **Subnets** = buildings on it
- **Public subnet** = a building with a street door (IGW)
- **Private subnet** = no street door
- **NAT** = a mailroom: letters can go out, but nobody can walk in
- **Security group** = a guard at each office door with a guest list ("only people wearing the backend badge")

### In this project

```
your IP ──:80──▶ [alb-sg] ──:8501──▶ [frontend-sg] ──:8000──▶ [backend-sg] ──:5432──▶ [db-sg]
```

- Each security group trusted only the **group** in front of it. IPs changed on every deploy, and the rules never needed updating.
- **RDS had two layers of protection:** no public IP (`--no-publicly-accessible`) **and** `db-sg`. That's **defense in depth**.
- Chrome **timed out** because it tried **:443**, which `alb-sg` didn't allow, so the packets were silently dropped.
- `assignPublicIp=ENABLED` let tasks pull images and call Gemini without a NAT gateway.
- Teardown had to delete the security groups **from the end of the chain**, because each one was referenced by the next.

### Traps

1. Opening database or SSH ports to `0.0.0.0/0` (the whole internet).
2. Writing rules between AWS resources using IPs instead of security group references.
3. A task with no public IP **and** no NAT can't pull images or call APIs, and fails with confusing timeouts.
4. Your home IP changes, so `/32` rules stop matching.

### Check yourself

1. What single thing makes a subnet public?
2. Why "allow 5432 **from backend-sg**" instead of from the backend task's IP?
3. Why did the browser **time out** instead of showing an error page?

<details><summary>Answers</summary>

1. Its **route table** has a route to an **Internet Gateway**.
2. Task IPs **change on every deploy**, and there may be **several** backend tasks. A group reference covers all of them, always.
3. The browser used **port 443** (HTTPS), which `alb-sg` didn't allow. Security groups **silently drop** traffic with no reply, so the browser waited and timed out.

</details>

---

## 4. Compute: EC2, ECS/Fargate, Lambda

### The idea

```
YOU manage more ◀──────────────────────────────────────────▶ AWS manages more
   EC2            ECS on EC2         ECS on Fargate          Lambda
   a VM           containers on      containers, no          functions, run
                  your VMs           visible servers         only when called
```

- **EC2:** a virtual machine. Full control, and **you** handle the OS, Docker, patches, restarts and monitoring. One instance is a single point of failure.
- **ECS:** the container orchestrator.

  | ECS term | Meaning | docker compose equivalent |
  |---|---|---|
  | **Cluster** | A group of services | The project |
  | **Task definition** | Versioned blueprint: image, CPU/RAM, ports, env vars, secrets, logs, roles | A service block |
  | **Task** | A running instance of a task definition | A running container |
  | **Service** | **Keeps N tasks running**: replaces failed ones, does rolling deploys, registers with the load balancer | `restart: unless-stopped`, and much more |

  - **Fargate** = AWS supplies the machines, and you only declare CPU and memory.
- **Lambda:** functions that run **only when triggered**, for up to **15 minutes**, billed per request. Cold starts are a downside, and they're poor for long-running servers or websockets.

| Situation | Choose |
|---|---|
| Short, event-driven or scheduled jobs | **Lambda** (scheduled with EventBridge Scheduler) |
| Long scheduled jobs (over 15 min) | **Scheduled ECS task** on Fargate |
| Always-on containerized web apps and APIs | **ECS on Fargate** |
| GPUs, special OS needs, large steady load | **EC2** |
| Kubernetes required | **EKS** |

### Analogy

- **EC2** = owning a car (you service it)
- **Fargate** = a chauffeur service
- **Lambda** = a taxi per trip

### In this project

| | Part A: EC2 | Part B: ECS Fargate |
|---|---|---|
| Setup | Installed Docker and Compose yourself | Nothing to install |
| A container crashes | Docker restarts it **on the same machine** | The ECS service **replaces the task**, possibly in another AZ |
| The machine dies | App **and database** are gone | A new task starts elsewhere; data is safe in RDS |
| Deploying | Manual commands over Session Manager | New task definition **revision** → **rolling update**, zero downtime |
| Rolling back | Manual | Deploy the previous revision |

### Traps

1. Too little Fargate memory, so the task is killed and restarts repeatedly.
2. x86 images on ARM machines (or the reverse). Match `runtimePlatform` to your images.
3. Storing data inside containers. **Tasks are disposable.**
4. Using Lambda for long-running servers or websockets.

### Check yourself

1. Task definition vs task vs service, in one line each.
2. The backend crashes at 3 a.m. What happens on EC2, and what happens on Fargate?
3. A 30-second script that runs once a night: EC2, Fargate or Lambda?

<details><summary>Answers</summary>

1. **Task definition** = the versioned blueprint. **Task** = a running instance of it. **Service** = keeps the desired number of tasks running, replaces failures, rolls out new revisions, and registers tasks with the load balancer.
2. **EC2:** with `restart: unless-stopped` and `systemctl enable docker`, Docker restarts the container on the **same machine**. If the machine itself dies, nothing recovers it. **Fargate:** ECS **replaces the task** wherever there's capacity, the load balancer follows it, and the data is safe in RDS.
3. **Lambda.** It pays only for about 30 s a night instead of an idle server, it's well under the 15-minute limit, and it can be scheduled with EventBridge Scheduler.

</details>

---

## 5. Images: ECR, tags, digests

### The idea

- **ECR (Elastic Container Registry):** AWS's private, regional image registry.
  ```
  Registry  <account>.dkr.ecr.<region>.amazonaws.com
   └── Repository  chatapp-backend   (one per image)
        └── Images  (tags + digest)
  ```
  - Pulling and pushing require **IAM permission**. `docker login` uses a **12-hour token**.
  - **Scan on push** finds known vulnerabilities (CVEs).
- **Tag vs digest:**

  | | Tag | Digest |
  |---|---|---|
  | Example | `:latest`, `:1c2a18bf…` | `@sha256:a82e7ed7…` |
  | What | A label **you** choose | A **fingerprint** of the contents |
  | Can change? | **Yes**, it can be moved | **Never** |

- **Use commit SHA tags in production**, for traceability and exact rollbacks. `latest` moves with every push.
- **Layers:** a push uploads only the layers the registry doesn't already have.
- **Image index:** modern Docker pushes a table of contents, the real image and a build attestation. That's why one push showed three ECR entries. **Never delete the untagged children.**
- **Hygiene:** rebuild regularly with `docker build --pull` for security patches. Use an ECR **lifecycle policy** to delete old images.

### Analogy

- **ECR** = a private warehouse
- **Repository** = an aisle
- **Tag** = a shelf label that can be moved
- **Digest** = the serial number stamped on the box

### In this project

- Day 5: build → tag → push by hand. EC2 and ECS pulled using **roles**.
- CI/CD tagged images with `${{ github.sha }}`, and you verified the running task definition named your commit.
- Teardown used `delete-repository --force`.

### Traps

1. Deploying only `:latest`, which loses traceability and rollbacks.
2. Deleting the "untagged" index children.
3. An expired ECR login after 12 hours.
4. **Secrets baked into images.** Anyone who can pull the image can read them, and old layers keep them even after "removal".

### Check yourself

1. Two benefits of tagging images with the commit SHA?
2. Tag vs digest?
3. "Let's copy `.env` into the image." What's wrong, and where should secrets go?

<details><summary>Answers</summary>

1. **Traceability** (you know exactly which code runs) and **exact rollback** (redeploy the previous SHA). Bonus: **consistency**, since restarted tasks pull the same code.
2. A **tag** is a movable label. A **digest** is the content's fingerprint, unique and unchangeable.
3. **Anyone who can pull the image** can read the secrets, and image layers keep history. Images contain **code**. Secrets are injected **at runtime**: SSM Parameter Store or Secrets Manager on ECS, or a locked-down `.env` **on the server** (never in the image) on EC2.

</details>

---

## 6. Traffic: load balancers and health checks

### The idea

Tasks come and go with changing IPs. Users need **one stable address** that always reaches a **healthy** copy of the app.

```
Users ─▶ DNS name ─▶ [LISTENER  HTTP :80] ─▶ [TARGET GROUP + health checks] ─▶ healthy tasks only
```

| Part | Job |
|---|---|
| **Load balancer** | Stable DNS name, spread across AZs, scaled and patched by AWS |
| **Listener** | Protocol and port, plus rules (e.g. route `/api/*` to the backend) |
| **Target group** | The list of targets (task IPs), plus **health checks** |

- **ECS registers and deregisters tasks** in the target group automatically.
- **Health checks** call a URL (e.g. `/_stcore/health`) every ~30 s. Unhealthy targets get no traffic, and ECS replaces them.
- **Grace period:** time for a new task to start before failed checks count against it.
- **Draining (deregistration delay):** during deploys, old targets get no *new* requests, but existing connections can finish. The default is 300 s.
- **HTTPS:** a domain + a free **ACM** certificate + a **:443 listener**, with :80 redirecting to it. The ALB does **TLS termination**.
- **ALB vs NLB:** an ALB understands HTTP (paths, hosts, websockets, HTTPS). An NLB is fast TCP-level balancing with fixed IPs.

### Analogy

A restaurant **host stand**:
- one entrance (the DNS name)
- seating rules (the listener)
- a list of tables (the target group)
- checking that each table is usable (health checks)
- at closing, no new guests at a table, but current diners can finish (draining)

### In this project

- The DNS name stayed the same through every deploy.
- `--load-balancers` connected the frontend service to the target group.
- You waited for `initial` → `healthy`, and CI's `wait-for-service-stability` depends on it.
- A healthy target plus a successful `/_stcore/health` test proved the problem was the browser, not AWS.
- Draining 300 s → 30 s made the frontend deploys noticeably faster.

### Traps

1. Health endpoints that always say OK, or check the wrong path.
2. A grace period shorter than the start-up time, which causes restart loops.
3. Plain HTTP in production.
4. Stateful websockets (Streamlit) with several tasks need **sticky sessions**.

### Check yourself

1. Why use the load balancer's DNS name instead of a task's IP?
2. A frontend task freezes but is still "running". What do the ALB and ECS do?
3. What is draining, and why did 300 s → 30 s speed up the second deploy?

<details><summary>Answers</summary>

1. Task IPs **change** with every deploy, restart or scaling event, and there may be several tasks. The DNS name is **stable**, always routes to **healthy** tasks, and spreads load across them and across AZs.
2. Its health checks start failing, so the **ALB marks it unhealthy and stops sending it traffic**. The **ECS service sees the unhealthy target, stops the task and starts a replacement**, which registers with the ALB once healthy.
3. Draining = during a deploy, the old task stops receiving **new** requests while **existing connections** get time to finish before it's stopped. ECS waits for draining to end before declaring the deployment complete, so a 300 s wait added about 5 minutes to every frontend deploy. 30 s is enough for in-flight requests.

</details>

---

## 7. Data: RDS

### The idea

**RDS (Relational Database Service)** runs Postgres (or MySQL and others) **for you**. You still design tables and write queries. AWS does the operational work:

| AWS handles | You handle |
|---|---|
| Installing and patching the database and OS | Schema, queries, indexes |
| **Automated backups** + point-in-time restore | Choosing the size and settings |
| Storage, monitoring, failover (Multi-AZ) | Who can connect (security groups, passwords) |

Key settings:
- **Instance class:** CPU and RAM (e.g. `db.t4g.micro`). ARM is fine here, because AWS runs the database software.
- **Endpoint:** the database's **DNS name** (`chatapp-db.xxxx.ap-south-1.rds.amazonaws.com`). Apps connect to this, not to an IP.
- **Multi-AZ:** a **standby copy in another AZ** with automatic failover. It's for resilience, and costs about double.
- **Read replicas:** extra **read-only** copies, used to **scale reads**. That's a different goal from Multi-AZ.
- **Backups:**
  - **Automated backups:** daily, kept for N days, and allow restoring to any second in that window.
  - **Snapshots:** manual, kept until you delete them, and still charged for storage.
- **Publicly accessible = No:** no public IP at all, so it's only reachable from inside the VPC.
- **Encryption:** in transit (SSL is enforced by default on recent Postgres) and at rest.

**RDS vs Postgres in a container:**
- In a container, **you** own backups, upgrades and recovery, and data is tied to one machine's disk (as in Part A).
- With RDS, data **outlives** your app servers, and backups happen without you.

(**Aurora** is AWS's cloud-native, Postgres-compatible engine. It's more scalable and more expensive. Worth knowing the name.)

### Analogy

- **Postgres in a container** = keeping your savings in a safe **at home**: you handle locks, fire and theft.
- **RDS** = a **bank vault**: you still decide what goes in and out, but the bank handles security, insurance and copies.

### In this project

- Only `DATABASE_URL` changed between local, EC2 and RDS. **The app code stayed the same** (config from env vars, Day 1).
- `db.t4g.micro`, single-AZ, 1-day backups, **not publicly accessible**, protected by `db-sg`.
- The app created its tables on first start (`create_all`). Real projects use **migrations** (Alembic) instead.
- Teardown used `--skip-final-snapshot`, which is fine for test data and **never** for real data.

### Traps

1. A publicly accessible database with an open security group.
2. Skipping the final snapshot when deleting real data.
3. Forgetting old **snapshots**, which cost money forever.
4. Too many connections from many app tasks. Each task's connection pool adds up, and small instances have low connection limits.
5. Confusing **Multi-AZ** (survive failure) with **read replicas** (scale reads).

### Check yourself

1. Name three things RDS does for you that you'd have to do yourself with Postgres in a container.
2. What's the difference between Multi-AZ and a read replica?
3. Our RDS had two separate protections against internet access. What were they?

<details><summary>Answers</summary>

1. Any three of: automated backups and point-in-time restore, OS and database patching, failover to another AZ, storage management, monitoring. Also, data no longer depends on one app server's disk.
2. **Multi-AZ** = a standby in another AZ for **automatic failover** (availability). **Read replica** = extra read-only copies to **scale read traffic** (performance).
3. **`--no-publicly-accessible`** (no public IP at all) and **`db-sg`** (only `backend-sg` may connect on 5432). That's defense in depth.

</details>

---

## 8. Secrets: Parameter Store, Secrets Manager, KMS

### The idea

Secrets (passwords, API keys, signing keys) must be:
- **stored encrypted**
- **readable only by whoever needs them** (IAM)
- **injected at runtime**, never in code, images or Git

AWS offers two stores:

| | **SSM Parameter Store** | **Secrets Manager** |
|---|---|---|
| Encrypted values | `SecureString` | Always |
| Cost | Standard tier **free** | ~$0.40 per secret per month |
| Automatic rotation | No | **Yes**, e.g. it can rotate RDS passwords for you |
| Good for | Config + simple secrets | Secrets that should rotate, database credentials |

- **KMS (Key Management Service)** holds the encryption keys. `SecureString` values are encrypted with a KMS key (by default the AWS-managed `aws/ssm` key).
- **Naming like paths** (`/chatapp/jwt-secret`) groups secrets, and lets IAM grant access to `/chatapp/*` only.
- **In ECS task definitions:**
  - `environment` = plain, visible values (e.g. `LLM_MODEL`)
  - `secrets` = **ARNs only**. ECS fetches the values at task start, using the **execution role**, and injects them as env vars.

  The task definition never contains a secret, so it's safe in Git.
- **Changing a secret:** the new value is only picked up when **new tasks start**, so you must redeploy (`--force-new-deployment`).

### Analogy

- **Parameter Store** = a **locked filing cabinet**
- **KMS** = the **locksmith who holds the keys**
- **IAM** = the **list of who may open which drawer**
- **Secrets Manager** = the same cabinet, plus an assistant who **changes the locks on a schedule**

### In this project

- Four `SecureString` parameters under `/chatapp/`: `db-password`, `database-url`, `jwt-secret`, `google-api-key`.
- Created **from variables**, so values never appeared on screen. The Gemini key was typed in hidden (`Read-Host -AsSecureString`).
- The execution role could read **only** `arn:...:parameter/chatapp/*`.
- **Separate production secrets** from development (new JWT secret, new DB password).
- Contrast with Part A: a `.env` file generated **on the server**, `chmod 600`.

### Traps

1. Printing secrets with `--with-decryption` into terminals, logs or chat.
2. Logging secrets by accident (e.g. logging a full `DATABASE_URL`).
3. Secrets in images, Git, or plain `environment` blocks.
4. Changing a secret and forgetting that running tasks still use the old value.

### Check yourself

1. Why was it safe to commit `infra/backend-task.json` to a public repo even though the backend needs three secrets?
2. Parameter Store or Secrets Manager: when would you pay for Secrets Manager?
3. You update `/chatapp/google-api-key`, but the app keeps using the old key. Why, and what do you do?

<details><summary>Answers</summary>

1. Its `secrets` section contains only **ARNs**, the addresses of the secrets, never the values. Reading the values requires IAM permission, which only the execution role has.
2. When you want **automatic rotation**, for example database passwords that change on a schedule without downtime, or other Secrets Manager features like cross-account sharing.
3. Secrets are injected **when a task starts**. Running tasks keep the old value. Redeploy: `aws ecs update-service ... --force-new-deployment`.

</details>

---

## 9. Observability: CloudWatch and CloudTrail

### The idea

Two different questions, two different services:

| | **CloudWatch** | **CloudTrail** |
|---|---|---|
| Answers | "What is **my app** doing?" | "Who did **what** to **my AWS account**, and when?" |
| Data | App **logs**, **metrics** (CPU, memory, request counts), **alarms** | A record of **every AWS API call**: who, what, when, from where, allowed or denied |
| Example | `Application startup complete`; the Gemini 503 traceback | `AssumeRoleWithWebIdentity` denied, with the exact identity presented |

**CloudWatch Logs:**
- **Log group** (e.g. `/ecs/chatapp-backend`) → **log streams** (one per task).
- ECS's `awslogs` driver ships container **stdout/stderr**. That's why `PYTHONUNBUFFERED=1` mattered.
- **Retention:** the default is **forever**, which you keep paying for. Set e.g. 7 days.
- `aws logs tail <group> --since 15m` or `--follow`.

**CloudWatch Metrics and Alarms:**
- Services publish metrics automatically: ECS CPU and memory, ALB request counts and **5xx errors**, RDS connections.
- **Alarms** notify you (e.g. by email) when a metric crosses a threshold, like "more than 10 errors in 5 minutes".

**CloudTrail:**
- **Event history** (90 days) is on by default.
- Essential for **security investigations** and **debugging permission problems**.

**Also useful:** **ECS service events** (`describe-services ... events`), which show deployments, task starts and stops, and draining.

### A debugging workflow (what we actually did)

1. **What does the user see?** (a timeout? a 500? a 504?)
2. **Is the infrastructure healthy?** (service running counts, target health)
3. **Read the logs** for the request time (CloudWatch Logs).
4. **For permission errors:** read the whole message, then **CloudTrail** to see what was actually presented.
5. Change **one thing**, then verify.

### Analogy

- **CloudWatch Logs** = the app's **diary**
- **Metrics and alarms** = the **dashboard gauges and warning lights**
- **CloudTrail** = the building's **security camera and visitor log**

### In this project

- `Application startup complete` proved that the image pull, secrets and DB connection all worked.
- Logs showed signup and login succeeding, but `POST .../messages` failing with **Gemini 503**. So the cause was the model, not AWS.
- The missing log line plus a 504 after about 15 s pointed at the **Service Connect proxy timeout**.
- **CloudTrail** showed GitHub's real `sub` (`repo:naamShahreyar@140060453/chatapp@...`), which fixed OIDC in one step.
- **Service events** showed the 5-minute **draining** gap.

### Traps

1. Infinite log retention, which costs money and grows forever.
2. Buffered output, so logs appear late or never.
3. Catching exceptions without logging them (use `logger.exception`).
4. Logging secrets or personal data.
5. No alarms, so you only find out about problems from users.

### Check yourself

1. CloudWatch or CloudTrail: "Who deleted the database yesterday?" "Why did the backend crash at 3 a.m.?"
2. How did the logs prove that a failed chat was Gemini's fault, not AWS's?
3. Why do CloudWatch log groups need a retention setting?

<details><summary>Answers</summary>

1. "Who deleted the database?" → **CloudTrail** (API call records). "Why did the backend crash?" → **CloudWatch Logs** (the app's own output), plus ECS service events.
2. The same backend task successfully handled signup, login and listing conversations (so networking, the database and secrets all worked). Only the message request failed, with a traceback ending in Google's **`503 UNAVAILABLE`** / quota error.
3. By default logs are kept **forever**, and storage keeps costing money. Old debug logs are rarely useful.

</details>

---

## 10. CI/CD and OIDC

### The idea

- **CI (Continuous Integration):** every change is automatically **tested and built**, so problems surface within minutes of a push.
- **CD:**
  - **Continuous Delivery:** every change is *ready* to release with one click.
  - **Continuous Deployment:** every change that passes is **released automatically**. That's what we built.

**Our pipeline:**

```
pull request ─▶ test ─▶ build                    (never deploys)
push to main ─▶ test ─▶ build ─▶ deploy:
                                   1. OIDC → temporary AWS credentials
                                   2. build + push images tagged with the commit SHA
                                   3. register a new task definition revision (from infra/*.json)
                                   4. update the ECS service → rolling deploy
                                   5. wait for stability (fail the run if it's unhealthy)
```

**OIDC (OpenID Connect): keyless access from CI to AWS:**

```
GitHub ── signed token: "repo X, branch main" ──▶ AWS STS
                                                    ├ is GitHub's signature valid?
                                                    ├ does the role's trust policy match aud + sub?
GitHub ◀────────── credentials valid for 1 hour ────┘
```

- No AWS keys stored in GitHub, so **nothing to leak**.
- The trust policy pins **`aud`** (`sts.amazonaws.com`) and **`sub`** (which repo and branch).
  - GitHub's `sub` now includes **IDs**: `repo:owner@<id>/repo@<id>:ref:refs/heads/main`. A deleted-and-recreated repo with the same name can't take over.
- The job needs `permissions: id-token: write`.

**Other pipeline principles we used:**
- **Gates:** deploy only if the tests and build pass.
- **Only `main` deploys,** enforced twice: by the workflow's `if:` **and** by the trust policy.
- **Immutable SHA tags** and **versioned task definitions** make rollback easy.
- **`concurrency`:** deploys run one at a time, never racing.
- **The repo is the source of truth:** manual changes in AWS get overwritten by the next deploy. Change the files instead.

### Analogy

- **CI** = a **quality inspector** checking every product off the line
- **CD** = the **automatic loading dock** that ships approved products
- **OIDC** = a **one-hour courier pass** issued at the gate after checking the driver's company ID. No permanent key ever changes hands.

### In this project

- Your Step 7.1 error-handling change reached production only through the pipeline.
- The first OIDC attempt failed because the `sub` used the old format. CloudTrail showed the real one.
- You verified that production ran image `chatapp-backend:<your commit SHA>`.
- The deploy job was disabled with `if: false` after teardown, so pushes don't fail.

### Traps

1. Long-lived AWS keys stored as CI secrets.
2. Letting pull requests or other branches deploy.
3. Deploying `latest` instead of immutable tags.
4. Manual changes in the console that drift from the repo (and get overwritten).
5. No health wait, so "deploy succeeded" while the app is actually crashing.

### Check yourself

1. Why is OIDC safer than storing an IAM user's access keys in GitHub Secrets? (Two reasons.)
2. Name the two independent things that stop a pull request from deploying.
3. You changed `LLM_MODEL` by hand in the ECS task definition. The next day someone pushes a README fix. What happens to your change, and what should you have done?

<details><summary>Answers</summary>

1. (a) **No long-lived secret exists** to leak: credentials are created per run and expire in an hour. (b) The **trust policy** limits use to one repository and branch, so a stolen token from elsewhere is useless. Bonus: no keys to rotate.
2. The workflow's **`if: github.event_name == 'push' && github.ref == 'refs/heads/main'`**, and the role's **trust policy `sub`**, which only accepts `refs/heads/main`.
3. The pipeline deploys `infra/backend-task.json` from Git, so **your manual change is overwritten** with the file's value. You should have **changed the file, committed and pushed**, letting the pipeline deploy it. (Also consider `paths-ignore` so docs-only commits don't deploy at all.)

</details>

---

## 11. Cost

### The idea

AWS bills **for what you use, while it exists**, along a few dimensions:

| Dimension | Examples |
|---|---|
| **Time running** | EC2 per second, Fargate per vCPU-second and GB-second, RDS per hour, **load balancer per hour**, NAT per hour |
| **Storage** | EBS disks, RDS storage, **snapshots**, ECR images, CloudWatch logs |
| **Requests / usage** | Lambda calls, API requests, load balancer capacity units |
| **Data transfer** | Data **out** to the internet, traffic between AZs/regions, NAT data processing |
| **Small fixed items** | **Public IPv4 addresses** (per hour each), Secrets Manager secrets, KMS keys |

**What this project cost (approximately, Mumbai):**

| Resource | ~Cost |
|---|---|
| EC2 `t3.small` | $0.55/day |
| Load balancer | $0.60/day (**even with zero traffic**) |
| Fargate (2 small tasks) | $0.50/day |
| RDS `db.t4g.micro` | $0.50/day |
| Public IPv4 address | ~$0.12/day each |
| ECR, Parameter Store, logs | cents |

**Hidden or forgotten costs:**
- **NAT gateways** (~$1+/day idle)
- **Stopped EC2** still pays for its **disk**
- **Load balancers** bill while idle
- **Logs with no retention**
- **Old snapshots**
- **Public IPs**
- Resources in **other regions** you forgot about

**Tools:**
- **Budgets:** alert emails at thresholds (your $10 budget).
- **Cost Explorer:** where the money went, by service and day.
- **Free Tier and credits:** your $120 credit.
- **Tags:** label resources (e.g. `project=chatapp`) to see cost per project.
- **Stop vs terminate:** stop pauses compute but keeps the disk. Terminate deletes everything.
- **Teardown in dependency order,** then **audit**: list each resource type, check other regions, and check the bill a few days later.

### Analogy

AWS is like a **utility meter**. Every light left on (a load balancer, a NAT gateway, a forgotten disk) keeps the meter running, even when nobody's home. **Budgets** are a smart meter that texts you. **Teardown** is switching off the main breaker before you go on holiday.

### In this project

- The budget alarm was set **before** building anything.
- The NAT gateway was **deliberately avoided** by using public subnets and security groups.
- EC2 was **stopped** overnight, then **terminated** once ECS replaced it.
- 7-day log retention.
- Full teardown in dependency order, plus an audit across resource types and regions.
- Deleting `chatapp-admin` and its long-lived keys (security hygiene, which also protects your bill).

### Traps

1. "It's stopped, so it's free." The disk, IP and load balancer still bill.
2. Forgotten resources in another region.
3. No budget alarm.
4. Leaked access keys used for crypto-mining, the classic surprise bill.
5. Skipping the post-teardown check of the bill.

### Check yourself

1. Name three things that keep costing money even when your app gets zero traffic.
2. What's the difference between **stopping** and **terminating** an EC2 instance, cost-wise?
3. Why did we avoid a NAT gateway, and what did we use instead?

<details><summary>Answers</summary>

1. Any three of: the **load balancer**, **RDS** (while running), **Fargate tasks** (while running), a **NAT gateway**, **public IPv4 addresses**, **EBS disks** (even on stopped instances), **snapshots**, **stored logs**, **ECR images**.
2. **Stopped:** no compute charge, but the **disk (EBS) is still billed**, and the public IP is released (and changes on restart). **Terminated:** the instance and its disk are deleted, and billing stops entirely.
3. It costs about **$1+/day even when idle**, plus data charges. We gave Fargate tasks **public IPs in public subnets** for outgoing access, and relied on **security groups** to block all unwanted inbound traffic.

</details>

---

## Where to go next

1. **Rebuild once from [aws-runbook.md](aws-runbook.md)** without help, and use this guide whenever a step's *why* is unclear.
2. **Draw the architecture from memory**, including which security group allows what.
3. **Explain one concept out loud** each day. Teaching is the fastest way to find gaps.
4. **Infrastructure as code (Terraform):** describe all of the above in files, and create or destroy everything with one command.
