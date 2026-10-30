FROM python:3.12-slim
WORKDIR /src
COPY . .
RUN pip install --no-cache-dir . && rm -rf /src
WORKDIR /work
ENTRYPOINT ["penpad"]
CMD ["--help"]
