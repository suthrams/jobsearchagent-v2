# models/__init__.py
# Retained as shared libraries used by v2 (ADR-063): the v2 scrapers import Job /
# JobSource / SalaryRange, AdzunaConfig, and the keyword filters from here. The v1
# Profile model was removed (v2 uses app/schemas/resume_profile.py).
# Usage: from models import Job, JobSource, AppConfig

from models.job import Job, JobSource, WorkMode, ApplicationStatus, CareerTrack, SalaryRange, TrackScore, TrackScores
from models.config_schema import AppConfig
