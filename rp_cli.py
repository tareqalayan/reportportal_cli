import sys
import argparse
import logging
import yaml
import requests
import json
import time
import traceback
import zipfile
import os
import xmltodict

from reportportal_client import ReportPortalServiceAsync

# default log file name
LOG_FILE_NAME = 'rp_cli.log'
# log levels mapping
LOG_LEVELS = {
    'debug': logging.DEBUG,
    'info':  logging.INFO,
    'warning': logging.WARNING,
    'error': logging.ERROR,
    'critical': logging.CRITICAL,
    }

DEFAULT_LOG_LEVEL = "info"

logger = logging.getLogger("rp_cli.py")


def zip_dir(path, zip_file):
    for root, dirs, files in os.walk(path):
        for f in files:
            zip_file.write(os.path.join(root, f))


def timestamp():
    return str(int(time.time() * 1000))


def init_logger(level, filename=LOG_FILE_NAME):
    handler = logging.FileHandler(filename)
    formatter = logging.Formatter(
        "%(asctime)s:%(name)s:%(levelname)s:%(threadName)s:%(message)s"
    )
    handler.setFormatter(formatter)
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    root_logger.setLevel(LOG_LEVELS.get(level, logging.NOTSET))


class MyCustomizations:
    def __init__(self):
        pass

    @staticmethod
    def my_error_handler(exc_info):
        """
        This callback function will be called by async service client when error occurs.
        Return True if error is not critical and you want to continue work.

        Args:
            exc_info: result of sys.exc_info() -> (type, value, traceback)

        """
        logger.error("Error occurred: {}".format(exc_info[1]))
        traceback.print_exception(*exc_info)

    @staticmethod
    def extract_failure_msg_from_xunit(case):
        text = ""
        data = case.get('failure', case.get('error'))
        if isinstance(data, list):
            for err in data:
                text += '{txt}\n'.format(txt=err.get('#text'))
            return text
        return data.get('#text')

    @staticmethod
    def get_logs_per_test_path(case):
        name = case.get('@classname') + '.' + case.get('@name')
        return '/'.join(name.split('.')[1:])

    @staticmethod
    def get_tags(case):
        tags = list()
        tags.append(case.get('@classname').split('.')[1])

        return tags
# END: Class MyCustomization


class RpManager:
    def __init__(self, config, strategy=MyCustomizations):
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
        self.strategy = strategy
        self.service = ReportPortalServiceAsync(
            endpoint=self.url, project=self.project, token=self.uuid, error_handler=self.strategy.my_error_handler
        )
        self.test_logs = config.get('test_logs')
        self.zipped = config.get('zipped')

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
            name=self.launch_name, start_time=timestamp(), description=self.launch_description, tags=self.launch_tags)

    def _end_launch(self):
        self.service.finish_launch(end_time=timestamp())
        self.service.terminate()

    def upload_test_case_attachments(self, path):
        for root, dirs, files in os.walk(path):
            for log_file in files:
                with open(root+"/"+log_file, "rb") as fh:
                    attachment = {
                        "name": log_file,
                        "data": fh.read(),
                        "mime": "text/plain"
                    }
                    self.service.log(timestamp(), log_file, "INFO", attachment)

    def upload_zipped_test_case_attachments(self, zip_file_name, path):
        with zipfile.ZipFile(zip_file_name, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            zip_dir(self.test_logs + '/' + path, zip_file)

        with open(zip_file_name, "rb") as fh:
            attachment = {
                "name": os.path.basename(zip_file_name),
                "data": fh.read(),
                "mime": "application/octet-stream"
            }
            self.service.log(timestamp(), "Logs for test case", "INFO", attachment)

    def _log_message_to_rp_console(self, msg, level):
        self.service.log(
            time=timestamp(),
            message=msg,
            level=level
        )

    def _process_failed_case(self, case):
        msg = self.strategy.extract_failure_msg_from_xunit(case)
        self._log_message_to_rp_console(msg, "ERROR")

    def _attach_logs_to_failed_case(self, case):
        path_to_logs_per_test = self.strategy.get_logs_per_test_path(case)
        if self.zipped:
            # zip logs per test and upload zip file
            self.upload_zipped_test_case_attachments("{0}.zip".format(case.get('@name')), path_to_logs_per_test)
        else:
            # upload logs per tests one by one and do not zip them
            self.upload_test_case_attachments("{0}/{1}".format(self.test_logs, path_to_logs_per_test))

    def feed_results(self):
        self._start_launch()

        with open(self.xunit_feed) as fd:
            data = xmltodict.parse(fd.read())

        xml = data.get("testsuite").get("testcase")

        for case in xml:
            name = "{class_name}.{tc_name}".format(class_name=case.get('@classname'), tc_name=case.get('@name'))
            description = "{tc_name} time: {case_time}".format(tc_name=case.get('@name'), case_time=case.get('@time'))
            tags = self.strategy.get_tags(case)

            self.service.start_test_item(
                name=name[:255],
                description=description,
                tags=tags,
                start_time=timestamp(),
                item_type="STEP",
            )
            # Create text log message with INFO level.
            if case.get('system_out'):
                self._log_message_to_rp_console(case.get('system_out'), "INFO")

            if case.get('skipped'):
                status = 'SKIPPED'
                self._log_message_to_rp_console(case.get('skipped').get('@message'), "DEBUG")
            elif case.get('failure') or case.get('error'):  # Error or failed cases
                status = 'FAILED'
                self._process_failed_case(case)
                if self.test_logs:
                    self._attach_logs_to_failed_case(case)
            else:
                status = 'PASSED'

            self.service.finish_test_item(end_time=timestamp(), status=status)

        # Finish launch.
        self._end_launch()
# End class RpManager


def parse_configuration_file(config):
    """
    Parses the configuration file.

    Returns: dictionary containing the configuration file data
    """

    try:
        with open(config, 'r') as stream:
            conf_data = yaml.load(stream)
    except (OSError, IOError) as error:
        logger.error("Failed when opening config file. Error: %s", error)
        sys.exit(1)

    # Check configuration file:
    if not all(key in conf_data for key in ['rp_endpoint', 'rp_uuid', 'rp_project']):
        logger.error('Configuration file missing one of: rp_endpoint, rp_uuid or rp_project')
        sys.exit(1)

    return conf_data


def parser():
    """
    Parses module arguments.

    Returns: A dictionary containing parsed arguments
    """
    rp_parser = argparse.ArgumentParser()
    rp_parser.add_argument(
        "--config", type=str, required=True,
        help="Configuration file path",
    )
    rp_parser.add_argument(
        "--upload_xunit", type=str, required=False,
        help="launch_name.zip: zip file contains the xunit.xml",
    )
    rp_parser.add_argument(
        "--launch_name", type=str, required=False,
        help="Description of the launch",
    )
    rp_parser.add_argument(
        "--launch_description", type=str, required=False,
        help="Description of the launch",
    )
    rp_parser.add_argument(
        "--launch_tags", type=str, required=False,
        help="Tags for that launch",
    )
    rp_parser.add_argument(
        "--xunit_feed", type=str, required=False,
        help="Parse xunit and feed data to report portal",
    )
    rp_parser.add_argument(
        "--test_logs", type=str, required=False,
        help="Path to folder where all logs per tests are located.",
    )
    rp_parser.add_argument(
        "--zipped", action='store_true',
        help="True to upload the logs zipped to save time and traffic",
    )
    rp_parser.add_argument(
        "--log_file", type=str, required=False, default=LOG_FILE_NAME,
        help="Log filename for rp_cli (default %s)" % (LOG_FILE_NAME, ),
    )
    rp_parser.add_argument(
        "--log_level", required=False, default=DEFAULT_LOG_LEVEL,
        choices=LOG_LEVELS.keys(),
        help="Log level (default %s)" % (DEFAULT_LOG_LEVEL, ),
    )
    return rp_parser


if __name__ == "__main__":

    rp_parser = parser()
    args = rp_parser.parse_args()
    init_logger(args.log_level, args.log_file)
    logger.info("Start")

    config_data = parse_configuration_file(args.config)
    config_data.update(args.__dict__)

    strategy = MyCustomizations()
    rp = RpManager(config_data, strategy)

    if args.upload_xunit:
        launch_id = rp.import_results()
        rp.verify_upload_succeeded(launch_id)
        rp.update_launch_description_and_tags(launch_id)
    elif args.xunit_feed and args.launch_name:
        rp.feed_results()
    else:
        logger.error("Bad command - see the usage!")
        rp_parser.print_help()
        sys.exit(1)
    logger.info("Finish")
