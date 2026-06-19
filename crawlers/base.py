from dataclasses import dataclass, field
from typing import Any, Literal

import pandas as pd

from .classical import upload_concerts, upload_potential_concerts


UploadTarget = Literal['classical', 'potential']


@dataclass(frozen=True)
class CrawlerConfig:
    slug: str
    source: str
    source_url: str
    country_code: str = 'SK'
    columns: list[str] | None = None
    upload_target: UploadTarget = 'classical'
    dedupe_subset: list[str] | None = None
    front_fields: list[tuple[str, Any]] = field(default_factory=list)
    csv_path: str | None = None

    def __post_init__(self):
        country_code = self.country_code.upper()
        if len(country_code) != 2:
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

    def run(self):
        print(f'Getting concerts for {self.config.slug.replace("_", ".")} ...')
        records = self.scrape()
        print(f'Found {len(records)} concerts')

        df = self.build_dataframe(records)
        df = self.transform(df)

        for column, value in self.config.front_fields:
            df.insert(0, column, value)

        if 'country_code' not in df.columns:
            df.insert(0, 'country_code', self.config.country_code)
        else:
            df['country_code'] = df['country_code'].apply(lambda value: value.upper() if isinstance(value, str) else value)

        if self.config.dedupe_subset:
            df.drop_duplicates(subset=self.config.dedupe_subset, inplace=True)

        save_path = self.config.save_path
        df.to_csv(save_path, index=False)
        print(f'Saved to {save_path}')

        records = df.to_dict(orient='records')
        print(f'Prepared {len(records)} concerts for upload')

        print('Uploading concerts to the API ...')
        inserted_count, skipped_count = self.upload(records)
        print(f'Uploaded {inserted_count} concerts, skipped {skipped_count} concerts')
        return records
