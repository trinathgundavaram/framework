"""Postgres connection from Secrets Manager (pg8000)."""

import json
import logging

import boto3
import pg8000

logger = logging.getLogger(__name__)


class RdsClient:
    def __init__(self, secret_name, rds_database_name=None, region="us-east-1"):
        self.secret_name = secret_name
        self.rds_database_name = rds_database_name
        self.region = region

    def get_secret(self):
        client = boto3.client("secretsmanager", region_name=self.region)
        try:
            response = client.get_secret_value(SecretId=self.secret_name)
        except Exception:
            logger.exception("Failed to read secret %s", self.secret_name)
            raise
        return json.loads(response["SecretString"])

    def connect(self):
        secret = self.get_secret()
        database = self.rds_database_name or secret.get("dbname", "postgres")
        conn = pg8000.connect(
            host=secret["host"],
            port=int(secret["port"]),
            user=secret["username"],
            password=secret["password"],
            database=database,
            ssl_context=True,
        )
        logger.info("Connected host='%s', db='%s', user='%s'", secret["host"], database, secret["username"])
        return conn
