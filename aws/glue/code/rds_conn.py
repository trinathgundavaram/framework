"""
rds_conn.py
=============================================================================
Postgres connection helper for the CMS Compliance metadata Glue job.

Ported from the same `RdsClient` pattern used in the team's
aae-aws-artf/module/aws/part c odr/glue-jobs/code/rds_conn.py (Secrets
Manager -> boto3 -> pg8000), adapted here to talk to Postgres instead of
that job's Teradata/RDS pairing. Same method names/shape (get_secret,
set_rds_connection_details, connect, ping_table) so it's a drop-in-familiar
class for anyone who's worked with the original.
=============================================================================
"""

import json

import boto3
import pg8000


class RdsClient:
    """Secrets Manager -> pg8000 connection helper for one Postgres database."""

    def __init__(self, secret_name, rds_database_name, region="us-east-1",
                 rds_jdbc_driver_class="org.postgresql.Driver"):
        self.secret_name = secret_name
        self.rds_database_name = rds_database_name
        self.region = region
        self.rds_jdbc_driver_class = rds_jdbc_driver_class

    def get_secret(self):
        """
        Get the postgres credential information from secret manager
        """
        client = boto3.client("secretsmanager", region_name=self.region)
        try:
            get_secret_value_response = client.get_secret_value(SecretId=self.secret_name)
        except Exception as e:
            print(f"Failed with exception {e}", exc_info=True)
            raise e
        else:
            # Decrypts secret using the associated KMS CMK.
            # Depending on whether the secret is a string or binary, one of these fields will be populated.
            if "SecretString" in get_secret_value_response:
                secret_string = get_secret_value_response["SecretString"]
                secret = json.loads(secret_string)
                print("Got the secret manager credentials")
                return secret

    def set_rds_connection_details(self):
        rds_account_secret_val = self.get_secret()
        self.rds_username = rds_account_secret_val["username"]
        self.rds_password = rds_account_secret_val["password"]
        self.rds_jdbc_port = rds_account_secret_val["port"]
        self.rds_jdbc_hostname = rds_account_secret_val["host"]

        self.rds_jdbc_url = f"jdbc:postgresql://{self.rds_jdbc_hostname}:{self.rds_jdbc_port}/{self.rds_database_name}"

        self.rds_connection_properties = {
            "user": self.rds_username,
            "password": self.rds_password,
            "driver": self.rds_jdbc_driver_class,
        }

        return self.rds_jdbc_url, self.rds_connection_properties

    def connect(self):
        try:
            jdbc_url, connection_properties = self.set_rds_connection_details()
        except Exception as e:
            print(f"Failed with exception {e}", exc_info=True)
            raise e

        conn = pg8000.connect(
            host=self.rds_jdbc_hostname,
            port=int(self.rds_jdbc_port),
            user=self.rds_username,
            password=self.rds_password,
            database=self.rds_database_name,
            ssl=True,
        )

        print(f"Connected host='{self.rds_jdbc_hostname}', "
              f"db='{self.rds_database_name}', user='{self.rds_username}'")
        return conn

    def ping_table(self, table_name) -> int:
        conn = self.connect()
        self.table_name = table_name
        try:
            cursor = conn.cursor()
            cursor.execute(f"SELECT COUNT(*) FROM {self.table_name}")
            count = cursor.fetchone()[0]
            print(f"Table '{self.table_name}' reachable. Row count = {count}")
            return count
        finally:
            cursor.close()
            conn.close()
            print("Connection closed")
