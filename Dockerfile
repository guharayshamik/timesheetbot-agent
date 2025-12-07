FROM mcr.microsoft.com/playwright/python:v1.55.0-jammy

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Copy pyproject + source code only
COPY pyproject.toml .
COPY timesheetbot_agent ./timesheetbot_agent

# Make empty output folder
RUN mkdir -p /app/generated_timesheets

# Install everything your app needs:
RUN pip install --upgrade pip && \
    pip install playwright && \
    playwright install chromium && \
    pip install .

ENTRYPOINT ["python", "-m", "timesheetbot_agent.tsbot_entry"]

