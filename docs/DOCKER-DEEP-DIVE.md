# Docker Deep Dive — AutoCare Maintenance Service

## Purpose

This document explains the container from a **Linux-server point of view**. It deliberately separates:

1. Dockerfile instructions and the image build process.
2. Runtime process, filesystem, user, networking, and environment behavior.
3. Application files consumed by the image.
4. Environment variables, credentials, and paths.
5. What Docker changes at the Linux OS/kernel boundary.

## Source snapshot

The repository currently contains:

- `app/main.py` — FastAPI application entry point.
- `app/__init__.py` — package marker.
- `requirements.txt` — Python dependencies.
- `.env.example` — currently defines `PORT=8001` and `SERVICE_NAME=autocare-maintenance-service`.

The Dockerfile currently used in the working Linux-oriented build is:

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

> Note: the Dockerfile shown above is present in the current working project context, but it is **not yet present on the GitHub `main` branch**. This documentation therefore records the working Dockerfile explicitly rather than pretending GitHub contains it.

---

## 1. Line-by-line Dockerfile analysis

### `FROM python:3.12-slim`

Creates the first image layer from the official Python 3.12 slim Linux base image.

Linux impact:

- Provides a Linux userspace filesystem containing Python and its runtime dependencies.
- The container does **not** boot a second Linux kernel. It uses the host Linux kernel through container isolation.
- `slim` reduces the amount of userspace software compared with a full Python image.

Build consequence:

- Docker resolves/pulls the base image if it is not already in the local image cache.
- Every following instruction creates filesystem/configuration changes on top of this base.

### `WORKDIR /app`

Sets `/app` as the working directory for subsequent Dockerfile instructions and the default working directory of the container process.

It effectively makes commands such as `COPY requirements.txt ./` target `/app/requirements.txt` and makes the final process start with `/app` as its current directory.

### `ENV PYTHONDONTWRITEBYTECODE=1`

Adds an image-level environment variable.

Python behavior: Python is instructed not to write `.pyc` bytecode files.

### `ENV PYTHONUNBUFFERED=1`

Forces Python stdout/stderr to be unbuffered. This is useful in containers because application logs reach Docker's logging system promptly.

### `RUN useradd --system --create-home appuser`

Executes a Linux command **while building the image**.

This creates a system user named `appuser` and a home directory.

Important distinction:

- This changes the image filesystem/user database.
- It does not create a user on the Linux host.
- The host only sees the container process running under the container's user namespace/identity model.

### `COPY requirements.txt ./`

Copies only the dependency manifest into `/app`.

This is intentionally before the application source copy so Docker can reuse the dependency-install layer when application source files change but `requirements.txt` does not.

### `RUN pip install --no-cache-dir -r requirements.txt`

Runs `pip` inside the temporary build container created for this image layer.

The repository's dependency file currently contains:

- `fastapi`
- `uvicorn[standard]`

Those packages and their dependencies are installed into the Python environment inside the image.

`--no-cache-dir` avoids retaining pip's package download cache, reducing image size.

### `COPY --chown=appuser:appuser app ./app`

Copies the repository's `app/` directory into `/app/app`.

The ownership is set to `appuser:appuser`, so the non-root runtime user can read the application files without requiring root privileges.

The application file actually used by the final command is:

`/app/app/main.py`

### `USER appuser`

Changes the default Linux user for subsequent image/runtime operations.

The important security effect is that Uvicorn/FastAPI does **not** run as root inside the container.

### `EXPOSE 8001`

Documents that the application expects TCP port `8001`.

It does not publish the port to the host by itself.

A Linux operator still needs a runtime mapping such as `-p 8001:8001` if the service must be reachable through the host network.

### `CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]`

Defines the default container process.

The chain is:

`uvicorn` → imports `app.main` → obtains the FastAPI object named `app` → listens on all container interfaces → TCP 8001.

`0.0.0.0` is important. Binding only to `127.0.0.1` would make the service reachable only from inside the container's network namespace.

---

## 2. What code is actually built/run?

There is no compilation step in this image.

The build creates a Python runtime environment and copies source code into the image.

Runtime flow:

```text
Linux server
   |
   +-- Docker daemon
         |
         +-- container filesystem
         |      /app
         |       ├── requirements.txt
         |       └── app/main.py
         |
         +-- process as appuser
                |
                +-- uvicorn
                      |
                      +-- app.main:app
                            |
                            +-- FastAPI routes
```

`app/main.py` defines:

- the FastAPI application object;
- `MaintenanceRequest` validation;
- deterministic maintenance-risk logic;
- `GET /health`;
- `POST /maintenance-analysis`.

---

## 3. Build process at a deeper level

A Linux server running:

```bash
docker build -t autocare-maintenance-service .
```

roughly performs:

1. Docker reads the Dockerfile and build context.
2. Docker obtains `python:3.12-slim`.
3. Each `RUN` instruction executes in an intermediate container filesystem.
4. Docker commits the resulting filesystem changes into image layers.
5. `requirements.txt` is copied and dependency installation creates another layer.
6. `app/` is copied into a later layer.
7. Image metadata records `USER`, `WORKDIR`, `ENV`, `EXPOSE`, and `CMD`.
8. The final image becomes a content-addressed collection of layers plus configuration metadata.

The final image is **not a VM disk image**. It is a filesystem plus metadata used to start an isolated Linux process.

---

## 4. Linux OS-level changes

### During build

The Docker build environment creates filesystem content such as:

- `/app`
- Python packages
- the `appuser` account/home directory
- application source files

These changes belong to the image, not directly to the host's `/etc/passwd`, `/home`, or `/usr`.

### During runtime

Docker creates/isolate Linux resources around the container process, typically including:

- a process namespace;
- a mount/filesystem namespace;
- a network namespace;
- cgroup resource controls;
- capability/security restrictions;
- a container-specific root filesystem view.

The exact isolation implementation depends on the Docker Engine and host configuration.

The host Linux kernel remains the kernel executing the process.

---

## 5. Paths

| Path | Meaning |
|---|---|
| `/app` | container working directory |
| `/app/requirements.txt` | dependency manifest copied into image |
| `/app/app/main.py` | FastAPI application code |
| `app` | source directory in Docker build context |
| `8001` | application/container TCP port |

No host bind mount is declared by this Dockerfile.

A bind mount would be supplied by the runtime command or Compose/Kubernetes configuration.

---

## 6. Environment variables and credentials

The Dockerfile itself defines:

- `PYTHONDONTWRITEBYTECODE=1`
- `PYTHONUNBUFFERED=1`

The repository's `.env.example` defines:

- `PORT=8001`
- `SERVICE_NAME=autocare-maintenance-service`

No secret credential is required by the application shown in `app/main.py`.

Do not put production passwords/tokens into the Dockerfile. They become part of image metadata/layers and can be exposed to anyone who can inspect the image.

---

## 7. What is *not* happening

- No Windows subsystem is required by the Linux deployment model.
- No second Linux kernel is created.
- `EXPOSE` does not open a firewall port.
- `USER appuser` does not create a host Linux account.
- `COPY` does not continuously synchronize the host directory with the container.
- Python source is not compiled into a native executable by this Dockerfile.

---

## 8. Operational mental model

Think of the production Linux server as:

```text
Host Linux kernel
      |
      +-- Docker Engine
             |
             +-- image: autocare-maintenance-service
             |       |
             |       +-- Python 3.12 userspace
             |       +-- FastAPI/Uvicorn packages
             |       +-- /app/app/main.py
             |
             +-- container
                     |
                     +-- PID 1: uvicorn
                     +-- user: appuser
                     +-- port: 8001
```

This is the model to use for future Docker/Kubernetes/Azure analysis of this service.
