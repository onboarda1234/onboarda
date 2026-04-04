#!/usr/bin/env python3
"""
Persistent external task retry queue backed by SQLite.
Handles tasks that failed and need to be retried asynchronously.
"""

import asyncio
import json
import logging
import aiosqlite
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class TaskStatus(Enum):
    """Task status values"""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    DEAD = "dead"  # Max retries exceeded


@dataclass
class QueueTask:
    """Represents a task in the queue"""
    id: int
    task_type: str
    application_id: str
    provider: str
    payload: Dict[str, Any]
    attempt_count: int
    max_retries: int
    next_retry_at: datetime
    last_error: Optional[str]
    status: TaskStatus
    created_at: datetime
    updated_at: datetime


class ExternalTaskQueue:
    """
    SQLite-backed persistent queue for retrying failed external API calls.
    """

    def __init__(self, db_path: str):
        """
        Initialize the task queue.

        Args:
            db_path: Path to SQLite database
        """
        self.db_path = db_path

    async def enqueue(
        self,
        task_type: str,
        application_id: str,
        provider: str,
        payload: Dict[str, Any],
        max_retries: int = 5
    ) -> int:
        """
        Enqueue a task for retry.

        Args:
            task_type: Type of task (e.g., "kyc_verification", "sanctions_check")
            application_id: Associated application ID
            provider: Provider name (e.g., "sumsub", "opencorporates")
            payload: Task payload (will be JSON serialized)
            max_retries: Maximum number of retry attempts

        Returns:
            Task ID
        """
        async with aiosqlite.connect(self.db_path) as db:
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            # Initial retry in 5 minutes
            next_retry = (datetime.now(timezone.utc) + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")

            cursor = await db.execute(
                """
                INSERT INTO external_retry_queue
                (task_type, application_id, provider, payload, attempt_count, max_retries, next_retry_at, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_type,
                    application_id,
                    provider,
                    json.dumps(payload),
                    0,
                    max_retries,
                    next_retry,
                    TaskStatus.PENDING.value,
                    now,
                    now
                )
            )
            await db.commit()

            task_id = cursor.lastrowid
            logger.info(
                f"Enqueued task {task_id}: type={task_type}, app={application_id}, provider={provider}"
            )
            return task_id

    async def dequeue_ready(self) -> List[QueueTask]:
        """
        Get tasks that are ready to be retried (next_retry_at <= now).

        Returns:
            List of QueueTask objects
        """
        async with aiosqlite.connect(self.db_path) as db:
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            cursor = await db.execute(
                """
                SELECT id, task_type, application_id, provider, payload, attempt_count, max_retries,
                       next_retry_at, last_error, status, created_at, updated_at
                FROM external_retry_queue
                WHERE (status = ? OR status = ?) AND next_retry_at <= ?
                ORDER BY next_retry_at ASC
                LIMIT 100
                """,
                (TaskStatus.PENDING.value, TaskStatus.PROCESSING.value, now)
            )
            rows = await cursor.fetchall()

            tasks = []
            for row in rows:
                task = QueueTask(
                    id=row[0],
                    task_type=row[1],
                    application_id=row[2],
                    provider=row[3],
                    payload=json.loads(row[4]),
                    attempt_count=row[5],
                    max_retries=row[6],
                    next_retry_at=datetime.fromisoformat(row[7].replace("Z", "+00:00")),
                    last_error=row[8],
                    status=TaskStatus(row[9]),
                    created_at=datetime.fromisoformat(row[10].replace("Z", "+00:00")),
                    updated_at=datetime.fromisoformat(row[11].replace("Z", "+00:00"))
                )
                tasks.append(task)

            return tasks

    async def mark_completed(self, task_id: int) -> None:
        """
        Mark a task as completed.

        Args:
            task_id: Task ID
        """
        async with aiosqlite.connect(self.db_path) as db:
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            await db.execute(
                """
                UPDATE external_retry_queue
                SET status = ?, updated_at = ?
                WHERE id = ?
                """,
                (TaskStatus.COMPLETED.value, now, task_id)
            )
            await db.commit()

            logger.info(f"Task {task_id} marked as completed")

    async def mark_failed(self, task_id: int, error: str) -> None:
        """
        Mark a task as failed and schedule next retry.
        If max retries exceeded, mark as dead.

        Args:
            task_id: Task ID
            error: Error message
        """
        async with aiosqlite.connect(self.db_path) as db:
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            # Get current task state
            cursor = await db.execute(
                "SELECT attempt_count, max_retries FROM external_retry_queue WHERE id = ?",
                (task_id,)
            )
            row = await cursor.fetchone()

            if not row:
                logger.warning(f"Task {task_id} not found")
                return

            attempt_count = row[0] + 1
            max_retries = row[1]

            if attempt_count >= max_retries:
                # Mark as dead
                status = TaskStatus.DEAD.value
                next_retry = None
                logger.warning(
                    f"Task {task_id} exceeded max retries ({max_retries}), marking as dead"
                )
            else:
                # Schedule next retry with exponential backoff
                status = TaskStatus.PENDING.value
                delay_minutes = min(5 * (2 ** attempt_count), 1440)  # Max 24 hours
                next_retry = (datetime.now(timezone.utc) + timedelta(minutes=delay_minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
                logger.info(
                    f"Task {task_id} failed, scheduled next retry in {delay_minutes} minutes (attempt {attempt_count}/{max_retries})"
                )

            if next_retry:
                await db.execute(
                    """
                    UPDATE external_retry_queue
                    SET status = ?, attempt_count = ?, last_error = ?, next_retry_at = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (status, attempt_count, error, next_retry, now, task_id)
                )
            else:
                await db.execute(
                    """
                    UPDATE external_retry_queue
                    SET status = ?, attempt_count = ?, last_error = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (status, attempt_count, error, now, task_id)
                )

            await db.commit()

    async def get_queue_stats(self) -> Dict[str, Any]:
        """
        Get queue statistics.

        Returns:
            Dictionary with queue stats:
            {
                "total": int,
                "by_status": {"pending": int, "processing": int, ...},
                "by_provider": {"sumsub": int, ...},
                "oldest_pending": "2025-03-17T10:00:00Z" or null,
                "dead_letter_count": int
            }
        """
        async with aiosqlite.connect(self.db_path) as db:
            # Total count
            cursor = await db.execute("SELECT COUNT(*) FROM external_retry_queue")
            total = (await cursor.fetchone())[0]

            # By status
            cursor = await db.execute(
                """
                SELECT status, COUNT(*) as count
                FROM external_retry_queue
                GROUP BY status
                """
            )
            status_rows = await cursor.fetchall()
            by_status = {row[0]: row[1] for row in status_rows}

            # By provider
            cursor = await db.execute(
                """
                SELECT provider, COUNT(*) as count
                FROM external_retry_queue
                WHERE status != ?
                GROUP BY provider
                """,
                (TaskStatus.COMPLETED.value,)
            )
            provider_rows = await cursor.fetchall()
            by_provider = {row[0]: row[1] for row in provider_rows}

            # Oldest pending
            cursor = await db.execute(
                """
                SELECT MIN(created_at)
                FROM external_retry_queue
                WHERE status = ?
                """,
                (TaskStatus.PENDING.value,)
            )
            oldest_row = await cursor.fetchone()
            oldest_pending = oldest_row[0] if oldest_row[0] else None

            # Dead letter count
            cursor = await db.execute(
                "SELECT COUNT(*) FROM external_retry_queue WHERE status = ?",
                (TaskStatus.DEAD.value,)
            )
            dead_count = (await cursor.fetchone())[0]

            return {
                "total": total,
                "by_status": by_status,
                "by_provider": by_provider,
                "oldest_pending": oldest_pending,
                "dead_letter_count": dead_count
            }

    async def mark_processing(self, task_id: int) -> None:
        """
        Mark a task as currently being processed.

        Args:
            task_id: Task ID
        """
        async with aiosqlite.connect(self.db_path) as db:
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            await db.execute(
                """
                UPDATE external_retry_queue
                SET status = ?, updated_at = ?
                WHERE id = ?
                """,
                (TaskStatus.PROCESSING.value, now, task_id)
            )
            await db.commit()

    async def get_task(self, task_id: int) -> Optional[QueueTask]:
        """
        Get a specific task by ID.

        Args:
            task_id: Task ID

        Returns:
            QueueTask or None if not found
        """
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT id, task_type, application_id, provider, payload, attempt_count, max_retries,
                       next_retry_at, last_error, status, created_at, updated_at
                FROM external_retry_queue
                WHERE id = ?
                """,
                (task_id,)
            )
            row = await cursor.fetchone()

            if not row:
                return None

            return QueueTask(
                id=row[0],
                task_type=row[1],
                application_id=row[2],
                provider=row[3],
                payload=json.loads(row[4]),
                attempt_count=row[5],
                max_retries=row[6],
                next_retry_at=datetime.fromisoformat(row[7].replace("Z", "+00:00")),
                last_error=row[8],
                status=TaskStatus(row[9]),
                created_at=datetime.fromisoformat(row[10].replace("Z", "+00:00")),
                updated_at=datetime.fromisoformat(row[11].replace("Z", "+00:00"))
            )
