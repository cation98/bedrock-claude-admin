"""S3+KMS 민감 파일 격리 저장소 — 파일 업로드, 다운로드, 만료 관리."""

import hashlib
import logging
from datetime import datetime, timedelta, timezone

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


class S3VaultService:
    """S3 버킷에 KMS 서버-사이드 암호화로 민감 파일을 저장/조회/삭제한다."""

    def __init__(self, bucket_name: str, kms_key_id: str, region: str = "ap-northeast-2"):
        self.s3 = boto3.client("s3", region_name=region)
        self.bucket_name = bucket_name
        self.kms_key_id = kms_key_id

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------

    def upload_file(
        self,
        username: str,
        filename: str,
        file_data: bytes,
        ttl_days: int = 7,
    ) -> dict:
        """민감 파일을 S3에 KMS 암호화 업로드.

        Returns:
            {"vault_id": str, "s3_key": str, "expires_at": str (ISO-8601)}
        """
        vault_id = hashlib.sha256(
            f"{username}/{filename}/{datetime.now(timezone.utc).isoformat()}".encode()
        ).hexdigest()[:16]

        s3_key = f"vault/{username}/{vault_id}/{filename}"
        expires_at = datetime.now(timezone.utc) + timedelta(days=ttl_days)

        try:
            self.s3.put_object(
                Bucket=self.bucket_name,
                Key=s3_key,
                Body=file_data,
                ServerSideEncryption="aws:kms",
                SSEKMSKeyId=self.kms_key_id,
                Metadata={
                    "owner": username,
                    "vault-id": vault_id,
                    "original-filename": filename,
                    "expires-at": expires_at.isoformat(),
                },
                Tagging=(
                    f"owner={username}"
                    f"&classification=sensitive"
                    f"&expires={expires_at.strftime('%Y-%m-%d')}"
                ),
            )
        except ClientError as exc:
            logger.error("S3 upload failed for %s/%s: %s", username, filename, exc)
            raise

        logger.info("Vault upload: user=%s vault_id=%s key=%s", username, vault_id, s3_key)
        return {
            "vault_id": vault_id,
            "s3_key": s3_key,
            "expires_at": expires_at.isoformat(),
        }

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------

    def download_file(self, username: str, vault_id: str) -> tuple[bytes, dict]:
        """S3에서 파일 다운로드 (소유자 확인).

        Returns:
            (file_bytes, metadata_dict)

        Raises:
            FileNotFoundError: vault_id가 해당 사용자의 prefix에 없을 때
        """
        prefix = f"vault/{username}/{vault_id}/"
        try:
            response = self.s3.list_objects_v2(
                Bucket=self.bucket_name,
                Prefix=prefix,
                MaxKeys=1,
            )
        except ClientError as exc:
            logger.error("S3 list failed for %s/%s: %s", username, vault_id, exc)
            raise

        if "Contents" not in response:
            raise FileNotFoundError(
                f"Vault item {vault_id} not found for user {username}"
            )

        s3_key = response["Contents"][0]["Key"]
        try:
            obj = self.s3.get_object(Bucket=self.bucket_name, Key=s3_key)
        except ClientError as exc:
            logger.error("S3 get_object failed for key %s: %s", s3_key, exc)
            raise

        metadata = obj.get("Metadata", {})
        return obj["Body"].read(), metadata

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    def delete_file(self, username: str, vault_id: str) -> bool:
        """S3에서 파일 삭제.

        Returns:
            True if deleted, False if not found
        """
        prefix = f"vault/{username}/{vault_id}/"
        try:
            response = self.s3.list_objects_v2(
                Bucket=self.bucket_name,
                Prefix=prefix,
            )
        except ClientError as exc:
            logger.error("S3 list failed for deletion %s/%s: %s", username, vault_id, exc)
            raise

        if "Contents" not in response:
            return False

        for obj in response["Contents"]:
            self.s3.delete_object(Bucket=self.bucket_name, Key=obj["Key"])
            logger.info("Vault delete: key=%s", obj["Key"])

        return True

    # ------------------------------------------------------------------
    # List
    # ------------------------------------------------------------------

    def list_user_files(self, username: str) -> list[dict]:
        """사용자의 vault 파일 목록.

        Returns:
            [{"key": str, "size": int, "last_modified": str}, ...]
        """
        prefix = f"vault/{username}/"
        try:
            response = self.s3.list_objects_v2(
                Bucket=self.bucket_name,
                Prefix=prefix,
            )
        except ClientError as exc:
            logger.error("S3 list failed for user %s: %s", username, exc)
            raise

        files = []
        for obj in response.get("Contents", []):
            last_modified = obj["LastModified"]
            files.append(
                {
                    "key": obj["Key"],
                    "size": obj["Size"],
                    "last_modified": (
                        last_modified.isoformat()
                        if hasattr(last_modified, "isoformat")
                        else str(last_modified)
                    ),
                }
            )
        return files
