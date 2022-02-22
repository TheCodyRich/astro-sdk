"""
Copyright Astronomer, Inc.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""
import pytest

"""
Unittest module to test Operators.

Requires the unittest, pytest, and requests-mock Python libraries.

"""

import logging
import math
import os
import pathlib
import unittest.mock

from airflow.models import DAG
from airflow.utils import timezone
from airflow.utils.state import State
from airflow.utils.types import DagRunType

log = logging.getLogger(__name__)
DEFAULT_DATE = timezone.datetime(2016, 1, 1)

original_import = __import__


def import_mock(name, *args):
    if name in [
        "airflow.providers.google.cloud.hooks.bigquery",
        "airflow.providers.postgres.hooks.postgres",
        "airflow.providers.snowflake.hooks.snowflake",
    ]:
        raise ModuleNotFoundError
    return original_import(name, *args)


class TestAggregateCheckOperator(unittest.TestCase):
    """
    Test Postgres Merge Operator.
    """

    cwd = pathlib.Path(__file__).parent

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    def clear_run(self):
        self.run = False

    def setUp(self):
        super().setUp()
        self.dag = DAG(
            "test_dag",
            default_args={
                "owner": "airflow",
                "start_date": DEFAULT_DATE,
            },
        )

    def create_and_run_task(self, decorator_func, op_args, op_kwargs):
        with self.dag:
            f = decorator_func(*op_args, **op_kwargs)

        dr = self.dag.create_dagrun(
            run_id=DagRunType.MANUAL.value,
            start_date=timezone.utcnow(),
            execution_date=DEFAULT_DATE,
            data_interval=[DEFAULT_DATE, DEFAULT_DATE],
            state=State.RUNNING,
        )
        f.operator.run(start_date=DEFAULT_DATE, end_date=DEFAULT_DATE)
        return f

    def test_missing_bigquery_package(self):
        with unittest.mock.patch("builtins.__import__", import_mock):
            from astro.utils.dependencies import BigQueryHook

            with pytest.raises(RuntimeError) as error:
                BigQueryHook.conn_type

            assert (
                str(error.value)
                == "Error loading the module airflow.providers.google.cloud.hooks.bigquery,"
                " please make sure all the dependencies are installed. try - pip install"
                " astro-projects[google]"
            )

    def test_missing_postgres_package(self):
        with unittest.mock.patch("builtins.__import__", import_mock):
            from astro.utils.dependencies import PostgresHook

            with pytest.raises(RuntimeError) as error:
                PostgresHook.conn_type

            assert (
                str(error.value)
                == "Error loading the module airflow.providers.postgres.hooks.postgres,"
                " please make sure all the dependencies are installed. try - pip install"
                " astro-projects[postgres]"
            )

    def test_missing_snowflake_package(self):
        with unittest.mock.patch("builtins.__import__", import_mock):
            from astro.utils.dependencies import SnowflakeHook

            with pytest.raises(RuntimeError) as error:
                SnowflakeHook.conn_type

            assert (
                str(error.value)
                == "Error loading the module airflow.providers.snowflake.hooks.snowflake,"
                " please make sure all the dependencies are installed. try - pip install"
                " astro-projects[snowflake]"
            )
