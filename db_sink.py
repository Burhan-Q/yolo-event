"""
Kafka Consumer that reads detection events from dynamic topics and writes them to PostgreSQL.

This script connects to Kafka topics matching patterns like 'device-{ID}' and 'class-{class_index}',
consumes detection event messages, and stores them in a PostgreSQL database table named 'detections'.
"""

import logging
import msgpack
import re
import signal
import time
from datetime import datetime
from typing import Any

from kafka import KafkaConsumer, TopicPartition
from kafka.admin import KafkaAdminClient, NewTopic
from kafka.errors import KafkaError
import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class DetectionEvent:
    """Class representing a detection event from Kafka message."""

    def __init__(
        self,
        device_id: str,
        class_index: int,
        class_name: str,
        confidence: float,
        bbox: list[float],
        timestamp: int,
        topic: str = "",
    ) -> None:
        """
        Initialize a detection event.

        Args:
            device_id: Unique identifier for the device
            class_index: Index of the detected object class
            class_name: Name of the detected object class (not stored in DB)
            confidence: Confidence score of the detection (0.0-1.0)
            bbox: Bounding box coordinates [x, y, width, height]
            timestamp: Unix timestamp of the detection event
            topic: Kafka topic the event was consumed from
        """
        self.device_id = device_id
        self.class_index = class_index
        self.class_name = class_name
        self.confidence = confidence
        self.bbox = bbox
        self.timestamp = timestamp
        self.topic = topic
        self.created_at = datetime.now()

    @classmethod
    def from_message(cls, message: dict[str, Any], topic: str) -> "DetectionEvent":
        """
        Create a DetectionEvent from a Kafka message.

        Args:
            message: Dictionary containing detection event data
            topic: Kafka topic the message was consumed from

        Returns:
            A new DetectionEvent instance
        """
        return cls(
            device_id=message["device_id"],
            class_index=message["class_index"],
            class_name=message["class_name"],
            confidence=message["confidence"],
            bbox=message["bbox"],
            timestamp=message["timestamp"],
            topic=topic,
        )

    def to_dict(self) -> dict[str, Any]:
        """
        Convert the detection event to a dictionary for database insertion.

        Returns:
            Dictionary containing detection event data with column names as keys
        """
        return {
            "device_id": self.device_id,
            "class_index": self.class_index,
            "confidence": self.confidence,
            "bbox": self.bbox,  # a list for FLOAT[] array type
            "timestamp": self.timestamp,
        }


class DynamicTopicKafkaConsumer:
    """Consumes detection events from dynamic Kafka topics and writes them to PostgreSQL."""

    def __init__(
        self,
        kafka_bootstrap_servers: list[str],
        consumer_group: str,
        pg_conninfo: str,
        topic_patterns: list[str],
        topic_refresh_interval: int = 60,  # seconds
        batch_size: int = 100,
        min_pool_size: int = 1,
        max_pool_size: int = 5,
    ) -> None:
        """
        Initialize the Kafka to PostgreSQL consumer.

        Args:
            kafka_bootstrap_servers: List of Kafka broker addresses
            consumer_group: Kafka consumer group ID
            pg_conninfo: PostgreSQL connection string
            topic_patterns: List of regex patterns to match topic names
            topic_refresh_interval: How often to check for new topics (seconds)
            batch_size: Number of records to batch insert
            min_pool_size: Minimum number of connections in the pool
            max_pool_size: Maximum number of connections in the pool
        """
        self.kafka_bootstrap_servers = kafka_bootstrap_servers
        self.consumer_group = consumer_group
        self.pg_conninfo = pg_conninfo
        self.topic_patterns = [re.compile(pattern) for pattern in topic_patterns]
        self.topic_refresh_interval = topic_refresh_interval
        self.batch_size = batch_size
        self.min_pool_size = min_pool_size
        self.max_pool_size = max_pool_size

        self.consumer = None
        self.admin_client = None
        self.pg_pool = None
        self.running = False
        self.subscribed_topics: set[str] = set()
        self.last_topic_refresh: float = 0
        self.health = {
            "status": "initializing",
            "last_message_time": None,
            "messages_processed": 0,
            "batches_committed": 0,
            "errors": 0,
        }
        self._setup_signal_handlers()

    def _setup_signal_handlers(self):
        """Set up handlers for OS signals."""
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        signal.signal(signal.SIGINT, self._handle_shutdown)

    def _handle_shutdown(self, signum, frame):
        """Handle shutdown signals gracefully."""
        logger.info(f"Received signal {signum}, shutting down...")
        self.stop()

    def get_health(self) -> dict:
        """Return health status of the consumer."""
        return self.health

    def _init_kafka_admin_client(self) -> None:
        """Initialize the Kafka admin client for listing topics."""
        self.admin_client = KafkaAdminClient(
            bootstrap_servers=self.kafka_bootstrap_servers, client_id=f"{self.consumer_group}-admin"
        )
        logger.info("Kafka admin client initialized")

    def _init_kafka_consumer(self) -> None:
        """Initialize and configure the Kafka consumer."""
        self.consumer = KafkaConsumer(
            bootstrap_servers=self.kafka_bootstrap_servers,
            group_id=self.consumer_group,
            auto_offset_reset="earliest",
            enable_auto_commit=False,
            value_deserializer=lambda m: msgpack.unpackb(m, raw=False),
            consumer_timeout_ms=1000,  # 1 second timeout for consumer.next()
        )
        logger.info("Kafka consumer initialized")

        # Initial topic subscription
        self._refresh_topic_subscriptions()

    def _init_postgres_connection(self) -> None:
        """Initialize the PostgreSQL connection pool."""
        self.pg_pool = ConnectionPool(
            conninfo=self.pg_conninfo,
            min_size=self.min_pool_size,
            max_size=self.max_pool_size,
            open=True,  # Open connections immediately
            kwargs={"row_factory": dict_row},  # Return query results as dictionaries
        )
        logger.info("Connected to PostgreSQL database")

        # Check if the table exists
        with self.pg_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables
                        WHERE table_name = 'detections'
                    );
                """)
                result = cur.fetchone()
                table_exists = result["exists"] if result else False

                if not table_exists:
                    logger.warning("Table 'detections' does not exist in the database.")
                    logger.info("Expected table schema:")
                    logger.info("""
                    CREATE TABLE detections (
                        id SERIAL PRIMARY KEY,
                        device_id VARCHAR(255) NOT NULL,
                        class_index INTEGER NOT NULL,
                        class_name VARCHAR(255),
                        confidence FLOAT NOT NULL,
                        bbox FLOAT[] NOT NULL,
                        timestamp BIGINT NOT NULL,
                        thumbnail BYTEA,
                        image_url TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                    """)
                    raise Exception("Required database table 'detections' does not exist")

                logger.info("Database table 'detections' verified")

    def _topic_matches_patterns(self, topic: str) -> bool:
        """
        Check if a topic name matches any of the defined patterns.

        Args:
            topic: The topic name to check

        Returns:
            True if the topic matches any pattern, False otherwise
        """
        return any(pattern.match(topic) for pattern in self.topic_patterns)

    def _refresh_topic_subscriptions(self) -> None:
        """Discover available topics and subscribe to those matching the patterns."""
        now = datetime.now().timestamp()

        # Only refresh if the interval has passed
        if now - self.last_topic_refresh < self.topic_refresh_interval:
            return

        backoff = 1  # Start with 1 second backoff
        max_attempts = 3
        attempt = 0

        while attempt < max_attempts:
            try:
                # List all topics in the Kafka cluster
                all_topics = self.admin_client.list_topics()
                matching_topics = {topic for topic in all_topics if self._topic_matches_patterns(topic)}
                new_topics = matching_topics - self.subscribed_topics

                if new_topics:
                    # Add the new topics to our subscription
                    self.consumer.subscribe(list(matching_topics))
                    self.subscribed_topics = matching_topics
                    logger.info(f"Subscribed to {len(new_topics)} new topics. Total topics: {len(matching_topics)}")
                    logger.debug(f"New topics: {new_topics}")
                else:
                    logger.debug("No new topics found")

                self.last_topic_refresh = now
                return  # Success, exit the retry loop

            except KafkaError as e:
                attempt += 1
                logger.warning(f"Kafka error refreshing topics (attempt {attempt}/{max_attempts}): {e}")
                if attempt < max_attempts:
                    time.sleep(backoff)
                    backoff *= 2  # Exponential backoff
            except Exception as e:
                logger.exception(f"Unexpected error refreshing topic subscriptions: {e}")
                break  # Don't retry on non-Kafka errors

        logger.error(f"Failed to refresh topics after {max_attempts} attempts")

    def _insert_batch(self, events: list[DetectionEvent]) -> None:
        """
        Insert a batch of detection events into PostgreSQL.

        Args:
            events: List of DetectionEvent objects to insert
        """
        if events:
            with self.pg_pool.connection() as conn:
                try:
                    with conn.cursor() as cur:
                        # Create a list of parameter dictionaries
                        params_list = [event.to_dict() for event in events]

                        # Use the RETURNING clause to get the inserted IDs if needed
                        query = """
                            INSERT INTO detections (
                                device_id, class_index, confidence, bbox, timestamp
                            ) VALUES (
                                %(device_id)s, %(class_index)s, %(confidence)s, %(bbox)s, %(timestamp)s
                            )
                        """

                        # Execute a batch insert with named parameters
                        cur.executemany(query, params_list)

                    # Explicitly commit the transaction
                    conn.commit()
                    logger.info(f"Inserted batch of {len(events)} events into PostgreSQL")
                except Exception as e:
                    # Automatically rolled back by context manager if an exception occurs
                    logger.error(f"Error inserting batch: {e}")
                    # Re-raise the exception to be caught by the caller
                    raise

    def start(self) -> None:
        """Start consuming messages from Kafka and writing to PostgreSQL."""
        try:
            self._init_kafka_admin_client()
            self._init_kafka_consumer()
            self._init_postgres_connection()

            self.running = True
            events_buffer: list[DetectionEvent] = []
            last_commit_time = datetime.now().timestamp()

            logger.info("Starting to consume messages...")
            while self.running:
                try:
                    # Periodically check for new topics
                    self._refresh_topic_subscriptions()

                    # Consume messages in a batch
                    for message in self.consumer:
                        try:
                            topic = message.topic
                            payload = message.value
                            event = DetectionEvent.from_message(payload, topic)
                            events_buffer.append(event)

                            # If batch size reached, insert into PostgreSQL
                            if len(events_buffer) >= self.batch_size:
                                self._insert_batch(events_buffer)
                                events_buffer = []
                                # Commit Kafka offsets after successful batch insert
                                self.consumer.commit()
                                last_commit_time = datetime.now().timestamp()

                        except KeyError as e:
                            logger.error(f"Missing required field in message: {e}")
                        except Exception as e:
                            logger.error(f"Error processing message: {e}")

                except StopIteration:
                    # This happens when consumer_timeout_ms is reached
                    now = datetime.now().timestamp()

                    # Process any remaining events or commit periodically (every 10 seconds)
                    if events_buffer or (now - last_commit_time >= 10):
                        if events_buffer:
                            self._insert_batch(events_buffer)
                            events_buffer = []

                        # Always commit to update offsets even if no new messages
                        self.consumer.commit()
                        last_commit_time = now
                except Exception as e:
                    logger.error(f"Unexpected error: {e}")
                    # Don't break the loop for transient errors

            # Insert any remaining events before stopping
            if events_buffer:
                self._insert_batch(events_buffer)
                self.consumer.commit()

        except KeyboardInterrupt:
            logger.info("Interrupted by user")
        finally:
            logger.info("Closing connections...")
            if self.consumer:
                self.consumer.close()
            if self.admin_client:
                self.admin_client.close()
            if self.pg_pool:
                self.pg_pool.close()
            logger.info("Consumer stopped")

    def stop(self) -> None:
        """Stop the consumer."""
        self.running = False


def main() -> None:
    """Main entry point for the Kafka to PostgreSQL consumer."""
    # Kafka configuration
    kafka_bootstrap_servers = ["localhost:9092"]
    consumer_group = "detection-events-consumer"

    # Topic patterns to match
    topic_patterns = [
        r"^device-.*$",  # Topics starting with "device-"
        r"^class-\d+$",  # Topics matching "class-" followed by digits
    ]

    # PostgreSQL connection string
    pg_conninfo = "host=localhost port=5432 dbname=detections_db user=postgres password=postgres"

    consumer = DynamicTopicKafkaConsumer(
        kafka_bootstrap_servers=kafka_bootstrap_servers,
        consumer_group=consumer_group,
        pg_conninfo=pg_conninfo,
        topic_patterns=topic_patterns,
        topic_refresh_interval=60,  # Check for new topics every 60 seconds
        batch_size=100,
    )

    consumer.start()


if __name__ == "__main__":
    main()
