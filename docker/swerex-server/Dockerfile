FROM python:3.12-slim
RUN pip install --no-cache-dir "swe-rex>=1.4.0"
EXPOSE 8000
ENTRYPOINT ["swerex-remote", "--host", "0.0.0.0", "--port", "8000"]
CMD ["--auth-token", "changeme"]
