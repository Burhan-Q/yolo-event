import os
import time

import msgpack
from kafka import KafkaProducer
from ultralytics import YOLO
from ultralytics.engine.results import Results

ADDRESS = os.getenv("KAFKA_BOOSTRAP_SERVER_IP")
PORT = os.getenv("KAFKA_BOOSTRAP_SERVER_PORT")
if ADDRESS is None or PORT is None:
    raise Exception("Kafka bootstrap server address and port must be set as environment variables")

bootstrap_servers = ADDRESS + ":" + PORT


def send_data(topic: str, payload: dict) -> None:
    """
    Sends data to a specified Kafka topic.
    Args:
        topic (str): The Kafka topic to which the data will be sent.
        payload (dict): The data payload to be sent. Must include a "timestamp" key.

    Returns:
        None
    """

    producer = KafkaProducer(
        bootstrap_servers=bootstrap_servers,
        value_serializer=msgpack.dumps,
    )

    producer.send(topic, key=str(payload["timestamp"]).encode(), value=payload)

    producer.flush()  # Ensure all messages are sent
    producer.close()


def process_result(r: Results, dev_id: str, time_stamp: int) -> list[dict]:
    """
    Processes the results from a YOLO model and returns a list of detection records.

    Args:
        r (Results): The results object from the YOLO model.
        dev_id (str): The ID of the device that generated the results.
        time_stamp (int): The timestamp of the detection event.

    Returns:
        list[dict]: A list of detection records.
    """
    r_np = r.numpy()
    detections = []
    for box, conf, cidx in zip(r_np.boxes.xyxy, r_np.boxes.conf, r_np.boxes.cls):
        detections.append(
            dict(
                device_id=dev_id,
                class_index=int(cidx),
                class_name=r.names[cidx],  # uses float
                confidence=float(conf),
                bbox=box.tolist(),
                thumbnail=None,
                image_url=None,
                timestamp=time_stamp,
            )
        )

    return detections


def submit_to_topics(detections: list[dict]) -> None:
    """
    Submits detection data to Kafka topics.

    Args:
        detections (list[dict]): A list of detection records. With keys:
            {
                "device_id": str,
                "class_index": int,
                "class_name": str,
                "confidence": float,
                "bbox": list[float],
                "timestamp": int
            }
    """
    for detect in detections:
        send_data(f"device-{detect['device_id']}", detect)
        send_data(f"class-{detect['class_index']}", detect)


def run(src: str, weights: str = None, **pred_kwargs):
    """
    Run the YOLO model on a video stream and submit the detection results to Kafka topics.

    Args:
        src (str): The video stream source.
        weights (str): The path to the YOLO model weights.
        pred_kwargs: Additional keyword arguments to pass to the YOLO model's predict method.

    Returns:
        None

    Note:
        The stream prediction argument is always set to True. This function will run indefinitely until stopped.
    """
    model = YOLO(weights or "yolo11n.pt")
    pred_kwargs |= {"stream": True}

    for result in model.predict(src, **pred_kwargs):
        messages = process_result(result, src, time_stamp=int(time.time()))
        submit_to_topics(messages)


if __name__ == "__main__":
    # Example Mock detection data:
    detections = [
        {
            "device_id": "dev-0afeb",
            "class_index": 0,
            "class_name": "person",
            "confidence": 0.940152645111084,
            "bbox": [3.8327693939208984, 229.36422729492188, 796.194580078125, 728.4122924804688],
            "timestamp": 1742065460,
        },
        {
            "device_id": "dev-0afeb",
            "class_index": 0,
            "class_name": "person",
            "confidence": 0.8882198929786682,
            "bbox": [671.0172119140625, 394.83306884765625, 809.8097534179688, 878.7124633789062],
            "timestamp": 1742065460,
        },
        {
            "device_id": "dev-0afeb",
            "class_index": 0,
            "class_name": "person",
            "confidence": 0.8782549500465393,
            "bbox": [47.404727935791016, 399.56512451171875, 239.3006591796875, 904.1950073242188],
            "timestamp": 1742065460,
        },
        {
            "device_id": "dev-0afeb",
            "class_index": 0,
            "class_name": "person",
            "confidence": 0.8557723164558411,
            "bbox": [223.05899047851562, 408.68865966796875, 344.4676208496094, 860.435791015625],
            "timestamp": 1742065460,
        },
        {
            "device_id": "dev-0afeb",
            "class_index": 5,
            "class_name": "bus",
            "confidence": 0.6219195127487183,
            "bbox": [0.021725893020629883, 556.0684204101562, 68.88545227050781, 872.3591918945312],
            "timestamp": 1742065460,
        },
    ]

    submit_to_topics(detections)
