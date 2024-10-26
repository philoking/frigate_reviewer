import paho.mqtt.client as mqtt
import requests
import json
import base64
import os
import threading
import queue
import time
from ultralytics import YOLO
from PIL import Image
import io
import logging
from datetime import datetime

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('frigate_validator.log'),
        logging.StreamHandler()
    ]
)

# Configuration parameters
MQTT_BROKER = '192.168.0.200'
MQTT_PORT = 1883
MQTT_TOPIC = 'frigate/events/#'
FRIGATE_API_URL = 'http://192.168.0.211:5000'

# Create directories for debugging
os.makedirs('reviewed_images/true', exist_ok=True)
os.makedirs('reviewed_images/false', exist_ok=True)
os.makedirs('reviewed_images/debug', exist_ok=True)

# Initialize queue and stop event
event_queue = queue.Queue()
stop_event = threading.Event()

# Initialize YOLOv8 model
yolo_model = YOLO('yolov8n.pt')

# Configuration for YOLO detection
CONFIDENCE_THRESHOLD = 0.5  # Lowered threshold since we're validating Frigate's detection
TARGET_CLASSES = {'person', 'car', 'truck', 'dog', 'cat'}  # Add or remove classes as needed

def on_connect(client, userdata, flags, rc):
    """
    Callback when the client receives a CONNACK response from the server.
    """
    if rc == 0:
        logging.info("Connected to MQTT Broker!")
        logging.info("Listening for Frigate events...")
        client.subscribe(MQTT_TOPIC)
    else:
        logging.error(f"Failed to connect to MQTT Broker. Return code: {rc}")

def on_message(client, userdata, msg, properties=None):
    """
    Callback when a PUBLISH message is received from the server.
    """
    try:
        payload = json.loads(msg.payload)
    except json.JSONDecodeError:
        logging.error("Received a message that is not valid JSON.")
        return

    event_type = payload.get('type')
    # Only process 'end' events
    if event_type == 'end':
        event = payload.get('after', {})
        event_id = event.get('id')
        camera = event.get('camera')
        labels = event.get('labels', [])
        has_snapshot = event.get('has_snapshot', False)

        if not event_id:
            logging.error("Received an event without an ID. Skipping.")
            return

        # Enqueue the event details
        event_details = {
            'id': event_id,
            'camera': camera,
            'labels': labels,
            'has_snapshot': has_snapshot
        }
        event_queue.put(event_details)
        logging.info(f"Enqueued event {event_id} from camera {camera}")

def mark_event_as_false_positive(event_id):
    """
    Marks an event as a false positive in Frigate.
    Requires Frigate Plus API Key environment variable for marking reviewed
    environment:
      - PLUS_API_KEY=your_key
    """
    url = f"{FRIGATE_API_URL}/api/events/{event_id}/false_positive"
    try:
        response = requests.put(url)
        if response.status_code == 200:
            logging.info(f"Event {event_id} marked as false positive")
        else:
            logging.error(f"Failed to mark event {event_id} as false positive. Status code: {response.status_code}")
    except requests.exceptions.RequestException as e:
        logging.error(f"Error marking event {event_id} as false positive: {e}")

def process_event(event_details):
    """
    Process event using only YOLO detection for validation.
    """
    event_id = event_details['id']
    camera = event_details['camera']
    has_snapshot = event_details['has_snapshot']

    if not has_snapshot:
        logging.info(f"Event {event_id} has no snapshot available")
        return

    logging.info(f"Processing event {event_id} from camera {camera}")
    
    # Get the snapshot image
    snapshot_url = f"{FRIGATE_API_URL}/api/events/{event_id}/snapshot.jpg"
    try:
        response = requests.get(snapshot_url)
        response.raise_for_status()
        image_data = response.content
        image = Image.open(io.BytesIO(image_data)).convert('RGB')
    except Exception as e:
        logging.error(f"Error retrieving/processing image for event {event_id}: {e}")
        return

    # Perform YOLO detection
    results = yolo_model(image)
    objects = results[0].boxes

    # Initialize detection flags and info
    valid_detection = False
    detections = []

    # Check each detected object
    for obj in objects:
        class_name = yolo_model.names[int(obj.cls[0])]
        confidence = float(obj.conf[0])  # Ensure we have a float
        
        # Detailed debug logging
        logging.info(f"Detection in event {event_id}:")
        logging.info(f"  Class: {class_name}")
        logging.info(f"  Confidence: {confidence:.3f}")
        logging.info(f"  Threshold: {CONFIDENCE_THRESHOLD}")
        logging.info(f"  In target classes: {class_name in TARGET_CLASSES}")
        logging.info(f"  Passes threshold: {confidence > CONFIDENCE_THRESHOLD}")
        
        # Record detection details
        detection_info = {
            'class': class_name,
            'confidence': confidence,
            'bbox': [float(x) for x in obj.xywh[0]]
        }
        detections.append(detection_info)
        
        # Explicit comparison with debug output
        if class_name in TARGET_CLASSES:
            if confidence > CONFIDENCE_THRESHOLD:
                valid_detection = True
                logging.info(f"VALID DETECTION: {class_name} with confidence {confidence:.3f}")
                break
            else:
                logging.info(f"Detection below threshold: {confidence:.3f} <= {CONFIDENCE_THRESHOLD}")

    # Save debug information
    debug_info = {
        'event_id': event_id,
        'timestamp': datetime.now().isoformat(),
        'camera': camera,
        'detections': detections,
        'confidence_threshold': CONFIDENCE_THRESHOLD,
        'final_decision': 'valid' if valid_detection else 'false_positive'
    }
    
    debug_path = f"reviewed_images/debug/{event_id}"
    os.makedirs(debug_path, exist_ok=True)
    
    # Save both the image and detection details
    with open(f"{debug_path}/detection_details.json", "w") as f:
        json.dump(debug_info, f, indent=2)
    with open(f"{debug_path}/snapshot.jpg", "wb") as f:
        f.write(image_data)

    # Save to appropriate directory and handle false positives
    if valid_detection:
        output_path = f"reviewed_images/true/{event_id}.jpg"
        logging.info(f"Event {event_id}: Valid detection confirmed by YOLO")
    else:
        output_path = f"reviewed_images/false/{event_id}.jpg"
        logging.info(f"Event {event_id}: No valid detection found by YOLO")
        mark_event_as_false_positive(event_id)

    with open(output_path, "wb") as f:
        f.write(image_data)

def worker():
    """
    Worker thread that processes events from the queue.
    """
    while not stop_event.is_set():
        try:
            # Wait for an event for up to 1 second
            event_details = event_queue.get(timeout=1)
            process_event(event_details)
            event_queue.task_done()
        except queue.Empty:
            continue
        except Exception as e:
            logging.error(f"Error processing event: {e}")
            if event_details:
                event_queue.task_done()

def start_worker():
    """
    Starts the worker thread.
    """
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return thread

def main():
    # Set up the MQTT client
    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message

    # Connect to the MQTT broker
    try:
        client.connect(MQTT_BROKER, MQTT_PORT, 60)
    except Exception as e:
        logging.error(f"Failed to connect to MQTT broker: {e}")
        return

    # Start the worker thread
    worker_thread = start_worker()

    # Start the MQTT client loop in a separate thread
    client_thread = threading.Thread(target=client.loop_forever, daemon=True)
    client_thread.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logging.info("Shutting down...")
        stop_event.set()
        worker_thread.join()
        client.disconnect()
        logging.info("Shutdown complete")

if __name__ == '__main__':
    main()
