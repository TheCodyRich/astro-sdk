"""Google BigQuery table implementation."""
from typing import Dict, List, Tuple

import pandas as pd
from airflow.providers.google.cloud.hooks.bigquery import BigQueryHook
from google.api_core.exceptions import NotFound as GoogleNotFound
from sqlalchemy import create_engine
from sqlalchemy.engine.base import Engine

from astro import settings
from astro.constants import (
    DEFAULT_CHUNK_SIZE,
    FileLocation,
    FileType,
    LoadExistStrategy,
    MergeConflictStrategy,
)
from astro.databases.base import BaseDatabase
from astro.files import File
from astro.sql.table import Metadata, Table

DEFAULT_CONN_ID = BigQueryHook.default_conn_name


class BigqueryDatabase(BaseDatabase):
    """
    Handle interactions with Bigquery databases. If this class is successful, we should not have any Bigquery-specific
    logic in other parts of our code-base.
    """

    NATIVE_PATHS = {FileLocation.GS: "gs_to_bigquery"}
    NATIVE_PATHS_SUPPORTED_FILE_TYPES = {
        FileType.CSV: "CSV",
        FileType.NDJSON: "NEWLINE_DELIMITED_JSON",
        FileType.PARQUET: "PARQUET",
    }

    illegal_column_name_chars: List[str] = ["."]
    illegal_column_name_chars_replacement: List[str] = ["_"]

    def __init__(self, conn_id: str = DEFAULT_CONN_ID):
        super().__init__(conn_id)

    @property
    def sql_type(self):
        return "bigquery"

    @property
    def hook(self) -> BigQueryHook:
        """Retrieve Airflow hook to interface with the BigQuery database."""
        return BigQueryHook(gcp_conn_id=self.conn_id, use_legacy_sql=False)

    @property
    def sqlalchemy_engine(self) -> Engine:
        """Return SQAlchemy engine."""
        uri = self.hook.get_uri()
        return create_engine(uri)

    @property
    def default_metadata(self) -> Metadata:
        """
        Fill in default metadata values for table objects addressing bigquery databases

        :return:
        """
        return Metadata(schema=settings.SCHEMA, database=self.hook.project_id)

    def schema_exists(self, schema: str) -> bool:
        """
        Checks if a dataset exists in the BigQuery

        :param schema: Bigquery namespace
        """
        try:
            self.hook.get_dataset(dataset_id=schema)
        except GoogleNotFound:
            return False
        return True

    @staticmethod
    def get_merge_initialization_query(parameters: Tuple) -> str:
        """
        Handles database-specific logic to handle constraints
        for BigQuery. The only constraint that BigQuery supports
        is NOT NULL.
        """
        return "RETURN"

    def load_pandas_dataframe_to_table(
        self,
        source_dataframe: pd.DataFrame,
        target_table: Table,
        if_exists: LoadExistStrategy = "replace",
        chunk_size: int = DEFAULT_CHUNK_SIZE,
    ) -> None:
        """
        Create a table with the dataframe's contents.
        If the table already exists, append or replace the content, depending on the value of `if_exists`.

        :param source_dataframe: Local or remote filepath
        :param target_table: Table in which the file will be loaded
        :param if_exists: Strategy to be used in case the target table already exists.
        :param chunk_size: Specify the number of rows in each batch to be written at a time.
        """
        source_dataframe.to_gbq(
            self.get_table_qualified_name(target_table),
            if_exists=if_exists,
            chunksize=chunk_size,
            project_id=self.hook.project_id,
        )

    def merge_table(
        self,
        source_table: Table,
        target_table: Table,
        source_to_target_columns_map: Dict[str, str],
        target_conflict_columns: List[str],
        if_conflicts: MergeConflictStrategy = "exception",
    ) -> None:
        """
        Merge the source table rows into a destination table.
        The argument `if_conflicts` allows the user to define how to handle conflicts.

        :param source_table: Contains the rows to be merged to the target_table
        :param target_table: Contains the destination table in which the rows will be merged
        :param source_to_target_columns_map: Dict of target_table columns names to source_table columns names
        :param target_conflict_columns: List of cols where we expect to have a conflict while combining
        :param if_conflicts: The strategy to be applied if there are conflicts.
        """

        source_columns = list(source_to_target_columns_map.keys())
        target_columns = list(source_to_target_columns_map.values())

        target_table_name = self.get_table_qualified_name(target_table)
        source_table_name = self.get_table_qualified_name(source_table)

        statement = f"MERGE {target_table_name} T USING {source_table_name} S\
            ON {' AND '.join(['T.' + col + '= S.' + col for col in target_conflict_columns])}\
            WHEN NOT MATCHED BY TARGET THEN INSERT ({','.join(target_columns)}) VALUES ({','.join(source_columns)})"

        if if_conflicts == "update":
            update_statement = "UPDATE SET {}".format(
                ", ".join(
                    [
                        f"T.{target_columns[idx]}=S.{source_columns[idx]}"
                        for idx in range(len(target_columns))
                    ]
                )
            )
            statement += f" WHEN MATCHED THEN {update_statement}"
        self.run_sql(sql_statement=statement)

    def check_native_path(self, source_file: File, target_table: Table) -> bool:
        """
        Check if there is an optimised path for source to destination.

        :param source_file: File from which we need to transfer data
        :param target_table: Table that needs to be populated with file data
        """
        file_type = self.NATIVE_PATHS_SUPPORTED_FILE_TYPES.get(source_file.type.name)
        location_type = self.NATIVE_PATHS.get(source_file.location.location_type)
        return bool(location_type and file_type)

    def load_file_to_table_natively(
        self,
        source_file: File,
        target_table: Table,
        if_exists: LoadExistStrategy = "replace",
        **kwargs,
    ):
        """
        Checks if optimised path for transfer between File location to database exists
        and if it does, it transfers it and returns true else false.
        """
        method_name = self.NATIVE_PATHS.get(source_file.location.location_type)
        if method_name:
            transfer_method = self.__getattribute__(method_name)
            transfer_method(
                source_file=source_file,
                target_table=target_table,
                if_exists=if_exists,
                **kwargs,
            )
        else:
            raise ValueError(
                f"No transfer performed since there is no optimised path "
                f"for {source_file.location.location_type} to bigquery."
            )

    def gs_to_bigquery(
        self,
        source_file: File,
        target_table: Table,
        if_exists: LoadExistStrategy = "replace",
        **kwargs,
    ) -> None:
        """
        Transfer data from gcs to bigquery

        :param source_file: Source file that is used as source of data
        :param target_table: Table that will be created on the bigquery
        :param if_exists: Overwrite table if exists. Default 'replace'
        """

        write_disposition_val = {"replace": "WRITE_TRUNCATE", "append": "WRITE_APPEND"}

        try:
            project_id = self.hook.project_id
        except AttributeError:
            raise ValueError(f"conn_id {target_table.conn_id} has no project id.")

        load_job_config = {
            "sourceUris": [source_file.path],
            "destinationTable": {
                "projectId": project_id,
                "datasetId": target_table.metadata.schema,
                "tableId": target_table.name,
            },
            "createDisposition": "CREATE_IF_NEEDED",
            "writeDisposition": write_disposition_val[if_exists],
            "sourceFormat": self.NATIVE_PATHS_SUPPORTED_FILE_TYPES[
                source_file.type.name
            ],
            "autodetect": True,
        }

        # Since bigquery has other options besides used here, we need to expose them to end user.
        # https://cloud.google.com/bigquery/docs/reference/rest/v2/Job#JobConfigurationLoad
        load_job_config.update(kwargs)

        job_config = {
            "jobType": "LOAD",
            "load": load_job_config,
            "labels": {"target_table": target_table.name},
        }
        self.hook.insert_job(
            configuration=job_config,
        )
