# Materials-Triage CLI image.
#
# Ships the `materials-triage` command-line entry point so the agent runs the
# same way on any OS with only Docker installed — no local Python toolchain.
# Credentials are supplied at run time via `--env-file` (never baked in); run
# traces persist to the mounted /data/runs volume.
FROM python:3.13-slim

# Faster, quieter, no .pyc clutter.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Copy only what the package build needs first, so dependency layers cache
# across source-only changes. (pyproject declares README.md as the long
# description, so it must be present at install time.)
COPY pyproject.toml README.md ./
COPY src ./src

# Install the package with the live LLM transport (langchain-aws, lazy-imported)
# plus python-dotenv so a mounted .env is honored.
RUN pip install ".[llm]" python-dotenv

# Run as an unprivileged user; give it an owned, writable runs directory.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data/runs \
    && chown -R appuser:appuser /data
USER appuser

VOLUME ["/data/runs"]

# `docker run <image> "<goal>" --runs-dir /data/runs` runs a triage;
# `docker run <image> doctor` runs the environment self-check (the default).
ENTRYPOINT ["materials-triage"]
CMD ["doctor"]
