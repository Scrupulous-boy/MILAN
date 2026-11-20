# AWS Migration Execution Plan

**Selected source:** `Scrupulous-boy/MILAN`  
**Status:** architecture and CI foundation only; no AWS resources or production data have been migrated.  
**Candidate region:** `ap-south-1` (Mumbai), pending final cost and latency validation.  
**Current production:** keep Google Cloud / Voroa unchanged until AWS passes the full acceptance gate and cutover is approved.

## 1. What is actually in this repository

| Component | Current implementation | AWS target | Migration condition |
|---|---|---|---|
| Main MILAN web/API | Node.js 24, Express, static frontend served by the backend; root `Dockerfile` | ECS Fargate service behind HTTPS Application Load Balancer; image in ECR | Reproduce Cloud Run health, startup, upload, range-streaming and shutdown behavior |
| Authoritative application data | PostgreSQL through `MILAN_DATABASE_URL`; schema migrations in `backend/migrations/` | Amazon RDS for PostgreSQL 18.6, private subnets, encrypted storage, backups/PITR | Restore and reconcile a verified source snapshot; preserve roles, grants, functions, constraints, indexes and row counts |
| Durable media | `public.dwn_storage` stores media bytes in PostgreSQL `bytea`; local disk is temporary processing/cache | Keep PostgreSQL as the initial source of truth; evaluate S3 only in a separate application-level storage redesign | Do not copy media to S3 and silently change the application's persistence contract |
| Video processing | FFmpeg/ffprobe, resumable chunks and HTTP Range streaming; 768 MiB shared media limit | Fargate task with explicitly sized memory/ephemeral disk; S3/EFS only if code contract is intentionally changed | Load-test worst-case upload/transcode and interrupted/resumed uploads |
| Travel Agent | Separate FastAPI + LangGraph + FAISS service in `Travel-Agent/`; Python 3.12 Dockerfile | Separate private ECS Fargate service and internal service discovery | Persist FAISS index and replace in-process `MemorySaver` before production; current README says `MemorySaver` is RAM-only |
| Android app | Gradle/Java 17 build under `app/` and `android/` | Keep as GitHub-hosted build/release artifact; AWS is not an Android runtime | Verify signing/release distribution separately; never put signing keys in repository |
| External integrations | Cashfree, UPI, email, AI providers, optional Redis and Travel Agent URL | AWS Secrets Manager for credentials; outbound access from private tasks; Redis only if verified as required | Revalidate provider webhooks, callback URLs, allowlists and DNS; preserve sandbox/test mode |
| Static assets and domain | `milanlife.in`, served by main app | ALB + ACM; existing DNS provider can point to ALB after validation | Do not alter DNS until rollback-ready cutover |

## 2. Important source-of-truth findings

- `backend/.env.example` says PostgreSQL is authoritative and explicitly says the signed DWN protocol engine is not currently integrated/verified in this path (`MILAN_REAL_DWN_ENGINE=false`). AWS deployment must not claim a live standards-compliant DWN engine merely because the product is described as Web5/DWN.
- `docs/ENVIRONMENT_CONTRACT.md` still describes Google Cloud Run/Cloud SQL, while `docs/MILAN_ONE_ARCHITECTURE.md` describes a Voroa target and names a different GitHub repository. The selected source of truth for this migration is the user-confirmed repository above; conflicting provider/repository instructions in old docs are historical notes, not deployment authority.
- The existing production Dockerfile is written for Cloud Run but is portable as a container. Its runtime contract is Node 24, `PORT=8080`, FFmpeg/ffprobe and `npm start`.
- The current schema and media contract must be preserved during the first cutover. A storage redesign is not a prerequisite for moving the existing behavior to RDS.

## 3. Proposed AWS landing zone

1. **Identity and delivery:** GitHub Actions → GitHub OIDC → narrowly scoped AWS IAM deployment role. No long-lived AWS access keys in GitHub. Scope trust to this repository and the approved deployment branch/environment.
2. **Container registry:** separate ECR repositories for `milan-web` and `milan-travel-agent`, immutable commit-SHA tags and image scanning.
3. **Network:** VPC spanning at least two Availability Zones; public ALB subnets; private ECS and RDS subnets; security groups allow only ALB-to-web and web-to-database/service traffic. No public database endpoint.
4. **Runtime:** ECS Fargate for the main service and Travel Agent. CloudWatch Logs, health checks, deployment circuit breaker and task rollback.
5. **Database:** RDS PostgreSQL 18.6 in private subnets, encrypted storage, automated backups and point-in-time recovery. Match extensions, collation, timezone, connection limits and PostgreSQL behavior before choosing instance size.
6. **Secrets:** AWS Secrets Manager; keep JWT, DID encryption, database URL, Cashfree, mail and AI provider credentials out of Terraform outputs, logs, artifacts and source control.
7. **Domain:** ACM certificate and ALB listener first; existing DNS unchanged until staging and smoke tests pass. Route 53 is optional, not a migration requirement.
8. **Optional services:** ElastiCache, S3, EFS and CloudFront are not automatically provisioned. Each needs a code-path requirement and a cost review.

RDS PostgreSQL 18.6 is listed in AWS's supported release notes. The final engine version must still be checked in the chosen region at apply time: https://docs.aws.amazon.com/AmazonRDS/latest/PostgreSQLReleaseNotes/postgresql-versions.html

## 4. Database and media migration procedure

1. Capture a consistent, encrypted source backup and record PostgreSQL version, database size, table row counts, constraints, indexes, functions, extensions, grants, owners and RLS policies.
2. Create RDS in an isolated subnet group. Create the target schema and roles from reviewed migrations; do not assume `npm run build` creates a production schema.
3. Restore a point-in-time-consistent copy into a staging RDS database. The known Voroa database snapshot was approximately 53 MB, with about 39.7 MB of `bytea` media bytes in `dwn_storage`; remeasure before the real migration.
4. Compare source and target table counts, key aggregates, bytea lengths/checksums, unique keys, foreign keys, functions, permissions and application-level read/write behavior.
5. Run the complete test and smoke suite against AWS staging, including sign-up/login, WebAuthn, DID encryption/decryption, private-record visibility, resumable upload, media byte-range playback, video transcoding, payment state/idempotency, mail and restart persistence.
6. Perform a final write freeze or planned replication/catch-up, take a final source backup, reconcile deltas and only then consider DNS cutover.
7. Keep the source database and GCP production intact through the rollback window. Decommission only after explicit sign-off and a verified retained backup.

**Hard blocker:** the source database URL and live access are not available to this hosted workflow. They were previously stored in the local WSL environment. No production database has been read or copied by this migration branch.

## 5. Release gates

- [ ] GitHub OIDC provider and least-privilege role are configured for this repository.
- [ ] Cost guardrails and budget alerts exist before any billable AWS resource is created.
- [ ] Infrastructure plan reviewed; Terraform state is encrypted and stored remotely with locking.
- [ ] AWS staging services and RDS created; no production DNS change.
- [ ] Source database backup/restore verified and data reconciliation is exact.
- [ ] App, AI, payment, identity, media, mobile, and integration smoke tests pass.
- [ ] Fortloom companion service passes its own AWS-specific persistence and auth gates.
- [ ] Cutover and rollback window approved; only then change DNS.
- [ ] GCP/Voroa retirement explicitly approved after the rollback period.

## 6. What this PR does not do

It does not create AWS infrastructure, accept new cloud charges, read or migrate production secrets, change DNS, modify the production database, change GCP services or deploy user traffic. It establishes a verifiable path to do those things safely through hosted automation rather than daily WSL commands.

## 7. One-time GitHub-to-AWS identity setup (no permanent AWS keys)

Use GitHub OIDC so workflows receive short-lived AWS credentials. Do not add an IAM user's access-key secret to GitHub.

Create the AWS IAM OIDC provider once:
- Provider URL: `https://token.actions.githubusercontent.com`
- Audience: `sts.amazonaws.com`

Then create a separate deployment role for this repository. Its trust policy must restrict both the audience and exact branch subject:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {
      "Federated": "arn:aws:iam::517570868920:oidc-provider/token.actions.githubusercontent.com"
    },
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {
        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
        "token.actions.githubusercontent.com:sub": "repo:Scrupulous-boy/MILAN:ref:refs/heads/migration/aws-autonomous-foundation"
      }
    }
  }]
}
```

This repository was created before GitHub's 15 July 2026 immutable-subject change, so its expected subject is the legacy repository-name form unless the repository has explicitly opted in to immutable subjects. Re-check the issued claim before finalizing trust.

**Do not attach AdministratorAccess.** The role's permission policy will be generated from the reviewed infrastructure stack and limited to the needed ECR/ECS/RDS/CloudFormation/Secrets Manager actions. The AWS account's current IAM identity is read-only, so an account administrator must create the provider and role through the AWS Console once.

## 8. AWS account-plan and cost gate

The account is currently on the AWS Free account plan. Before provisioning, confirm the selected services are eligible for this plan and that the plan will not close while the new production stack is running. AWS states that the Free account plan ends after six months or when credits are exhausted, whichever comes first; after expiry the account closes unless upgraded within the stated recovery window. A production migration must not rely on a trial account that can close and remove access to resources.

Create cost alerts and confirm whether an upgrade to the Paid account plan is required before any always-on ALB, ECS service, or RDS database is created. Do not interpret the promotional credits as a guarantee that the complete always-on stack will remain free.
