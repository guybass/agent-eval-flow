# Reuse the already verified local Codex executable; no credentials enter layers.
FROM aef-openkritt-engine:1ba10d4
WORKDIR /opt/opensre-dependencies
COPY pyproject.toml uv.lock ./
RUN python -m pip install --no-cache-dir uv \
    && uv export --frozen --no-dev --no-emit-project --format requirements-txt -o /tmp/opensre-requirements.txt \
    && uv pip install --system --require-hashes -r /tmp/opensre-requirements.txt \
    && uv pip install --system "pydantic>=2.13.5,<3" "anyio>=4.15.1,<5" "jinja2>=3.1.6,<4" "jsonschema>=4.26,<5" \
    && python -m pip freeze > /opt/opensre-dependencies/installed.txt
RUN git clone --no-checkout https://github.com/Tracer-Cloud/opensre.git /opt/opensre \
    && git -C /opt/opensre checkout --detach 1a81e1a1b378ecc9aeb211fcaeecb0f5c93548e4 \
    && test -z "$(git -C /opt/opensre status --porcelain --untracked-files=no)"
WORKDIR /aef/run
ENV PYTHONPATH=/workspace/src:/workspace:/opt/opensre \
    PYTHONDONTWRITEBYTECODE=1 \
    CODEX_HOME=/root/.codex
ENTRYPOINT ["python", "-m", "examples.integrations.opensre_local_worker"]
