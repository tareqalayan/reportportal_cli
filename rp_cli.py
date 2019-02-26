import sys
import argparse
import logging
import yaml
import requests
import json
import time
import traceback
import os
import xmltodict
import shutil
from mimetypes import guess_type

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
STRATEGIES = ["Rhv", "Raut", "Cfme", "Cnv"]
DEFAULT_OUT_FILE = "rp_cli.json"

logger = logging.getLogger("rp_cli.py")


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


class Strategy():
    """
    The class holds the interface of handling the xunit file.
    """

    def __init__(self):
        pass

    def my_error_handler(self, exc_info):
        """
        This callback function will be called by async service client when error occurs.

        Args:
            exc_info: result of sys.exc_info() -> (type, value, traceback)

        """
        logger.error("Error occurred: {}".format(exc_info[1]))
        traceback.print_exception(*exc_info)

    def extract_failure_msg_from_xunit(self, case):
        pass

    def get_tags(self, case, test_owners={}):
        pass

    def get_testcase_name(self, case):
        pass

    def get_testcase_description(self, case):
        pass

    def get_logs_per_test_path(self,  case):
        pass

    def should_create_folders_in_launch(self):
        return False

    def create_folder(self, case):
        pass

    def is_first_folder(self):
        pass


class Rhv(Strategy):

    def __init__(self):
        self.current_team = None
        self.first_folder = True

    def extract_failure_msg_from_xunit(self, case):
        text = ""
        data = case.get('failure', case.get('error'))
        if isinstance(data, list):
            for err in data:
                text += '{txt}\n'.format(txt=err.get('#text').encode('ascii', 'ignore'))
            return text
        return data.get('#text')

    def get_logs_per_test_path(self, case):
        name = case.get('@classname') + '.' + case.get('@name')
        return '/'.join(name.split('.')[1:])

    def get_testcase_name(self, case):
        return"{class_name}.{tc_name}".format(class_name=case.get('@classname'), tc_name=case.get('@name'))

    def get_testcase_description(self, case):
        return "{tc_name} time: {case_time}".format(tc_name=case.get('@name'), case_time=case.get('@time'))

    def _get_team_name(self, case):
        return case.get('@classname').split('.')[1]

    def _get_properties(self, case):
        tags = list()

        if 'properties' in case.keys():
            properties = case.get('properties').get('property')

            if not isinstance(properties, list):
                properties = [properties]

            for p in properties:
                tags.append(
                    '{key}:{value}'.format(
                        key=p.get('@name'),
                        value=p.get('@value'),
                    )
                )

        return tags

    def _get_test_owner(self, case, test_owners={}):

        for owner in test_owners.keys():
            for test in test_owners.get(owner):
                if test in case.get('@classname'):
                    return owner
        return

    def get_tags(self, case, test_owners={}):
        tags = list()
        # extract team name
        tags.append(self._get_team_name(case))
        # extract properties like polarion id and bz
        tags.extend(self._get_properties(case))
        # add test owner name to test case according to test_owner.yaml file
        tc_owner = self._get_test_owner(case, test_owners)
        if tc_owner:
            tags.append(tc_owner)

        return tags

    def should_create_folders_in_launch(self):
        return True

    def create_folder(self, case):
        if self.current_team != self._get_team_name(case):
            self.current_team = self._get_team_name(case)
            return True, self.current_team

        return False, self.current_team

    def is_first_folder(self):
        if self.first_folder:
            self.first_folder = False
            return True
        else:
            return False

# END: Class Rhv


class Raut(Rhv):

    def get_logs_per_test_path(self, case):
        name = self.get_testcase_name(case)
        return name.split('.')[-1]

    def get_tags(self, case, test_owners={}):
        tags = list()
        # extract properties like polarion id and bz
        tags.extend(self._get_properties(case))
        # add test owner name to test case according to test_owner.yaml file
        tc_owner = self._get_test_owner(case, test_owners)
        if tc_owner:
            tags.append(tc_owner)

        return tags

    def should_create_folders_in_launch(self):
        return False
# END: Class Raut


class Cfme(Rhv):

    # These properties will be attached as a simple (not key:value pair) tag to each test case
    properties_to_parse = ['rhv_tier']

    @staticmethod
    def get_testcase_name(case):
        """Example: cfme/tests/test_rest.py::test_product_info[rhv_cfme_integration]"""
        file, classname, name = case.get("@file"), case.get("@classname").split(".")[-1], case.get("@name")
        # If a test case is encapsulated in pytest class, include the class in test case signature
        if classname.startswith('Test'):
            return "{}::{}::{}".format(file, classname, name)
        else:
            return "{}::{}".format(file, name)

    @staticmethod
    def get_testcase_description(case):
        """Include info on skip reason and time it took to execute."""
        skip_msg = '\n' + case.get('skipped').get('@message') if case.get('skipped') else 'No skip message found on xunit'
        return "Time: {}{}".format(case.get('@time'), skip_msg)

    def get_tags(self, case, test_owners={}):
        """Only get values of properties we are explicitly interested in."""
        if test_owners:
            raise NotImplementedError('Test owners not implemented for CFME.')

        tags = []

        if 'properties' in case.keys():
            properties = case.get('properties').get('property')
            if not isinstance(properties, list):
                properties = [properties]
            for p in properties:
                if p.get("@name") in self.properties_to_parse:
                    tags.append(p.get("@value"))

        return tags

    def should_create_folders_in_launch(self):
        return False

class Cnv(Rhv):

    def get_logs_per_test_path(self, case):
        raise NotImplementedError('Logs per test no implemented for CNV.')

    def get_tags(self, case, test_owners={}):
        tags = list()
        # extract properties like polarion id and bz
        tags.extend(self._get_properties(case))
        # add test owner name to test case according to test_owner.yaml file

        return tags

    def should_create_folders_in_launch(self):
        return False
# END: Class Cnv


class RpManager:
    def __init__(self, config, strategy):
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
        self.launch_public_url = "{url}/ui/#{project_name}/launches/all/%s".format(
            url=self.url, project_name=self.project
        )
        self.launch_id = ''
        self.xunit_feed = config.get('xunit_feed')
        self.launch_name = config.get('launch_name', 'rp_cli-launch')
        self.strategy = strategy
        self.service = ReportPortalServiceAsync(
            endpoint=self.url, project=self.project, token=self.uuid, error_handler=self.strategy.my_error_handler
        )
        self.test_logs = config.get('test_logs')
        self.zipped = config.get('zipped')
        self.test_owners = config.get('test_owners', {})
        self.strategy = strategy

    @staticmethod
    def _check_return_code(req):
        if req.status_code != 200:
            logger.error('Something went wrong status code is %s; MSG: %s', req.status_code, req.json()['message'])
            sys.exit(1)

    def _import_results(self):
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

    def _verify_upload_succeeded(self, launch_id):
        launch_id_url = self.launch_url % launch_id
        req = requests.get(launch_id_url, headers=self.update_headers)
        self._check_return_code(req)
        logger.info('Launch have been created successfully')
        return True

    def _update_launch_description_and_tags(self, launch_id):
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

    def import_results(self):
        self.launch_id = self._import_results()
        self._verify_upload_succeeded(self.launch_id)
        self._update_launch_description_and_tags(self.launch_id)

    def _start_launch(self):
        return self.service.start_launch(
            name=self.launch_name, start_time=timestamp(), description=self.launch_description, tags=self.launch_tags)

    def _end_launch(self):
        self.service.finish_launch(end_time=timestamp())
        self.service.terminate()
        self.launch_id = self.service.rp_client.launch_id

    def _upload_attachment(self, file, name):
        with open(file, "rb") as fh:
            attachment = {
                "name": name,
                "data": fh.read(),
                "mime": guess_type(file)[0]
            }
            self.service.log(timestamp(), name, "INFO", attachment)

    def upload_test_case_attachments(self, path):
        for root, dirs, files in os.walk(path):
            for log_file in files:
                file_name = os.path.join(root, log_file)
                self._upload_attachment(file_name, log_file)

    def upload_zipped_test_case_attachments(self, zip_file_name, path):
        whole_path = os.path.join(self.test_logs, path)
        try:
            ld = os.listdir(whole_path)
        except OSError:
            logger.warning("Path (%s) with log files does not exist!" % (whole_path,))
            return
        # check if there is something to zip
        if len(ld) > 0:
            zip_file_name = shutil.make_archive(zip_file_name, 'zip', whole_path)
            self._upload_attachment(zip_file_name, os.path.basename(zip_file_name))
            os.remove(zip_file_name)

        else:
            logger.warning("There are no logs on the path (%s)!" % (whole_path, ))

    def _log_message_to_rp_console(self, msg, level):
        self.service.log(
            time=timestamp(),
            message=msg,
            level=level
        )

    def _process_failed_case(self, case):
        msg = self.strategy.extract_failure_msg_from_xunit(case)
        self._log_message_to_rp_console(msg, "ERROR")

    def store_launch_info(self, dest):
        launch_url = self.launch_public_url % self.launch_id
        json_data = {
            "rp_launch_url":  launch_url,
            "rp_launch_name": self.launch_name,
            "rp_launch_tags": self.launch_tags,
            "rp_launch_desc": self.launch_description,
            "rp_launch_id":   self.launch_id
        }
        with open(dest, "w") as file:
            json.dump(json_data, file)

    def attach_logs_to_failed_case(self, case):
        path_to_logs_per_test = self.strategy.get_logs_per_test_path(case)

        if self.zipped:
            # zip logs per test and upload zip file
            self.upload_zipped_test_case_attachments("{0}".format(case.get('@name')), path_to_logs_per_test)
        else:
            # upload logs per tests one by one and do not zip them
            self.upload_test_case_attachments("{0}/{1}".format(self.test_logs, path_to_logs_per_test))

    def _open_new_folder(self, folder_name):
        self.service.start_test_item(
            name=folder_name,
            start_time=timestamp(),
            item_type="SUITE",
        )

    def _close_folder(self):
        self.service.finish_test_item(end_time=timestamp(), status=None)

    def feed_results(self):
        self._start_launch()

        with open(self.xunit_feed) as fd:
            data = xmltodict.parse(fd.read())

        xml = data.get("testsuite").get("testcase")

        # if there is only 1 test case, convert 'xml' from dict to list
        # otherwise, 'xml' is always list
        if not isinstance(xml, list):
            xml = [xml]

        xml = sorted(xml, key=lambda k: k['@classname'])

        for case in xml:
            issue = None
            name = self.strategy.get_testcase_name(case)
            description = self.strategy.get_testcase_description(case)
            tags = self.strategy.get_tags(case, test_owners=self.test_owners)

            if self.strategy.should_create_folders_in_launch():
                open_new_folder, folder_name = self.strategy.create_folder(case)
                if self.strategy.is_first_folder():
                    if open_new_folder:
                        self._open_new_folder(folder_name)
                elif open_new_folder:  # in case a new folder should be open, need to close last one and open new one
                    self._close_folder()
                    self._open_new_folder(folder_name)

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

            if case.has_key('skipped'):
                issue = {"issue_type": "NOT_ISSUE"}  # this will cause skipped test to not be "To Investigate"
                status = 'SKIPPED'
                if case.get('skipped'):
                    self._log_message_to_rp_console(case.get('skipped').get('@message'), "DEBUG")
                else:
                    self._log_message_to_rp_console('No skip message is provided', "DEBUG")
            elif case.get('failure') or case.get('error'):  # Error or failed cases
                status = 'FAILED'
                self._process_failed_case(case)

                if self.test_logs:
                    self.attach_logs_to_failed_case(case)
            else:
                status = 'PASSED'
            self.service.finish_test_item(end_time=timestamp(), status=status, issue=issue)

        if self.strategy.should_create_folders_in_launch():
            self._close_folder()

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
    rp_parser.add_argument(
        "--strategy", type=str, required=False, choices=STRATEGIES,
        help="Strategies to handle the xunit file: {0}".format(STRATEGIES),
    )
    rp_parser.add_argument(
        "--store_out_file", nargs="?", const=DEFAULT_OUT_FILE, default=False,
        help="""Produce output file.
                When no name specified
                default name (%s) is used.""" % (DEFAULT_OUT_FILE, ),
    )
    return rp_parser


if __name__ == "__main__":
    rp = None
    rp_parser = parser()
    args = rp_parser.parse_args()
    init_logger(args.log_level, args.log_file)
    logger.info("Start")

    config_data = parse_configuration_file(args.config)
    config_data.update(args.__dict__)

    if args.upload_xunit:
        rp = RpManager(config_data, strategy=Strategy())
        rp.import_results()
    elif args.xunit_feed:
        if not args.strategy:
            rp_parser.error('You must specify --strategy if you use --xunit-feed.')
        if args.strategy == 'Rhv':
            rp = RpManager(config_data, strategy=Rhv())
        elif args.strategy == 'Raut':
            rp = RpManager(config_data, strategy=Raut())
        elif args.strategy == 'Cfme':
            rp = RpManager(config_data, strategy=Cfme())
        elif args.strategy == 'Cnv':
            rp = RpManager(config_data, strategy=Cnv())
        rp.feed_results()
    else:
        logger.error("Bad command - see the usage!")
        rp_parser.print_help()
        sys.exit(1)
    if rp is not None and args.store_out_file:
        rp.store_launch_info(args.store_out_file)
        logger.info("Output file generated in {}.".format(args.store_out_file))
    logger.info("Finish")
