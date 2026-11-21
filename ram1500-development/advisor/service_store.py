"""Storage for the Railway service: JSON records in the private bucket under
`advisor/`, or in memory for tests."""
from __future__ import annotations

import copy
import json


class MemoryStore:
    def __init__(self):
        self.objects = {}

    def get_json(self, key: str):
        value = self.objects.get(key)
        return copy.deepcopy(value)

    def put_json(self, key: str, value) -> None:
        self.objects[key] = json.loads(json.dumps(value))


class S3Store:
    def __init__(self, client, bucket: str, prefix: str = "advisor/"):
        self.client = client
        self.bucket = bucket
        self.prefix = prefix

    def get_json(self, key: str):
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=self.prefix + key)
        except self.client.exceptions.NoSuchKey:
            return None
        return json.loads(response["Body"].read())

    def put_json(self, key: str, value) -> None:
        body = json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")
        self.client.put_object(Bucket=self.bucket, Key=self.prefix + key, Body=body,
                               ContentType="application/json")
