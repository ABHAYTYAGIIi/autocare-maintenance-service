# Docker Deep Dive — AutoCare Maintenance Service

## 0. Purpose and scope

This document is an **engineering-level explanation of the Docker image for a Linux-server deployment**. It is intentionally more detailed than a Dockerfile walkthrough.

The goal is to answer, for every Dockerfile instruction:

- What Docker reads.
- What command or operation Docker performs.
- What is created or changed inside the image.
- Which application files are involved.
- Which Linux filesystem paths are affected.
- Which environment variables exist and who consumes them.
- Which credentials/secrets are involved.
- What is build-time versus runtime.
- What happens to the Linux kernel, namespaces, cgroups, filesystem, users, networking, and processes.
- What can be observed with Docker inspection commands.

The model used here is a **normal Linux server running Docker Engine**. Windows, WSL, and Docker Desktop are deliberately excluded from the architecture explanation.

---

# 1. Repository inputs

The repository currently contains the relevant application inputs:

```text
.
├── Dockerfile
├── requirements.txt
├── .env.example
├── .gitignore
└── app/
    ├── __init__.py
    └── main.py
```

The Dockerfile is:

```dockerfile
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN useradd --system --create-home appuser

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=appuser:appuser app ./app

USER appuser

EXPOSE 8001

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
```

## Build context is not the same thing as the repository

When the operator runs:

```bash
docker build -t autocare-maintenance-service:local .
```

`.` is the **build context**. Docker sends the files allowed by `.dockerignore` to the build engine.

The Dockerfile does not automatically copy the whole repository into the image.

This distinction matters:

```text
Repository on Linux host
        |
        | .dockerignore filtering
        v
Build context
        |
        | Dockerfile COPY instructions
        v
Image filesystem
```

A file can therefore exist in the repository and still never enter the image.

---

# 2. High-level build pipeline

The image creation process can be viewed as:

```text
Linux host
   |
   | docker build -t autocare-maintenance-service:local .
   v
Docker client
   |
   v
Docker Engine / BuildKit
   |
   +--> read Dockerfile
   |
   +--> read .dockerignore
   |
   +--> prepare build context
   |
   +--> resolve FROM image
   |
   +--> execute Dockerfile instructions
   |       |
   |       +--> filesystem snapshots / layers
   |       +--> image configuration metadata
   |
   v
Final image
   |
   +--> filesystem layers
   +--> config
   +--> manifest
   +--> content digests
```

An image is **not a virtual machine**. It is an immutable image definition consisting primarily of filesystem layers plus configuration/manifest metadata. A container is created later from that image.

---

# 3. Actual build evidence from the local build

The first real build was executed as:

```bash
docker build -t autocare-maintenance-service:local .
```

Docker reported:

```text
[+] Building 16.5s (11/11) FINISHED docker:desktop-linux
```

The important observed stages were:

```text
[internal] load build definition from Dockerfile
[internal] load metadata for docker.io/library/python:3.12-slim
[internal] load .dockerignore
[1/6] FROM docker.io/library/python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c...
[internal] load build context
[2/6] WORKDIR /app
[3/6] RUN useradd --system --create-home appuser
[4/6] COPY requirements.txt ./
[5/6] RUN pip install --no-cache-dir -r requirements.txt
[6/6] COPY --chown=appuser:appuser app ./app
exporting to image
```

The observed build context transferred was approximately:

```text
1.49 kB
```

The image was successfully tagged:

```text
autocare-maintenance-service:local
```

and `docker image ls` reported approximately:

```text
DISK USAGE     243MB
CONTENT SIZE   58.3MB
```

## What this proves

1. Docker successfully resolved the `python:3.12-slim` tag to a content digest for this build.
2. The build context was small and `.dockerignore` was active.
3. Six Dockerfile build instructions were executed after the base image step.
4. `requirements.txt` was copied before application source.
5. Python dependencies were installed during image construction.
6. Only the `app/` directory was explicitly copied as application source.
7. The final image was exported and stored locally.

## What the build log does NOT prove by itself

It does not, by itself, prove:

- the exact Linux distribution release inside the base image;
- the exact UID assigned to `appuser`;
- the exact installed Python package versions unless the dependency resolver output or image is inspected;
- the final image's effective environment/configuration;
- whether the application starts successfully;
- whether the service can be reached through a host/network path.

Those are inspection/runtime questions and should be verified separately.

---

# 4. Dockerfile line 1 — `FROM python:3.12-slim`

```dockerfile
FROM python:3.12-slim
```

## What Docker does

`FROM` establishes the starting point of the image build.

Docker resolves the image reference:

```text
docker.io/library/python:3.12-slim
```

In the observed build, Docker resolved the tag to a digest beginning:

```text
sha256:78387bc3881b8273120a12ebe6c...
```

The important concept is:

```text
human-readable tag
        |
        v
python:3.12-slim
        |
        | registry resolution
        v
content digest
        |
        v
specific image content
```

A tag is mutable; a digest identifies content. For reproducible production builds, pinning a deliberately selected digest is stronger than relying only on a mutable tag.

## What enters the image

The base image supplies a Linux userspace containing Python 3.12 and the operating-system/runtime files required by that Python distribution.

It establishes the initial root filesystem from which later instructions build:

```text
/
├── Linux userspace files
├── Python executable/runtime
├── Python standard library
├── system libraries
└── base-image configuration
```

The exact distribution/release should be verified from the actual image with:

```bash
docker run --rm --entrypoint /bin/sh autocare-maintenance-service:local -c 'cat /etc/os-release'
```

## What does NOT happen

`FROM` does not boot a Linux VM and does not install Python onto the host Linux server.

The Python installation belongs to the image filesystem.

---

# 5. Dockerfile line 2 — `WORKDIR /app`

```dockerfile
WORKDIR /app
```

## Build effect

Docker sets the working directory for subsequent Dockerfile instructions that use a working directory.

If `/app` does not already exist, Docker creates the directory in the image filesystem as needed.

After this instruction:

```text
current image working directory = /app
```

Therefore:

```dockerfile
COPY requirements.txt ./
```

means:

```text
build context: requirements.txt
             |
             v
image: /app/requirements.txt
```

## Runtime effect

The image configuration also records `/app` as the default working directory for the container process.

Therefore the final Uvicorn process starts with a current working directory of:

```text
/app
```

---

# 6. Dockerfile lines 3–4 — `ENV`

```dockerfile
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
```

These are image environment settings.

## `PYTHONDONTWRITEBYTECODE=1`

When Python processes source modules, it normally may create `.pyc` bytecode files. This variable tells Python not to write them.

The purpose in this container is primarily to avoid unnecessary bytecode files in the writable container filesystem.

## `PYTHONUNBUFFERED=1`

This tells Python to use unbuffered standard output/error behavior.

That is useful for containerized services because:

```text
Python stdout/stderr
       |
       v
container process output
       |
       v
Docker logging system
```

Logs become available without waiting for normal output buffering behavior.

## Important distinction

These two variables are not application business configuration. They control Python runtime behavior.

---

# 7. Dockerfile line 5 — creating `appuser`

```dockerfile
RUN useradd --system --create-home appuser
```

This is a **build-time Linux command**.

## What happens

BuildKit starts an execution environment based on the image produced so far and executes:

```bash
useradd --system --create-home appuser
```

This modifies the image's Linux account information and creates the requested home directory.

Conceptually, the image gains information represented in files such as:

```text
/etc/passwd
/etc/group
/etc/shadow       (implementation/configuration dependent)
/home/appuser
```

## Critical distinction

This does **not** create `appuser` on the Linux host.

It creates the account in the image's filesystem/account database.

The host has its own:

```text
/etc/passwd
/etc/group
```

which is separate from the container image's files.

## Why this matters

The Dockerfile later contains:

```dockerfile
USER appuser
```

so the application process is intentionally not started as root.

---

# 8. Dockerfile line 6 — copying `requirements.txt`

```dockerfile
COPY requirements.txt ./
```

The source is relative to the build context, not an arbitrary host path.

With the build command:

```bash
docker build -t autocare-maintenance-service:local .
```

and build context `.`:

```text
build context
└── requirements.txt
```

is copied to:

```text
image
└── /app/requirements.txt
```

## Why this is separated from `COPY app`

This ordering is deliberate:

```dockerfile
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=appuser:appuser app ./app
```

If only application source changes while `requirements.txt` remains unchanged, Docker can potentially reuse the dependency installation layer from cache.

If `requirements.txt` changes, dependency installation must be rebuilt.

This is both a correctness and build-performance decision.

---

# 9. Dockerfile line 7 — installing Python dependencies

```dockerfile
RUN pip install --no-cache-dir -r requirements.txt
```

This is the most substantial build-time application-environment operation.

## Input

```text
/app/requirements.txt
```

The repository's dependency manifest currently contains:

```text
fastapi
uvicorn[standard]
```

## Execution

Inside the build environment, Docker executes:

```bash
pip install --no-cache-dir -r requirements.txt
```

`pip` resolves and installs the requested packages and their dependencies into the Python environment supplied by the base image.

Conceptually:

```text
requirements.txt
      |
      v
pip dependency resolution
      |
      +--> fastapi
      +--> uvicorn[standard]
      +--> transitive dependencies
      |
      v
Python site-packages / executable environment
```

## `--no-cache-dir`

This tells pip not to retain its package download cache after installation.

It reduces unnecessary data in the final image.

It does **not** mean that Python packages are unavailable after the command finishes. The installed packages remain in the Python environment.

## Layering consequence

The filesystem changes produced by this command become part of the image's resulting layer/state.

This is why dependency installation is visible in the build as a distinct step:

```text
[5/6] RUN pip install --no-cache-dir -r requirements.txt
```

---

# 10. Dockerfile line 8 — copying application source

```dockerfile
COPY --chown=appuser:appuser app ./app
```

This copies the repository's `app/` directory from the build context into:

```text
/app/app
```

Therefore the resulting image contains at least:

```text
/app/app/__init__.py
/app/app/main.py
```

## `--chown`

The copied files are assigned ownership to:

```text
user: appuser
group: appuser
```

This matters because the container will later run as `appuser`.

Without appropriate ownership/permissions, a non-root process can encounter filesystem access problems.

## What is not copied

The Dockerfile does not contain:

```dockerfile
COPY . .
```

Therefore it does not intentionally copy the entire repository.

For this image, the explicit application source copy is only:

```text
app/
```

`requirements.txt` is copied separately.

---

# 11. Dockerfile line 9 — `USER appuser`

```dockerfile
USER appuser
```

This changes the default user for subsequent runtime behavior.

The final Uvicorn process is therefore intended to run as the Linux user created earlier:

```text
appuser
```

## Security effect

The application does not need to run as root for this service.

Running as a non-root user reduces the consequences of some application-level compromises because the process does not automatically have root privileges inside the container.

## Verify the actual identity

After building:

```bash
docker run --rm --entrypoint id autocare-maintenance-service:local
```

This should show the effective user and UID.

Do not assume the numeric UID from the Dockerfile; `useradd` chooses it according to the base image's account database/configuration.

---

# 12. Dockerfile line 10 — `EXPOSE 8001`

```dockerfile
EXPOSE 8001
```

This records that the application expects TCP port 8001.

It does **not**:

- open the Linux host firewall;
- publish the port to the host;
- create a Kubernetes Service;
- create an Azure load balancer;
- make the service publicly reachable.

To publish a port with ordinary Docker runtime configuration, the operator would use something such as:

```bash
docker run --rm -p 8001:8001 autocare-maintenance-service:local
```

The distinction is:

```text
EXPOSE 8001
    = image metadata / documentation

-p 8001:8001
    = runtime host-to-container port publishing
```

---

# 13. Dockerfile line 11 — `CMD`

```dockerfile
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
```

This defines the default process command when a container starts without an overriding command.

## How the Python import resolves

The image has:

```text
/app/
└── app/
    ├── __init__.py
    └── main.py
```

The working directory is:

```text
/app
```

Therefore:

```text
app.main
```

means the Python module:

```text
/app/app/main.py
```

The final `:app` means Uvicorn should obtain the Python object named `app` from that module.

So:

```text
app.main:app
     |      |
     |      +--> Python object named `app`
     |
     +---------> module app.main
```

## Host binding

```text
--host 0.0.0.0
```

means Uvicorn listens on all interfaces available inside the container's network namespace.

This is necessary for normal container networking. Binding only to `127.0.0.1` would restrict the listener to the container loopback interface.

## Port

```text
--port 8001
```

means Uvicorn listens on TCP port 8001 inside the container.

---

# 14. Complete application runtime chain

Once the container starts, the path is:

```text
Container created from image
        |
        v
working directory = /app
        |
        v
user = appuser
        |
        v
PID 1 / container entry process
        |
        v
uvicorn
        |
        v
import app.main
        |
        v
/app/app/main.py
        |
        v
FastAPI object named `app`
        |
        v
listen 0.0.0.0:8001
        |
        +--> GET /health
        |
        +--> POST /maintenance-analysis
```

There is no native-code compilation step for the application source in this Dockerfile.

The application remains Python source executed by the Python runtime in the image.

---

# 15. Image filesystem versus Linux host filesystem

This distinction is fundamental.

## Host

```text
Linux host filesystem
├── /etc
├── /usr
├── /home
├── /var/lib/docker
└── ...
```

## Image

The image contains its own filesystem view, for example:

```text
/
├── app/
│   ├── requirements.txt
│   └── app/
│       ├── main.py
│       └── __init__.py
├── Python runtime
├── installed Python packages
└── Linux userspace files
```

The image filesystem is stored and managed by Docker's storage subsystem. It is not the same directory tree as the host root filesystem.

When a container runs, Docker provides the process with a container-specific root filesystem assembled from the image plus the container's writable layer and any mounts.

---

# 16. What changes at the Linux kernel level?

Docker does not create a new kernel for each container.

A Linux container is fundamentally a normal Linux process (or process tree) running with isolation and resource controls.

Important kernel mechanisms include:

## PID namespace

The container gets an isolated process view. A process can have a different PID inside the container than the host sees.

## Mount namespace

The process receives an isolated filesystem mount view. `/app` refers to the container filesystem, not the host's arbitrary `/app` directory.

## Network namespace

The container receives its own network interfaces/routes as configured by Docker. Uvicorn's `0.0.0.0:8001` therefore refers to all interfaces in that container network namespace.

## Cgroups

Linux control groups can constrain/account for resources such as CPU and memory. Docker configures these according to runtime settings.

This Dockerfile itself does not specify CPU or memory limits. Those are runtime/orchestrator concerns.

## Capabilities/security controls

Docker applies Linux security restrictions/capabilities according to the daemon/runtime configuration. This Dockerfile does not explicitly define a custom capability set.

## Filesystem isolation

The container process sees the image filesystem as its root filesystem. Its writes normally go to a container-specific writable layer unless a volume/bind mount is provided.

---

# 17. Build-time versus runtime

This Dockerfile has a clean distinction:

| Item | Build time | Runtime |
|---|---:|---:|
| `FROM` base image | Yes | Provides filesystem |
| `WORKDIR` | Image config | Yes |
| `ENV` Python settings | Image config | Available |
| `useradd` | Yes | User exists |
| `requirements.txt` copy | Yes | File exists |
| `pip install` | Yes | Installed packages used |
| `app/` copy | Yes | Source is imported |
| `USER` | Image config | Effective default user |
| `EXPOSE` | Image metadata | Does not publish itself |
| `CMD` | Image metadata | Starts default process |

The important idea is:

```text
BUILD
Dockerfile + context
        |
        v
IMAGE

RUN
IMAGE
        |
        v
CONTAINER + runtime configuration
```

---

# 18. Environment variables

## Defined by the Dockerfile

```text
PYTHONDONTWRITEBYTECODE=1
PYTHONUNBUFFERED=1
```

These affect Python runtime behavior.

## Defined by `.env.example`

```text
PORT=8001
SERVICE_NAME=autocare-maintenance-service
```

A `.env.example` file is only a template unless the runtime explicitly loads it.

The Dockerfile does **not** contain:

```dockerfile
COPY .env .
```

and does not use `ENV PORT=8001` or `ENV SERVICE_NAME=...`.

The actual application code must be checked to determine whether `PORT` or `SERVICE_NAME` is consumed. The current Dockerfile's Uvicorn command hardcodes port `8001`, so the `.env.example` `PORT` value does not automatically control the Uvicorn port.

For production, configuration should be passed deliberately at runtime rather than accidentally baked into the image.

---

# 19. Credentials and secrets

No application credential is required by the Dockerfile itself.

The Dockerfile contains no password, token, API key, or database credential.

This is the desired pattern.

Do not do this:

```dockerfile
ENV DATABASE_PASSWORD=super-secret
```

or:

```dockerfile
COPY .env .
```

for production secrets.

Secrets should be supplied through the deployment platform's secret mechanism or another controlled runtime secret store.

---

# 20. Build cache strategy

The Dockerfile intentionally places:

```dockerfile
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
```

before:

```dockerfile
COPY --chown=appuser:appuser app ./app
```

Consider two builds.

## Only `app/main.py` changes

Potentially:

```text
FROM                 cache
WORKDIR              cache
ENV                  cache
useradd              cache
COPY requirements    cache
pip install          cache
COPY app              rebuild
```

The expensive dependency installation can be reused.

## `requirements.txt` changes

Then the dependency layer must be rebuilt because its input changed.

This is why Dockerfile instruction order affects build performance.

---

# 21. Image layers and metadata

A useful conceptual model is:

```text
Final image
│
├── filesystem layer: base Python/Linux userspace
├── filesystem layer: appuser creation
├── filesystem layer: requirements.txt
├── filesystem layer: installed Python dependencies
├── filesystem layer: application source
│
└── image configuration / metadata
      ├── WorkingDir = /app
      ├── Env = PYTHONDONTWRITEBYTECODE=1
      ├── Env = PYTHONUNBUFFERED=1
      ├── User = appuser
      ├── ExposedPorts = 8001
      └── Cmd = uvicorn ...
```

The exact layer representation and BuildKit internals are implementation details and can change with Docker versions/storage backends. The conceptual separation between filesystem content and image configuration is the useful operational model.

Useful inspection commands:

```bash
docker history autocare-maintenance-service:local
```

and:

```bash
docker image inspect autocare-maintenance-service:local
```

---

# 22. Container startup versus image build

Nothing in `CMD` executes during `docker build`.

During build, Docker stores the command as image configuration.

Later:

```bash
docker run autocare-maintenance-service:local
```

causes Docker to create a container and start the configured process.

Conceptually:

```text
BUILD

Dockerfile
   |
   v
Image


RUN

Image
   |
   v
Container
   |
   v
CMD -> uvicorn
```

This is why a successful build does not automatically prove the application works.

---

# 23. Verification procedure on a Linux server

After building the image, inspect it before deploying it.

## Image metadata

```bash
docker image inspect autocare-maintenance-service:local
```

Check:

- `Config.User`
- `Config.WorkingDir`
- `Config.Env`
- `Config.ExposedPorts`
- `Config.Cmd`

## Layer history

```bash
docker history autocare-maintenance-service:local
```

Use this to see the image's layer/history structure.

## Effective Linux user

```bash
docker run --rm --entrypoint id autocare-maintenance-service:local
```

## Filesystem

```bash
docker run --rm --entrypoint /bin/sh autocare-maintenance-service:local -c 'pwd; ls -la /app; ls -la /app/app'
```

## Base OS

```bash
docker run --rm --entrypoint /bin/sh autocare-maintenance-service:local -c 'cat /etc/os-release'
```

## Environment

```bash
docker run --rm --entrypoint /bin/sh autocare-maintenance-service:local -c 'env | sort'
```

## Start the actual service

```bash
docker run --rm --name autocare-maintenance-service -p 8001:8001 autocare-maintenance-service:local
```

In another shell:

```bash
curl http://127.0.0.1:8001/health
```

Then test the application endpoint according to its API contract.

---

# 24. Deep runtime mental model

On a Linux server, think of the system as:

```text
Linux kernel
    |
    +-- Docker Engine
          |
          +-- image store
          |     |
          |     +-- autocare-maintenance-service:local
          |           |
          |           +-- base Linux/Python filesystem
          |           +-- installed dependencies
          |           +-- /app/app/main.py
          |           +-- image configuration
          |
          +-- container
                |
                +-- isolated mount/filesystem view
                +-- isolated network namespace
                +-- cgroup/resource controls
                +-- Linux user: appuser
                +-- PID 1: uvicorn
                      |
                      +-- Python
                            |
                            +-- FastAPI
```

The host kernel executes the container's processes. The container supplies the userspace/filesystem and isolation boundary.

---

# 25. What this Dockerfile does NOT provide

This Dockerfile does not provide:

- Kubernetes Deployment
- Kubernetes Service
- Ingress
- TLS
- DNS
- Azure SQL connectivity
- Azure identity
- production secrets
- CPU/memory limits
- horizontal scaling
- health probes at the orchestrator level
- persistent storage
- service discovery
- public exposure

Those belong to the runtime/orchestration/deployment layer.

For our eventual Azure deployment, these concerns will be represented separately from the application image.

---

# 26. Deployment boundary for AutoCare

The maintenance-service image should have one clear responsibility:

```text
FastAPI maintenance analysis service
        |
        +--> listens on container port 8001
        +--> /health
        +--> /maintenance-analysis
```

The API container should call it over the internal deployment network using a configured service address.

The frontend should not need direct access to the maintenance service.

The eventual deployment architecture should therefore preserve:

```text
Frontend
   |
   v
API
   |
   v
Maintenance service
```

with the maintenance service preferably remaining internally reachable rather than publicly exposed.

---

# 27. Known facts versus things still requiring verification

## Verified from the Dockerfile/build

- Base image reference: `python:3.12-slim`.
- Build resolved that reference to a SHA-256 digest in the observed build.
- Working directory: `/app`.
- Python environment variables: `PYTHONDONTWRITEBYTECODE=1`, `PYTHONUNBUFFERED=1`.
- Linux user created: `appuser`.
- Dependency file copied: `requirements.txt`.
- Dependencies installed with pip.
- Application directory copied: `app/`.
- Application files include `app/main.py` and `app/__init__.py`.
- Runtime user configured as `appuser`.
- Declared container port: `8001`.
- Default runtime command: Uvicorn serving `app.main:app` on `0.0.0.0:8001`.
- Image build completed successfully.

## Still requiring direct runtime/image inspection

- Exact base Linux distribution and release.
- Exact UID/GID for `appuser`.
- Exact resolved versions of every Python dependency.
- Complete final image filesystem inventory.
- Effective image environment after all metadata is applied.
- Actual container process tree.
- Actual HTTP response from `/health`.
- Exact Linux capabilities/security profile applied by the Docker runtime.

These should be **measured**, not guessed.

---

# 28. Key lessons from this image

1. **The repository is an input to the build; it is not automatically the image.**
2. **The build context is filtered by `.dockerignore`.**
3. **`COPY` determines which repository files enter the image.**
4. **`RUN` executes commands during image construction.**
5. **The Python dependencies are installed into the image, not the Linux host Python environment.**
6. **`USER appuser` controls the identity of the runtime process inside the container.**
7. **`EXPOSE` documents a port; it does not publish it.**
8. **`CMD` is image configuration until a container is actually started.**
9. **A container is an isolated Linux process environment, not a virtual machine.**
10. **The host Linux kernel remains the kernel executing the container's processes.**
11. **Build success proves image construction, not application correctness.**
12. **Production secrets should enter at runtime through a controlled secret mechanism.**
13. **Dockerfile order affects cache reuse and therefore build time.**
14. **Image tags and content digests are different reproducibility concepts.**
15. **Anything important should be verified with `docker inspect`, `docker history`, and an actual container rather than inferred from the Dockerfile alone.**
