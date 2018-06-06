import sys
import argparse
import logging
import yaml
import requests
import json
import junitparser
from time import time
import traceback
from reportportal_client import ReportPortalServiceAsync
import zipfile
import os
# from ipdb import set_trace


logger = logging.getLogger("reportportal_cli.py")


def zipdir(path, ziph):
    # ziph is zipfile handle
    for root, dirs, files in os.walk(path):
        for file in files:
            ziph.write(os.path.join(root, file))


def timestamp():
    return str(int(time() * 1000))


def my_error_handler(exc_info):
    """
    This callback function will be called by async service client when error occurs.
    Return True if error is not critical and you want to continue work.
    :param exc_info: result of sys.exc_info() -> (type, value, traceback)
    :return:
    """
    print("Error occurred: {}".format(exc_info[1]))
    traceback.print_exception(*exc_info)


def extract_error_msg_from_xunit(s, first, last):
    try:
        start = s.index(first) + len(first)
        end = s.index(last, start)
        return s[start:end]
    except ValueError:
        return ""


def get_logs_per_test_path(name):
    return '/'.join(name.split('.')[1:])


class RpManager:
    def __init__(self, config, error_msg_extractor=None):
        self.url = config.get('rp_endpoint')
        self.uuid = config.get('rp_uuid')
        self.project = config.get('rp_project')
        self.launch_description = config.get('launch_description')
        self.launch_tags = config.get('launch_tags').split()
        self.upload_xunit = config.get('upload_xunit')
        self.update_headers = {
            'Authorization': 'bearer %s' % self.uuid,
            'Accept': 'application/json',
            'Cache-Control': 'no-cache',
            'content-type': 'application/json',
        }
        self.import_headers = {
            'Authorization': 'bearer %s' % self.uuid,
            'Accept': 'application/json',
            'Cache-Control': 'no-cache',
        }
        self.launch_url = "{url}/api/v1/{project_name}/launch/%s".format(
            url=self.url, project_name=self.project
        )
        self.xunit_feed = config.get('xunit_feed')
        self.launch_name = config.get('launch_name', 'rp_cli-launch')
        self.service = ReportPortalServiceAsync(
            endpoint=self.url, project=self.project, token=self.uuid, error_handler=my_error_handler
        )
        self.test_logs = config.get('test_logs')
        self.zipped = config.get('zipped')
        self.extract_err_msg = error_msg_extractor

    @staticmethod
    def _check_return_code(req):
        if req.status_code != 200:
            logger.error('Something went wrong status code is %s; MSG: %s', req.status_code, req.json()['message'])
            sys.exit(1)

    def import_results(self):
        with open(self.upload_xunit, 'rb') as xunit_file:
            files = {'file': xunit_file}
            req = requests.post(self.launch_url % "import", headers=self.import_headers, files=files)

        response = req.json()
        self._check_return_code(req)
        logger.info("Import is done successfully")
        response_msg = response['msg'].encode('ascii', 'ignore')
        logger.info('Status code: %s; %s', req.status_code, response_msg)

        # returning the launch_id
        return response_msg.split()[4]

    def verify_upload_succeeded(self, launch_id):

        launch_id_url = self.launch_url % launch_id

        req = requests.get(launch_id_url, headers=self.update_headers)

        self._check_return_code(req)

        logger.info('Launch have been created successfully')

        return True

    def update_launch_description_and_tags(self, launch_id):
        update_url = self.launch_url % launch_id + "/update"

        data = {
            "description": self.launch_description,
            "tags": self.launch_tags
        }

        req = requests.put(url=update_url, headers=self.update_headers, data=json.dumps(data))
        self._check_return_code(req)
        logger.info(
            'Launch description %s and tags %s where updated for launch id %s',
            self.launch_description, self.launch_tags, launch_id
        )

    def _start_launch(self):

        # Start launch.
        return self.service.start_launch(
            name=self.launch_name, start_time=timestamp(), description=self.launch_description)

    def _end_launch(self):
        self.service.finish_launch(end_time=timestamp())
        self.service.terminate()

    def upload_test_case_attachments(self, path):
        # set_trace()
        for root, dirs, files in os.walk(path):
            for log_file in files:
                with open(root+"/"+log_file, "rb") as fh:
                    attachment = {
                        "name": log_file,
                        "data": fh.read(),
                        "mime": "text/plain"  # "application/octet-stream"
                    }
                    self.service.log(timestamp(), log_file, "INFO", attachment)

    def upload_zipped_test_case_attachments(self, zip_file_name, path):
        # set_trace()
        zipf = zipfile.ZipFile(zip_file_name, 'w', zipfile.ZIP_DEFLATED)
        zipdir(self.test_logs + '/' + path, zipf)
        zipf.close()
        with open(zip_file_name, "rb") as fh:
            attachment = {
                "name": os.path.basename(zip_file_name),
                "data": fh.read(),
                "mime": "application/octet-stream"
            }
            self.service.log(timestamp(), "Logs for test case", "INFO", attachment)

    def feed_results(self):
        self._start_launch()
        status = None
        xml = junitparser.JUnitXml.fromfile(self.xunit_feed)
        for case in xml:

            name = case.classname + '.' + case.name
            description = case.name + " time:" + str(case.time)
            tags = list()
            tags.append(case.classname.split('.')[1])

            self.service.start_test_item(
                name=name[:255],
                description=description,
                tags=tags,
                start_time=timestamp(),
                item_type="STEP",
            )
            # Create text log message with INFO level.
            self.service.log(
                time=timestamp(),
                message=str(case.system_out),
                level="INFO"
            )

            if not case.result:
                status = 'PASSED'
            if isinstance(case.result, junitparser.junitparser.Skipped):
                status = 'SKIPPED'
            if isinstance(case.result, junitparser.junitparser.Error):
                status = 'FAILED'
                error = case.result.tostring()
                self.service.log(
                    time=timestamp(),
                    message=case.result.message,
                    level="ERROR"
                )
                self.service.log(
                    time=timestamp(),
                    message=extract_error_msg_from_xunit(error, ">", "</error>"),
                    level="ERROR"
                )
                if self.test_logs:
                    if self.zipped:
                        # zip logs per test and upload zip file
                        path_to_logs_per_test = '/'.join(name.split('.')[1:])
                        self.upload_zipped_test_case_attachments(case.name+".zip", path_to_logs_per_test)

                    else:
                        # set_trace()
                        # upload logs per tests one by one and not zip them
                        path_to_logs_per_test = '/'.join(name.split('.')[1:])
                        self.upload_test_case_attachments(self.test_logs+'/'+path_to_logs_per_test)

            self.service.finish_test_item(end_time=timestamp(), status=status)  # issue=issue)

        # Finish launch.
        self._end_launch()


def init_logger(level):
    fmt = "%(asctime)s:%(name)s:%(levelname)s:%(threadName)s:%(message)s"
    logging.basicConfig(format=fmt, level=level)


def parse_configuration_file(config):
    """
    Parses the configuration file.

    Returns: dictionary containing the configuration file data
    """
    try:
        with open(config, 'r') as stream:
            config_data = yaml.load(stream)
    except (OSError, IOError) as error:
        logger.error("Failed when opening config file. Error: %s", error)
        sys.exit(1)

    # Check configuration file:
    if not all(key in config_data for key in ['rp_endpoint', 'rp_uuid', 'rp_project']):
        logger.error('Configuration file missing one of: rp_endpoint, rp_uuid or rp_project')
        sys.exit(1)

    return config_data


def parser():
    """
    Parses module arguments.

    Returns: A dictionary containing parsed arguments
    """
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config", type=str, required=True,
        help="Configuration file path",
    )
    parser.add_argument(
        "--upload_xunit", type=str, required=False,
        help="launch_name.zip: zip file contains the xunit.xml",
    )
    parser.add_argument(
        "--launch_name", type=str, required=False,
        help="Description of the launch",
    )
    parser.add_argument(
        "--launch_description", type=str, required=False,
        help="Description of the launch",
    )
    parser.add_argument(
        "--launch_tags", type=str, required=False,
        help="Tags for that launch",
    )
    parser.add_argument(
        "--xunit_feed", type=str, required=False,
        help="Parse xunit and feed data to report portal",
    )
    parser.add_argument(
        "--test_logs", type=str, required=False,
        help="Path to folder where all logs per tests are located.",
    )
    parser.add_argument(
        "--zipped", action='store_true',
        help="True to upload the logs zipped to save time and traffic",
    )

    return parser


if __name__ == "__main__":
    init_logger(logging.DEBUG)

    parser = parser()
    args = parser.parse_args()

    config_data = parse_configuration_file(args.config)
    config_data.update(args.__dict__)
    rp = RpManager(config_data, extract_error_msg_from_xunit)

    if args.upload_xunit:
        launch_id = rp.import_results()
        rp.verify_upload_succeeded(launch_id)
        rp.update_launch_description_and_tags(launch_id)
    elif args.xunit_feed and args.launch_name:
        rp.feed_results()
    else:
        logger.error("Bad command see usage:")
        parser.print_help()
