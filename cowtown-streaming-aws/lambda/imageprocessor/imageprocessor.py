# Copyright 2017 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# Licensed under the Amazon Software License (the "License"). You may not use this file except in compliance with the License. A copy of the License is located at
#     http://aws.amazon.com/asl/
# or in the "license" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and limitations under the License.

from __future__ import print_function
import base64
import datetime
import time
from decimal import Decimal
import uuid
import json
import pickle
import boto3
import pytz
from pytz import timezone
from copy import deepcopy

def load_config():
    '''Load configuration from file.'''
    with open('imageprocessor-params.json', 'r') as conf_file:
        conf_json = conf_file.read()
        return json.loads(conf_json)

def convert_ts(ts, config):
    '''Converts a timestamp to the configured timezone. Returns a localized datetime object.'''
    #lambda_tz = timezone('US/Pacific')
    tz = timezone(config['timezone'])
    utc = pytz.utc
    
    utc_dt = utc.localize(datetime.datetime.utcfromtimestamp(ts))

    localized_dt = utc_dt.astimezone(tz)

    return localized_dt


def process_image(event, context):

    print('[INFO] Enter process_image/imageprocessor ...')

    #Initialize clients
    rekog_client = boto3.client('rekognition')
    sns_client = boto3.client('sns')
    s3_client = boto3.client('s3')
    dynamodb = boto3.resource('dynamodb')

    #Load config
    config = load_config()

    s3_bucket = config["s3_bucket"]
    s3_key_frames_root = config["s3_key_frames_root"]

    ddb_table = dynamodb.Table(config["ddb_table"])
      
    rekog_max_labels = config["rekog_max_faces"]
    rekog_min_conf = float(config["rekog_min_conf"])
    collection_name = str(config["face_collection_id"])

    name_watch_list = config["name_watch_list"]
    name_watch_min_conf = float(config["name_watch_min_conf"])
    name_watch_phone_num = config.get("name_watch_phone_num", "")
    name_watch_sns_topic_arn = config.get("name_watch_sns_topic_arn", "")

    #Iterate on frames fetched from Kinesis
    print('[INFO] #Iterate on frames fetched from Kinesis ...')
    for record in event['Records']:

        frame_package_b64 = record['kinesis']['data']
        frame_package = pickle.loads(base64.b64decode(frame_package_b64))

        img_bytes = frame_package["ImageBytes"]
        approx_capture_ts = frame_package["ApproximateCaptureTime"]
        frame_count = frame_package["FrameCount"]
        
        now_ts = time.time()

        frame_id = str(uuid.uuid4())
        processed_timestamp = Decimal(now_ts)
        approx_capture_timestamp = Decimal(approx_capture_ts)
        
        now = convert_ts(now_ts, config)
        year = now.strftime("%Y")
        mon = now.strftime("%m")
        day = now.strftime("%d")
        hour = now.strftime("%H")

        try:
            print('[INFO] Perform face recognition ...')
            rekog_response = rekog_client.search_faces_by_image(
                CollectionId= collection_name,
                Image={
                    'Bytes': img_bytes
                },
                MaxFaces=rekog_max_labels,
                FaceMatchThreshold=rekog_min_conf
            )
        except Exception as e:
            #Log error and ignore frame. You might want to add that frame to a dead-letter queue.
            print(e)
            return

        print('[INFO] rekog_response')
        print(rekog_response)

        #Iterate on rekognition labels. Enrich and prep them for storage in DynamoDB
        name_on_watch_list = []
        for face in rekog_response['FaceMatches']:

            name = str(face['Face']['ExternalImageId']).split('.')[0]
            similarity = face['Similarity']
            
            # lbl = label['Name']
            # conf = label['Confidence']
            face['OnWatchList'] = False

            #Print labels and confidence to lambda console
            print('{} .. conf %{:.2f}'.format(name, similarity))

            #Check label watch list and trigger action
            if (name.lower() in (name.lower() for name in name_watch_list)
                and similarity >= name_watch_min_conf):

                face['OnWatchList'] = True
                name_on_watch_list.append(deepcopy(face))

            #Convert from float to decimal for DynamoDB
            face['Similarity'] = Decimal(similarity)

            face['Face']['BoundingBox']['Width'] = Decimal(face['Face']['BoundingBox']['Width'])
            face['Face']['BoundingBox']['Height'] = Decimal(face['Face']['BoundingBox']['Height'])
            face['Face']['BoundingBox']['Left'] = Decimal(face['Face']['BoundingBox']['Left'])
            face['Face']['BoundingBox']['Top'] = Decimal(face['Face']['BoundingBox']['Top'])
            face['Face']['Confidence'] = Decimal(face['Face']['Confidence'])

        #Send out notification(s), if needed
        if len(name_on_watch_list) > 0 \
                and (name_watch_phone_num or name_watch_sns_topic_arn):

            notification_txt = 'On {}...\n'.format(now.strftime('%x, %-I:%M %p %Z'))

            for face in name_on_watch_list:

                notification_txt += '- "{}" was detected with {}% confidence.\n'.format(
                    str(face['Face']['ExternalImageId']).split('.')[0],
                    round(face['Similarity'], 2))

            print(notification_txt)

            if name_watch_phone_num:
                sns_client.publish(PhoneNumber=name_watch_phone_num, Message=notification_txt)

            if name_watch_sns_topic_arn:
                resp = sns_client.publish(
                    TopicArn=name_watch_sns_topic_arn,
                    Message=json.dumps(
                        {
                            "message": notification_txt,
                            "labels": name_on_watch_list
                        }
                    )
                )

                if resp.get("MessageId", ""):
                    print("Successfully published alert message to SNS.")

        #Store frame image in S3
        s3_key = (s3_key_frames_root + '{}/{}/{}/{}/{}.jpg').format(year, mon, day, hour, frame_id)
        
        s3_client.put_object(
            Bucket=s3_bucket,
            Key=s3_key,
            Body=img_bytes
        )
        
        #Persist frame data in dynamodb

        item = {
            'frame_id': frame_id,
            'processed_timestamp' : processed_timestamp,
            'approx_capture_timestamp' : approx_capture_timestamp,
            'rekog_labels' : rekog_response['FaceMatches'],
            # 'rekog_orientation_correction' : 
            #     rekog_response['OrientationCorrection'] 
            #     if 'OrientationCorrection' in rekog_response else 'ROTATE_0',
            'processed_year_month' : year + mon, #To be used as a Hash Key for DynamoDB GSI
            's3_bucket' : s3_bucket,
            's3_key' : s3_key
        }

        ddb_table.put_item(Item=item)

    print('Successfully processed {} records.'.format(len(event['Records'])))
    return

def handler(event, context):
    return process_image(event, context)
