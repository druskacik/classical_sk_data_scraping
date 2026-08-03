from dataclasses import dataclass, field
import logging
import re
from typing import Any, Literal

import pandas as pd

from .classical import upload_concerts, upload_potential_concerts
from observability import configure_logging


UploadTarget = Literal['classical', 'potential']
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CrawlerConfig:
    slug: str
    source: str
    source_url: str
    country_code: str | None = 'SK'
    columns: list[str] | None = None
    upload_target: UploadTarget = 'classical'
    dedupe_subset: list[str] | None = None
    front_fields: list[tuple[str, Any]] = field(default_factory=list)
    csv_path: str | None = None

    def __post_init__(self):
        if self.country_code is None:
            return
        country_code = self.country_code.upper()
        if not re.fullmatch(r'[A-Z]{2}', country_code):
            raise ValueError(f'country_code must be an ISO 3166-1 alpha-2 code, got {self.country_code!r}')
        object.__setattr__(self, 'country_code', country_code)

    @property
    def save_path(self) -> str:
        return self.csv_path or f'data/{self.slug}.csv'


class BaseCrawler:
    config: CrawlerConfig

    def scrape(self) -> list[dict]:
        raise NotImplementedError

    def build_dataframe(self, records: list[dict]) -> pd.DataFrame:
        return pd.DataFrame(records, columns=self.config.columns)

    def transform(self, df: pd.DataFrame) -> pd.DataFrame:
        return df

    def upload(self, records: list[dict]) -> tuple[int, int]:
        if self.config.upload_target == 'potential':
            return upload_potential_concerts(records)
        return upload_concerts(records)

    def prepare_records(self, records: list[dict]) -> list[dict]:
        """Apply production transformations without writing or uploading."""
        df = self.build_dataframe(records)
        df = self.transform(df)

        for column, value in self.config.front_fields:
            df.insert(0, column, value)

        if 'country_code' not in df.columns and self.config.country_code is None:
            raise ValueError(
                'country_code is required on every record when CrawlerConfig.country_code is None'
            )
        if 'country_code' not in df.columns:
            df.insert(0, 'country_code', self.config.country_code)
        else:
            df['country_code'] = df['country_code'].apply(
                lambda value: value.upper() if isinstance(value, str) else value
            )

        if self.config.dedupe_subset:
            df.drop_duplicates(subset=self.config.dedupe_subset, inplace=True)

        return df.to_dict(orient='records')

    def run(self):
        configure_logging()
        context = {'crawler': self.config.slug, 'source_url': self.config.source_url}
        logger.info('Getting concerts', extra={'event': 'crawler_started', **context})
        try:
            return self._run(context)
        except Exception as error:
            logger.exception(
                'Crawler failed',
                extra={
                    'event': 'crawler_failed',
                    'error_type': type(error).__name__,
                    'error_message': str(error),
                    **context,
                },
            )
            raise

    def _run(self, context: dict[str, str]):
        records = self.scrape()
        logger.info(
            'Scrape completed',
            extra={'event': 'crawler_scrape_completed', 'record_count': len(records), **context},
        )

        records = self.prepare_records(records)
        df = pd.DataFrame(records)

        save_path = self.config.save_path
        df.to_csv(save_path, index=False)
        logger.info(
            'CSV backup saved',
            extra={'event': 'crawler_csv_saved', 'path': save_path, 'record_count': len(records), **context},
        )
        logger.info(
            'Uploading concerts',
            extra={'event': 'crawler_upload_started', 'record_count': len(records), **context},
        )
        inserted_count, skipped_count = self.upload(records)
        logger.info(
            'Upload completed',
            extra={
                'event': 'crawler_upload_completed',
                'inserted_count': inserted_count,
                'skipped_count': skipped_count,
                **context,
            },
        )
        return records
