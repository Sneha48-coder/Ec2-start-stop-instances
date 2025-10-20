import boto3
from datetime import datetime, timezone
import logging
import time as t
import os

# Set up logging
log = logging.getLogger()
log.setLevel(logging.INFO)

# AWS region
region = 'us-east-2'
ssm_client = boto3.client('ssm', region_name=region)

def get_sns_topic_arn():
    """Fetch SNS topic ARN from SSM Parameter Store"""
    try:
        parameter_name = os.environ.get('SNS_TOPIC_PARAM_NAME', 'ec2-notification-sns')
        response = ssm_client.get_parameter(Name=parameter_name)
        return response['Parameter']['Value']
    except Exception as e:
        log.error(f"Failed to get SNS topic ARN from SSM: {e}")
        raise

# SNS topic ARN (Replace with your actual SNS topic ARN)
SNS_TOPIC_ARN = get_sns_topic_arn()

# Lambda function name
LAMBDA_NAME = 'ec2-scheduler-lambda'

def send_sns_notification(subject, message):
    """Send SNS notification with subject and message"""
    sns_client = boto3.client('sns', region_name=region)
    try:
        sns_client.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject=subject,
            Message=message
        )
        log.info(f"SNS Notification sent: {subject}")
    except Exception as e:
        log.error(f"Failed to send SNS notification: {e}")


def lambda_handler(event, context):
    start_time = datetime.now(tz=timezone.utc)
    ec2_client = boto3.client('ec2', region_name=region)

    # Filter to find instances tagged with Reboot=WORKDAY
    workday_filter = [{'Name': 'tag:Reboot', 'Values': ['WORKDAY']}]
    log.info("Fetching instances with tag Reboot=WORKDAY")

    # Get all instances matching the tag
    response = ec2_client.describe_instances(Filters=workday_filter)

    for reservation in response['Reservations']:
        for instance in reservation['Instances']:
            instance_id = instance['InstanceId']
            instance_state = instance['State']['Name']
            instance_ip = instance.get('PrivateIpAddress', 'N/A')
            instance_type = instance.get('InstanceType', 'N/A')
            instance_region = region  # region from your config

            # Default values for tags
            instance_name = 'N/A'
            environment = 'N/A'
            owner = 'N/A'

            if 'Tags' in instance:
                for tag in instance['Tags']:
                    if tag['Key'] == 'Name':
                        instance_name = tag['Value']
                    elif tag['Key'] == 'Environment':
                        environment = tag['Value']
                    elif tag['Key'] == 'Owner':
                        owner = tag['Value']

            log.info(f"Checking instance {instance_id} ({instance_name}, {instance_ip}) - Current state: {instance_state}")

            # Determine action based on working hours
            current_utc_time = datetime.now(tz=timezone.utc).time()
            workday_start = datetime.strptime("03:30:00", "%H:%M:%S").time()
            workday_stop = datetime.strptime("8:00:00", "%H:%M:%S").time()

            action = None
            previous_state = instance_state
            try:
                if workday_start <= current_utc_time < workday_stop:
                    # START instances
                    if instance_state == 'stopped':
                        ec2_client.start_instances(InstanceIds=[instance_id])
                        action = 'STARTED'
                        current_state = 'running'
                    else:
                        current_state = instance_state
                else:
                    # STOP instances
                    if instance_state == 'running':
                        ec2_client.stop_instances(InstanceIds=[instance_id])
                        action = 'STOPPED'
                        current_state = 'stopped'
                    else:
                        current_state = instance_state

                end_time = datetime.now(tz=timezone.utc)
                duration = (end_time - start_time).total_seconds()

                if action:
                    # Send SNS with all details
                    message = (
                        f"Action        : {action}\n"
                        f"Instance ID   : {instance_id}\n"
                        f"Instance Name : {instance_name}\n"
                        f"Region        : {instance_region}\n"
                        f"Instance Type : {instance_type}\n"
                        f"Previous State: {previous_state}\n"
                        f"Current State : {current_state}\n"
                        f"Environment   : {environment}\n"
                        f"Owner         : {owner}\n"
                        f"Triggered By  : EventBridge Rule - ec2-start-stop-instances\n"
                        f"Lambda Name   : {LAMBDA_NAME}\n"
                        f"Execution Time: {end_time.isoformat()}\n"
                        f"Duration      : {duration:.2f} seconds\n"
                        f"Result        : SUCCESS"
                    )
                    send_sns_notification(f"EC2 Instance {action}", message)

            except Exception as e:
                log.error(f"Error processing instance {instance_id}: {e}")
                # Send failure SNS
                message = (
                    f"Action        : {action if action else 'N/A'}\n"
                    f"Instance ID   : {instance_id}\n"
                    f"Instance Name : {instance_name}\n"
                    f"Region        : {instance_region}\n"
                    f"Instance Type : {instance_type}\n"
                    f"Previous State: {previous_state}\n"
                    f"Current State : ERROR\n"
                    f"Environment   : {environment}\n"
                    f"Owner         : {owner}\n"
                    f"Triggered By  : EventBridge Rule - ec2-start-stop-instances\n"
                    f"Lambda Name   : {LAMBDA_NAME}\n"
                    f"Execution Time: {datetime.now(tz=timezone.utc).isoformat()}\n"
                    f"Duration      : {(datetime.now(tz=timezone.utc) - start_time).total_seconds():.2f} seconds\n"
                    f"Result        : FAILURE\n"
                    f"Error         : {str(e)}"
                )
                send_sns_notification(f"EC2 Instance {action if action else 'FAILED'}", message)

    log.info("EC2 start/stop check and SNS notification completed successfully.")

    return {
        'statusCode': 200,
        'body': 'EC2 start/stop check completed successfully.'
    }
