
## Host Setup (Env variables)

Either setup `.env` file or use `export` command to set environment variables

```sh
KAFKA_BOOSTRAP_SERVER_IP=
KAFKA_BOOSTRAP_SERVER_PORT=9092  # default
POSTGRES_USER=
POSTGRES_PASSWORD=
GF_SECURITY_ADMIN_USER=
GF_SECURITY_ADMIN_PASSWORD=
```

```sh
# used environment variable to populate host machine IP
i=enp9s0
export KAFKA_HOST_IP=$(ip addr show $i | grep "inet\b" | awk '{print $2}' | cut -d '/' -f 1)
```

## Pull Kafka image

```sh
k=apache/kafka:latest

docker pull $k

docker run -d -p 9092:9092 --name yolo-event $k
```

### Postgres Config

1. Designing the Table:

    - First, we need to design a table that can efficiently store your detection data. Here's a proposed structure:

    ```sql
    CREATE TABLE detections (
        id SERIAL PRIMARY KEY,
        device_id VARCHAR(255) NOT NULL,
        class_index INTEGER NOT NULL,
        class_name VARCHAR(255) NOT NULL
        confidence FLOAT NOT NULL,
        bbox FLOAT[] NOT NULL,
        timestamp BIGINT NOT NULL,
        thumbnail BYTEA,  -- For thumbnail data (binary)
        image_url TEXT,     -- For the URL to the full-size image
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP -- Add a created_at column.
    );

    CREATE INDEX idx_device_id ON detections (device_id);
    CREATE INDEX idx_class_index ON detections (class_index);
    CREATE INDEX idx_timestamp ON detections (timestamp);
    ```

    - `id SERIAL PRIMARY KEY`: A unique, auto-incrementing integer ID for each detection.

    - `device_id VARCHAR(255) NOT NULL`: The ID of the device that generated the detection. NOT NULL means this column cannot be empty.

    - `class_index INTEGER NOT NULL`: The index of the detected class.

    - `class_name VARCHAR(255) NOT NULL`: The class name as a string.

    - `confidence FLOAT NOT NULL`: The confidence score of the detection.

    - `bbox FLOAT[] NOT NULL`: A PostgreSQL array to store the bounding box coordinates.

    - `timestamp BIGINT NOT NULL`: The timestamp of the detection (e.g., Unix timestamp).

    - `thumbnail BYTEA`: A BYTEA (byte array) column to store the thumbnail image data.

    - `image_url TEXT`: A TEXT column to store the URL of the full-size image.

    - `created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP`: A timestamp that is automatically set to the time the record is created.

    **NOTE:** any column with `NOT NULL` means it wont' accept `NULL` values

2. Adding Indexes:

    - Indexes improve query performance by allowing the database to quickly locate specific rows. Here's how to add indexes:

    ```sql
    CREATE INDEX idx_device_id ON detections (device_id);
    CREATE INDEX idx_class_index ON detections (class_index);
    CREATE INDEX idx_timestamp ON detections (timestamp);
    ```

    **Explanation:**

    - `CREATE INDEX idx_device_id ON detections (device_id)`: Creates an index on the device_id column. This is useful for querying detections by device.

    - `CREATE INDEX idx_class_index ON detections (class_index)`: Creates an index on the class_index column. This is useful for querying detections by class.

    - `CREATE INDEX idx_timestamp ON detections (timestamp)`: Creates an index on the timestamp column. This is useful for querying detections by time range.

3. Modifying ~~the Faust Application or~~ Kafka Connect:

    - ~~Faust:~~

        - ~~You'll need to modify your Faust application to send the detection data to PostgreSQL.~~

        - ~~This will involve using a PostgreSQL client library for python, and inserting the data into the database.~~

        - ~~You will also need to add the thumbnail and image url data to the messages sent to postgres.~~

    - Kafka Connect:

    - Use the Kafka Connect JDBC Sink connector to move data from Kafka to PostgreSQL.

        ```
        https://docs.confluent.io/kafka-connectors/jdbc/current/sink-connector/overview.html#install-the-connector-using-the-confluent-cli
        ```

    - Configure the connector to map the Kafka message fields to the corresponding columns in the detections table.

    - The Kafka connect configuration will also need to be configured to handle the thumbnail data and image url.

4. Handling Thumbnail Data (BYTEA):

    - Encoding: When sending thumbnail data to PostgreSQL, you'll need to encode it as a byte array. In Python, you can use the bytes type.

    - Decoding: When retrieving thumbnail data from PostgreSQL, you'll receive it as a byte array. You can then decode it back into an image format.

5. Handling Image URLs (TEXT):

    - Storing and retrieving image URLs is straightforward. Simply insert the URL as a string into the image_url column.

    **Important Considerations:**

    - Data Types: Ensure that the data types in your Kafka messages match the column data types in your PostgreSQL table.

    - Error Handling: Implement robust error handling in your Faust application or Kafka Connect configuration.

    - Security: Use strong passwords and restrict access to your PostgreSQL database.

    - Performance: Monitor the performance of your database and adjust indexes or queries as needed.

    - Kafka Connect: When using Kafka connect, you will need to configure the connector to handle the conversion of the data from the kafka message, to the correct postgres data types.

    - ~~Faust: When using Faust, you will need to use a postgresql client library, and handle the data conversion within your python code.~~


Start the Connector:

Call the Kafka Connect REST API from the host machine to start the connector.

```sh
curl -X POST -H "Content-Type: application/json" --data @connect-config/postgresql-sink.json http://yolo-connect:8083/connectors
```

**NOTE:** may need to use `http://yolo-connect:8083/connectors/config` endpoint

    - see [kafka-connect-101](https://developer.confluent.io/courses/kafka-connect/intro/)

    - also see [kafka-connect user guide](https://docs.confluent.io/platform/current/connect/userguide.html)


### Grafana

1. Start Docker Compose:

    - Run `docker-compose up -d` to start the Grafana container.

2. Access Grafana:

    - Open your web browser and navigate to http://localhost:3000.

    - Log in with the username `admin` and the password set in the `.env` file or from the environment.

6. Create a Dashboard:

    - Click the "+" icon in the left sidebar and select "Dashboard."

    - Click "Add new panel."

    - Select "PostgreSQL" as the data source.

    - Write SQL queries to visualize your detection data.

    - Choose appropriate visualization types (time series, tables, bar charts, etc.).

7. Create a Dashboard Provisioning File (optional):

    - You can also provision dashboards using YAML files.

    - Create a YAML file in the grafana-dashboards directory and add your dashboard configuration.

    - This will automatically create the dashboard on Grafana startup.

#### Important Notes:

- Security: Change the default admin password in production.

- Data Source Configuration: Adjust the data source provisioning file to match your PostgreSQL credentials and database name.

- Dashboard Design: Design your dashboards to effectively visualize your detection data.

- Grafana Plugins: If you need additional Grafana features, you can install plugins. This can be done by creating a custom docker image, or by mounting the plugin into the correct directory.

- Grafana Variables: Use Grafana variables to allow users to dynamically filter data (e.g., by device ID or class name).

- Time Range: Use Grafana's time range selector to view data over different time periods.

- Alerting: Set up Grafana alerts to notify you of important events.

- Dashboard JSON: You can export dashboards as JSON files, and then import them into other grafana instances.


### Example Kafka Message

```py
[
    {
        "device_id": "dev-id",
        "class_index": 0,
        "class_name": "person",
        "confidence": 0.940152645111084,
        "bbox": [
            3.8327693939208984,
            229.36422729492188,
            796.194580078125,
            728.4122924804688
        ],
        "timestamp: 1742065460
    },
    {
        "device_id": "dev-id",
        "class_index": 0,
        "class_name": "person",
        "confidence": 0.8882198929786682,
        "bbox": [
            671.0172119140625,
            394.83306884765625,
            809.8097534179688,
            878.7124633789062
        ],
        "timestamp: 1742065460
    },
    {
        "device_id": "dev-id",
        "class_index": 0,
        "class_name": "person",
        "confidence": 0.8782549500465393,
        "bbox": [
            47.404727935791016,
            399.56512451171875,
            239.3006591796875,
            904.1950073242188
        ],
        "timestamp: 1742065460
    },
    {
        "device_id": "dev-id",
        "class_index": 0,
        "class_name": "person",
        "confidence": 0.8557723164558411,
        "bbox": [
            223.05899047851562,
            408.68865966796875,
            344.4676208496094,
            860.435791015625
        ],
        "timestamp: 1742065460
    },
    {
        "device_id": "dev-id",
        "class_index": 5,
        "class_name": "bus",
        "confidence": 0.6219195127487183,
        "bbox": [
            0.021725893020629883,
            556.0684204101562,
            68.88545227050781,
            872.3591918945312
        ],
        "timestamp: 1742065460
    }
]
```

### Progress

- 2025-03-15 :: `grafana`, `kafka`, and `postgres` services are all working, but `kafka-connect` fails every time and I'm unable to determine the cause.

    #### Error

    ```sh
    Error: Could not find or load main class io.confluent.connect.hub.cli.ConfluentHubClient
    Caused by: java.lang.ClassNotFoundException: io.confluent.connect.hub.cli.ConfluentHubClient
    ```

    Would be _really_ nice to be able to find a resource for debugging this issue.


### References

- https://rmoff.net/2018/12/15/docker-tips-and-tricks-with-kafka-connect-ksqldb-and-kafka/

- https://hub.docker.com/r/apache/kafka

- https://developer.confluent.io/courses/kafka-connect/intro/

- https://docs.confluent.io/platform/current/connect/userguide.html


## Testing and Troubleshooting

### Testing

1. Run services

```sh
docker compose up -d --build
```

2. Execute `producer.py` to submit test messages to broker.

3. Check consumer messages (as needed).

4. Check Postgres DB records

    a. Connect to container

    ```sh
    docker exec -it yolo-postgres bash
    ```

    b. Connect to DB, use `POSTGRES_USER` from `.env` file.

    ```sh
    psql -U < POSTGRES_USER from .env > -d yolo_db
    ```
    
    c. List all tables

    ```sh
    \dt
    ```
    
    d Query target table to view records

    ```sh
    SELECT * FROM detections;
    ```

### Check Consumer Messages

1. Connect to Kafka service container

```sh
docker exec -it kafka bash
```

2. Check messages

```sh
kafka-console-consumer \
  --bootstrap-server kafka:29092 \
  --topic class-0 \  # can use a different topic name as needed
  --from-beginning
```

