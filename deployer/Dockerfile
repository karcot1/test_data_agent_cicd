# Dependencies-only image for the Data Agent deployer.
# Code is NOT copied in: Cloud Build steps run from /workspace (the checked-out
# repo), so code changes never invalidate the cached pip layer below.
FROM python:3.11.14-slim

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

WORKDIR /workspace
