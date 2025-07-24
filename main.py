import os
import json
import logging
import requests
from flask import Flask, request, jsonify
from urllib.parse import parse_qs, urlencode

# Configure logging
# In a serverless environment, logs are typically sent to standard output
# and collected by the platform's logging service (e.g., Google Cloud Logging).
logging.basicConfig(level=logging.INFO)

app = Flask(__name__)

# --- Configuration ---
# API configurations are kept the same.
API_CONFIGS = [
    {"BaseUrl": "https://partners-us-east1.zeronetworks.com/api/v1/internal", "KeyName": "us_east1_key", "Region": "us-east1"},
    {"BaseUrl": "https://partners-eu-west12.zeronetworks.com/api/v1/internal", "KeyName": "eu_west12_key", "Region": "eu-west12"}
]

DEPLOYMENTS_URI = "/deployments"
# It's best practice to fetch sensitive IDs from environment variables.
SLACK_CHANNEL = os.environ.get("SLACK_CHANNEL", "C123ABC456") # Replace with your Slack channel ID or set as env var

def invoke_api_call(base_url, endpoint, headers):
    """Calls the specified API endpoint."""
    full_url = f"{base_url}{endpoint}"
    try:
        response = requests.get(full_url, headers=headers)
        # This will raise an HTTPError for bad responses (4xx or 5xx)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logging.error(f"API Call Failed to {full_url}: {e}")
        return None
    except json.JSONDecodeError as e:
        logging.error(f"Failed to decode JSON from {full_url}: {e}")
        return None


def format_slack_message(environments):
    """Formats the Slack message with environment details and deployment buttons."""
    blocks = [{
        "type": "header",
        "text": {
            "type": "plain_text",
            "text": "Select Environment to pull logs",
            "emoji": True
        }
    }]

    for env in environments:
        # Use a consistent emoji for each region
        emoji = ":football:" if env.get('region') == "us-east1" else ":soccer:"
        env_name = env.get('Name') or env.get('name') or "Unknown Name"
        env_id = env.get('id') or "Unknown ID"

        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"{emoji} `{env_name} : {env_id}`"
            }
        })

        # Get deployments for each environment
        deployment_headers = env.get('_headers', {}).copy()
        deployment_headers['zn-env-id'] = env_id
        base_url = env.get('_baseUrl')
        deployment_response = invoke_api_call(base_url, DEPLOYMENTS_URI, deployment_headers)

        if deployment_response and "detailedDeploymentsFormatted" in deployment_response:
            deployments = deployment_response["detailedDeploymentsFormatted"]
            if not deployments:
                 blocks.append({
                    "type": "section",
                    "text": { "type": "mrkdwn", "text": "_No deployments found for this environment._" }
                })
                 continue

            for dep in deployments:
                # Value for the button action, serialized as a JSON string
                value = json.dumps({
                    "id": env_id,
                    "region": env.get('region'),
                    "deployment": dep.get('id')
                })
                # Set button style based on deployment state
                style = "primary" if dep.get('state') == "Primary" else "danger"
                dep_name = dep.get('name') or "Unnamed Deployment"

                blocks.append({
                    "type": "actions",
                    "elements": [{
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "emoji": True,
                            "text": f"Get Logs for {dep_name}"
                        },
                        "value": value,
                        "action_id": "get_logs",
                        "style": style
                    }]
                })
        elif deployment_response is None:
            # This case handles network/API errors from invoke_api_call
            blocks.append({
                "type": "section",
                "text": { "type": "mrkdwn", "text": "*Error fetching deployments.*" }
            })
        else:
            # This case handles successful API calls that don't find deployments
            blocks.append({
                "type": "section",
                "text": { "type": "mrkdwn", "text": "*No deployments found.*" }
            })

    return {
        "channel": SLACK_CHANNEL,
        "text": "Get Support Details", # Fallback text for notifications
        "blocks": blocks
    }

@app.route('/', methods=['GET'])
def health_check():
    """
    Health check endpoint.
    Cloud Run sends a GET request to / to check if the container is healthy.
    """
    return "Service is healthy.", 200


@app.route('/', methods=['POST'])
def slack_trigger():
    """Handles the Slack app trigger from a slash command."""
    try:
        # The request body from Slack is URL-encoded form data.
        body = parse_qs(request.get_data(as_text=True))
        text = body.get('text', [None])[0]

        if not text:
            return jsonify({
                "response_type": "ephemeral",
                "text": "Please provide an environment name to search for. Usage: `/your-command <env-name>`"
            }), 200

        # Construct environment search URI
        # Using a filter to search for the environment name
        encoded_text = urlencode({
            '_filters': json.dumps([{'id': 'name', 'includeValues': [text], 'excludeValues': []}]),
            '_limit': 5
        })
        env_uri = f"/provisioning/environments/?{encoded_text}"

        aggregated_results = []
        for config in API_CONFIGS:
            # In Cloud Run, environment variables are set in the service configuration.
            secret_value = os.environ.get(config['KeyName'])
            if not secret_value:
                logging.warning(f"Environment variable '{config['KeyName']}' not set. Skipping region {config['Region']}.")
                continue

            headers = {
                "Authorization": secret_value,
                "content-type": "application/json"
            }
            response_obj = invoke_api_call(config['BaseUrl'], env_uri, headers)
            
            if response_obj and "items" in response_obj:
                for item in response_obj['items']:
                    # Augment each result with metadata for later use
                    item['region'] = config['Region']
                    item['_baseUrl'] = config['BaseUrl']
                    item['_headers'] = headers
                    aggregated_results.append(item)

        logging.info(f"Found {len(aggregated_results)} total results for '{text}'")

        if aggregated_results:
            slack_message = format_slack_message(aggregated_results)
            SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")
            
            if not SLACK_WEBHOOK_URL:
                logging.error("SLACK_WEBHOOK_URL environment variable is not set.")
                return jsonify({
                    "response_type": "ephemeral",
                    "text": "Error: The Slack webhook is not configured on the server."
                }), 500

            # Post the detailed message to the specified channel
            slack_response = requests.post(SLACK_WEBHOOK_URL, headers={'Content-Type': 'application/json'}, json=slack_message)
            slack_response.raise_for_status()
            
            # Send an ephemeral confirmation message back to the user who triggered the command
            return jsonify({
                "response_type": "ephemeral",
                "text": f"Found {len(aggregated_results)} matching environments. Details sent to the designated channel."
            }), 200
        else:
            return jsonify({
                "response_type": "ephemeral",
                "text": f"No results found for '{text}' in any region."
            }), 200

    except Exception as e:
        logging.exception("An unhandled error occurred in slack_trigger")
        return jsonify({
            "response_type": "ephemeral",
            "text": "A critical error occurred while processing your request."
        }), 500

# The __main__ block is removed. The container's entrypoint (gunicorn)
# will be responsible for running the app.
