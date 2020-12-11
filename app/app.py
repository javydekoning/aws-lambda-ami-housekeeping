import json
import os
import re
import boto3

from datetime import datetime, timedelta
from aws_lambda_powertools import Logger
from botocore.exceptions import ClientError

logger = Logger()

dry_run = bool('true' == str(os.environ.get('DRY_RUN')).lower())


@logger.inject_lambda_context(log_event=True)
def lambda_handler(event, context):
    """Lambda function that automatically cleans outdated AMI's
    """
    ec = boto3.client('ec2')
    ec2 = boto3.resource('ec2')

    if dry_run:
        logger.warning('Running in DRYRUN mode!')

    # Gather AMIs and figure out which ones to delete
    my_images = ec2.images.filter(Owners=['self'])
    logger.debug('Found {} images in {}'.format(
        len(list(my_images)), os.environ['AWS_REGION']))

    # Don't delete images in use
    used_images = {
        instance.image_id for instance in ec2.instances.all()
    }
    logger.debug('Found {} AMIs in use by EC2 instances in {}'.format(
        len(used_images), os.environ['AWS_REGION']))

    # Keep everything < os.environ['MAX_AGE_DAYS']
    recent_images = set()
    for image in my_images:
        created_at = datetime.strptime(
            image.creation_date,
            "%Y-%m-%dT%H:%M:%S.000Z",
        )
        if created_at > datetime.now() - timedelta(int(os.environ['MAX_AGE_DAYS'])):
            recent_images.add(image.id)
            logger.info("Image '{}' ('{}'), created at {} is considered recent, keeping.".format(
                image.name, image.id, image.creation_date))

    # Deregister
    safe = used_images | recent_images
    snapshots = ec2.snapshots.filter(OwnerIds=['self'], Filters=[
                                     {'Name': 'description', 'Values': ['*ami*']}])

    for image in (
        image for image in my_images if image.id not in safe
    ):
        logger.info(
            "Performing action 'deregister' on AMI: {}'".format(image.id))
        try:
            image.deregister(DryRun=dry_run)
        except ClientError as e:
            if 'DryRunOperation' not in str(e):
                raise

        # Delete
        for snapshot in snapshots:
            if snapshot.description.find(image.id) > 0:
                logger.info(
                    "Performing action 'delete' on snapshot: '{}'".format(snapshot.id))
                try:
                    snapshot.delete(DryRun=dry_run)
                except ClientError as e:
                    if 'DryRunOperation' not in str(e):
                        raise
