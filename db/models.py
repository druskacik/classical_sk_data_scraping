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
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
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
    program_analysis_eligible = Column(Boolean, nullable=False, server_default="true")


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
    __table_args__ = (UniqueConstraint("normalized_name", name="uq_composer_normalized_name"),)

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    normalized_name = Column(String, nullable=False)


class ClassicalConcertComposer(Base):
    __tablename__ = "classical_concert_composer"
    __table_args__ = (
        UniqueConstraint(
            "classical_concert_id",
            "composer_id",
            name="uq_classical_concert_composer_link",
        ),
    )

    id = Column(Integer, primary_key=True)
    classical_concert_id = Column(Integer, ForeignKey("classical_concert.id"))
    composer_id = Column(Integer, ForeignKey("composer.id"))


class Work(Base):
    __tablename__ = "work"
    __table_args__ = (
        UniqueConstraint("composer_id", "normalized_title", name="uq_work_composer_title"),
    )

    id = Column(Integer, primary_key=True)
    composer_id = Column(Integer, ForeignKey("composer.id"), nullable=False)
    title = Column(String, nullable=False)
    normalized_title = Column(String, nullable=False)
    catalogue_number = Column(String)


class ClassicalConcertWork(Base):
    __tablename__ = "classical_concert_work"
    __table_args__ = (
        UniqueConstraint(
            "classical_concert_id",
            "work_id",
            name="uq_classical_concert_work_link",
        ),
    )

    id = Column(Integer, primary_key=True)
    classical_concert_id = Column(Integer, ForeignKey("classical_concert.id"), nullable=False)
    work_id = Column(Integer, ForeignKey("work.id"), nullable=False)
    programme_label = Column(Text, nullable=False)
    source_url = Column(Text, nullable=False)
    evidence = Column(Text)


class ConcertProgramAnalysis(Base):
    __tablename__ = "concert_program_analysis"
    __table_args__ = (
        UniqueConstraint(
            "classical_concert_id",
            name="uq_concert_program_analysis_concert",
        ),
    )

    id = Column(Integer, primary_key=True)
    classical_concert_id = Column(Integer, ForeignKey("classical_concert.id"), nullable=False)
    status = Column(String, nullable=False)
    attempts = Column(Integer, nullable=False, server_default="0")
    model = Column(String)
    raw_result = Column(JSONB)
    last_error = Column(Text)
    last_attempted_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
