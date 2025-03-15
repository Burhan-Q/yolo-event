CREATE TABLE detections (
    id SERIAL PRIMARY KEY,
    device_id VARCHAR(255) NOT NULL,
    class_index INTEGER NOT NULL,
    confidence FLOAT NOT NULL,
    bbox FLOAT[] NOT NULL,
    timestamp BIGINT NOT NULL,
    thumbnail BYTEA,
    image_url TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_device_id ON detections (device_id);
CREATE INDEX idx_class_index ON detections (class_index);
CREATE INDEX idx_timestamp ON detections (timestamp);