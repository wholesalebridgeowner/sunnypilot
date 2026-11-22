"""Storage for the Railway service: JSON records in the private bucket under
`advisor/`, or in memory for tests."""
from __future__ import annotations

import copy
import json
from threading import Lock


class MemoryStore:
    def __init__(self):
        self.objects = {}
        self.create_lock = Lock()

    def get_json(self, key: str):
        value = self.objects.get(key)
        return copy.deepcopy(value)

    def put_json(self, key: str, value) -> None:
        self.objects[key] = json.loads(json.dumps(value))

    def put_if_absent(self, key: str, value) -> bool:
        with self.create_lock:
            if key in self.objects:
                return False
            self.put_json(key, value)
            return True

    def list_keys(self, prefix: str):
        return sorted(key for key in list(self.objects) if key.startswith(prefix))

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)


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
        self._put(key, value)

    def _put(self, key, value, **conditions):
        body = json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")
        self.client.put_object(Bucket=self.bucket, Key=self.prefix + key, Body=body,
                               ContentType="application/json", **conditions)

    def put_if_absent(self, key: str, value) -> bool:
        for attempt in range(2):
            try:
                self._put(key, value, IfNoneMatch="*")
                return True
            except self.client.exceptions.ClientError as exc:
                status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
                if status == 412:       # the canonical grant already exists
                    return False
                if status != 409 or attempt:
                    raise

    def list_keys(self, prefix: str):
        pages = self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=self.prefix + prefix)
        for page in pages:
            for item in page.get("Contents", []):
                yield item["Key"][len(self.prefix):]

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=self.prefix + key)
