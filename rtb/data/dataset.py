import os
import shutil
from pathlib import Path
from typing import Dict

import pandas as pd
from rtb.data.database import Database
from rtb.data.table import Table
from rtb.data.task import Task
from rtb.utils import download_url, one_window_sampler, rolling_window_sampler, unzip
from torch_frame import stype


class Dataset:
    r"""Base class for dataset. A dataset includes a database and tasks defined
    on it."""

    # name of dataset, to be specified by subclass
    name: str

    def __init__(self, root: str | os.PathLike, process=False) -> None:
        r"""Initializes the dataset."""

        self.root = root

        # download
        if not os.path.exists(os.path.join(root, self.name)):
            url = f"http://ogb-data.stanford.edu/data/rtb/{self.name}.zip"
            self.download(url, root)

        path = f"{root}/{self.name}/processed/db"
        if process or not Path(f"{path}/done").exists():
            # delete processed db dir if exists to avoid possibility of corruption
            shutil.rmtree(path, ignore_errors=True)

            # process
            db = self.process()

            db.save(path)
            Path(f"{path}/done").touch()

        # load database
        self._db = Database.load(path)

        # we want to keep the database private, because it also contains
        # test information

        self.min_time, self.max_time = self._db.get_time_range()
        self.train_max_time, self.val_max_time = self.get_cutoff_times()

        self.tasks = self.get_tasks()

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}(\n"
            f"  tables={list(self._db.tables.keys())},\n"
            f"  tasks={list(self.tasks.keys())},\n"
            f"  min_time={self.min_time},\n"
            f"  max_time={self.max_time},\n"
            f"  train_max_time={self.train_max_time},\n"
            f"  val_max_time={self.val_max_time},\n"
            f")"
        )

    def get_tasks(self) -> dict[str, Task]:
        r"""Returns a list of tasks defined on the dataset. To be implemented
        by subclass."""

        raise NotImplementedError

    def get_cutoff_times(self) -> tuple[pd.Timestamp, pd.Timestamp]:
        r"""Returns the train and val cutoff times. To be implemented by
        subclass, but can implement a sensible default strategy here."""

        train_max_time = self.min_time + 0.8 * (self.max_time - self.min_time)
        val_max_time = self.min_time + 0.9 * (self.max_time - self.min_time)
        return train_max_time, val_max_time

    def download(self, url: str, path: str | os.PathLike) -> None:
        r"""Downloads the raw data to the path directory. To be implemented by
        subclass."""

        download_path = download_url(url, path)
        unzip(download_path, path)

    def process(self) -> Database:
        r"""Processes the raw data into a database. To be implemented by
        subclass."""

        raise NotImplementedError

    @property
    def db_train(self) -> Database:
        return self._db.time_cutoff(self.train_max_time)

    @property
    def db_val(self) -> Database:
        return self._db.time_cutoff(self.val_max_time)

    def make_train_table(
        self,
        task_name: str,
        window_size: int | None = None,
        time_window_df: pd.DataFrame | None = None,
    ) -> Table:
        """Returns the train table for a task.

        User can either provide the window_size and get the train table
        generated by our default sampler, or explicitly provide the
        time_window_df obtained by their sampling strategy."""

        if time_window_df is None:
            assert window_size is not None
            # default sampler
            time_window_df = rolling_window_sampler(
                self.min_time,
                self.train_max_time,
                window_size,
                stride=window_size,
            )

        task = self.tasks[task_name]
        return task.make_table(self.db_train, time_window_df)

    def make_val_table(
        self,
        task_name: str,
        window_size: int | None = None,
        time_window_df: pd.DataFrame | None = None,
    ) -> Table:
        r"""Returns the val table for a task.

        User can either provide the window_size and get the train table
        generated by our default sampler, or explicitly provide the
        time_window_df obtained by their sampling strategy."""

        if time_window_df is None:
            assert window_size is not None
            # default sampler
            time_window_df = one_window_sampler(
                self.train_max_time,
                window_size,
            )

        task = self.tasks[task_name]
        return task.make_table(self.db_val, time_window_df)

    def make_test_table(self, task_name: str, window_size: int) -> Table:
        r"""Returns the test table for a task."""

        task = self.tasks[task_name]
        time_window_df = one_window_sampler(
            self.val_max_time,
            window_size,
        )
        table = task.make_table(self._db, time_window_df)

        # hide the label information
        df = table.df
        df.drop(columns=[task.target_col], inplace=True)
        table.df = df
        return table

    @property
    def col_to_stype_dict(self) -> Dict[str, Dict[stype, str]]:
        r"""Specifies col_to_stype for each table. Used as input to
        utils.make_pkey_fkey_graph"""
        if not hasattr(self, "_col_to_stype_dict"):
            raise RuntimeError("col_to_stype_dict has not been set.")
        else:
            return self._col_to_stype_dict

    @col_to_stype_dict.setter
    def col_to_stype_dict(self, col_to_stype_dict: Dict[str, Dict[stype, str]]):
        self._col_to_stype_dict = col_to_stype_dict
