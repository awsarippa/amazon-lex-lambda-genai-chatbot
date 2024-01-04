import json
import boto3
import http
import os
import logging
from botocore.exceptions import ClientError
from PIL import Image
import io
import base64
import random
import pyshorteners

LOG = logging.getLogger()
LOG.setLevel(logging.INFO)

region_name = os.getenv("region", "us-east-1")
s3_bucket = os.getenv("bucket")
model_id = os.getenv("model_id", "stability.stable-diffusion-xl-v0")
style_preset = os.getenv("style_preset", "photographic")  # digital-art, cinematic

# Bedrock client used to interact with APIs around models
bedrock = boto3.client(service_name="bedrock", region_name=region_name)

# Bedrock Runtime client used to invoke and question the models
bedrock_runtime = boto3.client(service_name="bedrock-runtime", region_name=region_name)

# S3 client used to interact with S3 buckets
s3_client = boto3.client('s3')


def get_session_attributes(intent_request):
    sessionState = intent_request['sessionState']
    if 'sessionAttributes' in sessionState:
        return sessionState['sessionAttributes']

    return {}


def close(intent_request, session_attributes, fulfillment_state, message):
    intent_request['sessionState']['intent']['state'] = fulfillment_state
    return {
        'sessionState': {
            'sessionAttributes': session_attributes,
            'dialogAction': {
                'type': 'Close'
            },
            'intent': intent_request['sessionState']['intent']
        },
        'messages': [message],
        'sessionId': intent_request['sessionId'],
        'requestAttributes': intent_request['requestAttributes'] if 'requestAttributes' in intent_request else None
    }


def generate_and_save_image(event, base_64_img_str):
    # try:
    session_attributes = get_session_attributes(event)

    # Convert the base64 encoded data to image
    generated_img = Image.open(io.BytesIO(base64.decodebytes(bytes(base_64_img_str, "utf-8"))))
    temp_path = "/tmp/generatedFilePath"
    temp_file_name = 'generatedImage' + '_' + str(random.randint(1, 100000000000000000)) + '.png'
    temp_file_path = temp_path + "/" + temp_file_name

    # Save the file temporarily in Lambda memory
    os.makedirs(temp_path, exist_ok=True)
    generated_img.save(temp_file_path)

    # Upload the generated image to S3 bucket
    uploadresponse = s3_client.upload_file(temp_file_path, s3_bucket, temp_file_name)
    LOG.info(f"Upload response is {uploadresponse}")

    presignedurl = s3_client.generate_presigned_url(
        ClientMethod="get_object",
        Params={
            "Bucket": s3_bucket,
            "Key": temp_file_name
        },
        ExpiresIn=604800,
    )

    # Shorten the url
    type_tiny = pyshorteners.Shortener()
    shorturl = type_tiny.tinyurl.short(presignedurl)
    message_text = "An image has been generated and saved in a S3 bucket. A s3 presigned url has been generated and image can be downloaded from the url " + shorturl
    message = {
        'contentType': 'PlainText',
        'content': message_text
    }
    fulfillment_state = "Fulfilled"
    LOG.info(f"Pre-signed S3 url is {shorturl}")
    return close(event, session_attributes, fulfillment_state, message)


def lambda_handler(event, context):
    LOG.info(f"Event is {event}")
    accept = 'application/json'
    content_type = 'application/json'
    prompt = event["inputTranscript"]

    negative_prompts = [
        "poorly rendered",
        "poor background details",
        "poorly drawn",
        "disfigured features",
    ]
    try:

        request = json.dumps({
            "text_prompts": (
                    [{"text": prompt, "weight": 1.0}]
                    + [{"text": negprompt, "weight": -1.0} for negprompt in negative_prompts]
            ),
            "cfg_scale": 5,
            "seed": 5450,
            "steps": 70,
            "style_preset": style_preset,
        })

        response = bedrock_runtime.invoke_model(body=request, modelId=model_id)
        # LOG.info(response)

        response_body = json.loads(response.get("body").read())
        LOG.info(f"Response body: {response_body}")

        base_64_img_str = response_body["artifacts"][0].get("base64")
        LOG.info(f"Base string is {base_64_img_str}")

        s3_save_response = generate_and_save_image(event, base_64_img_str)
        return s3_save_response

    except ClientError as e:
        LOG.error(f"Exception raised while execution and the error is {e}")
