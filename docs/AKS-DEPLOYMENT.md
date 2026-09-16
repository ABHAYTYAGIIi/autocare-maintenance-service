# AutoCare Maintenance Service — AKS Deployment

## Status

The AutoCare maintenance-analysis service is deployed and verified in the `dev` namespace of AKS.

It is an internal FastAPI service. It is not exposed directly to the Internet. The AutoCare API calls it through a Kubernetes ClusterIP Service.

## Complete architecture

```text
                         Internet / Browser
                                |
                                v
                    +--------------------------+
                    | Azure Application Gateway |
                    | Public IP: 4.247.238.128 |
                    +------------+-------------+
                                 |
                                 | Ingress /autocare/
                                 v
                    +--------------------------+
                    | autocare-frontend Service |
                    | ClusterIP :8080           |
                    +------------+-------------+
                                 |
                                 v
                    +--------------------------+
                    | Frontend nginx            |
                    | React static application   |
                    +------------+-------------+
                                 |
                                 | /autocare/api/
                                 v
                    +--------------------------+
                    | autocare-api Service      |
                    | ClusterIP :3000           |
                    +------------+-------------+
                                 |
                                 v
                    +--------------------------+
                    | AutoCare API Pod          |
                    | Node.js + Express :3000   |
                    +------------+-------------+
                                 |
                                 | POST /maintenance-analysis
                                 | http://autocare-maintenance-service:8001
                                 v
                    +-----------------------------+
                    | Maintenance Service         |
                    | ClusterIP :8001             |
                    | FastAPI + Uvicorn           |
                    +-----------------------------+

                         AutoCare API data path
                                 |
                                 v
                    +--------------------------+
                    | Azure SQL                |
                    | sqldb-autocare-dev       |
                    +--------------------------+
```

## Service purpose

The service provides deterministic maintenance-risk analysis based on:

- vehicle mileage;
- vehicle age in years;
- service history.

The application currently exposes:

```text
GET  /health
POST /maintenance-analysis
```

## API contract

Request:

```json
{
  "mileage": 18500,
  "vehicle_age_years": 4,
  "service_history": []
}
```

Response:

```json
{
  "riskLevel": "medium",
  "recommendation": "Schedule a routine maintenance check."
}
```

The current recommendation logic is intentionally simple and deterministic.

The service returns:

```text
high   -> comprehensive inspection recommendation
medium -> routine maintenance recommendation
low    -> continue regular manufacturer service schedule
```

## Kubernetes environment

- Cluster: `aks-azure-project`
- Resource group: `rg-azure-aks`
- Namespace: `dev`
- ACR: `acrazureproject.azurecr.io`
- Deployment: `autocare-maintenance-service`
- Service: `autocare-maintenance-service:8001`
- Service type: `ClusterIP`
- Container port: `8001`

The service is internal by design.

## Health probes

The Kubernetes Deployment uses the application health endpoint for readiness and liveness checks:

```text
GET /health
```

Readiness indicates whether the pod is ready to receive traffic. Liveness allows Kubernetes to detect an unhealthy process and restart the container when required.

## Internal service discovery

The AutoCare API reaches the maintenance service using Kubernetes DNS:

```text
http://autocare-maintenance-service:8001
```

The API then calls:

```text
POST /maintenance-analysis
```

The service does not need a public IP, LoadBalancer, or external Ingress rule.

## Container image

The image used by the development deployment is stored in ACR:

```text
acrazureproject.azurecr.io/autocare-maintenance-service:v1
```

The repository application is built as a Python 3.12 slim container and runs FastAPI/Uvicorn on port `8001`.

## Manual deployment

Current development deployment is manual.

Example:

```powershell
az acr login --name acrazureproject

docker build -t acrazureproject.azurecr.io/autocare-maintenance-service:<tag> .
docker push acrazureproject.azurecr.io/autocare-maintenance-service:<tag>

kubectl set image deployment/autocare-maintenance-service `
  maintenance-service=acrazureproject.azurecr.io/autocare-maintenance-service:<tag> `
  -n dev

kubectl rollout status deployment/autocare-maintenance-service -n dev
```

## Verification completed

The running service was tested from inside the AKS cluster.

Health check:

```text
GET http://autocare-maintenance-service:8001/health
-> 200 OK
```

Analysis check:

```text
POST http://autocare-maintenance-service:8001/maintenance-analysis
-> 200 OK
```

The service returned the expected risk level and recommendation for the development test input.

The complete application integration was also verified through the public API path:

```text
Browser
  -> Application Gateway
  -> Ingress
  -> Frontend
  -> AutoCare API
  -> Maintenance Service
  -> analysis response
```

## Security boundary

The maintenance service is an internal backend component.

Traffic should follow:

```text
External client
      |
      v
Application Gateway
      |
      v
Frontend
      |
      v
AutoCare API
      |
      v
Maintenance Service
```

The maintenance service should not be directly exposed to the public Internet.

## Future deployment model

The current deployment is intentionally simple and manual. The future CI/CD design will use Kustomize for environment-specific manifests and image references.

Target flow:

```text
Git push
   -> CI build/test
   -> Docker image
   -> ACR
   -> image tag/digest
   -> Kustomize environment overlay
   -> AKS Deployment
```

The service remains an internal ClusterIP workload in each environment.
