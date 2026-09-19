# AutoCare CI/CD Pipeline — Architecture, Decisions & Troubleshooting Record

## 1. Purpose

This document explains what the AutoCare CI/CD pipeline does, why each stage exists, which alternatives were considered, and the implementation problems discovered during the first DEV run.

## 2. Executive Summary

AutoCare uses GitHub for source control and environment promotion and Azure AKS for deployment. The same environment-neutral workflow is used for dev, uat and main; the Git branch determines the target environment.

Promotion model:

feature → Pull Request + approval → dev → Pull Request + approval → uat → Pull Request + approval → main → production Environment approval

PR rules provide change governance. GitHub Environment protection controls production deployment approval. Azure authentication uses GitHub OIDC with a user-assigned managed identity rather than a stored Azure client secret.

## 3. Git Branch and Promotion Model

| Branch | Meaning | Pipeline behavior |
|---|---|---|
| dev | Development | Build → deploy to Kubernetes dev → verify |
| uat | User acceptance testing | Build → deploy to Kubernetes uat → verify |
| main | Production | Production Environment approval → build/deploy → verify |

Direct pushes to protected branches are blocked. Changes enter through pull requests, with reviewer approval and latest-push approval protection.

## 4. Workflow Trigger — Push Only

The workflow intentionally triggers only after a push to dev, uat or main.

```yaml
on:
  push:
    branches:
      - dev
      - uat
      - main
```

Why: pull-request governance is already handled by GitHub branch rulesets. The deployment workflow acts on code that has actually entered the protected environment branch. There is intentionally no pull_request deployment trigger.

## 5. Five-Stage Pipeline

```text
Push to protected branch
        ↓
Stage 1 — Application Validation
        ↓
Stage 2 — Kubernetes Validation
        ↓
Stage 3 — Build Image
        ↓
Stage 4 — Deploy
        ↓
Stage 5 — Verify
```

Each stage has a separate responsibility.

## 6. Stage 1 — Application Validation

Stage 1 performs lightweight application checks that do not require Azure or AKS access:

- Checkout the repository.
- Set up the application runtime.
- Install dependencies.
- Compile Python source.
- Perform a basic import check.

Why: fail early on inexpensive application/package problems before consuming Docker build time or Azure/Kubernetes access.

## 7. Stage 2 — Kubernetes Validation

Stage 2 is intended to validate the Kubernetes manifests for the branch-selected environment before deployment.

Expected mapping:

- dev → k8s/dev/
- uat → k8s/uat/
- main → k8s/prod/

### First DEV-run issue

The initial workflow used kubectl apply with dry-run and validation enabled. The intention was to validate manifests without actually deploying them.

However, schema validation can retrieve the Kubernetes API/OpenAPI schema. Because AKS is private, kubectl attempted to contact the private AKS API endpoint. kubelogin then needed Azure credentials, but Stage 2 deliberately did not perform az login.

Observed failure:

```text
AzureCLICredential: ERROR: Please run 'az login' to setup account
exec: executable kubelogin failed with exit code 1
```

Important: this failure does not prove that the Kubernetes manifests are invalid. It proves that the selected validation method unexpectedly depended on Azure authentication, kubelogin and private AKS access.

## 8. Stage 2 — Options Considered

### Option A — Add Azure login to Stage 2

Technically workable, but it makes an otherwise static validation stage depend on Azure authentication, OIDC, private-network access and AKS availability.

Decision: not chosen.

### Option B — Disable schema validation

Using validation=false would avoid the authentication problem but weakens validation instead of solving the underlying design issue.

Decision: not chosen.

### Option C — Offline Kubernetes schema validation

Use a tool such as kubeconform to validate manifests without contacting AKS.

Decision: chosen.

Why: it keeps Stage 2 independent of Azure, AKS authentication and private cluster availability while still providing meaningful Kubernetes schema validation.

Status: this is the next workflow correction; it had not yet been applied when this document was created.

## 9. Stage 3 — Build Image

Stage 3 builds the Docker image without Azure login.

Responsibilities:

- Determine the environment from the branch.
- Build the image using the commit SHA.
- Save the image to a temporary archive.
- Upload the archive as a short-retention GitHub Actions artifact.

Why: build and deployment are separate responsibilities. A fresh Docker build is intentionally performed for each environment branch; this project is not using a build-once/promote-the-same-image model.

## 10. Stage 4 — Deploy

Stage 4 is the Azure/Kubernetes deployment stage and is intentionally the only stage that references the GitHub Environment.

Responsibilities:

- Use the target GitHub Environment and environment-specific variables.
- For production, wait for the Environment's required reviewer approval.
- Authenticate to Azure through GitHub OIDC.
- Use the AutoCare GitHub Actions user-assigned managed identity.
- Load the Docker artifact.
- Log in to ACR and push the immutable environment/SHA image.
- Obtain AKS credentials into a temporary KUBECONFIG.
- Authenticate kubectl with kubelogin.
- Render IMAGE_PLACEHOLDER with the final image.
- Run one kubectl apply.
- Wait for rollout completion.
- If rollout fails, attempt rollback and verify it.
- Clean up the temporary kubeconfig.

Environment-specific values such as ACR login server/name and AKS resource group/cluster name are kept in GitHub Environment variables so the workflow remains environment-neutral.

## 11. Why GitHub Environments Are Used

The production Environment provides a human deployment approval gate. Dev and UAT do not require that production deployment approval.

Production flow:

main push → Stage 4 → prod Environment approval → Azure authentication → ACR push → AKS deployment

## 12. Stage 5 — Verify

Stage 5 proves that deployment is usable after Stage 4.

Responsibilities:

- Authenticate to Azure.
- Access the target AKS cluster.
- Check rollout status.
- Inspect the deployment and pods.
- Run an application-level health check.
- Clean up the temporary kubeconfig.

Why: a successful Kubernetes rollout proves Kubernetes accepted and started the workload, but it does not necessarily prove that the application itself is healthy.

## 13. Stage 5 — Production Approval Problem

We identified a design problem before finalizing the workflow: if Stage 5 also referenced the prod GitHub Environment, it would also be part of the Environment protection model. That could create another production approval dependency and would make verification less independent.

The goal is for production approval to happen at deployment while verification remains an independent technical check.

## 14. Stage 5 Options and Decision

### Option A — Independent verification

Remove the GitHub Environment reference from Stage 5 and authenticate using a branch-based GitHub OIDC federated credential.

Chosen design:

Stage 4 → GitHub Environment → production approval → deployment

Stage 5 → no GitHub Environment → branch-based OIDC → independent AKS verification

### Option B — Reuse the Environment-based identity

Keep Stage 5 attached to the GitHub Environment and use the environment-based federated credential.

Decision: not chosen because it couples verification to the Environment protection gate.

## 15. OIDC Design

The Azure user-assigned managed identity is authenticated using GitHub OIDC. No Azure client secret is stored in GitHub.

Two federated-credential patterns are deliberately used:

- Stage 4: environment-based subject ending in environment:<environment>.
- Stage 5: branch-based subject ending in ref:refs/heads/<branch>.

The maintenance-service repository has environment credentials for dev, uat and prod, plus branch credentials for dev, uat and main.

This separation allows production deployment to remain Environment-gated while post-deployment verification runs independently.

## 16. Kubernetes Manifest Strategy

The project uses simple environment directories rather than Kustomize overlays:

```text
k8s/
├── dev/
├── uat/
└── prod/
```

The older k8s/base and k8s/overlays structure is being retired only after the new workflow and DEV deployment are established.

## 17. Image Rendering Strategy

Source-controlled deployment manifests contain:

```yaml
image: IMAGE_PLACEHOLDER
```

The pipeline creates a temporary rendered copy and replaces the placeholder with an immutable image such as:

```text
<ACR_LOGIN_SERVER>/<IMAGE_NAME>:dev-<COMMIT_SHA>
```

Only the rendered copy is applied.

Why: an earlier apply-then-set-image approach would cause two rollout operations and leave the source manifest different from the deployed image. The chosen design renders the final image and performs one kubectl apply.

## 18. Reliability and Security Controls

- Every job has a timeout.
- contents: read is global; id-token: write is limited to Azure-authenticated jobs.
- Validation concurrency can cancel obsolete runs; active deployment is not cancelled.
- AKS credentials use a temporary KUBECONFIG rather than a persistent runner kubeconfig.
- Temporary kubeconfig cleanup runs even when a job fails.
- Images use commit-SHA-based immutable tags.
- Rollout failure triggers an automatic rollback attempt and rollback verification.

## 19. Self-Hosted Runner Context

The project uses a self-hosted GitHub Actions runner because AKS has a private API endpoint and the runner can reach that endpoint.

The GitHub Actions managed identity is not attached to the runner VM. Azure authentication happens explicitly through GitHub OIDC in the workflow.

## 20. Current DEV Run Status

| Area | Status | Result |
|---|---|---|
| Protected branch governance | Passed | Direct push to dev was blocked as intended |
| PR reviewer governance | Passed | abhay-reviewer supplied the required approval |
| PR merge | Passed | PR merged into dev |
| Workflow trigger | Passed | Merge generated the dev push workflow |
| Stage 1 | Passed | Application validation completed |
| Stage 2 | Failed | kubectl validation attempted authenticated access to private AKS |
| Stage 3 | Not reached | Blocked by Stage 2 |
| Stage 4 | Not reached | Blocked by Stage 2 |
| Stage 5 | Not reached | Deployment must succeed first |

## 21. Decisions We Have Made

1. Protected branches are changed through pull requests, not direct pushes.
2. The workflow is push-only for dev, uat and main.
3. One environment-neutral workflow is used for all environments.
4. A fresh Docker build is intentionally made for each environment branch.
5. Images use immutable commit-SHA-based tags.
6. Kubernetes deployment uses IMAGE_PLACEHOLDER plus one kubectl apply.
7. Only Stage 4 references the GitHub Environment and owns the production approval gate.
8. Stage 5 is independent and uses branch-based OIDC credentials.
9. Offline Kubernetes schema validation is the intended fix for the current Stage 2 problem.
10. Automatic rollback is attempted when rollout fails.

## 22. Manager / Interview Explanation

> We separated validation, build, deployment and verification. GitHub branch protection controls what enters each environment branch, while GitHub Actions deploys only after the merge. Azure access uses OIDC instead of stored secrets. Production deployment is gated through the prod Environment, while post-deployment verification is intentionally independent so it does not create another production approval dependency. We verify both Kubernetes rollout and application health. During the first DEV run, we found that our initial kubectl schema validation contacted the private AKS API and therefore required Azure authentication. Instead of weakening validation or coupling Stage 2 to Azure, we are moving that validation to an offline schema validator.

## 23. Next Step

Replace the current cluster-dependent Stage 2 validation with offline Kubernetes schema validation, then rerun the DEV workflow.

Do not change Azure networking or undo the successful Git governance setup because of the Stage 2 failure. The failure is isolated to the validation method.

## Related Documentation

- docs/AKS-DEPLOYMENT.md — AKS deployment and Kubernetes context
- docs/DOCKER-DEEP-DIVE.md — Docker/container details
- .github/workflows/pipeline.yaml — actual CI/CD implementation