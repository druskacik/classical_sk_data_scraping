from sqlalchemy import (
    ARRAY,
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Text,
    Time,
    func,
)
from sqlalchemy.orm import declarative_base


metadata = MetaData()
Base = declarative_base(metadata=metadata)


class ClassicalConcert(Base):
    __tablename__ = "classical_concert"

    id = Column(Integer, primary_key=True)
    title = Column(String, nullable=False)
    date = Column(Date, nullable=False)
    url = Column(String, nullable=False)
    source = Column(String)
    source_url = Column(String)
    time_from = Column(Time)
    time_to = Column(Time)
    city = Column(String)
    country_code = Column(String(2))
    venue = Column(String)
    type = Column(String)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())
    description = Column(Text)
    is_concert_details_filled = Column(Boolean, server_default="false")
    composers = Column(ARRAY(Text))


class PotentialEvent(Base):
    __tablename__ = "potential_event"

    id = Column(Integer, primary_key=True)
    title = Column(String, nullable=False)
    date = Column(Date, nullable=False)
    url = Column(String, nullable=False)
    source = Column(String)
    source_url = Column(String)
    time_from = Column(Time)
    time_to = Column(Time)
    city = Column(String)
    country_code = Column(String(2))
    venue = Column(String)
    type = Column(String)
    analyzed = Column(Boolean, server_default="false")
    is_classical_concert = Column(Boolean, server_default="false")
    added = Column(Boolean, server_default="false")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())
    description = Column(Text)
    is_concert_details_filled = Column(Boolean, server_default="false")
    composers = Column(ARRAY(Text))


class Composer(Base):
    __tablename__ = "composer"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)


class ClassicalConcertComposer(Base):
    __tablename__ = "classical_concert_composer"

    id = Column(Integer, primary_key=True)
    classical_concert_id = Column(Integer, ForeignKey("classical_concert.id"))
    composer_id = Column(Integer, ForeignKey("composer.id"))
