# reportportal_cli

## What does it do?
rp_cli.py is command line utility written in python. It takes xunit output file
and upload it to report portal instance. It is able also to parse the xunit file and feed the results to report portal
while being able to add more information per test like tags and logs.

## Installation
```bash
git clone git@github.com:tareqalayan/reportportal_cli.git
cd reportportal_cli
virtualenv rp-cli
source rp-cli/bin/activate
pip install -rrequierments.txt
```
Modify the rp_conf.yaml:
```plain/text
rp_endpoint: http://reportportal
rp_uuid: 1111111-1111-1111-1111-111111
rp_project: my_project
```

## Usage

### Upload xunit file as is:

In report portal you have the ability to upload xunit file. That can be done by running:
```bash
python rp_cli.py --config rp_conf.yaml --upload_xunit ./my-product-smoke-tests.zip   --launch_description 'some description of the launch '  --launch_tags 'smoke tag1 tag2 tag3'
```
Note that the xunit file should be zipped and the name of the launch in report portal will be the name of the zip file.
However in this case:
1. you will have no tags per test case
2. only the test case name will be shown in the report portal
3. only the system_out or system_err which appears in the xunit file.

### Parse xunit file and send test case results one by one:
Xunit have more information like properties and full class name from wich for example i can tag the test case to make it easy to lookup in reportportal. This can be achieved by running:
```bash
python rp_cli.py --strategy Cnv \
                 --xunit_feed tier1_xunit.xml \
                 --config rp_conf.yaml \
                 --launch_tags 'tier1 tag1 tag2' \
                 --launch_name 'tier1'
```
Note here you can set launch_name via command line.

### You can attach logs per tests:

In report portal you can attach logs per test case, so i took advantage of this ability and it can be done by running:
```bash
python rp_cli.py --strategy Cnv \
                 --xunit_feed tier1_xunit.xml \
                 --config rp_conf.yaml \
                 --launch_tags 'tier1' \
                 --test_logs logs_per_test \
                 --launch_name 'tier1 test'
```

If your logs are big you may consider to upload them zipped:
```bash
python rp_cli.py --strategy Cnv \
                 --xunit_feed tier1_xunit.xml \
                 --config rp_conf.yaml \
                 --launch_tags 'tag1 tag2' \
                 --test_logs logs_per_test \
                 --zipped \
                 --launch_name 'tier1'
```

## My tags, logs are somehwere else..
Yes. I collect different information from xunit and my test logs are found somewhere else how can i still use this utility?
What you need to do is to implement:
```python

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
        """
        True if you are intending to create folders in the report portal
        """
        return False


    def is_first_folder(self):
        """
        Used if you want to split the xunit into folders in RP
        """
        pass

```