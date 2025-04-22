import json
from pydantic import BaseModel
from datetime import datetime, timezone
from pathlib import Path
from typing import List


class Stage(BaseModel):
    stage: int
    created_at: datetime
    coverage: int
    coverage_percent: int


class CrashInstance(BaseModel):
    crash_id: str
    crash_time: datetime

class CrashBucket(BaseModel):
    hsh: str
    info: str
    crash_count: int
    instances: List[CrashInstance]
    has_reproduced: bool = False
    has_minimized: bool = False
    first_found_at: datetime
    last_found_at: datetime

class StageDetails(BaseModel):
    stage: Stage
    crash_buckets: List[CrashBucket]


def get_stages(workdir: Path) -> List[Stage]:
    stages = []
    for stage in workdir.iterdir():
        if stage.name.startswith('stage_'):
            stages.append(Stage(
                stage=int(stage.name.split('_')[1]),
                created_at=stage.stat().st_ctime,
                coverage=0,
                coverage_percent=0
            ))

    stages.sort(key=lambda x: x.stage)
    return stages

    
def get_stage_details(workdir: Path, stage: int) -> StageDetails:
    stage_dir = workdir / f'stage_{stage}'
    if not stage_dir.exists():
        raise ValueError(f'Stage {stage} does not exist')
    
    # Get stage info
    stage_model = Stage(
        stage=stage,
        created_at=stage_dir.stat().st_ctime,
        coverage=0,
        coverage_percent=0
    )
    
    # Get crash buckets
    crash_buckets = []
    crash_bucket_path = stage_dir / 'crash_buckets'
    
    if crash_bucket_path.exists():
        for bucket_dir in crash_bucket_path.iterdir():
            if not bucket_dir.is_dir():
                continue
                
            # Get bucket info
            hsh = bucket_dir.name
            info_path = bucket_dir / 'info'
            info = info_path.read_text().strip() if info_path.exists() else "Unknown crash"
            
            # Check for crashes
            crashes_path = bucket_dir / 'crashes'
            crash_instances = []
            crash_count = 0
            
            if crashes_path.exists():
                for crash_file in crashes_path.glob('crash_*.json'):
                    crash_tracker = json.loads(crash_file.read_text())

                    crash_id = crash_file.stem
                    crash_time: datetime = datetime.strptime(crash_tracker['found_at'], '%Y-%m-%dT%H:%M:%S.%fZ')
                    crash_instances.append(CrashInstance(
                        crash_id=crash_id,
                        crash_time=crash_time
                    ))
                    crash_count += 1
            
            # Check if bucket has reproducers
            reproduced_path = bucket_dir / 'reproduced'
            has_reproduced = reproduced_path.exists() and any(reproduced_path.iterdir())
            
            # Check if bucket has minimized cases
            minimized_path = bucket_dir / 'minimized'
            has_minimized = minimized_path.exists() and any(minimized_path.iterdir())

            first_found_at = min(crash_instances, key=lambda x: x.crash_time).crash_time
            last_found_at = max(crash_instances, key=lambda x: x.crash_time).crash_time
            
            crash_buckets.append(CrashBucket(
                hsh=hsh,
                info=info,
                crash_count=crash_count,
                instances=crash_instances,
                has_reproduced=has_reproduced,
                has_minimized=has_minimized,
                first_found_at=first_found_at,
                last_found_at=last_found_at
            ))

    # Sort by last found at
    crash_buckets.sort(key=lambda x: x.last_found_at, reverse=True)
    
    return StageDetails(
        stage=stage_model,
        crash_buckets=crash_buckets
    )

    
    