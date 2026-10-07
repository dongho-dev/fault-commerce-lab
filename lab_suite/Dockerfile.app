FROM fcl-cs-base:v2
WORKDIR /app
COPY . /app
CMD ["python", "-m", "lab_suite.server"]
